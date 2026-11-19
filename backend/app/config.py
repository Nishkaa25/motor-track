import os
import socket
from pathlib import Path
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(exist_ok=True, parents=True)

DB_PATH = Path(os.environ.get("DB_PATH", DATA_DIR / "parkinson_engine.db"))
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite+aiosqlite:///{DB_PATH}")

def get_lan_ip() -> str:
    """Auto-detect the local Wi-Fi / Ethernet IPv4 address for QR code and mobile connections."""
    if os.environ.get("LAN_IP"):
        return os.environ["LAN_IP"]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

class AppConfig(BaseModel):
    app_name: str = "MotorTrack: Movement-Signal Telemetry Research Prototype"
    version: str = "2.0.0"
    host: str = os.environ.get("HOST", "0.0.0.0")
    port: int = int(os.environ.get("PORT", "8000"))
    lan_ip: str = get_lan_ip()
    api_secret: str = os.environ.get("MOTORTRACK_SECRET", "motortrack-research-local-secret")
    allowed_origins: str = os.environ.get("ALLOWED_ORIGINS", "*")
    public_base_url: str = os.environ.get("PUBLIC_BASE_URL", "")  # e.g. https://my-host:8000
    
    # DSP & Sampling settings
    sampling_rate_hz: float = float(os.environ.get("TARGET_SAMPLING_HZ", "50.0"))
    nyquist_freq: float = 25.0
    gravity_highpass_cutoff: float = 0.5  # Hz (>0.5 Hz removes DC gravity)
    tremor_band_low: float = 3.5  # Hz
    tremor_band_high: float = 7.5  # Hz
    tremor_power_ratio_threshold: float = 0.40  # > 40% in tremor band
    noise_rms_threshold: float = 0.05  # m/s^2
    
    # Windowing settings
    window_duration_sec: float = 4.0  # 4-second sliding analysis window
    window_samples: int = 200  # 4 * 50
    step_samples: int = 50  # 1 second step
    
    # Buffer lengths
    live_buffer_samples: int = 500  # 10 seconds of 50Hz data
    
    # FOG (Freezing of Gait) settings
    locomotion_band_low: float = 0.5
    locomotion_band_high: float = 3.0
    freeze_band_low: float = 3.0
    freeze_band_high: float = 8.0
    fog_threshold: float = 2.5
    
    # Bradykinesia Finger Tapping settings
    tapping_test_duration_sec: float = 15.0
    
    # Standard 3-Test Tremor protocol durations & preparation time
    prep_countdown_sec: float = 10.0  # 10s calm preparation countdown
    resting_test_sec: float = 20.0    # 20s recording duration
    postural_test_sec: float = 20.0   # 20s recording duration
    action_test_sec: float = 20.0     # 20s recording duration
    gait_test_sec: float = 25.0       # 25s recording duration

config = AppConfig()
