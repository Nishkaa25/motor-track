import numpy as np
from scipy import signal
from typing import Dict, Any, List, Optional
from backend.app.config import config

class FOGDetector:
    """
    Experimental Freezing of Gait (FOG) power ratio detector.
    Calculates FOG Index:
      Ratio of Freeze-band power (3.0 - 8.0 Hz) to Locomotion-band power (0.5 - 3.0 Hz).
    Gated by total movement power to avoid dividing noise floors on stationary signals.
    Tracks chronological multi-window episodes with start, end, and duration.
    """
    def __init__(self, fs: float = 50.0, threshold: float = 2.5):
        self.fs = max(10.0, float(fs))
        self.threshold = threshold
        self.in_freeze = False
        self.freeze_episodes = 0
        self.total_freeze_duration = 0.0
        self.freeze_start_time = None
        self.history_fog_indices: List[float] = []
        self.episodes: List[Dict[str, float]] = []

    def reset(self):
        self.in_freeze = False
        self.freeze_episodes = 0
        self.total_freeze_duration = 0.0
        self.freeze_start_time = None
        self.history_fog_indices.clear()
        self.episodes.clear()

    def compute_fog_index(self, vertical_acc: np.ndarray) -> float:
        """
        Computes FOG Index on a window (e.g. 2-4 seconds) of dynamic vertical acceleration.
        Gated by locomotion/movement power floor: stationary sensors yield 0.0.
        """
        if len(vertical_acc) < 25:
            return 0.0
            
        # Check overall RMS to reject stationary sensor noise
        rms_val = float(np.sqrt(np.mean(vertical_acc**2)))
        if rms_val < 0.06:
            return 0.0

        nperseg = min(len(vertical_acc), 128)
        freqs, psd = signal.welch(
            vertical_acc,
            fs=self.fs,
            nperseg=nperseg,
            scaling='density'
        )
        
        loco_mask = (freqs >= config.locomotion_band_low) & (freqs <= config.locomotion_band_high)
        freeze_mask = (freqs >= config.freeze_band_low) & (freqs <= config.freeze_band_high)
        
        trapz_fn = getattr(np, 'trapezoid', getattr(np, 'trapz', None))
        p_loco = trapz_fn(psd[loco_mask], freqs[loco_mask]) if np.any(loco_mask) else 0.0
        p_freeze = trapz_fn(psd[freeze_mask], freqs[freeze_mask]) if np.any(freeze_mask) else 0.0
        
        # If total locomotion power is negligible, do not flag false freezing
        if p_loco < 1e-4 and p_freeze < 1e-4:
            return 0.0
            
        fog_idx = float(p_freeze / max(1e-4, p_loco))
        return round(fog_idx, 3)

    def update(self, current_time: float, vertical_acc: np.ndarray) -> Dict[str, Any]:
        fog_idx = self.compute_fog_index(vertical_acc)
        self.history_fog_indices.append(fog_idx)
        
        is_freezing = fog_idx >= self.threshold
        
        if is_freezing and not self.in_freeze:
            # Entering freeze episode
            self.in_freeze = True
            self.freeze_start_time = current_time
        elif not is_freezing and self.in_freeze:
            # Exiting freeze episode
            self.in_freeze = False
            if self.freeze_start_time is not None:
                duration = current_time - self.freeze_start_time
                if duration > 0:
                    self.total_freeze_duration += duration
                    self.episodes.append({
                        "start_sec": round(self.freeze_start_time, 2),
                        "end_sec": round(current_time, 2),
                        "duration_sec": round(duration, 2)
                    })
                self.freeze_start_time = None

        self.freeze_episodes = len(self.episodes) + (1 if self.in_freeze else 0)
        max_fog = max(self.history_fog_indices) if self.history_fog_indices else 0.0
        
        # Research category
        if max_fog < 1.5:
            category = "BASELINE"
        elif max_fog < 2.5:
            category = "MODERATE_RATIO"
        else:
            category = "ELEVATED_FREEZE_RATIO"

        return {
            "current_fog_index": fog_idx,
            "is_freezing": self.in_freeze,
            "freeze_episodes": self.freeze_episodes,
            "total_freeze_duration": round(self.total_freeze_duration, 2),
            "max_fog_index": round(max_fog, 2),
            "episodes": list(self.episodes),
            "status_category": category,
            "risk_level": category
        }

    def close_trial(self, end_time: float) -> Dict[str, Any]:
        """Closes any active freeze episode at the end of the recording trial."""
        if self.in_freeze and self.freeze_start_time is not None:
            duration = end_time - self.freeze_start_time
            if duration > 0:
                self.total_freeze_duration += duration
                self.episodes.append({
                    "start_sec": round(self.freeze_start_time, 2),
                    "end_sec": round(end_time, 2),
                    "duration_sec": round(duration, 2)
                })
            self.in_freeze = False
            self.freeze_start_time = None
        self.freeze_episodes = len(self.episodes)
        max_fog = max(self.history_fog_indices) if self.history_fog_indices else 0.0
        return {
            "freeze_episodes": self.freeze_episodes,
            "total_freeze_duration": round(self.total_freeze_duration, 2),
            "max_fog_index": round(max_fog, 2),
            "episodes": list(self.episodes)
        }
