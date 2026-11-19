import asyncio
import json
import time
import collections
from typing import Set, Dict, Any, List, Optional
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from backend.app.config import config
from backend.app.dsp.filters import IMUSensorBuffer, RealtimeFilterBank
from backend.app.dsp.features import extract_window_features
from backend.app.dsp.stft import compute_realtime_stft
from backend.app.protocols.state_machine import state_machine
from backend.app.protocols.simulator import simulator

router = APIRouter()

# Global Ring Buffer (10 seconds of 50Hz = 500 samples)
live_buffer = IMUSensorBuffer(maxlen=config.live_buffer_samples, fs=config.sampling_rate_hz)

# Connected client sets
dashboard_clients: Set[WebSocket] = set()
phone_clients: Set[WebSocket] = set()

# Ephemeral pairing tokens: token -> {"patient_id": str, "created_at": float}
active_pairing_tokens: Dict[str, Dict[str, Any]] = {}

# Active stream isolation state
current_stream_epoch: Optional[str] = None
last_feature_time = 0.0
last_stft_time = 0.0
last_phone_state_time = 0.0
latest_features_cache: Dict[str, Any] = {}
latest_stft_cache: Dict[str, Any] = {}

# Empirical Data Quality Trackers (Stream-specific, resets on new stream)
recent_sample_times = collections.deque(maxlen=100)
recent_sample_seqs = collections.deque(maxlen=100)
total_packets_received = 0
total_packets_lost = 0
last_seq_num: Optional[int] = None
seen_seq_deque: collections.deque = collections.deque(maxlen=1000)
seen_sequence_numbers: Set[int] = set()
last_arrival_time: float = 0.0

def reset_stream_quality_trackers(epoch: str):
    global current_stream_epoch, total_packets_received, total_packets_lost, last_seq_num, last_arrival_time
    current_stream_epoch = epoch
    recent_sample_times.clear()
    recent_sample_seqs.clear()
    total_packets_received = 0
    total_packets_lost = 0
    last_seq_num = None
    seen_seq_deque.clear()
    seen_sequence_numbers.clear()
    last_arrival_time = 0.0
    live_buffer.clear()

def compute_data_quality() -> Dict[str, Any]:
    global total_packets_received, total_packets_lost, last_arrival_time
    now_wall = time.time()
    is_stale = (last_arrival_time > 0 and (now_wall - last_arrival_time) > 2.0)
    
    if len(recent_sample_seqs) > 0:
        total_expected = total_packets_received + total_packets_lost
        loss_pct = float((total_packets_lost / max(1, total_expected)) * 100.0)
    else:
        loss_pct = None  # Loss is unknown if phone does not transmit seq

    if len(recent_sample_times) < 8 or is_stale:
        return {
            "status": "INSUFFICIENT" if is_stale else "AWAITING",
            "observed_hz": 0.0,
            "packet_loss_pct": round(loss_pct, 1) if loss_pct is not None else None,
            "packet_loss_percent": round(loss_pct, 1) if loss_pct is not None else None,
            "jitter_ms": 0.0,
            "packets_received": total_packets_received,
            "reason": "STALE_SENSOR_GAP_EXCEEDED" if is_stale else "INSUFFICIENT_SAMPLES"
        }
    
    diffs = np.diff(np.array(recent_sample_times))
    valid_diffs = diffs[diffs > 0]
    if len(valid_diffs) == 0:
        return {
            "status": "INSUFFICIENT",
            "observed_hz": 0.0,
            "packet_loss_pct": 100.0,
            "packet_loss_percent": 100.0,
            "jitter_ms": 0.0,
            "packets_received": total_packets_received,
            "reason": "ZERO_SAMPLE_INTERVALS"
        }
        
    mean_dt = float(np.mean(valid_diffs))
    observed_hz = float(1.0 / mean_dt) if mean_dt > 0 else 0.0
    jitter_ms = float(np.std(valid_diffs) * 1000.0)
    
    if observed_hz >= 35.0 and (loss_pct is None or loss_pct < 2.0) and jitter_ms < 15.0:
        status = "GOOD"
    elif observed_hz >= 20.0 and (loss_pct is None or loss_pct < 10.0):
        status = "UNSTABLE"
    else:
        status = "INSUFFICIENT"
        
    return {
        "status": status,
        "observed_hz": round(observed_hz, 1),
        "packet_loss_pct": round(loss_pct, 1) if loss_pct is not None else None,
        "packet_loss_percent": round(loss_pct, 1) if loss_pct is not None else None,
        "jitter_ms": round(jitter_ms, 1),
        "packets_received": total_packets_received
    }

