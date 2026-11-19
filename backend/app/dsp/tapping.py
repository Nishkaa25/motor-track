import numpy as np
from typing import List, Dict, Any, Optional

class FingerTappingAnalyzer:
    """
    Experimental movement timing analyzer for screen tapping trials.
    Measures cadence, inter-tap interval (ITI) variability, and slowing trends.
    Does not provide diagnostic MDS-UPDRS scoring.
    """
    def __init__(self, duration_sec: float = 15.0):
        self.duration_sec = duration_sec
        self.tap_timestamps: List[float] = []
        self.trial_start_time: Optional[float] = None
        self.trial_end_time: Optional[float] = None

    def reset(self):
        self.tap_timestamps.clear()
        self.trial_start_time = None
        self.trial_end_time = None

    def set_trial_bounds(self, start_t: float, end_t: float):
        self.trial_start_time = float(start_t)
        self.trial_end_time = float(end_t)

    def add_tap(self, timestamp: float):
        ts = float(timestamp)
        # Reject non-finite, out-of-order, or duplicate timestamps (< 30ms debounce)
        if not np.isfinite(ts):
            return
        if self.tap_timestamps and ts <= self.tap_timestamps[-1] + 0.030:
            return
        if self.trial_start_time is None:
            self.trial_start_time = ts
        self.tap_timestamps.append(ts)

    def analyze(self) -> Dict[str, Any]:
        total_taps = len(self.tap_timestamps)
        
        # Consistent schema with explicit nulls when insufficient
        if total_taps < 3:
            return {
                "total_taps": total_taps,
                "trial_tap_rate_per_sec": 0.0,
                "active_tap_rate_per_sec": 0.0,
                "tap_rate_per_sec": 0.0,
                "mean_iti_ms": None,
                "sd_iti_ms": None,
                "cv_irregularity_percent": None,
                "fatigue_slope_ms_per_tap": None,
                "status": "INSUFFICIENT_DATA",
                "tap_rates_over_time": []
            }

        # Whole-trial duration calculation
        if self.trial_start_time is not None and self.trial_end_time is not None:
            full_trial_duration = max(1.0, self.trial_end_time - self.trial_start_time)
        else:
            full_trial_duration = max(1.0, self.tap_timestamps[-1] - self.tap_timestamps[0])

        trial_rate = float(total_taps / full_trial_duration)
        
        # Active tapping interval calculation
        active_span = self.tap_timestamps[-1] - self.tap_timestamps[0]
        active_rate = float((total_taps - 1) / max(0.1, active_span))

        # Inter-tap intervals (ITIs)
        itis = np.diff(self.tap_timestamps)  # seconds
        mean_iti = float(np.mean(itis)) * 1000.0  # ms
        sd_iti = float(np.std(itis)) * 1000.0    # ms
        cv_irregularity = float((sd_iti / mean_iti) * 100.0) if mean_iti > 0 else 0.0
        
        # Observed ITI slowing trend (ms per tap)
        if len(itis) >= 4:
            tap_indices = np.arange(len(itis))
            slope, _ = np.polyfit(tap_indices, itis * 1000.0, 1)
            fatigue_slope = float(slope)
        else:
            fatigue_slope = 0.0

        # Descriptive research tier based on cadence
        if active_rate >= 4.0:
            brady_grade = "NORMAL"
        elif active_rate >= 3.0:
            brady_grade = "MILD_BRADYKINESIA"
        elif active_rate >= 2.0:
            brady_grade = "MODERATE_BRADYKINESIA"
        else:
            brady_grade = "SEVERE_BRADYKINESIA"

        # 1-second bin histogram across full trial duration
        bins = np.arange(0, np.ceil(full_trial_duration) + 1, 1.0)
        rel_times = np.array(self.tap_timestamps) - (self.trial_start_time or self.tap_timestamps[0])
        bin_counts, _ = np.histogram(rel_times, bins=bins)
        tap_rates_over_time = [int(c) for c in bin_counts]

        return {
            "total_taps": total_taps,
            "trial_tap_rate_per_sec": round(trial_rate, 2),
            "active_tap_rate_per_sec": round(active_rate, 2),
            "tap_rate_per_sec": round(active_rate, 2),
            "mean_iti_ms": round(mean_iti, 1),
            "sd_iti_ms": round(sd_iti, 1),
            "cv_irregularity_percent": round(cv_irregularity, 1),
            "fatigue_slope_ms_per_tap": round(fatigue_slope, 2),
            "bradykinesia_grade": brady_grade,
            "status": "VALID",
            "tap_rates_over_time": tap_rates_over_time
        }
