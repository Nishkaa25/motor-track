import unittest
import numpy as np
from backend.app.dsp.features import extract_window_features
from tests.fixtures.generator import generate_sine

class TestFFTAndAmplitudeAccuracy(unittest.TestCase):
    """
    Validates FFT spectral estimation and amplitude scaling against strict acceptance criteria:
      1. Frequency Accuracy: Known sine waves (4.0, 5.0, 6.0, 7.0 Hz) recovered within <= 0.15 Hz.
      2. Amplitude Linearity: Measured RMS scales with theoretical A / sqrt(2) within 5%.
      3. Out-of-Band Discrimination: Low frequency (2.0 Hz) and high frequency (12.0 Hz)
         are not classified as dominant tremor band activity.
    """
    def setUp(self):
        self.fs = 50.0

    def test_frequency_estimation_accuracy(self):
        """Sub-bin parabolic peak interpolation reduced dominant-frequency estimation error to <= 0.15 Hz on tested synthetic tones."""
        target_frequencies = [4.0, 4.8, 5.0, 5.5, 6.0, 7.0]
        
        for f_target in target_frequencies:
            _, sig = generate_sine(
                frequency=f_target, amplitude=0.25, duration=4.0, fs=self.fs, noise_std=0.005
            )
            # Both dynamic and bandpass signal passed to feature extractor
            feats = extract_window_features(sig, sig, fs=self.fs)
            f_detected = feats["dominant_freq"]
            err = abs(f_detected - f_target)
            self.assertLessEqual(
                err, 0.15,
                f"Target {f_target} Hz detected as {f_detected} Hz; error {err:.3f} Hz exceeds 0.15 Hz limit"
            )

    def test_amplitude_rms_linearity(self):
        """Verify measured RMS scales within 5% of theoretical RMS = peak_amplitude_A / sqrt(2)."""
        test_peak_amplitudes = [0.10, 0.20, 0.35, 0.50]
        
        for peak_amp_A in test_peak_amplitudes:
            _, sig = generate_sine(
                frequency=5.0, amplitude=peak_amp_A, duration=4.0, fs=self.fs, noise_std=0.0
            )
            feats = extract_window_features(sig, sig, fs=self.fs)
            measured_rms = feats["rms_amp"]
            expected_rms = peak_amp_A / np.sqrt(2.0)
            rel_error = abs(measured_rms - expected_rms) / expected_rms
            self.assertLess(
                rel_error, 0.05,
                f"Peak amplitude {peak_amp_A}: measured RMS {measured_rms:.4f} vs expected {expected_rms:.4f} exceeds 5% error"
            )

    def test_out_of_band_rejection(self):
        """Verify non-tremor frequencies (2.0 Hz voluntary, 12.0 Hz noise) are not flagged as tremor."""
        # 2.0 Hz (voluntary arm movement)
        _, sig_2hz = generate_sine(frequency=2.0, amplitude=0.40, duration=4.0, fs=self.fs)
        feats_2hz = extract_window_features(sig_2hz, sig_2hz * 0.05, fs=self.fs)
        self.assertFalse(feats_2hz["tremor_detected"], "2.0 Hz voluntary motion was falsely flagged as tremor")
        
        # 12.0 Hz (high-frequency device vibration / noise)
        _, sig_12hz = generate_sine(frequency=12.0, amplitude=0.30, duration=4.0, fs=self.fs)
        feats_12hz = extract_window_features(sig_12hz, sig_12hz * 0.05, fs=self.fs)
        self.assertFalse(feats_12hz["tremor_detected"], "12.0 Hz vibration was falsely flagged as tremor")

if __name__ == "__main__":
    unittest.main()