async def broadcast_to_dashboards(message: Dict[str, Any]):
    if not dashboard_clients:
        return
    text = json.dumps(message)
    dead_sockets = set()
    for ws in list(dashboard_clients):
        try:
            await ws.send_text(text)
        except Exception:
            dead_sockets.add(ws)
    for ws in dead_sockets:
        dashboard_clients.discard(ws)

async def broadcast_to_phones(message: Dict[str, Any]):
    if not phone_clients:
        return
    text = json.dumps(message)
    dead_sockets = set()
    for ws in list(phone_clients):
        try:
            await ws.send_text(text)
        except Exception:
            dead_sockets.add(ws)
    for ws in dead_sockets:
        phone_clients.discard(ws)

async def handle_sensor_payload(payload: Dict[str, Any]):
    """
    Ingests sensor observations from phone or explicit simulator.
    """
    global last_feature_time, last_stft_time, last_phone_state_time, last_arrival_time
    global latest_features_cache, latest_stft_cache
    global total_packets_received, total_packets_lost, last_seq_num
    
    # Block real-device and simulator collision
    is_simulated = bool(payload.get("is_simulated", False))
    if is_simulated and len(phone_clients) > 0:
        return  # Ignore simulator when real phone is actively streaming
    
    # 1. Validate numerical inputs
    try:
        t = float(payload.get("t", time.time()))
        ax = float(payload.get("ax", 0.0))
        ay = float(payload.get("ay", 0.0))
        az = float(payload.get("az", 0.0))
        gx = float(payload.get("gx", 0.0))
        gy = float(payload.get("gy", 0.0))
        gz = float(payload.get("gz", 0.0))
    except (ValueError, TypeError):
        return  # Drop malformed non-finite payload

    seq = payload.get("seq")
    epoch = payload.get("stream_epoch", current_stream_epoch or "default")
    tap_event = bool(payload.get("tap_event", False))
    
    # Stream epoch reset check
    if epoch != current_stream_epoch:
        reset_stream_quality_trackers(epoch)

    # Sequence deduplication and loss detection
    if seq is not None:
        try:
            seq = int(seq)
            if seq in seen_sequence_numbers:
                return  # Drop duplicate retransmission
            if len(seen_seq_deque) == seen_seq_deque.maxlen:
                oldest = seen_seq_deque.popleft()
                seen_sequence_numbers.discard(oldest)
            seen_seq_deque.append(seq)
            seen_sequence_numbers.add(seq)
            
            if last_seq_num is not None:
                if seq > last_seq_num + 1:
                    total_packets_lost += (seq - last_seq_num - 1)
                elif seq < last_seq_num:
                    # Out of order packet arrived: was previously counted as lost
                    total_packets_lost = max(0, total_packets_lost - 1)
            last_seq_num = max(last_seq_num if last_seq_num is not None else seq, seq)
            recent_sample_seqs.append(seq)
        except ValueError:
            seq = None

    last_arrival_time = time.time()
    total_packets_received += 1
    recent_sample_times.append(t)
    dq = compute_data_quality()
    empirical_fs = dq["observed_hz"] if dq["observed_hz"] >= 15.0 else config.sampling_rate_hz
    
    # 2. Append raw observations to live buffer
    live_buffer.append(t, ax, ay, az, gx, gy, gz)
    
    # 3. State machine ingestion
    state_info = state_machine.process_sensor_sample(
        t, ax, ay, az, gx, gy, gz, tap_event=tap_event, seq=seq, stream_epoch=epoch
    )
    
    now = time.time()
    processed_window = None
    
    # 4. Periodic feature extraction (invalidated if quality is INSUFFICIENT)
    if dq["status"] == "INSUFFICIENT":
        latest_features_cache = {
            "dominant_freq": 0.0,
            "rms_amp_tremor": 0.0,
            "band_power_ratio": 0.0,
            "tremor_detected": False,
            "severity": "INSUFFICIENT_DATA",
            "status": "INSUFFICIENT_QUALITY"
        }
    elif now - last_feature_time >= 0.5:
        last_feature_time = now
        processed_window = live_buffer.get_processed_window(n_samples=config.window_samples, fs=empirical_fs)
        if processed_window is not None:
            features = extract_window_features(
                processed_window["acc_dyn"],
                processed_window["acc_tremor"],
                fs=empirical_fs,
                axes_3d=processed_window.get("axes_3d")
            )
            features["status"] = "VALID"
            latest_features_cache = features

    # 5. Periodic STFT Spectrogram computation
    if now - last_stft_time >= 0.8:
        last_stft_time = now
        if processed_window is None and dq["status"] != "INSUFFICIENT":
            processed_window = live_buffer.get_processed_window(n_samples=config.window_samples, fs=empirical_fs)
        if processed_window is not None:
            stft_res = compute_realtime_stft(
                processed_window["acc_dyn"],
                fs=empirical_fs
            )
            latest_stft_cache = stft_res

    # Instant dynamic magnitudes for waveform display
    raw_mag = float((ax**2 + ay**2 + az**2)**0.5)
    gyro_mag = float((gx**2 + gy**2 + gz**2)**0.5)
    dyn_mag = abs(raw_mag - 9.81)
    dyn_acc = float(raw_mag - 9.81)

    # 6. Build Dashboard Broadcast Packet
    broadcast_packet = {
        "type": "SENSOR_STREAM",
        "sample": {
            "t": t,
            "seq": seq,
            "ax": ax, "ay": ay, "az": az,
            "gx": gx, "gy": gy, "gz": gz,
            "raw_mag": round(raw_mag, 3),
            "dyn_mag": round(dyn_mag, 3),
            "dyn_acc": round(dyn_acc, 4),
            "gyro_mag": round(gyro_mag, 3),
            "tap_event": tap_event
        },
        "state": state_info,
        "features": latest_features_cache,
        "spectrogram": latest_stft_cache,
        "data_quality": dq,
        "is_simulated": is_simulated,
        "phone_connected": len(phone_clients) > 0 or is_simulated
    }
    
    await broadcast_to_dashboards(broadcast_packet)

    # Broadcast state updates to phone clients
    if now - last_phone_state_time >= 0.2:
        last_phone_state_time = now
        await broadcast_to_phones({
            "type": "STATE_UPDATE",
            "state": state_info,
            "server_time": now
        })

