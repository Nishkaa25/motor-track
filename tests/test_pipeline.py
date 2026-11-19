import unittest
import asyncio
from backend.app.config import config
from backend.app.database import init_db, AsyncSessionLocal
from backend.app.models.db_models import Patient, Session, ClinicalSummary
from backend.app.protocols.state_machine import state_machine
from backend.app.protocols.simulator import simulator
from sqlalchemy import select

class TestPipelineAndDatabase(unittest.TestCase):
    def test_database_and_protocol(self):
        async def run_async_tests():
            await init_db()
            
            import uuid
            test_pid = f"P-TEST-{uuid.uuid4().hex[:6]}"
            # Verify patient query
            async with AsyncSessionLocal() as db:
                p = Patient(
                    patient_id=test_pid,
                    name="Test Patient",
                    age=65,
                    gender="Male"
                )
                db.add(p)
                await db.commit()
                
                res = await db.execute(select(Patient).where(Patient.patient_id == test_pid))
                fetched = res.scalar_one_or_none()
                self.assertIsNotNone(fetched)
                self.assertEqual(fetched.name, "Test Patient")

            # Verify Protocol State Machine transitions
            state_machine.start_session("TREMOR_3TEST", patient_id="P-TEST-99")
            self.assertEqual(state_machine.state, "PREP_RESTING")
            
            # Feed some simulated samples
            for i in range(20):
                t = 1000.0 + i * 0.02
                state_machine.process_sensor_sample(t, 0.2, -0.1, 9.8, 0.1, 0.0, 0.0)
                
            status = state_machine.get_status()
            self.assertEqual(status["state"], "PREP_RESTING")
            self.assertEqual(status["patient_id"], "P-TEST-99")
            
            state_machine.reset()
            self.assertEqual(state_machine.state, "IDLE")

        asyncio.run(run_async_tests())

if __name__ == "__main__":
    unittest.main()
