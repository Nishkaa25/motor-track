import unittest
import numpy as np
from backend.app.dsp.filters import RealtimeFilterBank
from backend.app.dsp.features import extract_window_features
from tests.fixtures.generator import generate_sine, generate_multi_tone, generate_resting_baseline

class TestTremorDetectionMatrix(unittest.TestCase):
    """
    Evaluates rule-based tremor-band detection against synthetic benchmark scenarios:
      1. Stationary resting baseline (low sensor noise): tremor-band activity NOT detected
      2. 2.0 Hz voluntary hand motion: tremor-band activity NOT detected
      3. 5.0 Hz sub-threshold micro-movement: tremor-band activity NOT detected (<0.05 m/s^2)
      4. Synthetic 5 Hz Tremor-like Signal (0.25 m/s^2 peak amplitude): tremor-band activity detected
      5. Mixed motion: 2.0 Hz voluntary + 5.2 Hz tremor-like: tremor band peak isolated
    """
    def setUp(self):
        self.fs = 50.0
        self.filter_bank = RealtimeFilterBank(fs=self.fs)

    def test_stationary_baseline_no_tremor(self):
        """Stationary sensor on table must yield zero tremor-band detection."""
        base_data = generate_resting_baseline(duration=4.0, fs=self.fs, noise_std=0.01)
        acc_dyn, acc_tremor, _, _ = self.filter_bank.process_raw_imu(
            base_data["ax"], base_data["ay"], base_data["az"],
            base_data["gx"], base_data["gy"], base_data["gz"]
        )
        feats = extract_window_features(acc_dyn, acc_tremor, fs=self.fs)
        self.assertFalse(feats["tremor_detected"])
        self.assertEqual(feats["severity"], "NONE")
        self.assertLess(feats["rms_amp_tremor"], 0.05)

    def test_voluntary_movement_rejection(self):
        """Pure 2.0 Hz voluntary limb movement must not trigger tremor-band detection."""
        _, sig_2hz = generate_sine(frequency=2.0, amplitude=0.40, duration=4.0, fs=self.fs)
        # Apply bandpass to simulate filter bank output
        tremor_sig = self.filter_bank.apply_tremor_bandpass(sig_2hz)
        feats = extract_window_features(sig_2hz, tremor_sig, fs=self.fs)
        self.assertFalse(feats["tremor_detected"])

    def test_sub_threshold_micro_motion(self):
        """5.0 Hz oscillation below noise threshold (0.02 m/s^2) is not flagged as tremor."""
        _, sig_tiny = generate_sine(frequency=5.0, amplitude=0.02, duration=4.0, fs=self.fs)
        feats = extract_window_features(sig_tiny, sig_tiny, fs=self.fs)
        self.assertFalse(feats["tremor_detected"])

    def test_synthetic_tremor_band_signal(self):
        """Synthetic 5 Hz Tremor-like Signal: 5.0 Hz oscillation with 0.25 m/s^2 peak amplitude triggers tremor-band detection."""
        _, sig_5hz = generate_sine(frequency=5.0, amplitude=0.25, duration=4.0, fs=self.fs, noise_std=0.01)
        tremor_sig = self.filter_bank.apply_tremor_bandpass(sig_5hz)
        feats = extract_window_features(sig_5hz, tremor_sig, fs=self.fs)
        self.assertTrue(feats["tremor_detected"])
        self.assertIn(feats["severity"], ["MILD", "MODERATE"])
        self.assertAlmostEqual(feats["dominant_freq"], 5.0, delta=0.15)
        self.assertGreater(feats["band_power_ratio"], 0.40)

    def test_mixed_voluntary_and_tremor_motion(self):
        """Composite 2.0 Hz voluntary (0.4 m/s^2) + 5.2 Hz tremor (0.20 m/s^2) isolates tremor frequency."""
        _, composite = generate_multi_tone(
            frequencies=[2.0, 5.2],
            amplitudes=[0.40, 0.20],
            duration=4.0,
            fs=self.fs,
            noise_std=0.01
        )
        tremor_sig = self.filter_bank.apply_tremor_bandpass(composite)
        feats = extract_window_features(composite, tremor_sig, fs=self.fs)
        # Dominant frequency in tremor band (3.5-7.5 Hz) must identify ~5.2 Hz
        self.assertAlmostEqual(feats["dominant_freq"], 5.2, delta=0.20)

if __name__ == "__main__":
    unittest.main()
