import io
import json
import html
import base64
import datetime
import secrets
from typing import List, Optional, Dict, Any
import qrcode
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.config import config
from backend.app.database import get_db
from backend.app.models.db_models import Patient, Session, TestSegment, ClinicalSummary, Medication, MedicationEvent, SymptomEvent, Reading
from backend.app.protocols.state_machine import state_machine
from backend.app.protocols.simulator import simulator
from backend.app.routers.ws import active_pairing_tokens
from backend.app.dsp.recording_utility import build_hardware_recording_bundle

router = APIRouter(prefix="/api")

# Pydantic Schemas
class PatientCreate(BaseModel):
    patient_id: str
    name: str
    age: Optional[int] = None
    gender: Optional[str] = None
    diagnosis_year: Optional[int] = None
    notes: Optional[str] = None

class MedicationCreate(BaseModel):
    name: str
    dosage: str = "100/25 mg"
    scheduled_time: str = "08:00 AM"
    notes: Optional[str] = None

class MedicationEventCreate(BaseModel):
    medication_name: str
    dose: str = "100/25 mg"
    timestamp: Optional[str] = None
    source: str = "USER_LOGGED"
    notes: Optional[str] = None

class SymptomEventCreate(BaseModel):
    symptom: str
    severity: str = "MILD"
    timestamp: Optional[str] = None
    source: str = "USER_LOGGED"
    notes: Optional[str] = None

class ProtocolStartRequest(BaseModel):
    protocol_type: str = "TREMOR_3TEST"
    patient_id: str = "P-101"

class SimulatorStartRequest(BaseModel):
    profile: str = "SEVERE_PD"

# --- Patient Endpoints ---
@router.get("/patients")
async def list_patients(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Patient).order_by(Patient.created_at.desc()))
    patients = result.scalars().all()
    return [{
        "patient_id": p.patient_id,
        "name": p.name,
        "age": p.age,
        "gender": p.gender,
        "diagnosis_year": p.diagnosis_year,
        "notes": p.notes,
        "created_at": p.created_at.isoformat() if p.created_at else None
    } for p in patients]

