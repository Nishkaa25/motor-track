import numpy as np
from scipy import signal
from typing import Dict, Any, List

def compute_realtime_stft(
    signal_data: np.ndarray,
    fs: float = 50.0,
    nperseg: int = 64,
    noverlap: int = 48,
    max_freq: float = 15.0
) -> Dict[str, Any]:
    """
    Computes Short-Time Fourier Transform (STFT) for live spectrogram visualization.
    Returns:
      - frequencies (0 to max_freq Hz)
      - time bins (relative seconds)
      - log power magnitude matrix (normalized for plasma/viridis heatmap)
    """
    if len(signal_data) < nperseg:
        return {
            "frequencies": [],
            "time_bins": [],
            "spectrogram": []
        }
        
    f, t, Zxx = signal.stft(
        signal_data,
        fs=fs,
        nperseg=nperseg,
        noverlap=noverlap,
        boundary=None,
        padded=False
    )
    
    # Filter frequencies up to max_freq (e.g. 15 Hz)
    freq_mask = f <= max_freq
    f_filtered = f[freq_mask]
    Zxx_filtered = Zxx[freq_mask, :]
    
    # Power spectral density in dB: 10 * log10(|Zxx|^2 + eps)
    power = np.abs(Zxx_filtered) ** 2
    power_db = 10.0 * np.log10(power + 1e-6)
    
    # Normalize between 0 and 100 for responsive heatmap rendering
    min_db = -40.0
    max_db = 20.0
    norm_power = np.clip((power_db - min_db) / (max_db - min_db), 0.0, 1.0) * 100.0
    
    return {
        "frequencies": [round(float(freq), 2) for freq in f_filtered],
        "time_bins": [round(float(tb), 2) for tb in t],
        "spectrogram": np.round(norm_power, 1).tolist()  # 2D array [freq_idx][time_idx]
    }
