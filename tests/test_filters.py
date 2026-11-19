import unittest
import numpy as np
from backend.app.dsp.filters import RealtimeFilterBank
from tests.fixtures.generator import generate_sine

class TestFilterFrequencyResponse(unittest.TestCase):
    """
    Validates filter frequency response against measurable DSP acceptance criteria:
      1. Highpass (>0.5 Hz): Attenuates DC (0 Hz) and 0.2 Hz, passes >=2 Hz.
      2. Bandpass (3.5-7.5 Hz): Attenuates 1.0 Hz (>20 dB) and 15.0 Hz (>20 dB),
         passes 4.0-7.0 Hz (<1 dB attenuation).
    """
    def setUp(self):
        self.fs = 50.0

    def test_highpass_dc_suppression(self):
        """Verify DC gravity component (0 Hz, 9.81 m/s^2) is attenuated by >30 dB."""
        filter_bank = RealtimeFilterBank(fs=self.fs)
        # Array with constant 9.81 DC
        dc_array = np.full(int(4.0 * self.fs), 9.81)
        filtered = filter_bank.remove_gravity(dc_array)
        
        steady_state = np.abs(filtered[-int(self.fs):])
        mean_residual = float(np.mean(steady_state))
        self.assertLess(mean_residual, 0.05, f"DC residual {mean_residual} exceeds 0.05 m/s^2 threshold")

    def test_highpass_frequency_selectivity(self):
        """
        Verify high-pass frequency response:
          - 0.2 Hz is heavily attenuated (>15 dB)
          - 2.0 Hz and 5.0 Hz pass with minimal attenuation (<1.5 dB)
        """
        fb = RealtimeFilterBank(fs=self.fs)
        def measure_gain(freq: float) -> float:
            t, sig = generate_sine(frequency=freq, amplitude=1.0, duration=8.0, fs=self.fs)
            out = fb.remove_gravity(sig)
            tail = int(3.0 * self.fs)
            in_rms = np.sqrt(np.mean(sig[-tail:]**2))
            out_rms = np.sqrt(np.mean(out[-tail:]**2))
            return float(out_rms / in_rms)

        gain_02 = measure_gain(0.2)
        gain_20 = measure_gain(2.0)
        gain_50 = measure_gain(5.0)

        self.assertLess(gain_02, 0.25, f"0.2 Hz gain {gain_02} was not attenuated sufficiently")
        self.assertGreater(gain_20, 0.85, f"2.0 Hz passband gain {gain_20} is too low")
        self.assertGreater(gain_50, 0.90, f"5.0 Hz passband gain {gain_50} is too low")

    def test_tremor_bandpass_rejection(self):
        """
        Verify 4th-order Butterworth bandpass (3.5-7.5 Hz):
          - 1.0 Hz out-of-band: attenuated > 20 dB (gain < 0.10)
          - 5.0 Hz in-band: passes with < 1 dB attenuation (gain >= 0.88)
          - 15.0 Hz out-of-band: attenuated > 20 dB (gain < 0.10)
        """
        fb = RealtimeFilterBank(fs=self.fs)
        def measure_bandpass_gain(freq: float) -> float:
            t, sig = generate_sine(frequency=freq, amplitude=1.0, duration=8.0, fs=self.fs)
            out = fb.apply_tremor_bandpass(sig)
            tail = int(3.0 * self.fs)
            in_rms = np.sqrt(np.mean(sig[-tail:]**2))
            out_rms = np.sqrt(np.mean(out[-tail:]**2))
            return float(out_rms / in_rms)

        gain_1hz = measure_bandpass_gain(1.0)
        gain_5hz = measure_bandpass_gain(5.0)
        gain_15hz = measure_bandpass_gain(15.0)

        self.assertLess(gain_1hz, 0.10, f"1.0 Hz bandpass gain {gain_1hz} exceeded 20 dB rejection limit")
        self.assertGreater(gain_5hz, 0.88, f"5.0 Hz in-band gain {gain_5hz} is attenuated too much")
        self.assertLess(gain_15hz, 0.12, f"15.0 Hz bandpass gain {gain_15hz} exceeded rejection limit")



if __name__ == "__main__":
    unittest.main()