@router.post("/patients")
async def create_patient(payload: PatientCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.get(Patient, payload.patient_id)
    if existing:
        raise HTTPException(status_code=400, detail="Patient ID already exists")
    
    patient = Patient(
        patient_id=payload.patient_id,
        name=payload.name,
        age=payload.age,
        gender=payload.gender,
        diagnosis_year=payload.diagnosis_year,
        notes=payload.notes
    )
    db.add(patient)
    await db.commit()
    return {"status": "success", "patient_id": patient.patient_id}

# --- QR Code, Pairing & Network Endpoints ---
@router.get("/network-info")
async def get_network_info():
    base = config.public_base_url if config.public_base_url else f"http://{config.lan_ip}:{config.port}"
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    
    token = secrets.token_urlsafe(16)
    active_pairing_tokens[token] = {"patient_id": state_machine.patient_id, "created_at": datetime.datetime.now().timestamp()}
    
    phone_url = f"{base}/phone?token={token}"
    ws_url = f"{ws_base}/ws/phone?token={token}"
    return {
        "lan_ip": config.lan_ip,
        "port": config.port,
        "pairing_token": token,
        "phone_url": phone_url,
        "ws_url": ws_url
    }

@router.get("/qr")
async def generate_qr_code():
    """Generates QR code with short-lived pairing token for authenticated mobile access."""
    base = config.public_base_url if config.public_base_url else f"http://{config.lan_ip}:{config.port}"
    token = secrets.token_urlsafe(16)
    active_pairing_tokens[token] = {"patient_id": state_machine.patient_id, "created_at": datetime.datetime.now().timestamp()}
    phone_url = f"{base}/phone?token={token}"
    
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(phone_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="#0d9488", back_color="#0f172a")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    
    base64_str = base64.b64encode(png_bytes).decode("utf-8")
    return {
        "url": phone_url,
        "token": token,
        "image_data": f"data:image/png;base64,{base64_str}"
    }

# --- Sessions & Longitudinal Trends ---
@router.get("/sessions")
async def list_sessions(patient_id: Optional[str] = None, provenance_filter: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    query = select(Session).options(selectinload(Session.clinical_summary), selectinload(Session.patient)).order_by(desc(Session.started_at))
    if patient_id:
        query = query.where(Session.patient_id == patient_id)
    if provenance_filter:
        query = query.where(Session.provenance == provenance_filter)
        
    result = await db.execute(query)
    sessions = result.scalars().all()
    
    return [{
        "session_id": s.session_id,
        "patient_id": s.patient_id,
        "patient_name": s.patient.name if s.patient else "Unknown",
        "session_type": s.session_type,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        "provenance": getattr(s, "provenance", "UNKNOWN"),
        "is_simulated": s.is_simulated,
        "quality_status": getattr(s, "quality_status", "RELIABLE"),
        "summary": {
            "overall_severity": s.clinical_summary.overall_tremor_severity if s.clinical_summary else "N/A",
            "resting_freq": s.clinical_summary.resting_dominant_freq if s.clinical_summary else None,
            "resting_rms": s.clinical_summary.resting_rms_amp if s.clinical_summary else None,
            "fog_episodes": s.clinical_summary.fog_episodes_count if s.clinical_summary else 0,
            "tap_rate": s.clinical_summary.tap_rate_per_sec if s.clinical_summary else None,
            "cadence": s.clinical_summary.gait_cadence_spm if s.clinical_summary else None,
            "ml_confidence": s.clinical_summary.ml_tremor_confidence if s.clinical_summary else None,
            "overall_status": s.clinical_summary.overall_status if s.clinical_summary else "N/A"
        } if s.clinical_summary else None
    } for s in sessions]

@router.get("/sessions/{session_id}")
async def get_session_detail(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Session)
        .options(selectinload(Session.segments), selectinload(Session.clinical_summary), selectinload(Session.patient))
        .where(Session.session_id == session_id)
    )
    sess = result.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
        
    segments_data = []
    for seg in sess.segments:
        summary_parsed = json.loads(seg.summary_json) if seg.summary_json else {}
        segments_data.append({
            "segment_id": seg.segment_id,
            "test_type": seg.test_type,
            "start_t": seg.start_t,
            "end_t": seg.end_t,
            "summary": summary_parsed
        })

    return {
        "session_id": sess.session_id,
        "patient_id": sess.patient_id,
        "provenance": getattr(sess, "provenance", "REAL_DEVICE"),
        "is_simulated": sess.is_simulated,
        "patient": {
            "name": sess.patient.name,
            "age": sess.patient.age,
            "gender": sess.patient.gender,
            "diagnosis_year": sess.patient.diagnosis_year
        } if sess.patient else None,
        "session_type": sess.session_type,
        "started_at": sess.started_at.isoformat() if sess.started_at else None,
        "completed_at": sess.completed_at.isoformat() if sess.completed_at else None,
        "segments": segments_data,
        "clinical_summary": {
            "overall_tremor_severity": sess.clinical_summary.overall_tremor_severity if sess.clinical_summary else None,
            "resting_dominant_freq": sess.clinical_summary.resting_dominant_freq if sess.clinical_summary else None,
            "resting_rms_amp": sess.clinical_summary.resting_rms_amp if sess.clinical_summary else None,
            "postural_dominant_freq": sess.clinical_summary.postural_dominant_freq if sess.clinical_summary else None,
            "action_dominant_freq": sess.clinical_summary.action_dominant_freq if sess.clinical_summary else None,
            "fog_episodes_count": sess.clinical_summary.fog_episodes_count if sess.clinical_summary else 0,
            "tap_rate_per_sec": sess.clinical_summary.tap_rate_per_sec if sess.clinical_summary else None,
            "tap_irregularity_cv": sess.clinical_summary.tap_irregularity_cv if sess.clinical_summary else None,
            "tap_fatigue_slope": sess.clinical_summary.tap_fatigue_slope if sess.clinical_summary else None,
            "gait_cadence_spm": sess.clinical_summary.gait_cadence_spm if sess.clinical_summary else None,
            "gait_shuffle_score": sess.clinical_summary.gait_shuffle_score if sess.clinical_summary else None,
            "ml_tremor_confidence": sess.clinical_summary.ml_tremor_confidence if sess.clinical_summary else None,
            "overall_status": sess.clinical_summary.overall_status if sess.clinical_summary else None
        } if sess.clinical_summary else None
    }

@router.get("/analytics/longitudinal/{patient_id}")
async def get_longitudinal_trends(patient_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Session)
        .options(selectinload(Session.clinical_summary))
        .where(Session.patient_id == patient_id)
        .order_by(Session.started_at.asc())
    )
    sessions = result.scalars().all()
    
    res_meds = await db.execute(
        select(MedicationEvent)
        .where(MedicationEvent.patient_id == patient_id)
        .order_by(MedicationEvent.timestamp.asc())
    )
    med_events = res_meds.scalars().all()
    
    res_symps = await db.execute(
        select(SymptomEvent)
        .where(SymptomEvent.patient_id == patient_id)
        .order_by(SymptomEvent.timestamp.asc())
    )
    symp_events = res_symps.scalars().all()
    
    dates = []
    iso_timestamps = []
    resting_amps = []
    postural_amps = []
    action_amps = []
    dominant_freqs = []
    severities = []
    detailed_sessions = []
    
    for s in sessions:
        if not s.clinical_summary:
            continue
        c = s.clinical_summary
        dt_str = s.started_at.strftime("%b %d, %H:%M") if s.started_at else "N/A"
        dates.append(dt_str)
        iso_timestamps.append(s.started_at.isoformat() if s.started_at else None)
        r_amp = round(c.resting_rms_amp or 0.0, 4)
        p_amp = round(c.postural_rms_amp or 0.0, 4)
        a_amp = round(c.action_rms_amp or 0.0, 4)
        dom_f = round(c.resting_dominant_freq or c.postural_dominant_freq or 0.0, 2)
        sev = c.overall_tremor_severity or "NONE"
        
        resting_amps.append(r_amp)
        postural_amps.append(p_amp)
        action_amps.append(a_amp)
        dominant_freqs.append(dom_f)
        severities.append(sev)
        
        # Calculate elapsed interval since most recent medication event
        time_since_dose_str = "No prior dose recorded"
        if s.started_at:
            prior_meds = [m for m in med_events if m.timestamp <= s.started_at]
            if prior_meds:
                latest_dose = prior_meds[-1]
                delta_mins = int((s.started_at - latest_dose.timestamp).total_seconds() / 60.0)
                hrs = delta_mins // 60
                mins = delta_mins % 60
                time_since_dose_str = f"{hrs}h {mins}m since recorded {latest_dose.medication_name} dose"
                
        detailed_sessions.append({
            "session_id": s.session_id,
            "date_str": dt_str,
            "timestamp": s.started_at.isoformat() if s.started_at else None,
            "resting_amp": r_amp,
            "postural_amp": p_amp,
            "action_amp": a_amp,
            "dominant_freq": dom_f,
            "severity": sev,
            "provenance": getattr(s, "provenance", "REAL_DEVICE"),
            "time_since_medication": time_since_dose_str
        })

    med_list = [
        {
            "event_id": m.event_id,
            "name": m.medication_name,
            "dose": m.dose,
            "timestamp": m.timestamp.isoformat(),
            "time_str": m.timestamp.strftime("%b %d, %H:%M"),
            "source": m.source,
            "notes": m.notes
        }
        for m in med_events
    ]
    
    symp_list = [
        {
            "event_id": sy.event_id,
            "symptom": sy.symptom,
            "severity": sy.severity,
            "timestamp": sy.timestamp.isoformat(),
            "time_str": sy.timestamp.strftime("%b %d, %H:%M"),
            "source": sy.source,
            "notes": sy.notes
        }
        for sy in symp_events
    ]

    return {
        "patient_id": patient_id,
        "total_sessions": len(dates),
        "timeline": {
            "dates": dates,
            "iso_timestamps": iso_timestamps,
            "resting_amps": resting_amps,
            "postural_amps": postural_amps,
            "action_amps": action_amps,
            "dominant_freqs": dominant_freqs,
            "severities": severities
        },
        "sessions": detailed_sessions,
        "medication_events": med_list,
        "symptom_events": symp_list
    }

