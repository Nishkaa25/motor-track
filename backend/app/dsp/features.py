import numpy as np
from scipy import signal
from typing import Dict, Any, Tuple, Optional
from backend.app.config import config

def compute_fft_psd(
    data: np.ndarray,
    fs: float = 50.0,
    nperseg: int = 128
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Computes Power Spectral Density (PSD) using Welch's method.
    Returns (frequencies, psd_values).
    """
    if len(data) < 16:
        return np.array([]), np.array([])
    
    nperseg = min(len(data), nperseg)
    freqs, psd = signal.welch(data, fs=fs, nperseg=nperseg, scaling='density')
    return freqs, psd

def parabolic_peak_interpolation(freqs: np.ndarray, psd: np.ndarray, peak_idx: int) -> float:
    """
    Sub-bin frequency estimation via parabolic interpolation of the log spectrum.
    Refines peak location between discrete FFT bins.
    """
    if len(freqs) < 3 or peak_idx <= 0 or peak_idx >= len(freqs) - 1:
        return float(freqs[peak_idx]) if len(freqs) > peak_idx else 0.0
    
    y0 = float(np.log(max(psd[peak_idx - 1], 1e-12)))
    y1 = float(np.log(max(psd[peak_idx], 1e-12)))
    y2 = float(np.log(max(psd[peak_idx + 1], 1e-12)))
    
    denom = y0 - 2.0 * y1 + y2
    if abs(denom) < 1e-12:
        return float(freqs[peak_idx])
    
    delta = 0.5 * (y0 - y2) / denom
    delta = max(-0.5, min(0.5, delta))
    df = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 0.0
    return float(freqs[peak_idx] + delta * df)

def extract_window_features(
    signal_dyn: np.ndarray,
    signal_tremor: np.ndarray,
    fs: float = 50.0,
    axes_3d: Optional[Dict[str, np.ndarray]] = None
) -> Dict[str, Any]:
    """
    Extracts tremor-band spectral and amplitude features from acceleration signals.
    When axes_3d is provided (ax_dyn, ay_dyn, az_dyn), computes summed 3D PSD
    to guarantee 3D orientation invariance without frequency-doubling magnitude rectification.
    """
    N = len(signal_dyn)
    if N < 15:
        return {
            "dominant_freq": 0.0,
            "peak_freq_full": 0.0,
            "rms_amp": 0.0,
            "rms_amp_tremor": 0.0,
            "band_power_ratio": 0.0,
            "spectral_entropy": 0.0,
            "zero_crossing_rate": 0.0,
            "tremor_detected": False,
            "severity": "NONE",
            "psd_freqs": [],
            "psd_values": []
        }
    
    # 1. Resultant Band-Limited RMS
    if axes_3d is not None and "ax_bp" in axes_3d:
        ax_bp = axes_3d["ax_bp"]
        ay_bp = axes_3d["ay_bp"]
        az_bp = axes_3d["az_bp"]
        rms_tremor = float(np.sqrt(np.mean(ax_bp**2 + ay_bp**2 + az_bp**2)))
        
        ax_dyn = axes_3d.get("ax_dyn", signal_dyn)
        ay_dyn = axes_3d.get("ay_dyn", signal_dyn)
        az_dyn = axes_3d.get("az_dyn", signal_dyn)
        rms_dyn = float(np.sqrt(np.mean(ax_dyn**2 + ay_dyn**2 + az_dyn**2)))
    else:
        rms_dyn = float(np.sqrt(np.mean(signal_dyn ** 2)))
        rms_tremor = float(np.sqrt(np.mean(signal_tremor ** 2)))
    
    # 2. Power Spectral Density (PSD)
    nperseg = min(N, 128 if N >= 128 else N)
    
    if axes_3d is not None and "ax_dyn" in axes_3d:
        freqs, psd_x = compute_fft_psd(axes_3d["ax_dyn"], fs=fs, nperseg=nperseg)
        _, psd_y = compute_fft_psd(axes_3d["ay_dyn"], fs=fs, nperseg=nperseg)
        _, psd_z = compute_fft_psd(axes_3d["az_dyn"], fs=fs, nperseg=nperseg)
        if len(freqs) > 0 and len(psd_x) == len(freqs) and len(psd_y) == len(freqs) and len(psd_z) == len(freqs):
            psd = psd_x + psd_y + psd_z
        else:
            freqs, psd = compute_fft_psd(signal_dyn, fs=fs, nperseg=nperseg)
    else:
        freqs, psd = compute_fft_psd(signal_dyn, fs=fs, nperseg=nperseg)
    
    if len(freqs) == 0 or np.sum(psd) <= 1e-12:
        return {
            "dominant_freq": 0.0,
            "peak_freq_full": 0.0,
            "rms_amp": rms_dyn,
            "rms_amp_tremor": rms_tremor,
            "band_power_ratio": 0.0,
            "spectral_entropy": 0.0,
            "zero_crossing_rate": 0.0,
            "tremor_detected": False,
            "severity": "NONE",
            "psd_freqs": [],
            "psd_values": []
        }
    
    # Peak frequency across valid spectrum (> 0.5 Hz)
    valid_mask = freqs >= config.gravity_highpass_cutoff
    if np.any(valid_mask):
        valid_indices = np.where(valid_mask)[0]
        max_valid_idx = valid_indices[np.argmax(psd[valid_mask])]
        peak_freq_full = parabolic_peak_interpolation(freqs, psd, max_valid_idx)
    else:
        peak_freq_full = 0.0
        
    # Tremor band mask (3.5 - 7.5 Hz)
    tremor_mask = (freqs >= config.tremor_band_low) & (freqs <= config.tremor_band_high)
    total_mask = freqs >= config.gravity_highpass_cutoff
    
    trapz_fn = getattr(np, 'trapezoid', getattr(np, 'trapz', None))
    power_total = trapz_fn(psd[total_mask], freqs[total_mask]) if np.any(total_mask) else 1e-9
    power_tremor = trapz_fn(psd[tremor_mask], freqs[tremor_mask]) if np.any(tremor_mask) else 0.0
    
    band_power_ratio = float(power_tremor / power_total) if power_total > 1e-12 else 0.0
        
    # Dominant frequency in 3.5 - 7.5 Hz with parabolic refinement
    if np.any(tremor_mask) and np.max(psd[tremor_mask]) > 1e-9:
        tremor_indices = np.where(tremor_mask)[0]
        max_tremor_idx = tremor_indices[np.argmax(psd[tremor_mask])]
        dominant_freq = parabolic_peak_interpolation(freqs, psd, max_tremor_idx)
    else:
        dominant_freq = peak_freq_full if (config.tremor_band_low <= peak_freq_full <= config.tremor_band_high) else 0.0
        
    # 3. Spectral Entropy
    psd_norm = psd[total_mask] / (np.sum(psd[total_mask]) + 1e-12)
    psd_norm = psd_norm[psd_norm > 0]
    spectral_entropy = float(-np.sum(psd_norm * np.log2(psd_norm)) / (np.log2(len(psd_norm)) + 1e-12))
    
    # 4. Zero Crossing Rate
    centered = signal_dyn - np.mean(signal_dyn)
    zero_crossings = np.where(np.diff(np.signbit(centered)))[0]
    zcr = float(len(zero_crossings) / (N / fs))
    
    # 5. Rule-Based Tremor Detector
    # Strictly requires dominant peak inside monitored band + sufficient band power ratio + above noise floor
    has_dominant_in_band = (config.tremor_band_low <= dominant_freq <= config.tremor_band_high)
    
    tremor_detected = bool(
        has_dominant_in_band and
        (band_power_ratio >= config.tremor_power_ratio_threshold) and
        (rms_tremor >= config.noise_rms_threshold)
    )
    
    # Research amplitude tier (descriptive, not diagnostic)
    if not tremor_detected or rms_tremor < config.noise_rms_threshold:
        severity = "NONE"
    elif rms_tremor < 0.20:
        severity = "MILD"
    elif rms_tremor < 0.60:
        severity = "MODERATE"
    else:
        severity = "SEVERE"
        
    return {
        "dominant_freq": round(dominant_freq, 2) if dominant_freq > 0 else 0.0,
        "peak_freq_full": round(peak_freq_full, 2),
        "rms_amp": round(rms_dyn, 4),
        "rms_amp_tremor": round(rms_tremor, 4),
        "band_power_ratio": round(band_power_ratio, 4),
        "spectral_entropy": round(spectral_entropy, 4),
        "zero_crossing_rate": round(zcr, 2),
        "tremor_detected": tremor_detected,
        "severity": severity,
        "psd_freqs": [round(float(f), 2) for f in freqs[:32]],
        "psd_values": [round(float(p), 5) for p in psd[:32]]
    }