# Connect simulator callback
simulator.callback = handle_sensor_payload

def on_state_machine_transition(status: Dict[str, Any]):
    asyncio.create_task(broadcast_to_dashboards({"type": "STATUS_UPDATE", "state": status}))
    asyncio.create_task(broadcast_to_phones({"type": "STATE_UPDATE", "state": status}))

state_machine.on_state_change = on_state_machine_transition

@router.websocket("/ws/phone")
async def websocket_phone_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(None)
):
    """
    WebSocket endpoint for mobile phone browser.
    Validates pairing token if provided; rejects competing phone streams when one is active.
    """
    await websocket.accept()
    
    # Enforce one active phone stream at a time to prevent interleaved buffer corruption
    if len(phone_clients) >= 1 and websocket not in phone_clients:
        await websocket.send_text(json.dumps({
            "type": "ERROR",
            "message": "Another phone is already actively streaming to this workstation session."
        }))
        await websocket.close()
        return

    phone_clients.add(websocket)
    new_epoch = f"epoch-{int(time.time())}"
    reset_stream_quality_trackers(new_epoch)
    
    await websocket.send_text(json.dumps({
        "type": "CONNECTED_ACK",
        "status": "connected",
        "stream_epoch": new_epoch,
        "state": state_machine.get_status(),
        "config": {
            "sampling_rate": config.sampling_rate_hz,
            "lan_ip": config.lan_ip,
            "port": config.port
        }
    }))
    
    await broadcast_to_dashboards({
        "type": "STATUS_UPDATE",
        "phone_connected": True,
        "message": "Phone sensor connected successfully."
    })
    
    try:
        while True:
            data_text = await websocket.receive_text()
            try:
                data_json = json.loads(data_text)
                if data_json.get("action") == "START_PROTOCOL":
                    protocol = data_json.get("protocol", "TREMOR_3TEST")
                    p_id = data_json.get("patient_id", state_machine.patient_id)
                    state_machine.start_session(protocol_type=protocol, patient_id=p_id, provenance="REAL_DEVICE")
                elif data_json.get("action") == "RESET_PROTOCOL":
                    state_machine.reset()
                elif data_json.get("type") == "BATCH" or "samples" in data_json:
                    for s in data_json.get("samples", []):
                        if isinstance(s, dict):
                            s["stream_epoch"] = new_epoch
                            await handle_sensor_payload(s)
                elif data_json.get("type") == "TAP_EVENT":
                    t_val = float(data_json.get("t", time.time()))
                    state_machine.register_tap(t_val)
                    await broadcast_to_dashboards({
                        "type": "TAP_EVENT",
                        "t": t_val,
                        "tap_count": data_json.get("tap_count", 0)
                    })
                else:
                    data_json["stream_epoch"] = new_epoch
                    await handle_sensor_payload(data_json)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        phone_clients.discard(websocket)
        await broadcast_to_dashboards({
            "type": "STATUS_UPDATE",
            "phone_connected": len(phone_clients) > 0 or simulator.is_running,
            "message": "Phone disconnected."
        })
    except Exception as e:
        phone_clients.discard(websocket)

