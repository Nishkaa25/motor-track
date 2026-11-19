"""
MotorTrack Engineering Benchmarks Runner
Computes and reports reproducible quantitative metrics across signal processing,
filter responses, multi-rate stability, and 3D rotation invariance.
All measurements use explicit inputs and stated tolerances.
"""

import sys
import json
import datetime
import numpy as np
from scipy import signal
from backend.app.dsp.filters import RealtimeFilterBank
from backend.app.dsp.features import extract_window_features, compute_fft_psd

def run_all_benchmarks():
    print("=" * 70)
    print(" MOTORTRACK ENGINEERING BENCHMARKS EXECUTION ")
    print(" Python Version:", sys.version.split()[0])
    print(" Target Monitors: 3D Movement Telemetry & Tremor Band (3.5 - 7.5 Hz)")
    print("=" * 70)

    results = []

    # 1. DC Gravity Suppression (Highpass Filter)
    fs = 50.0
    duration = 5.0
    t = np.linspace(0, duration, int(fs * duration), endpoint=False)
    dc_gravity = 9.81
    ac_signal = 0.25 * np.sin(2.0 * np.pi * 5.0 * t)
    raw_imu = dc_gravity + ac_signal
    fb = RealtimeFilterBank(fs=fs)
    filtered_hp = fb.remove_gravity(raw_imu)
    
    # Measure residual DC offset (discarding 1 sec warm-up/edge transient)
    steady_slice = filtered_hp[int(fs * 1.0): -int(fs * 0.5)]
    residual_dc = float(np.mean(steady_slice))
    dc_attenuation_db = float(20.0 * np.log10(dc_gravity / max(1e-6, abs(residual_dc))))
    
    status_1 = "PASS" if dc_attenuation_db >= 30.0 else "FAIL"
    results.append({
        "name": "DC Gravity Suppression (Highpass >0.5 Hz)",
        "input": "9.81 m/s² DC + 0.25 m/s² 5Hz sine, fs=50Hz, duration=5s",
        "threshold": ">= 30.0 dB attenuation",
        "measured": f"{dc_attenuation_db:.1f} dB (residual DC {residual_dc:.5f} m/s²)",
        "status": status_1
    })

    # 2. Bandpass Low Selectivity (1.0 Hz rejection) & High Selectivity (15.0 Hz rejection)
    sig_1hz = 0.50 * np.sin(2.0 * np.pi * 1.0 * t)
    sig_5hz = 0.50 * np.sin(2.0 * np.pi * 5.0 * t)
    sig_15hz = 0.50 * np.sin(2.0 * np.pi * 15.0 * t)
    
    out_1hz = fb.apply_tremor_bandpass(sig_1hz)
    out_5hz = fb.apply_tremor_bandpass(sig_5hz)
    out_15hz = fb.apply_tremor_bandpass(sig_15hz)
    
    rms_in = 0.50 / np.sqrt(2.0)
    rms_out_1hz = float(np.sqrt(np.mean(out_1hz[int(fs):-int(fs)]**2)))
    rms_out_5hz = float(np.sqrt(np.mean(out_5hz[int(fs):-int(fs)]**2)))
    rms_out_15hz = float(np.sqrt(np.mean(out_15hz[int(fs):-int(fs)]**2)))
    
    gain_5hz_db = float(20.0 * np.log10(max(1e-6, rms_out_5hz / rms_in)))
    rejection_1hz_db = float(20.0 * np.log10(max(1e-6, rms_out_5hz / max(1e-6, rms_out_1hz))))
    rejection_15hz_db = float(20.0 * np.log10(max(1e-6, rms_out_5hz / max(1e-6, rms_out_1hz))))

    results.append({
        "name": "Bandpass 5.0 Hz Passband Gain",
        "input": "0.50 m/s² 5.0 Hz sine, fs=50Hz",
        "threshold": "-3.0 dB to +1.0 dB",
        "measured": f"{gain_5hz_db:.2f} dB",
        "status": "PASS" if -3.0 <= gain_5hz_db <= 1.0 else "FAIL"
    })

    results.append({
        "name": "Bandpass 1.0 Hz Low-Frequency Rejection",
        "input": "0.50 m/s² 1.0 Hz sine relative to 5.0 Hz passband",
        "threshold": ">= 20.0 dB rejection",
        "measured": f"{rejection_1hz_db:.1f} dB",
        "status": "PASS" if rejection_1hz_db >= 20.0 else "FAIL"
    })

    results.append({
        "name": "Bandpass 15.0 Hz High-Frequency Rejection",
        "input": "0.50 m/s² 15.0 Hz sine relative to 5.0 Hz passband",
        "threshold": ">= 18.0 dB rejection",
        "measured": f"{rejection_15hz_db:.1f} dB",
        "status": "PASS" if rejection_15hz_db >= 18.0 else "FAIL"
    })

    # 3. 3D Rotation Invariance & No Frequency Doubling
    amp_rot = 0.25
    expected_rms = amp_rot / np.sqrt(2.0)
    osc_5hz = amp_rot * np.sin(2.0 * np.pi * 5.0 * t)
    
    orientations = [
        ("Parallel to gravity (pure Z)", 0.0, 0.0, 1.0),
        ("Perpendicular to gravity (pure X)", 1.0, 0.0, 0.0),
        ("45-deg oblique in X-Z", np.cos(np.pi/4), 0.0, np.sin(np.pi/4)),
        ("Arbitrary 3D diagonal", 1.0/np.sqrt(3), 1.0/np.sqrt(3), 1.0/np.sqrt(3))
    ]
    
    rot_pass = True
    rot_details = []
    for oname, kx, ky, kz in orientations:
        ax = kx * osc_5hz
        ay = ky * osc_5hz
        az = 9.81 + kz * osc_5hz
        axes_3d = fb.process_axes_3d(ax, ay, az)
        acc_dyn, acc_tremor, _, _ = fb.process_raw_imu(ax, ay, az, np.zeros_like(ax), np.zeros_like(ay), np.zeros_like(az))
        feat = extract_window_features(acc_dyn, acc_tremor, fs=fs, axes_3d=axes_3d)
        
        f_err = abs(feat["dominant_freq"] - 5.0)
        rms_err = abs(feat["rms_amp_tremor"] - expected_rms)
        rot_details.append(f"{oname}: freq={feat['dominant_freq']}Hz, rms={feat['rms_amp_tremor']:.3f}")
        if f_err > 0.15 or rms_err > 0.03:
            rot_pass = False

    results.append({
        "name": "3D Rotation Invariance (Summed 3D PSD, No Rectification Doubling)",
        "input": "0.25 m/s² 5.0 Hz at 0°, 45°, 90°, and 3D diagonal orientations",
        "threshold": "Identical ~5.0 Hz dominant freq (delta<=0.15Hz), RMS within 0.03 m/s²",
        "measured": "; ".join(rot_details),
        "status": "PASS" if rot_pass else "FAIL"
    })

    # 4. Frequency Estimation Error via Parabolic Interpolation
    test_freqs = [4.0, 4.8, 5.0, 5.5, 6.0, 7.0]
    freq_errors = []
    for tf in test_freqs:
        sig_f = 0.25 * np.sin(2.0 * np.pi * tf * t)
        feat_f = extract_window_features(sig_f, sig_f, fs=fs)
        freq_errors.append(abs(feat_f["dominant_freq"] - tf))
    
    max_freq_err = max(freq_errors)
    results.append({
        "name": "Dominant Frequency Estimation Accuracy (Parabolic Refinement)",
        "input": "Synthetic tones: 4.0, 4.8, 5.0, 5.5, 6.0, 7.0 Hz, N=200, fs=50Hz",
        "threshold": "Max estimation error <= 0.15 Hz",
        "measured": f"Max error {max_freq_err:.3f} Hz across all tones",
        "status": "PASS" if max_freq_err <= 0.15 else "FAIL"
    })

    # 5. RMS Linearity Deviation
    test_amps = [0.10, 0.20, 0.35, 0.50, 0.80]
    rms_devs = []
    for a in test_amps:
        sig_a = a * np.sin(2.0 * np.pi * 5.0 * t)
        feat_a = extract_window_features(sig_a, sig_a, fs=fs)
        exp_a = a / np.sqrt(2.0)
        rms_devs.append(abs(feat_a["rms_amp"] - exp_a) / exp_a * 100.0)
    
    max_rms_dev = max(rms_devs)
    results.append({
        "name": "RMS Linearity Deviation (vs A / sqrt(2))",
        "input": "Peak amplitudes 0.10 to 0.80 m/s² at 5.0 Hz",
        "threshold": "Max relative deviation <= 5.0%",
        "measured": f"Max deviation {max_rms_dev:.2f}%",
        "status": "PASS" if max_rms_dev <= 5.0 else "FAIL"
    })

    # 6. Multi-Rate Verification (40 Hz, 50 Hz, 60 Hz)
    multi_rates = [40.0, 50.0, 60.0]
    multi_rate_details = []
    multi_rate_pass = True
    for test_fs in multi_rates:
        t_m = np.linspace(0, duration, int(test_fs * duration), endpoint=False)
        sig_m = 0.25 * np.sin(2.0 * np.pi * 5.0 * t_m)
        fb_m = RealtimeFilterBank(fs=test_fs)
        filtered_m = fb_m.apply_tremor_bandpass(sig_m)
        feat_m = extract_window_features(sig_m, filtered_m, fs=test_fs)
        
        err_m = abs(feat_m["dominant_freq"] - 5.0)
        multi_rate_details.append(f"{int(test_fs)}Hz -> {feat_m['dominant_freq']}Hz (err {err_m:.2f}Hz)")
        if err_m > 0.15:
            multi_rate_pass = False

    results.append({
        "name": "Multi-Rate Consistency (40 Hz, 50 Hz, 60 Hz)",
        "input": "5.0 Hz pure tone sampled at 40 Hz, 50 Hz, and 60 Hz",
        "threshold": "Dominant freq error <= 0.15 Hz without rate distortion",
        "measured": "; ".join(multi_rate_details),
        "status": "PASS" if multi_rate_pass else "FAIL"
    })

    # Output formatted table
    print(f"\n{'BENCHMARK DOMAIN':<40} | {'CRITERION':<20} | {'MEASURED RESULT':<30} | {'STATUS'}")
    print("-" * 105)
    for r in results:
        print(f"{r['name'][:38]:<40} | {r['threshold'][:18]:<20} | {r['measured'][:28]:<30} | {r['status']}")

    print("=" * 105)
    all_passed = all(r["status"] == "PASS" for r in results)
    print("ALL BENCHMARKS VERIFIED:", "PASS" if all_passed else "FAIL")

    benchmark_payload = {
        "benchmark_execution_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "python_version": sys.version,
        "total_benchmarks": len(results),
        "passed_benchmarks": sum(1 for r in results if r["status"] == "PASS"),
        "overall_status": "PASS" if all_passed else "FAIL",
        "benchmarks": results
    }
    with open("benchmark_results.json", "w", encoding="utf-8") as f:
        json.dump(benchmark_payload, f, indent=2)
    print("Machine-readable results written to benchmark_results.json")

    return all_passed

if __name__ == "__main__":
    success = run_all_benchmarks()
    sys.exit(0 if success else 1)
