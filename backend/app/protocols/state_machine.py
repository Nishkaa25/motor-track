import time
import asyncio
import json
import uuid
import datetime
from typing import Dict, Any, Optional, List, Callable
import numpy as np

from backend.app.config import config
from backend.app.dsp.filters import RealtimeFilterBank, IMUSensorBuffer
from backend.app.dsp.features import extract_window_features
from backend.app.dsp.stft import compute_realtime_stft
from backend.app.dsp.fog import FOGDetector
from backend.app.dsp.tapping import FingerTappingAnalyzer
from backend.app.dsp.gait import GaitAnalyzer
from backend.app.models.ml_models import ml_classifier
from backend.app.database import AsyncSessionLocal
from backend.app.models.db_models import Patient as DBPatient, Session as DBSession, TestSegment as DBSegment, Reading as DBReading, ClinicalSummary as DBClinicalSummary

class ProtocolStateMachine:
    """
    Coordinates clinical test sessions and state transitions:
      - 3-Test Tremor Protocol: IDLE -> PREP_RESTING (10s) -> RESTING (20s) -> PREP_POSTURAL (10s) -> POSTURAL (20s) -> PREP_ACTION (10s) -> ACTION (20s) -> COMPLETE
      - FOG & Gait Test: IDLE -> PREP_GAIT (10s) -> GAIT_WALK (25s) -> COMPLETE
      - Bradykinesia Finger Tapping: IDLE -> PREP_TAPPING (10s) -> TAPPING (15s) -> COMPLETE
    """
    def __init__(self):
        self.state: str = "IDLE"
        self.active_protocol: str = "TREMOR_3TEST"
        self.patient_id: str = "P-101"
        self.session_id: Optional[str] = None
        self.segment_id: Optional[str] = None
        self.provenance: str = "REAL_DEVICE"
        
        self.session_start_utc: Optional[datetime.datetime] = None
        self.segment_start_mono: float = 0.0
        self.segment_start_utc: Optional[datetime.datetime] = None
        self.segment_duration_target: float = 10.0
        
        # Buffers for current active segment
        self.current_segment_readings: List[Dict[str, Any]] = []
        self.all_segment_readings: Dict[str, List[Dict[str, Any]]] = {}
        self.segment_summaries: Dict[str, Any] = {}
        self.segment_time_bounds: Dict[str, Dict[str, float]] = {}
        
        # Sub-analyzers and DSP
        self.filter_bank = RealtimeFilterBank(fs=config.sampling_rate_hz)
        self.fog_detector = FOGDetector(fs=config.sampling_rate_hz)
        self.tapping_analyzer = FingerTappingAnalyzer(duration_sec=config.tapping_test_duration_sec)
        self.gait_analyzer = GaitAnalyzer(fs=config.sampling_rate_hz)
        
        # Callbacks
        self.on_state_change: Optional[Callable[[Dict[str, Any]], None]] = None
        self.save_status: str = "IDLE"  # IDLE, SAVING, SAVED, SAVE_FAILED
        self._is_saving: bool = False

    def start_protocol(self, protocol_type: str = "TREMOR_3TEST", patient_id: str = "P-101", provenance: str = "REAL_DEVICE"):
        # Prevent restart if active save is pending
        if self._is_saving:
            return False

        self.active_protocol = protocol_type
        self.patient_id = patient_id
        self.provenance = provenance
        
        # Collision-resistant UUID-based session ID
        unique_token = uuid.uuid4().hex[:8].upper()
        ts_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.session_id = f"SESS-{ts_str}-{unique_token}"
        self.session_start_utc = datetime.datetime.now(datetime.timezone.utc)
        
        self.segment_summaries.clear()
        self.all_segment_readings.clear()
        self.segment_time_bounds.clear()
        self.fog_detector.reset()
        self.tapping_analyzer.reset()
        self.save_status = "IN_PROGRESS"
        
        if protocol_type == "TREMOR_3TEST":
            self.transition_to("PREP_RESTING", config.prep_countdown_sec)
        elif protocol_type == "GAIT_FOG":
            self.transition_to("PREP_GAIT", config.prep_countdown_sec)
        elif protocol_type == "BRADYKINESIA":
            self.transition_to("PREP_TAPPING", config.prep_countdown_sec)
        else:
            self.transition_to("PREP_RESTING", config.prep_countdown_sec)
        return True

    def start_session(self, protocol_type: str = "TREMOR_3TEST", patient_id: str = "P-101", provenance: str = "REAL_DEVICE"):
        return self.start_protocol(protocol_type=protocol_type, patient_id=patient_id, provenance=provenance)

    def transition_to(self, new_state: str, duration_sec: float = 15.0):
        # Finalize previous active recording segment
        if self.state not in ["IDLE", "COMPLETE"] and not self.state.startswith("PREP_") and self.segment_id is not None:
            self.all_segment_readings[self.state] = list(self.current_segment_readings)
            actual_end_mono = time.monotonic()
            rel_start = float(self.segment_start_mono - (self.session_start_mono if hasattr(self, 'session_start_mono') else self.segment_start_mono))
            rel_end = rel_start + max(0.1, actual_end_mono - self.segment_start_mono)
            self.segment_time_bounds[self.state] = {"start_t": round(rel_start, 2), "end_t": round(rel_end, 2)}
            self._finalize_segment(self.state)
            
        self.state = new_state
        self.segment_duration_target = duration_sec
        self.segment_start_mono = time.monotonic()
        if not hasattr(self, 'session_start_mono') or self.state.startswith("PREP_"):
            self.session_start_mono = self.segment_start_mono
            
        self.segment_start_utc = datetime.datetime.now(datetime.timezone.utc)
        self.segment_id = f"SEG-{uuid.uuid4().hex[:8].upper()}"
        self.current_segment_readings.clear()
        
        if new_state == "TAPPING":
            self.tapping_analyzer.set_trial_bounds(time.time(), time.time() + duration_sec)
        
        if new_state == "COMPLETE":
            self._save_completed_session()
        
        if self.on_state_change:
            self.on_state_change(self.get_status())

    def reset(self):
        self.state = "IDLE"
        self.session_id = None
        self.segment_id = None
        self.current_segment_readings.clear()
        self.all_segment_readings.clear()
        self.segment_summaries.clear()
        self.segment_time_bounds.clear()
        self.fog_detector.reset()
        self.tapping_analyzer.reset()
        self.save_status = "IDLE"

    def register_tap(self, t: float):
        """
        Registers an explicit touch tap event without injecting synthetic IMU acceleration.
        """
        if self.state == "TAPPING":
            self.tapping_analyzer.add_tap(t)

    def process_sensor_sample(
        self,
        t: float,
        ax: float,
        ay: float,
        az: float,
        gx: float,
        gy: float,
        gz: float,
        tap_event: bool = False,
        seq: Optional[int] = None,
        stream_epoch: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Ingests an IMU sample, updates state countdown using monotonic clock,
        and transitions automatically at stage completion.
        """
        now_mono = time.monotonic()
        
        if tap_event and self.state == "TAPPING":
            self.tapping_analyzer.add_tap(t)

        # Store reading for active recording segment (skip prep)
        if self.state not in ["IDLE", "COMPLETE"] and not self.state.startswith("PREP_"):
            raw_mag = float((ax**2 + ay**2 + az**2)**0.5)
            dyn_mag = abs(raw_mag - 9.81)
            self.current_segment_readings.append({
                "t": t, "seq": seq, "stream_epoch": stream_epoch,
                "ax": ax, "ay": ay, "az": az,
                "gx": gx, "gy": gy, "gz": gz,
                "filtered_mag": dyn_mag
            })
            
        # Check elapsed duration on monotonic clock
        if self.state not in ["IDLE", "COMPLETE"]:
            elapsed = now_mono - self.segment_start_mono
            if elapsed >= self.segment_duration_target:
                self._handle_timeout_transition()

        remaining = max(0.0, self.segment_duration_target - (now_mono - self.segment_start_mono)) if self.state not in ["IDLE", "COMPLETE"] else 0.0
        
        return {
            "state": self.state,
            "session_id": self.session_id,
            "protocol": self.active_protocol,
            "remaining_sec": round(remaining, 1),
            "target_duration_sec": self.segment_duration_target,
            "progress_percent": round(min(100.0, (1.0 - (remaining / max(1.0, self.segment_duration_target))) * 100.0), 1) if self.state not in ["IDLE", "COMPLETE"] else 100.0
        }

    def _handle_timeout_transition(self):
        if self.active_protocol == "TREMOR_3TEST":
            if self.state == "PREP_RESTING":
                self.transition_to("RESTING", config.resting_test_sec)
            elif self.state == "RESTING":
                self.transition_to("PREP_POSTURAL", config.prep_countdown_sec)
            elif self.state == "PREP_POSTURAL":
                self.transition_to("POSTURAL", config.postural_test_sec)
            elif self.state == "POSTURAL":
                self.transition_to("PREP_ACTION", config.prep_countdown_sec)
            elif self.state == "PREP_ACTION":
                self.transition_to("ACTION", config.action_test_sec)
            elif self.state == "ACTION":
                self.transition_to("COMPLETE", 0.0)
        elif self.active_protocol == "GAIT_FOG":
            if self.state == "PREP_GAIT":
                self.transition_to("GAIT_WALK", config.gait_test_sec)
            elif self.state == "GAIT_WALK":
                self.transition_to("COMPLETE", 0.0)
        elif self.active_protocol == "BRADYKINESIA":
            if self.state == "PREP_TAPPING":
                self.transition_to("TAPPING", config.tapping_test_duration_sec)
            elif self.state == "TAPPING":
                self.transition_to("COMPLETE", 0.0)

    def _finalize_segment(self, segment_type: str):
        if not self.current_segment_readings:
            return

        # Determine actual empirical sampling rate from segment timestamps
        if len(self.current_segment_readings) >= 10:
            t_vals = [r["t"] for r in self.current_segment_readings if "t" in r and r["t"] is not None]
            if len(t_vals) >= 10:
                duration_span = float(t_vals[-1] - t_vals[0])
                if duration_span > 0:
                    emp_fs = (len(t_vals) - 1) / duration_span
                    if 10.0 <= emp_fs <= 250.0:
                        actual_fs = float(emp_fs)
                    else:
                        actual_fs = config.sampling_rate_hz
                else:
                    actual_fs = config.sampling_rate_hz
            else:
                actual_fs = config.sampling_rate_hz
        else:
            actual_fs = config.sampling_rate_hz

        self.filter_bank.set_sampling_rate(actual_fs)
        self.gait_analyzer.fs = actual_fs
        self.fog_detector.fs = actual_fs

        ax = np.array([r["ax"] for r in self.current_segment_readings])
        ay = np.array([r["ay"] for r in self.current_segment_readings])
        az = np.array([r["az"] for r in self.current_segment_readings])
        gx = np.array([r["gx"] for r in self.current_segment_readings])
        gy = np.array([r["gy"] for r in self.current_segment_readings])
        gz = np.array([r["gz"] for r in self.current_segment_readings])
        
        acc_dyn, acc_tremor, gyro_mag, az_dyn = self.filter_bank.process_raw_imu(ax, ay, az, gx, gy, gz)
        axes_3d = self.filter_bank.process_axes_3d(ax, ay, az)
        
        # Robust 3D feature extraction with actual sampling rate
        tremor_feats = extract_window_features(acc_dyn, acc_tremor, fs=actual_fs, axes_3d=axes_3d)
        ml_res = ml_classifier.predict_confidence(tremor_feats)
        
        summary = {
            "test_type": segment_type,
            "samples_count": len(self.current_segment_readings),
            "dominant_freq": tremor_feats["dominant_freq"],
            "rms_amp": tremor_feats["rms_amp"],
            "rms_amp_tremor": tremor_feats["rms_amp_tremor"],
            "band_power_ratio": tremor_feats["band_power_ratio"],
            "spectral_entropy": tremor_feats["spectral_entropy"],
            "tremor_detected": tremor_feats["tremor_detected"],
            "severity": tremor_feats["severity"],
            "ml_confidence": ml_res["confidence_percent"],
            "psd_freqs": tremor_feats["psd_freqs"],
            "psd_values": tremor_feats["psd_values"]
        }

        if segment_type == "GAIT_WALK":
            gait_res = self.gait_analyzer.analyze_gait_segment(
                az_dyn, acc_dyn, gyro_mag, self.segment_duration_target
            )
            
            # Chronological multi-window sliding FOG evaluation
            self.fog_detector.reset()
            win_len = max(25, int(3.0 * actual_fs))
            hop_len = max(10, int(1.0 * actual_fs))
            N_samples = len(az_dyn)
            
            if N_samples <= win_len:
                fog_res = self.fog_detector.update(float(N_samples) / actual_fs, az_dyn)
                fog_close = self.fog_detector.close_trial(float(N_samples) / actual_fs)
                fog_res.update(fog_close)
            else:
                idx = 0
                fog_res = {}
                while idx + win_len <= N_samples:
                    sub_win = az_dyn[idx : idx + win_len]
                    rel_time = float(idx + win_len) / actual_fs
                    fog_res = self.fog_detector.update(rel_time, sub_win)
                    idx += hop_len
                total_trial_sec = float(N_samples) / actual_fs
                fog_close = self.fog_detector.close_trial(total_trial_sec)
                fog_res.update(fog_close)

            summary.update({
                "gait": gait_res,
                "fog": fog_res
            })
        elif segment_type == "TAPPING":
            tap_res = self.tapping_analyzer.analyze()
            summary.update({
                "tapping": tap_res
            })

        self.segment_summaries[segment_type] = summary

    def _create_snapshot(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "patient_id": self.patient_id,
            "protocol": self.active_protocol,
            "provenance": self.provenance,
            "started_at": self.session_start_utc or datetime.datetime.now(datetime.timezone.utc),
            "completed_at": datetime.datetime.now(datetime.timezone.utc),
            "segment_summaries": dict(self.segment_summaries),
            "all_segment_readings": {k: list(v) for k, v in self.all_segment_readings.items()},
            "segment_time_bounds": dict(self.segment_time_bounds)
        }

    def _save_completed_session(self):
        """Asynchronously creates an immutable snapshot and persists transactionally."""
        if self._is_saving:
            return
            
        snapshot = self._create_snapshot()
        self._is_saving = True
        self.save_status = "SAVING"
        try:
            loop = asyncio.get_running_loop()
            self.persist_task = loop.create_task(self._persist_session_snapshot(snapshot))
        except RuntimeError:
            self._is_saving = False
            self.save_status = "SAVE_FAILED"

    async def _persist_session_to_db(self):
        """Direct coroutine method for awaiting persistence in test suites."""
        snapshot = self._create_snapshot()
        await self._persist_session_snapshot(snapshot)

    async def _persist_session_snapshot(self, snapshot: Dict[str, Any]):
        session_id = snapshot["session_id"]
        patient_id = snapshot["patient_id"]
        
        try:
            async with AsyncSessionLocal() as db:
                # 1. Enforce patient existence (or create Demo Participant if needed)
                patient = await db.get(DBPatient, patient_id)
                if not patient:
                    patient = DBPatient(
                        patient_id=patient_id,
                        name=f"Participant {patient_id}",
                        notes="Auto-registered research participant"
                    )
                    db.add(patient)
                    await db.flush()

                # 2. Prevent duplicate session saves
                existing = await db.get(DBSession, session_id)
                if existing:
                    self._is_saving = False
                    self.save_status = "SAVED"
                    return

                # 3. Create Session Record
                started_at_naive = snapshot["started_at"].replace(tzinfo=None) if snapshot["started_at"] else datetime.datetime.utcnow()
                completed_at_naive = snapshot["completed_at"].replace(tzinfo=None) if snapshot["completed_at"] else datetime.datetime.utcnow()
                
                db_session = DBSession(
                    session_id=session_id,
                    patient_id=patient_id,
                    session_type=snapshot["protocol"],
                    started_at=started_at_naive,
                    completed_at=completed_at_naive,
                    provenance=snapshot["provenance"],
                    is_simulated=(snapshot["provenance"] == "SIMULATED"),
                    notes=f"Completed {snapshot['protocol']} assessment"
                )
                db.add(db_session)
                
                # 4. Add segments and readings
                for seg_type, seg_data in snapshot["segment_summaries"].items():
                    seg_id = f"{session_id}-{seg_type}"
                    bounds = snapshot["segment_time_bounds"].get(seg_type, {"start_t": 0.0, "end_t": 20.0})
                    
                    seg_record = DBSegment(
                        segment_id=seg_id,
                        session_id=session_id,
                        test_type=seg_type,
                        start_t=bounds["start_t"],
                        end_t=bounds["end_t"],
                        summary_json=json.dumps(seg_data)
                    )
                    db.add(seg_record)
                    
                    readings = snapshot["all_segment_readings"].get(seg_type, [])
                    if readings:
                        db_readings = [
                            DBReading(
                                segment_id=seg_id,
                                timestamp=r.get("t", 0.0),
                                seq_num=r.get("seq"),
                                stream_epoch=r.get("stream_epoch"),
                                ax=r.get("ax", 0.0),
                                ay=r.get("ay", 0.0),
                                az=r.get("az", 0.0),
                                gx=r.get("gx", 0.0),
                                gy=r.get("gy", 0.0),
                                gz=r.get("gz", 0.0),
                                filtered_mag=r.get("filtered_mag", 0.0)
                            )
                            for r in readings
                        ]
                        db.add_all(db_readings)

                # 5. Create clinical summary record
                rest_sum = snapshot["segment_summaries"].get("RESTING", {})
                post_sum = snapshot["segment_summaries"].get("POSTURAL", {})
                act_sum = snapshot["segment_summaries"].get("ACTION", {})
                gait_sum = snapshot["segment_summaries"].get("GAIT_WALK", {})
                tap_sum = snapshot["segment_summaries"].get("TAPPING", {})
                
                max_rms = max(
                    rest_sum.get("rms_amp_tremor", 0.0),
                    post_sum.get("rms_amp_tremor", 0.0),
                    act_sum.get("rms_amp_tremor", 0.0)
                )
                if max_rms < 0.05:
                    overall_sev = "NONE"
                elif max_rms < 0.20:
                    overall_sev = "MILD"
                elif max_rms < 0.60:
                    overall_sev = "MODERATE"
                else:
                    overall_sev = "SEVERE"

                clin_summary = DBClinicalSummary(
                    session_id=session_id,
                    resting_dominant_freq=rest_sum.get("dominant_freq"),
                    resting_rms_amp=rest_sum.get("rms_amp_tremor"),
                    resting_tremor_ratio=rest_sum.get("band_power_ratio"),
                    resting_tremor_detected=rest_sum.get("tremor_detected", False),
                    
                    postural_dominant_freq=post_sum.get("dominant_freq"),
                    postural_rms_amp=post_sum.get("rms_amp_tremor"),
                    postural_tremor_ratio=post_sum.get("band_power_ratio"),
                    postural_tremor_detected=post_sum.get("tremor_detected", False),
                    
                    action_dominant_freq=act_sum.get("dominant_freq"),
                    action_rms_amp=act_sum.get("rms_amp_tremor"),
                    action_tremor_ratio=act_sum.get("band_power_ratio"),
                    action_tremor_detected=act_sum.get("tremor_detected", False),
                    
                    overall_tremor_severity=overall_sev,
                    
                    fog_episodes_count=gait_sum.get("fog", {}).get("freeze_episodes", 0),
                    total_freeze_duration_sec=gait_sum.get("fog", {}).get("total_freeze_duration", 0.0),
                    max_fog_index=gait_sum.get("fog", {}).get("max_fog_index", 0.0),
                    
                    gait_cadence_spm=gait_sum.get("gait", {}).get("cadence_spm"),
                    gait_shuffle_score=gait_sum.get("gait", {}).get("shuffle_index"),
                    gait_symmetry_ratio=gait_sum.get("gait", {}).get("step_regularity_score"),
                    
                    tap_rate_per_sec=tap_sum.get("tapping", {}).get("trial_tap_rate_per_sec"),
                    tap_irregularity_cv=tap_sum.get("tapping", {}).get("cv_irregularity_percent"),
                    tap_fatigue_slope=tap_sum.get("tapping", {}).get("fatigue_slope_ms_per_tap"),
                    
                    ml_tremor_confidence=rest_sum.get("ml_confidence", 0.0) / 100.0,
                    overall_status=f"TREMOR_{overall_sev}" if overall_sev != "NONE" else "NORMAL"
                )
                db.add(clin_summary)
                await db.commit()
                self.save_status = "SAVED"
        except Exception as e:
            print(f"[ERROR] Transactional session save failed: {e}")
            self.save_status = "SAVE_FAILED"
        finally:
            self._is_saving = False

    def get_status(self) -> Dict[str, Any]:
        now_mono = time.monotonic()
        remaining = max(0.0, self.segment_duration_target - (now_mono - self.segment_start_mono)) if self.state not in ["IDLE", "COMPLETE"] else 0.0
        
        return {
            "state": self.state,
            "session_id": self.session_id,
            "patient_id": self.patient_id,
            "protocol": self.active_protocol,
            "provenance": self.provenance,
            "save_status": self.save_status,
            "remaining_sec": round(remaining, 1),
            "target_duration_sec": self.segment_duration_target,
            "summaries": self.segment_summaries
        }

state_machine = ProtocolStateMachine()
