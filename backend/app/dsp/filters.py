import numpy as np
from scipy import signal
from typing import Tuple, List, Optional, Dict, Any
from collections import deque
from backend.app.config import config

class RealtimeFilterBank:
    """
    Real-time filter bank for Parkinson's movement telemetry.
    Applies per-axis causal/zero-phase filtering:
      1. Gravity removal (High-pass filter > 0.5 Hz per axis)
      2. 4th-order Butterworth Bandpass filter (3.5 - 7.5 Hz per axis)
      3. Resultant band-limited RMS: sqrt(mean(ax_bp^2 + ay_bp^2 + az_bp^2))
    """
    def __init__(self, fs: float = 50.0):
        self.fs = max(10.0, float(fs))
        self._init_filters()

    def _init_filters(self):
        # 2nd-order Butterworth high-pass for gravity removal (> 0.5 Hz)
        hp_cutoff = min(config.gravity_highpass_cutoff, self.fs * 0.45)
        self.hp_sos = signal.butter(
            N=2,
            Wn=hp_cutoff,
            btype='highpass',
            fs=self.fs,
            output='sos'
        )
        
        # 4th-order Butterworth bandpass for tremor band (3.5 - 7.5 Hz)
        low = min(config.tremor_band_low, self.fs * 0.45)
        high = min(config.tremor_band_high, self.fs * 0.48)
        if low >= high:
            low = high * 0.5
        self.bp_sos = signal.butter(
            N=4,
            Wn=[low, high],
            btype='bandpass',
            fs=self.fs,
            output='sos'
        )
        
        # Locomotion bandpass (0.5 - 3.0 Hz) for FOG
        self.loco_sos = signal.butter(
            N=3,
            Wn=[config.locomotion_band_low, min(config.locomotion_band_high, self.fs * 0.45)],
            btype='bandpass',
            fs=self.fs,
            output='sos'
        )
        
        # Freeze bandpass (3.0 - 8.0 Hz) for FOG
        self.freeze_sos = signal.butter(
            N=3,
            Wn=[config.freeze_band_low, min(config.freeze_band_high, self.fs * 0.48)],
            btype='bandpass',
            fs=self.fs,
            output='sos'
        )

    def set_sampling_rate(self, new_fs: float):
        if abs(new_fs - self.fs) > 1.0 and new_fs >= 10.0:
            self.fs = float(new_fs)
            self._init_filters()

    def remove_gravity(self, acc_data: np.ndarray) -> np.ndarray:
        """
        Applies high-pass filter to remove DC component (gravity ~9.8 m/s^2).
        Handles arbitrary input lengths with adaptive padlen.
        """
        if len(acc_data) < 10:
            return acc_data - np.mean(acc_data, axis=0)
        pad = min(len(acc_data) - 1, 3 * (2 * self.hp_sos.shape[0]))
        return signal.sosfiltfilt(self.hp_sos, acc_data, axis=0, padlen=pad)

    def apply_tremor_bandpass(self, data: np.ndarray) -> np.ndarray:
        """
        Applies 4th-order Butterworth 3.5 - 7.5 Hz bandpass.
        Handles short input windows without padding ValueError.
        """
        if len(data) < 10:
            return data
        pad = min(len(data) - 1, 3 * (2 * self.bp_sos.shape[0]))
        return signal.sosfiltfilt(self.bp_sos, data, axis=0, padlen=pad)

    def compute_magnitude(self, x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
        """Combines 3-axis signals into 1D Euclidean magnitude."""
        return np.sqrt(x**2 + y**2 + z**2)

    def process_raw_imu(
        self,
        ax: np.ndarray,
        ay: np.ndarray,
        az: np.ndarray,
        gx: np.ndarray,
        gy: np.ndarray,
        gz: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Per-axis transformation on raw IMU signals:
        1. High-pass filter each acceleration axis individually to remove gravity DC.
        2. Bandpass filter each axis in 3.5-7.5 Hz.
        3. Scalar resultant dynamic acceleration and tremor components.
        """
        # Step 1: Per-axis gravity removal
        ax_dyn = self.remove_gravity(ax)
        ay_dyn = self.remove_gravity(ay)
        az_dyn = self.remove_gravity(az)

        # Step 2: Per-axis tremor bandpass
        ax_bp = self.apply_tremor_bandpass(ax_dyn)
        ay_bp = self.apply_tremor_bandpass(ay_dyn)
        az_bp = self.apply_tremor_bandpass(az_dyn)

        # Dynamic scalar acceleration (zero-mean signed component along dominant direction)
        acc_mag_dyn = np.sign(az_dyn) * np.sqrt(ax_dyn**2 + ay_dyn**2 + az_dyn**2)
        
        # Resultant band-limited tremor amplitude waveform
        acc_mag_tremor = np.sign(az_bp) * np.sqrt(ax_bp**2 + ay_bp**2 + az_bp**2)
        
        # Gyroscope magnitude
        gyro_mag = self.compute_magnitude(gx, gy, gz)
        
        return acc_mag_dyn, acc_mag_tremor, gyro_mag, az_dyn

    def process_axes_3d(
        self,
        ax: np.ndarray,
        ay: np.ndarray,
        az: np.ndarray
    ) -> Dict[str, np.ndarray]:
        """
        Processes 3-axis acceleration individually and returns full 3D dictionary.
        """
        ax_dyn = self.remove_gravity(ax)
        ay_dyn = self.remove_gravity(ay)
        az_dyn = self.remove_gravity(az)

        ax_bp = self.apply_tremor_bandpass(ax_dyn)
        ay_bp = self.apply_tremor_bandpass(ay_dyn)
        az_bp = self.apply_tremor_bandpass(az_dyn)

        return {
            "ax_dyn": ax_dyn, "ay_dyn": ay_dyn, "az_dyn": az_dyn,
            "ax_bp": ax_bp, "ay_bp": ay_bp, "az_bp": az_bp
        }


class IMUSensorBuffer:
    """
    Sliding ring buffer for sensor data with empirical timestamp tracking.
    """
    def __init__(self, maxlen: int = 500, fs: float = 50.0):
        self.maxlen = maxlen
        self.fs = fs
        self.filter_bank = RealtimeFilterBank(fs=fs)
        
        self.timestamps: deque = deque(maxlen=maxlen)
        self.ax: deque = deque(maxlen=maxlen)
        self.ay: deque = deque(maxlen=maxlen)
        self.az: deque = deque(maxlen=maxlen)
        self.gx: deque = deque(maxlen=maxlen)
        self.gy: deque = deque(maxlen=maxlen)
        self.gz: deque = deque(maxlen=maxlen)
        
        self.mag_dyn: deque = deque(maxlen=maxlen)
        self.mag_tremor: deque = deque(maxlen=maxlen)
        self.mag_gyro: deque = deque(maxlen=maxlen)

    def append(self, t: float, ax: float, ay: float, az: float, gx: float, gy: float, gz: float):
        self.timestamps.append(t)
        self.ax.append(ax)
        self.ay.append(ay)
        self.az.append(az)
        self.gx.append(gx)
        self.gy.append(gy)
        self.gz.append(gz)
        
        raw_mag = np.sqrt(ax**2 + ay**2 + az**2)
        gyro_m = np.sqrt(gx**2 + gy**2 + gz**2)
        self.mag_dyn.append(raw_mag - 9.81)
        self.mag_gyro.append(gyro_m)
        self.mag_tremor.append(raw_mag - 9.81)

    def clear(self):
        self.timestamps.clear()
        self.ax.clear()
        self.ay.clear()
        self.az.clear()
        self.gx.clear()
        self.gy.clear()
        self.gz.clear()
        self.mag_dyn.clear()
        self.mag_tremor.clear()
        self.mag_gyro.clear()

    def __len__(self):
        return len(self.timestamps)

    def get_numpy_arrays(self):
        return (
            np.array(self.timestamps),
            np.array(self.ax),
            np.array(self.ay),
            np.array(self.az),
            np.array(self.gx),
            np.array(self.gy),
            np.array(self.gz),
        )

    def get_processed_window(self, n_samples: int = 200, fs: Optional[float] = None):
        """
        Extracts last `n_samples` and runs 3D per-axis filtering.
        """
        if len(self.timestamps) < 15:
            return None
        
        if fs is not None and fs >= 10.0:
            self.filter_bank.set_sampling_rate(fs)
            
        n = min(len(self.timestamps), n_samples)
        t = np.array(list(self.timestamps))[-n:]
        ax = np.array(list(self.ax))[-n:]
        ay = np.array(list(self.ay))[-n:]
        az = np.array(list(self.az))[-n:]
        gx = np.array(list(self.gx))[-n:]
        gy = np.array(list(self.gy))[-n:]
        gz = np.array(list(self.gz))[-n:]
        
        acc_dyn, acc_tremor, gyro_mag, az_dyn = self.filter_bank.process_raw_imu(ax, ay, az, gx, gy, gz)
        axes_3d = self.filter_bank.process_axes_3d(ax, ay, az)
        
        return {
            "t": t,
            "ax": ax, "ay": ay, "az": az,
            "gx": gx, "gy": gy, "gz": gz,
            "acc_dyn": acc_dyn,
            "acc_tremor": acc_tremor,
            "gyro_mag": gyro_mag,
            "az_dyn": az_dyn,
            "axes_3d": axes_3d
        }
