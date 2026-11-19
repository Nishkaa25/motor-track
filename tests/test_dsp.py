import unittest
import numpy as np
from backend.app.dsp.filters import RealtimeFilterBank, IMUSensorBuffer
from backend.app.dsp.features import extract_window_features, compute_fft_psd
from backend.app.dsp.stft import compute_realtime_stft
from backend.app.dsp.fog import FOGDetector
from backend.app.dsp.tapping import FingerTappingAnalyzer
from backend.app.dsp.gait import GaitAnalyzer
from backend.app.models.ml_models import ml_classifier

class TestParkinsonDSPEngine(unittest.TestCase):
    def setUp(self):
        self.fs = 50.0  # 50 Hz sampling rate
        self.duration = 4.0  # 4 seconds
        self.N = int(self.fs * self.duration)
        self.t = np.linspace(0, self.duration, self.N, endpoint=False)

    def test_gravity_removal(self):
        """Verify high-pass filter removes ~9.8 m/s^2 DC component."""
        bank = RealtimeFilterBank(fs=self.fs)
        # 5.0 Hz oscillation superimposed on 9.81 m/s^2 gravity
        raw_az = 9.81 + 0.5 * np.sin(2.0 * np.pi * 5.0 * self.t)
        dyn_az = bank.remove_gravity(raw_az)
        
        # Mean should be close to 0
        self.assertAlmostEqual(float(np.mean(dyn_az[50:-50])), 0.0, delta=0.1)

    def test_tremor_bandpass(self):
        """Verify 4th-order Butterworth bandpass emphasizes configured 3.5–7.5 Hz band while attenuating tested low- and high-frequency components."""
        bank = RealtimeFilterBank(fs=self.fs)
        # Combined signal: 1.0 Hz low-freq (0.8 amp) + 5.2 Hz tremor-band signal (0.6 amp) + 15 Hz high-freq (0.3 amp)
        sig = (
            0.8 * np.sin(2.0 * np.pi * 1.0 * self.t) +
            0.6 * np.sin(2.0 * np.pi * 5.2 * self.t) +
            0.3 * np.sin(2.0 * np.pi * 15.0 * self.t)
        )
        filtered = bank.apply_tremor_bandpass(sig)
        
        # Compute PSD of filtered signal
        freqs, psd = compute_fft_psd(filtered, fs=self.fs)
        peak_freq = freqs[np.argmax(psd)]
        self.assertAlmostEqual(peak_freq, 5.2, delta=0.5)

    def test_feature_extraction_pd_tremor(self):
        """Verify feature extraction accurately detects a synthetic 5.1 Hz tremor-like signal."""
        tremor_sig = 0.5 * np.sin(2.0 * np.pi * 5.1 * self.t) + np.random.normal(0, 0.02, self.N)
        bank = RealtimeFilterBank(fs=self.fs)
        tremor_filtered = bank.apply_tremor_bandpass(tremor_sig)
        
        features = extract_window_features(tremor_sig, tremor_filtered, fs=self.fs)
        
        self.assertAlmostEqual(features["dominant_freq"], 5.1, delta=0.4)
        self.assertGreater(features["band_power_ratio"], 0.40)
        self.assertTrue(features["tremor_detected"])
        self.assertIn(features["severity"], ["MILD", "MODERATE", "SEVERE"])
        
        # Test ML inference
        ml_res = ml_classifier.predict_confidence(features)
        self.assertGreater(ml_res["probability"], 0.50)

    def test_stft_spectrogram(self):
        """Verify STFT returns valid 2D matrix with frequency bins up to 15 Hz."""
        sig = 0.4 * np.sin(2.0 * np.pi * 4.8 * self.t)
        stft_res = compute_realtime_stft(sig, fs=self.fs)
        
        self.assertIn("frequencies", stft_res)
        self.assertIn("spectrogram", stft_res)
        self.assertGreater(len(stft_res["frequencies"]), 5)
        self.assertEqual(len(stft_res["spectrogram"]), len(stft_res["frequencies"]))

    def test_fog_detector(self):
        """Verify FOG Index spikes when high frequency trembling replaces walking rhythm."""
        detector = FOGDetector(fs=self.fs, threshold=2.5)
        
        # Normal walking (1.8 Hz bounce)
        walk_sig = 3.0 * np.sin(2.0 * np.pi * 1.8 * self.t)
        fog_res_walk = detector.update(1.0, walk_sig)
        self.assertLess(fog_res_walk["current_fog_index"], 2.0)
        self.assertFalse(fog_res_walk["is_freezing"])
        
        # Freezing of Gait episode (5.5 Hz leg trembling)
        freeze_sig = 2.5 * np.sin(2.0 * np.pi * 5.5 * self.t)
        fog_res_freeze = detector.update(5.0, freeze_sig)
        self.assertGreater(fog_res_freeze["current_fog_index"], 2.5)
        self.assertTrue(fog_res_freeze["is_freezing"])

    def test_finger_tapping_analyzer(self):
        """Verify Bradykinesia finger tapping analysis."""
        analyzer = FingerTappingAnalyzer(duration_sec=10.0)
        # Simulate 30 taps over 6 seconds (~5 taps/s)
        tap_times = np.cumsum(np.random.normal(0.20, 0.02, 30))
        for t_val in tap_times:
            analyzer.add_tap(float(t_val))
            
        res = analyzer.analyze()
        self.assertEqual(res["total_taps"], 30)
        self.assertAlmostEqual(res["tap_rate_per_sec"], 5.0, delta=1.0)
        self.assertGreater(res["mean_iti_ms"], 150)
        self.assertIn(res["bradykinesia_grade"], ["NORMAL", "MILD_BRADYKINESIA", "MODERATE_BRADYKINESIA"])

    def test_gait_analyzer(self):
        """Verify gait step detection and cadence calculation."""
        analyzer = GaitAnalyzer(fs=self.fs)
        # 1.8 Hz walking rhythm = ~108 steps per minute
        walk_az = 3.0 * np.sin(2.0 * np.pi * 1.8 * self.t)
        dyn_mag = np.abs(walk_az)
        gyro_mag = np.zeros_like(walk_az)
        
        gait_res = analyzer.analyze_gait_segment(walk_az, dyn_mag, gyro_mag, duration_sec=4.0)
        self.assertGreater(gait_res["step_count"], 4)
        self.assertAlmostEqual(gait_res["cadence_spm"], 108.0, delta=15.0)

if __name__ == "__main__":
    unittest.main()
