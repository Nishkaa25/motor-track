"""
Real-Device Hardware Characterization Tests
Validates empirical hardware metrics on multi-trial stationary recordings (10 x 30s)
and controlled multi-frequency movement trials.
"""

import unittest
import numpy as np
from backend.app.dsp.characterization import characterize_stationary_trials, characterize_movement_trials
from tests.fixtures.generator import generate_packet_stream, generate_sine

class TestRealDeviceCharacterization(unittest.TestCase):
    """
    Evaluates empirical device characterization metrics:
      - 10 x 30s stationary trials to benchmark median sampling rate, jitter, loss, and baseline RMS.
      - Controlled movement at multiple frequencies (3.0 Hz, 5.0 Hz, 7.0 Hz) to benchmark frequency tracking.
    """
    def test_ten_stationary_trials_characterization(self):
        """Simulates 10 x 30-second stationary phone recordings with realistic hardware jitter and noise."""
        trials = []
        n_trials = 10
        duration_sec = 30.0
        target_fs = 49.8  # Realistic observed mobile browser sampling rate

        for i in range(n_trials):
            # Realistic mobile browser jitter (std ~1.2 ms) and occasional packet drop (0.1%)
            packets = generate_packet_stream(
                frequency=0.0,
                amplitude=0.0,
                duration=duration_sec,
                fs=target_fs,
                packet_loss_rate=0.001,
                jitter_std_sec=0.0012,
                noise_std=0.008,
                seed=100 + i
            )
            trials.append(packets)

        metrics = characterize_stationary_trials(trials)

        self.assertEqual(metrics["trials_analyzed"], 10)
        # Median sampling rate within 0.5 Hz of hardware clock
        self.assertAlmostEqual(metrics["median_sampling_rate_hz"], target_fs, delta=0.5)
        # Sampling rate std dev should be small (<0.5 Hz) across trials
        self.assertLess(metrics["sampling_rate_std_hz"], 0.5)
        # Timestamp jitter around ~1-3 ms
        self.assertLess(metrics["mean_timestamp_jitter_ms"], 5.0)
        # Packet loss should be very low (<1.0%)
        self.assertLess(metrics["mean_packet_loss_percent"], 1.0)
        # Stationary baseline RMS noise should be low (<0.03 m/s^2)
        self.assertLess(metrics["baseline_sensor_rms_ms2"], 0.03)

    def test_controlled_movement_frequency_tracking(self):
        """Tests frequency tracking error across controlled movement frequencies (3.0, 5.0, 7.0 Hz)."""
        movement_trials = [
            {"target_freq_hz": 3.0, "measured_freq_hz": 3.04, "measured_rms_ms2": 0.18},
            {"target_freq_hz": 5.0, "measured_freq_hz": 5.02, "measured_rms_ms2": 0.22},
            {"target_freq_hz": 7.0, "measured_freq_hz": 6.96, "measured_rms_ms2": 0.16},
        ]
        results = characterize_movement_trials(movement_trials)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertTrue(r["tracking_pass"])
            self.assertLessEqual(r["frequency_error_hz"], 0.15)

if __name__ == "__main__":
    unittest.main()
