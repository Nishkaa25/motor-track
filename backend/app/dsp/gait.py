import numpy as np
from scipy import signal
from typing import Dict, Any, List

class GaitAnalyzer:
    """
    Experimental gait & mobility movement feature extractor.
    Extracts step cadence, vertical excursion, step-time regularity,
    and peak angular rate from waist/pocket IMU recordings.
    """
    def __init__(self, fs: float = 50.0):
        self.fs = max(10.0, float(fs))

    def analyze_gait_segment(
        self,
        vertical_acc: np.ndarray,
        dyn_acc_mag: np.ndarray,
        gyro_mag: np.ndarray,
        duration_sec: float
    ) -> Dict[str, Any]:
        if len(vertical_acc) < 40:
            return {
                "step_count": 0,
                "cadence_spm": 0.0,
                "vertical_amplitude": 0.0,
                "shuffle_index": 0.0,
                "gait_pattern": "INSUFFICIENT_DATA",
                "step_regularity_score": 0.0,
                "symmetry_score": 0.0,
                "peak_angular_speed_dps": 0.0,
                "max_turn_rate_dps": 0.0
            }

        # Step 1: Bandpass filter in walking cadence band (0.5 - 3.0 Hz)
        low = min(0.5, self.fs * 0.45)
        high = min(3.0, self.fs * 0.48)
        sos_walk = signal.butter(2, [low, high], btype='bandpass', fs=self.fs, output='sos')
        pad = min(len(vertical_acc) - 1, 3 * (2 * sos_walk.shape[0]))
        filtered_steps = signal.sosfiltfilt(sos_walk, vertical_acc, padlen=pad)
        
        # Step 2: Peak detection for step intervals
        min_distance = int(0.30 * self.fs)
        prominence = max(0.08, 0.35 * np.std(filtered_steps))
        peaks, _ = signal.find_peaks(
            filtered_steps,
            distance=min_distance,
            prominence=prominence
        )
        
        step_count = len(peaks)
        elapsed = max(1.0, float(duration_sec))
        cadence_spm = float((step_count / elapsed) * 60.0)
        
        # Step 3: Vertical excursion amplitude (95th - 5th percentile)
        vert_p2p = float(np.percentile(dyn_acc_mag, 95) - np.percentile(dyn_acc_mag, 5))
        
        # Relative vertical excursion indicator (experimental scale 0 to 10)
        if vert_p2p >= 12.0:
            shuffle_index = 1.0
        elif vert_p2p >= 7.0:
            shuffle_index = 3.5
        elif vert_p2p >= 3.5:
            shuffle_index = 6.5
        else:
            shuffle_index = 9.0

        # Step 4: Step-time regularity (% from coefficient of variation)
        if len(peaks) >= 4:
            step_times = peaks / self.fs
            step_intervals = np.diff(step_times)
            mean_si = np.mean(step_intervals)
            std_si = np.std(step_intervals)
            cv_si = (std_si / mean_si) if mean_si > 0 else 1.0
            regularity_score = float(np.clip(100.0 * (1.0 - cv_si), 0.0, 100.0))
        else:
            regularity_score = 50.0

        # Step 5: Angular speed unit conversion
        # Phone sends rotationRate in radians/second -> convert to deg/s
        peak_angular_speed_rad_s = float(np.max(gyro_mag)) if len(gyro_mag) > 0 else 0.0
        peak_angular_speed_dps = float(np.degrees(peak_angular_speed_rad_s))

        # Descriptive research classification
        if cadence_spm >= 95.0 and shuffle_index < 4.0:
            pattern = "ROBUST_CADENCE"
        elif shuffle_index >= 6.0 and cadence_spm < 90.0:
            pattern = "LOW_VERTICAL_EXCURSION"
        elif regularity_score < 65.0:
            pattern = "IRREGULAR_STEP_TIMING"
        else:
            pattern = "VARIABLE_LOCOMOTION"

        return {
            "step_count": step_count,
            "cadence_spm": round(cadence_spm, 1),
            "vertical_amplitude": round(vert_p2p, 2),
            "shuffle_index": round(shuffle_index, 1),
            "gait_pattern": pattern,
            "step_regularity_score": round(regularity_score, 1),
            "symmetry_score": round(regularity_score, 1),  # Backwards compatibility
            "peak_angular_speed_dps": round(peak_angular_speed_dps, 2),
            "max_turn_rate_dps": round(peak_angular_speed_dps, 2)
        }