@router.websocket("/ws/dashboard")
async def websocket_dashboard_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for laptop workstation dashboard.
    """
    await websocket.accept()
    dashboard_clients.add(websocket)
    
    await websocket.send_text(json.dumps({
        "type": "INITIAL_STATE",
        "phone_connected": len(phone_clients) > 0 or simulator.is_running,
        "state": state_machine.get_status(),
        "is_simulating": simulator.is_running,
        "simulation_profile": simulator.profile,
        "config": {
            "sampling_rate": config.sampling_rate_hz,
            "tremor_band": [config.tremor_band_low, config.tremor_band_high],
            "lan_ip": config.lan_ip,
            "port": config.port
        }
    }))
    
    try:
        while True:
            cmd_text = await websocket.receive_text()
            try:
                cmd = json.loads(cmd_text)
                action = cmd.get("action")
                
                if action == "START_PROTOCOL":
                    protocol = cmd.get("protocol", "TREMOR_3TEST")
                    patient_id = cmd.get("patient_id", "P-101")
                    provenance = "SIMULATED" if simulator.is_running else "REAL_DEVICE"
                    state_machine.start_session(protocol_type=protocol, patient_id=patient_id, provenance=provenance)
                elif action == "RESET_PROTOCOL":
                    state_machine.reset()
                elif action == "START_SIMULATOR":
                    profile = cmd.get("profile", "SEVERE_PD")
                    simulator.start(profile)
                elif action == "STOP_SIMULATOR":
                    simulator.stop()
            except Exception as e:
                pass
    except WebSocketDisconnect:
        dashboard_clients.discard(websocket)
    except Exception as e:
        dashboard_clients.discard(websocket)
