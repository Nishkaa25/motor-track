import os
import datetime
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select

from backend.app.config import config
from backend.app.database import init_db, AsyncSessionLocal
from backend.app.models.db_models import Patient, Session, TestSegment, ClinicalSummary, Medication, MedicationEvent, SymptomEvent, Reading
from backend.app.routers.ws import router as ws_router
from backend.app.routers.api import router as api_router

BASE_DIR = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

async def seed_initial_patients_and_history():
    """Seeds baseline demo participant and sample synthetic records for demonstration."""
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Patient))
        if res.scalars().first() is None:
            # Seed Demo Participant
            p1 = Patient(
                patient_id="P-101",
                name="Demo Participant · Synthetic Data",
                age=68,
                gender="Non-specified",
                diagnosis_year=2021,
                notes="Synthetic demonstration record for motor signal telemetry evaluation."
            )
            db.add(p1)
            await db.commit()

        # Check if medication events exist
        res_events = await db.execute(select(MedicationEvent))
        if res_events.scalars().first() is None:
            now = datetime.datetime.utcnow()
            m_ev1 = MedicationEvent(
                patient_id="P-101",
                medication_name="Levodopa / Carbidopa",
                dose="100/25 mg",
                timestamp=now - datetime.timedelta(hours=3, minutes=15),
                source="SIMULATED",
                notes="Morning scheduled dose."
            )
            m_ev2 = MedicationEvent(
                patient_id="P-101",
                medication_name="Levodopa / Carbidopa",
                dose="100/25 mg",
                timestamp=now - datetime.timedelta(days=1, hours=4),
                source="SIMULATED",
                notes="Midday dose."
            )
            s_ev1 = SymptomEvent(
                patient_id="P-101",
                symptom="Resting Tremor Increase",
                severity="MODERATE",
                timestamp=now - datetime.timedelta(hours=1, minutes=10),
                source="SIMULATED",
                notes="Wearing-off tremor noticed before scheduled afternoon dose."
            )
            db.add_all([m_ev1, m_ev2, s_ev1])
            await db.commit()

        # Check if sessions exist
        res_sess = await db.execute(select(Session))
        if res_sess.scalars().first() is None:
            now = datetime.datetime.utcnow()
            history_data = [
                {"days_ago": 5, "rest_f": 5.4, "rest_rms": 0.42, "sev": "MODERATE"},
                {"days_ago": 4, "rest_f": 5.2, "rest_rms": 0.38, "sev": "MODERATE"},
                {"days_ago": 3, "rest_f": 5.1, "rest_rms": 0.28, "sev": "MILD"},
                {"days_ago": 2, "rest_f": 5.3, "rest_rms": 0.22, "sev": "MILD"},
                {"days_ago": 1, "rest_f": 5.0, "rest_rms": 0.16, "sev": "MILD"}
            ]

            import math
            for item in history_data:
                s_date = now - datetime.timedelta(days=item["days_ago"], hours=2)
                s_id = f"SESS-HIST-{item['days_ago']}"
                hist_sess = Session(
                    session_id=s_id,
                    patient_id="P-101",
                    session_type="TREMOR_3TEST",
                    started_at=s_date,
                    completed_at=s_date + datetime.timedelta(minutes=1),
                    notes="Synthetic demonstration session"
                )
                db.add(hist_sess)
                
                # Add RESTING segment
                seg_id = f"{s_id}-RESTING"
                seg = TestSegment(
                    segment_id=seg_id,
                    session_id=s_id,
                    test_type="RESTING",
                    start_t=0.0,
                    end_t=10.0,
                    summary_json=f'{{"dominant_freq": {item["rest_f"]}, "rms_amp_tremor": {item["rest_rms"]}}}'
                )
                db.add(seg)
                
                # Add sample readings for replay
                for i in range(100):
                    t_sec = i * 0.1
                    osc = math.sin(2 * math.pi * item["rest_f"] * t_sec) * item["rest_rms"]
                    db.add(Reading(
                        segment_id=seg_id,
                        seq_num=i,
                        timestamp=t_sec,
                        ax=round(osc * 0.7, 3),
                        ay=round(osc * 0.4, 3),
                        az=round(9.81 + osc * 0.5, 3),
                        gx=0.01, gy=-0.01, gz=0.02,
                        filtered_mag=round(abs(osc), 3)
                    ))
                
                clin = ClinicalSummary(
                    session_id=s_id,
                    resting_dominant_freq=item["rest_f"],
                    resting_rms_amp=item["rest_rms"],
                    resting_tremor_ratio=0.55,
                    resting_tremor_detected=True,
                    postural_dominant_freq=item["rest_f"] + 0.1,
                    postural_rms_amp=round(item["rest_rms"] * 0.6, 4),
                    postural_tremor_detected=True,
                    action_dominant_freq=item["rest_f"] - 0.2,
                    action_rms_amp=round(item["rest_rms"] * 0.4, 4),
                    action_tremor_detected=False,
                    overall_tremor_severity=item["sev"],
                    fog_episodes_count=0,
                    total_freeze_duration_sec=0.0,
                    max_fog_index=1.2,
                    gait_cadence_spm=104.0,
                    gait_shuffle_score=4.0,
                    tap_rate_per_sec=4.8,
                    tap_irregularity_cv=18.5,
                    tap_fatigue_slope=1.2,
                    ml_tremor_confidence=0.75,
                    overall_status=f"TREMOR_{item['sev']}"
                )
                db.add(clin)

            await db.commit()
            print("[DB] Initialized database and seeded baseline clinical history.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: init database and seed
    await init_db()
    await seed_initial_patients_and_history()
    print(f"===============================================================")
    print(f" Parkinson's Motor Symptom Monitoring Engine is ONLINE! ")
    print(f" Dashboard:  http://localhost:{config.port}")
    print(f" Phone App:  http://{config.lan_ip}:{config.port}/phone")
    print(f"===============================================================")
    yield
    # Shutdown

app = FastAPI(
    title=config.app_name,
    version=config.version,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(ws_router)
app.include_router(api_router)

# Mount Frontend static files
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

@app.get("/")
async def serve_dashboard():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse({"status": "Dashboard frontend index not found"})

@app.get("/phone")
async def serve_phone_client():
    phone_path = FRONTEND_DIR / "phone.html"
    if phone_path.exists():
        return FileResponse(str(phone_path))
    return JSONResponse({"status": "Phone client HTML not found"})

