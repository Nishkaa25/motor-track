"""
MotorTrack Hardware Benchmarking and Recording Export Utility
Phase 9 Deliverable: Standardized export and characterization format for real-device movement streams.

Captures:
  1. Device, Browser, and OS Environment
  2. Placement Configuration (Handheld, Pocket, Table Stationary)
  3. Protocol and Consent Metadata
  4. Monotonic and UTC Sample Timestamps
  5. Monotonic Sequence Numbers
  6. Raw 6-DOF IMU Observations (ax, ay, az, gx, gy, gz in SI units)
  7. Empirical Transport and Quality Metrics (Jitter, Packet Loss, Rate)
"""

import json
import time
import datetime
from typing import Dict, Any, List, Optional, Tuple

SUPPORTED_PLACEMENTS = ["HANDHELD", "POCKET", "TABLE_STATIONARY", "WRIST", "UNSPECIFIED"]

def build_hardware_recording_bundle(
    session_id: str,
    patient_id: str,
    protocol_type: str,
    provenance: str,
    device_info: Optional[Dict[str, Any]] = None,
    placement: str = "HANDHELD",
    started_at: Optional[datetime.datetime] = None,
    completed_at: Optional[datetime.datetime] = None,
    raw_readings: Optional[List[Dict[str, Any]]] = None,
    quality_summary: Optional[Dict[str, Any]] = None,
    derived_summary: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Constructs an authoritative, self-contained hardware benchmarking recording bundle.
    """
    if placement not in SUPPORTED_PLACEMENTS:
        placement = "UNSPECIFIED"

    readings = raw_readings or []
    sample_count = len(readings)

    # Calculate empirical sampling rate if readings have timestamps
    empirical_hz = 0.0
    jitter_ms = 0.0
    if sample_count >= 10:
        times = [r["t"] for r in readings if "t" in r and r["t"] is not None]
        if len(times) >= 10:
            diffs = [times[i] - times[i-1] for i in range(1, len(times)) if times[i] > times[i-1]]
            if diffs:
                mean_dt = sum(diffs) / len(diffs)
                empirical_hz = round(1.0 / mean_dt, 2) if mean_dt > 0 else 0.0
                var_dt = sum((d - mean_dt) ** 2 for d in diffs) / len(diffs)
                jitter_ms = round((var_dt ** 0.5) * 1000.0, 2)

    return {
        "format_version": "motortrack-benchmark-v1.0",
        "exported_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "recording_metadata": {
            "session_id": session_id,
            "patient_id": patient_id,
            "protocol_type": protocol_type,
            "provenance": provenance,
            "placement": placement,
            "started_at": started_at.isoformat() if started_at else None,
            "completed_at": completed_at.isoformat() if completed_at else None,
            "duration_sec": round((times[-1] - times[0]), 2) if sample_count >= 2 else 0.0
        },
        "device_environment": {
            "browser": (device_info or {}).get("browser", "Unknown"),
            "os": (device_info or {}).get("os", "Unknown"),
            "device_model": (device_info or {}).get("device_model", "Unknown"),
            "user_agent": (device_info or {}).get("user_agent", "Unknown"),
            "reported_sample_rate_hz": (device_info or {}).get("hardware_hz", None)
        },
        "empirical_quality_metrics": {
            "sample_count": sample_count,
            "empirical_hz": empirical_hz,
            "jitter_ms": jitter_ms,
            "packet_loss_pct": (quality_summary or {}).get("packet_loss_pct", None),
            "quality_status": (quality_summary or {}).get("status", "UNSPECIFIED")
        },
        "derived_summary": derived_summary or {},
        "raw_observations": [
            {
                "t": r.get("t"),
                "t_mono": r.get("t_mono"),
                "seq": r.get("seq"),
                "ax": r.get("ax"),
                "ay": r.get("ay"),
                "az": r.get("az"),
                "gx": r.get("gx"),
                "gy": r.get("gy"),
                "gz": r.get("gz")
            }
            for r in readings
        ]
    }

def validate_recording_bundle(bundle: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validates schema conformance and integrity of an exported recording bundle.
    """
    if "format_version" not in bundle or "recording_metadata" not in bundle:
        return False, "Missing format_version or recording_metadata"
    meta = bundle["recording_metadata"]
    for req_field in ["session_id", "patient_id", "protocol_type", "provenance"]:
        if req_field not in meta:
            return False, f"Missing required metadata field: {req_field}"
    if "raw_observations" not in bundle or not isinstance(bundle["raw_observations"], list):
        return False, "Missing or invalid raw_observations list"
    return True, "Valid"
