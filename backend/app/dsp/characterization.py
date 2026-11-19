"""
Real-Device Characterization Engine for MotorTrack
Analyzes empirical sensor streams from actual smartphone hardware to quantify:
  - Median sampling rate (Hz)
  - Sampling rate standard deviation (Hz)
  - Timestamp jitter (ms)
  - Packet loss rate (%)
  - Baseline stationary sensor noise RMS (m/s^2)
  - Frequency tracking error under controlled movement (Hz)
"""

import numpy as np
from typing import List, Dict, Any, Optional

def characterize_stationary_trials(
    trials: List[List[Dict[str, Any]]],
    gravity_nominal: float = 9.81
) -> Dict[str, Any]:
    """
    Analyzes multiple stationary recordings (e.g., 10 x 30s trials resting on a flat surface).
    Returns empirical hardware metrics.
    """
    if not trials:
        return {}

    trial_sampling_rates = []
    trial_jitters_ms = []
    trial_loss_rates = []
    trial_baseline_rms = []

    for trial in trials:
        if len(trial) < 10:
            continue

        timestamps = np.array([p["t"] for p in trial], dtype=float)
        seq_nums = [p.get("seq") for p in trial if p.get("seq") is not None]

        # 1. Empirical Sampling Rate
        dts = np.diff(timestamps)
        positive_dts = dts[dts > 0]
        if len(positive_dts) > 0:
            trial_fs = 1.0 / np.mean(positive_dts)
            trial_sampling_rates.append(trial_fs)
            # Timestamp Jitter (std dev of sample interval in ms)
            jitter_ms = float(np.std(positive_dts) * 1000.0)
            trial_jitters_ms.append(jitter_ms)

        # 2. Packet Loss Rate
        if len(seq_nums) > 1:
            seq_diffs = np.diff(seq_nums)
            lost_packets = int(np.sum(np.maximum(0, seq_diffs - 1)))
            total_expected = len(seq_nums) + lost_packets
            loss_pct = (lost_packets / total_expected) * 100.0 if total_expected > 0 else 0.0
            trial_loss_rates.append(loss_pct)
        else:
            trial_loss_rates.append(0.0)

        # 3. Baseline Stationary Signal RMS
        ax = np.array([p.get("ax", 0.0) for p in trial])
        ay = np.array([p.get("ay", 0.0) for p in trial])
        az = np.array([p.get("az", 9.81) for p in trial])
        raw_mag = np.sqrt(ax**2 + ay**2 + az**2)
        dyn_mag = raw_mag - gravity_nominal
        rms_noise = float(np.sqrt(np.mean(dyn_mag**2)))
        trial_baseline_rms.append(rms_noise)

    return {
        "trials_analyzed": len(trial_sampling_rates),
        "median_sampling_rate_hz": round(float(np.median(trial_sampling_rates)), 2) if trial_sampling_rates else 0.0,
        "sampling_rate_std_hz": round(float(np.std(trial_sampling_rates)), 3) if trial_sampling_rates else 0.0,
        "mean_timestamp_jitter_ms": round(float(np.mean(trial_jitters_ms)), 2) if trial_jitters_ms else 0.0,
        "max_timestamp_jitter_ms": round(float(np.max(trial_jitters_ms)), 2) if trial_jitters_ms else 0.0,
        "mean_packet_loss_percent": round(float(np.mean(trial_loss_rates)), 2) if trial_loss_rates else 0.0,
        "baseline_sensor_rms_ms2": round(float(np.mean(trial_baseline_rms)), 4) if trial_baseline_rms else 0.0,
    }


def characterize_movement_trials(
    movement_trials: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Analyzes controlled movement recordings across target frequencies (e.g., 3.0 Hz, 5.0 Hz, 7.0 Hz).
    Returns frequency estimation error and tracking status per tested frequency.
    """
    results = []
    for test in movement_trials:
        target_f = test["target_freq_hz"]
        measured_f = test["measured_freq_hz"]
        measured_rms = test["measured_rms_ms2"]
        err = abs(measured_f - target_f)
        results.append({
            "target_freq_hz": target_f,
            "measured_freq_hz": round(measured_f, 2),
            "frequency_error_hz": round(err, 3),
            "measured_rms_ms2": round(measured_rms, 3),
            "tracking_pass": bool(err <= 0.15)
        })
    return results
