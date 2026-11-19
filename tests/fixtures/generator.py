"""
Synthetic Sensor & Signal Generator for MotorTrack Testing
Provides deterministic, reproducible ground-truth IMU streams for mathematical DSP,
streaming reliability, and acceptance testing without relying on static CSV files.
"""

import numpy as np
from typing import Dict, List, Tuple, Any

def generate_sine(
    frequency: float = 5.0,
    amplitude: float = 0.2,
    duration: float = 10.0,
    fs: float = 50.0,
    phase: float = 0.0,
    noise_std: float = 0.0,
    seed: int = 42
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Generates a pure single-frequency sinusoidal test signal.
    Returns (timestamps, signal_values).
    """
    if seed is not None:
        np.random.seed(seed)
    n_samples = int(duration * fs)
    t = np.linspace(0, duration, n_samples, endpoint=False)
    signal = amplitude * np.sin(2.0 * np.pi * frequency * t + phase)
    if noise_std > 0:
        signal += np.random.normal(0, noise_std, size=n_samples)
    return t, signal

def generate_multi_tone(
    frequencies: List[float],
    amplitudes: List[float],
    duration: float = 10.0,
    fs: float = 50.0,
    noise_std: float = 0.0,
    seed: int = 42
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Generates a multi-frequency synthetic composite signal (e.g. 2Hz voluntary + 5Hz tremor).
    Returns (timestamps, composite_signal).
    """
    if seed is not None:
        np.random.seed(seed)
    n_samples = int(duration * fs)
    t = np.linspace(0, duration, n_samples, endpoint=False)
    signal = np.zeros(n_samples)
    for f, a in zip(frequencies, amplitudes):
        signal += a * np.sin(2.0 * np.pi * f * t)
    if noise_std > 0:
        signal += np.random.normal(0, noise_std, size=n_samples)
    return t, signal

def generate_resting_baseline(
    duration: float = 10.0,
    fs: float = 50.0,
    baseline_g: float = 9.81,
    noise_std: float = 0.012,
    seed: int = 42
) -> Dict[str, np.ndarray]:
    """
    Generates realistic 3-axis IMU accelerometer resting baseline data (stationary on flat surface).
    """
    if seed is not None:
        np.random.seed(seed)
    n_samples = int(duration * fs)
    t = np.linspace(0, duration, n_samples, endpoint=False)
    ax = np.random.normal(0, noise_std, size=n_samples)
    ay = np.random.normal(0, noise_std, size=n_samples)
    az = baseline_g + np.random.normal(0, noise_std, size=n_samples)
    gx = np.random.normal(0, 0.005, size=n_samples)
    gy = np.random.normal(0, 0.005, size=n_samples)
    gz = np.random.normal(0, 0.005, size=n_samples)
    return {
        "t": t, "ax": ax, "ay": ay, "az": az,
        "gx": gx, "gy": gy, "gz": gz
    }

def generate_packet_stream(
    frequency: float = 5.0,
    amplitude: float = 0.2,
    duration: float = 5.0,
    fs: float = 50.0,
    packet_loss_rate: float = 0.0,
    jitter_std_sec: float = 0.0,
    noise_std: float = 0.0,
    seed: int = 42
) -> List[Dict[str, Any]]:
    """
    Generates an ordered list of WebSocket sensor payloads with sequence numbers,
    realistic timestamp jitter, optional noise, and optional dropped packets.
    """
    if seed is not None:
        np.random.seed(seed)
    n_samples = int(duration * fs)
    dt = 1.0 / fs
    packets = []
    base_time = 1000.0

    current_t = base_time
    for seq in range(n_samples):
        # Apply packet loss
        if packet_loss_rate > 0 and np.random.random() < packet_loss_rate:
            continue
            
        jitter = np.random.normal(0, jitter_std_sec) if jitter_std_sec > 0 else 0.0
        sample_time = current_t + jitter
        
        noise = np.random.normal(0, noise_std) if noise_std > 0 else 0.0
        osc = amplitude * np.sin(2.0 * np.pi * frequency * (sample_time - base_time)) + noise
        packets.append({
            "seq": seq,
            "t": sample_time,
            "ax": round(float(osc * 0.7), 4),
            "ay": round(float(osc * 0.3), 4),
            "az": round(float(9.81 + osc * 0.5), 4),
            "gx": 0.02, "gy": -0.01, "gz": 0.01,
            "tap_event": False,
            "is_simulated": True
        })
        current_t += dt

    return packets