# --- Protocol & Simulator Controls ---
@router.post("/protocol/start")
async def api_start_protocol(payload: ProtocolStartRequest):
    provenance = "SIMULATED" if simulator.is_running else "REAL_DEVICE"
    state_machine.start_session(protocol_type=payload.protocol_type, patient_id=payload.patient_id, provenance=provenance)
    return state_machine.get_status()

@router.post("/protocol/reset")
async def api_reset_protocol():
    state_machine.reset()
    return {"status": "IDLE"}

@router.get("/protocol/status")
async def api_get_protocol_status():
    return state_machine.get_status()

@router.post("/simulation/start")
async def api_start_simulation(payload: SimulatorStartRequest):
    simulator.start(profile=payload.profile)
    return {"status": "SIMULATING", "profile": payload.profile}

@router.post("/simulation/stop")
async def api_stop_simulation():
    simulator.stop()
    return {"status": "STOPPED"}

@router.get("/simulation/status")
async def api_get_simulation_status():
    return {"is_simulating": simulator.is_running, "profile": simulator.profile}

# --- Clinical Report Exporter with Safe HTML Escaping ---
@router.get("/reports/{session_id}/html", response_class=HTMLResponse)
async def generate_clinical_html_report(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Session)
        .options(selectinload(Session.segments), selectinload(Session.clinical_summary), selectinload(Session.patient))
        .where(Session.session_id == session_id)
    )
    sess = result.scalar_one_or_none()
    if not sess or not sess.clinical_summary:
        raise HTTPException(status_code=404, detail="Session or clinical summary not found")
        
    c = sess.clinical_summary
    p = sess.patient
    
    # Safe text escaping for all untrusted string fields
    safe_session_id = html.escape(str(sess.session_id))
    safe_patient_name = html.escape(str(p.name if p else 'Anonymous'))
    safe_patient_id = html.escape(str(sess.patient_id))
    safe_age = html.escape(str(p.age if p and p.age is not None else 'N/A'))
    safe_diag = html.escape(str(p.diagnosis_year if p and p.diagnosis_year is not None else 'N/A'))
    safe_provenance = html.escape(str(getattr(sess, 'provenance', 'REAL_DEVICE')))
    safe_severity = html.escape(str(c.overall_tremor_severity or 'NONE'))

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>MotorTrack Research Report - {safe_session_id}</title>
    <style>
        body {{ font-family: 'Segoe UI', -apple-system, sans-serif; margin: 40px; color: #1e293b; line-height: 1.6; }}
        .header {{ border-bottom: 2px solid #0d9488; padding-bottom: 12px; margin-bottom: 24px; }}
        .title {{ font-size: 24px; font-weight: bold; color: #0f766e; }}
        .badge {{ display: inline-block; padding: 4px 10px; border-radius: 6px; font-weight: bold; font-size: 13px; }}
        .badge-provenance {{ background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }}
        .badge-severe {{ background: #ffe4e6; color: #e11d48; }}
        .badge-moderate {{ background: #fef3c7; color: #d97706; }}
        .badge-mild {{ background: #e0f2fe; color: #0284c7; }}
        .badge-normal {{ background: #dcfce7; color: #16a34a; }}
        .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-top: 16px; }}
        .card {{ border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; background: #f8fafc; }}
        .metric {{ font-size: 26px; font-weight: bold; color: #0f172a; }}
        .unit {{ font-size: 13px; color: #64748b; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
        th, td {{ border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left; }}
        th {{ background: #f1f5f9; }}
    </style>
</head>
<body>
    <div class="header">
        <div class="title">MotorTrack Movement-Signal Telemetry Report</div>
        <div><strong>Session ID:</strong> {safe_session_id} | <strong>Provenance:</strong> <span class="badge badge-provenance">{safe_provenance}</span></div>
        <div><strong>Participant:</strong> {safe_patient_name} (ID: {safe_patient_id}) | <strong>Age:</strong> {safe_age} | <strong>Diagnosis Year:</strong> {safe_diag}</div>
    </div>

    <h2>Monitored Tremor-Band Summary</h2>
    <div>
        Research Amplitude Tier: <span class="badge badge-{safe_severity.lower()}">{safe_severity}</span>
    </div>

    <div class="grid">
        <div class="card">
            <h3>1. Resting Tremor Band</h3>
            <div class="metric">{round(c.resting_dominant_freq or 0.0, 2)} <span class="unit">Hz</span></div>
            <div>Resultant RMS: <strong>{round(c.resting_rms_amp or 0.0, 3)} m/s²</strong></div>
            <div>Band Ratio: <strong>{round((c.resting_tremor_ratio or 0.0) * 100, 1)}%</strong></div>
            <div>Activity Detected: <strong>{'YES' if c.resting_tremor_detected else 'NO'}</strong></div>
        </div>

        <div class="card">
            <h3>2. Postural Tremor Band</h3>
            <div class="metric">{round(c.postural_dominant_freq or 0.0, 2)} <span class="unit">Hz</span></div>
            <div>Resultant RMS: <strong>{round(c.postural_rms_amp or 0.0, 3)} m/s²</strong></div>
            <div>Band Ratio: <strong>{round((c.postural_tremor_ratio or 0.0) * 100, 1)}%</strong></div>
            <div>Activity Detected: <strong>{'YES' if c.postural_tremor_detected else 'NO'}</strong></div>
        </div>

        <div class="card">
            <h3>3. Action Tremor Band</h3>
            <div class="metric">{round(c.action_dominant_freq or 0.0, 2)} <span class="unit">Hz</span></div>
            <div>Resultant RMS: <strong>{round(c.action_rms_amp or 0.0, 3)} m/s²</strong></div>
            <div>Band Ratio: <strong>{round((c.action_tremor_ratio or 0.0) * 100, 1)}%</strong></div>
            <div>Activity Detected: <strong>{'YES' if c.action_tremor_detected else 'NO'}</strong></div>
        </div>
    </div>

    <div style="margin-top: 40px; font-size: 12px; color: #64748b; border-top: 1px solid #e2e8f0; padding-top: 8px;">
        MotorTrack Research Prototype • Investigational use only. Not for medical diagnosis or therapeutic modification.
    </div>
</body>
</html>"""
    return HTMLResponse(content=html_content)

# --- Medication Timeline Endpoints (No Synthetic Prior Dose on Creation) ---
@router.get("/patients/{patient_id}/medications")
async def get_patient_medications(patient_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Medication).where(Medication.patient_id == patient_id))
    meds = res.scalars().all()
    
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    output = []
    for m in meds:
        time_since_dose_str = "No recorded dose"
        if m.last_taken_at:
            delta = (now - m.last_taken_at).total_seconds() / 60.0
            hours = int(delta // 60)
            mins = int(delta % 60)
            time_since_dose_str = f"{hours}h {mins}m since recorded dose"
                
        output.append({
            "medication_id": m.medication_id,
            "patient_id": m.patient_id,
            "name": m.name,
            "dosage": m.dosage,
            "scheduled_time": m.scheduled_time,
            "last_taken_at": m.last_taken_at.isoformat() if m.last_taken_at else None,
            "time_since_dose": time_since_dose_str,
            "notes": m.notes
        })
    return output

@router.post("/patients/{patient_id}/medications")
async def add_patient_medication(patient_id: str, payload: MedicationCreate, db: AsyncSession = Depends(get_db)):
    # Does NOT create a synthetic past dose; last_taken_at is None until recorded
    med = Medication(
        patient_id=patient_id,
        name=payload.name,
        dosage=payload.dosage,
        scheduled_time=payload.scheduled_time,
        last_taken_at=None,
        notes=payload.notes
    )
    db.add(med)
    await db.commit()
    await db.refresh(med)
    return {"status": "success", "medication_id": med.medication_id}

@router.post("/medications/{med_id}/take")
async def log_medication_taken(med_id: int, db: AsyncSession = Depends(get_db)):
    med = await db.get(Medication, med_id)
    if not med:
        raise HTTPException(status_code=404, detail="Medication not found")
        
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    med.last_taken_at = now
    
    # Also create authoritative MedicationEvent for timeline
    ev = MedicationEvent(
        patient_id=med.patient_id,
        medication_name=med.name,
        dose=med.dosage,
        timestamp=now,
        source="USER_LOGGED",
        notes=f"Logged via dose-taken button for medication #{med_id}"
    )
    db.add(ev)
    await db.commit()
    return {"status": "success", "taken_at": now.isoformat()}

@router.delete("/medications/{med_id}")
async def delete_patient_medication(med_id: int, db: AsyncSession = Depends(get_db)):
    med = await db.get(Medication, med_id)
    if not med:
        raise HTTPException(status_code=404, detail="Medication not found")
    await db.delete(med)
    await db.commit()
    return {"status": "success", "deleted_id": med_id}

@router.get("/patients/{patient_id}/events/medications")
async def get_patient_medication_events(patient_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(MedicationEvent)
        .where(MedicationEvent.patient_id == patient_id)
        .order_by(MedicationEvent.timestamp.asc())
    )
    events = res.scalars().all()
    return [
        {
            "event_id": e.event_id,
            "patient_id": e.patient_id,
            "medication_name": e.medication_name,
            "dose": e.dose,
            "timestamp": e.timestamp.isoformat(),
            "time_str": e.timestamp.strftime("%b %d, %H:%M"),
            "source": e.source,
            "notes": e.notes
        }
        for e in events
    ]

@router.post("/patients/{patient_id}/events/medications")
async def add_patient_medication_event(patient_id: str, payload: MedicationEventCreate, db: AsyncSession = Depends(get_db)):
    ts = datetime.datetime.fromisoformat(payload.timestamp).replace(tzinfo=None) if payload.timestamp else datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    event = MedicationEvent(
        patient_id=patient_id,
        medication_name=payload.medication_name,
        dose=payload.dose,
        timestamp=ts,
        source=payload.source,
        notes=payload.notes
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return {"status": "success", "event_id": event.event_id, "timestamp": event.timestamp.isoformat()}

@router.delete("/events/medications/{event_id}")
async def delete_medication_event(event_id: int, db: AsyncSession = Depends(get_db)):
    ev = await db.get(MedicationEvent, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Medication event not found")
    await db.delete(ev)
    await db.commit()
    return {"status": "success", "deleted_id": event_id}

@router.get("/patients/{patient_id}/events/symptoms")
async def get_patient_symptom_events(patient_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(SymptomEvent)
        .where(SymptomEvent.patient_id == patient_id)
        .order_by(SymptomEvent.timestamp.asc())
    )
    events = res.scalars().all()
    return [
        {
            "event_id": e.event_id,
            "patient_id": e.patient_id,
            "symptom": e.symptom,
            "severity": e.severity,
            "timestamp": e.timestamp.isoformat(),
            "time_str": e.timestamp.strftime("%b %d, %H:%M"),
            "source": e.source,
            "notes": e.notes
        }
        for e in events
    ]

@router.post("/patients/{patient_id}/events/symptoms")
async def add_patient_symptom_event(patient_id: str, payload: SymptomEventCreate, db: AsyncSession = Depends(get_db)):
    ts = datetime.datetime.fromisoformat(payload.timestamp).replace(tzinfo=None) if payload.timestamp else datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    event = SymptomEvent(
        patient_id=patient_id,
        symptom=payload.symptom,
        severity=payload.severity,
        timestamp=ts,
        source=payload.source,
        notes=payload.notes
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return {"status": "success", "event_id": event.event_id, "timestamp": event.timestamp.isoformat()}

@router.delete("/events/symptoms/{event_id}")
async def delete_symptom_event(event_id: int, db: AsyncSession = Depends(get_db)):
    ev = await db.get(SymptomEvent, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Symptom event not found")
    await db.delete(ev)
    await db.commit()
    return {"status": "success", "deleted_id": event_id}

# --- Session Replay Endpoint (Timestamp-Aware & Segment Synchronized) ---
@router.get("/sessions/{session_id}/replay")
async def get_session_replay_data(session_id: str, db: AsyncSession = Depends(get_db)):
    res_sess = await db.execute(
        select(Session)
        .options(selectinload(Session.segments).selectinload(TestSegment.readings), selectinload(Session.clinical_summary))
        .where(Session.session_id == session_id)
    )
    sess = res_sess.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
        
    segments_data = []
    all_readings = []
    
    for seg in sess.segments:
        seg_readings = [
            {
                "t": r.timestamp,
                "seq": r.seq_num,
                "segment_type": seg.test_type,
                "ax": r.ax,
                "ay": r.ay,
                "az": r.az,
                "gx": r.gx,
                "gy": r.gy,
                "gz": r.gz,
                "filtered_mag": r.filtered_mag if r.filtered_mag is not None else float(r.az - 9.81)
            }
            for r in sorted(seg.readings, key=lambda x: x.timestamp)
        ]
        
        summary_parsed = json.loads(seg.summary_json) if seg.summary_json else {}
        segments_data.append({
            "segment_id": seg.segment_id,
            "test_type": seg.test_type,
            "start_t": seg.start_t,
            "end_t": seg.end_t,
            "summary": summary_parsed,
            "reading_count": len(seg_readings)
        })
        all_readings.extend(seg_readings)
        
    all_readings.sort(key=lambda x: x["t"])
    
    return {
        "session_id": sess.session_id,
        "patient_id": sess.patient_id,
        "provenance": getattr(sess, "provenance", "REAL_DEVICE"),
        "is_simulated": sess.is_simulated,
        "started_at": sess.started_at.isoformat() if sess.started_at else None,
        "session_type": sess.session_type,
        "clinical_summary": {
            "resting_dominant_freq": sess.clinical_summary.resting_dominant_freq if sess.clinical_summary else None,
            "resting_rms_amp": sess.clinical_summary.resting_rms_amp if sess.clinical_summary else None,
            "overall_severity": sess.clinical_summary.overall_tremor_severity if sess.clinical_summary else "NONE"
        } if sess.clinical_summary else None,
        "segments": segments_data,
        "readings": all_readings
    }

@router.get("/recordings/export/{session_id}")
async def export_hardware_recording(session_id: str, db: AsyncSession = Depends(get_db)):
    res_sess = await db.execute(
        select(Session)
        .options(selectinload(Session.segments).selectinload(TestSegment.readings), selectinload(Session.clinical_summary))
        .where(Session.session_id == session_id)
    )
    sess = res_sess.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
        
    all_readings = []
    for seg in sess.segments:
        for r in sorted(seg.readings, key=lambda x: x.timestamp):
            all_readings.append({
                "t": r.timestamp,
                "t_mono": getattr(r, "t_mono", None),
                "seq": r.seq_num,
                "ax": r.ax, "ay": r.ay, "az": r.az,
                "gx": r.gx, "gy": r.gy, "gz": r.gz
            })
    all_readings.sort(key=lambda x: x["t"])
    
    device_info = {
        "device_model": getattr(sess, "device_model", "Unknown"),
        "os": getattr(sess, "client_os", "Unknown"),
        "browser": getattr(sess, "client_browser", "Unknown"),
        "hardware_hz": getattr(sess, "nominal_hz", None)
    }
    
    quality_summary = {
        "status": getattr(sess, "quality_status", "UNKNOWN"),
        "packet_loss_pct": getattr(sess, "packet_loss_pct", None),
        "jitter_ms": getattr(sess, "jitter_ms", None)
    }
    
    derived_summary = {
        "overall_severity": sess.clinical_summary.overall_tremor_severity if sess.clinical_summary else "NONE",
        "resting_dominant_freq": sess.clinical_summary.resting_dominant_freq if sess.clinical_summary else None,
        "resting_rms_amp": sess.clinical_summary.resting_rms_amp if sess.clinical_summary else None
    } if sess.clinical_summary else {}
    
    bundle = build_hardware_recording_bundle(
        session_id=sess.session_id,
        patient_id=sess.patient_id,
        protocol_type=sess.session_type,
        provenance=getattr(sess, "provenance", "REAL_DEVICE"),
        device_info=device_info,
        placement="HANDHELD",
        started_at=sess.started_at,
        completed_at=sess.completed_at,
        raw_readings=all_readings,
        quality_summary=quality_summary,
        derived_summary=derived_summary
    )
    return bundle

