import sys
import uvicorn
from backend.app.config import config

if __name__ == "__main__":
    print("\n" + "=" * 68)
    print("  MOTORTRACK: SMARTPHONE MOTOR SYMPTOM MONITORING SYSTEM")
    print("  (Research Prototype • Quantitative Motor Telemetry)")
    print("=" * 68)
    print(f"  Laptop Dashboard:  http://localhost:{config.port}")
    print(f"  Phone Sensor App:  http://{config.lan_ip}:{config.port}/phone")
    print(f"  Target Sampling:   {config.sampling_rate_hz} Hz (Empirical Measurement-Driven)")
    print(f"  Tremor Bandpass:   {config.tremor_band_low} - {config.tremor_band_high} Hz")
    print("=" * 68 + "\n")
    
    uvicorn.run(
        "backend.app.main:app",
        host=config.host,
        port=config.port,
        reload=False,
        log_level="info"
    )
