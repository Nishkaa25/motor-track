import unittest
import asyncio
import datetime
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.database import AsyncSessionLocal, init_db
from backend.app.models.db_models import (
    Patient, Session as DBSession, TestSegment, Reading, ClinicalSummary,
    MedicationEvent, SymptomEvent
)

class TestDatabasePersistence(unittest.IsolatedAsyncioTestCase):
    """
    Validates SQLite durability and persistence against acceptance criteria:
      1. Patient records persist and are retrievable.
      2. MedicationEvent and SymptomEvent persist with source tags (USER_LOGGED, SIMULATED).
      3. Sessions, TestSegments, and Readings (with seq_num) persist completely.
      4. 100% of committed records are retrievable after transaction commit.
    """
    async def asyncSetUp(self):
        await init_db()

    async def test_patient_and_event_persistence(self):
        """Verify Patient, MedicationEvent, and SymptomEvent persistence and retrieval."""
        async with AsyncSessionLocal() as db:
            p_id = f"TEST-P-{datetime.datetime.now().strftime('%f')}"
            patient = Patient(
                patient_id=p_id,
                name="Demo Participant · Synthetic Data",
                age=65,
                gender="Non-specified"
            )
            db.add(patient)
            
            med_ev = MedicationEvent(
                patient_id=p_id,
                medication_name="Levodopa / Carbidopa",
                dose="100/25 mg",
                timestamp=datetime.datetime.utcnow(),
                source="USER_LOGGED",
                notes="Dose taken with breakfast"
            )
            
            symp_ev = SymptomEvent(
                patient_id=p_id,
                symptom="Resting Tremor Spike",
                severity="MODERATE",
                timestamp=datetime.datetime.utcnow(),
                source="SIMULATED",
                notes="Synthetic observation event"
            )
            db.add_all([med_ev, symp_ev])
            await db.commit()

        # Retrieve in new session
        async with AsyncSessionLocal() as db:
            res_p = await db.execute(select(Patient).where(Patient.patient_id == p_id))
            saved_p = res_p.scalar_one_or_none()
            self.assertIsNotNone(saved_p)
            self.assertEqual(saved_p.name, "Demo Participant · Synthetic Data")
            
            res_m = await db.execute(select(MedicationEvent).where(MedicationEvent.patient_id == p_id))
            saved_m = res_m.scalars().all()
            self.assertEqual(len(saved_m), 1)
            self.assertEqual(saved_m[0].source, "USER_LOGGED")
            self.assertEqual(saved_m[0].medication_name, "Levodopa / Carbidopa")

            res_s = await db.execute(select(SymptomEvent).where(SymptomEvent.patient_id == p_id))
            saved_s = res_s.scalars().all()
            self.assertEqual(len(saved_s), 1)
            self.assertEqual(saved_s[0].source, "SIMULATED")
            self.assertEqual(saved_s[0].severity, "MODERATE")

    async def test_session_and_readings_persistence(self):
        """Verify 100% of recorded readings and session summary survive commit."""
        p_id = f"TEST-P-{datetime.datetime.now().strftime('%f')}"
        sess_id = f"TEST-SESS-{datetime.datetime.now().strftime('%f')}"
        seg_id = f"{sess_id}-RESTING"
        
        async with AsyncSessionLocal() as db:
            db.add(Patient(patient_id=p_id, name="Test Participant"))
            db.add(DBSession(session_id=sess_id, patient_id=p_id, session_type="TREMOR_3TEST"))
            db.add(TestSegment(segment_id=seg_id, session_id=sess_id, test_type="RESTING", start_t=0.0, end_t=10.0))
            
            # Insert 50 sequential readings
            for i in range(50):
                db.add(Reading(
                    segment_id=seg_id,
                    seq_num=i,
                    timestamp=1000.0 + i * 0.02,
                    ax=0.1, ay=-0.1, az=9.81,
                    gx=0.0, gy=0.0, gz=0.0,
                    filtered_mag=0.15
                ))
                
            db.add(ClinicalSummary(
                session_id=sess_id,
                resting_dominant_freq=5.1,
                resting_rms_amp=0.18,
                overall_tremor_severity="MILD"
            ))
            await db.commit()

        # Verify in fresh session
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(DBSession)
                .options(selectinload(DBSession.segments).selectinload(TestSegment.readings), selectinload(DBSession.clinical_summary))
                .where(DBSession.session_id == sess_id)
            )
            saved_sess = res.scalar_one_or_none()
            self.assertIsNotNone(saved_sess)
            self.assertEqual(len(saved_sess.segments), 1)
            readings = saved_sess.segments[0].readings
            self.assertEqual(len(readings), 50, "100% of 50 readings must be persisted")
            self.assertEqual(readings[0].seq_num, 0)
            self.assertEqual(readings[49].seq_num, 49)
            self.assertEqual(saved_sess.clinical_summary.resting_dominant_freq, 5.1)

if __name__ == "__main__":
    unittest.main()
