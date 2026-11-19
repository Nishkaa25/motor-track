import unittest
import json
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.database import init_db

class TestRestAPIEndpoints(unittest.TestCase):
    """
    Validates REST API endpoints for:
      1. Patient management
      2. Medication events (POST, GET, DELETE)
      3. Symptom events (POST, GET, DELETE)
      4. Longitudinal trends with temporal medication association
      5. Session replay endpoint
    """
    @classmethod
    def setUpClass(cls):
        import asyncio
        asyncio.run(init_db())
        cls.client = TestClient(app)

    def test_patients_endpoint(self):
        """Verify GET /api/patients returns list with Demo Participant."""
        response = self.client.get("/api/patients")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_medication_and_symptom_event_lifecycle(self):
        """Verify full lifecycle of medication and symptom event logging and deletion."""
        patient_id = "P-101"
        
        # 1. Add medication event
        med_payload = {
            "medication_name": "Levodopa / Carbidopa Test",
            "dose": "100/25 mg",
            "source": "USER_LOGGED",
            "notes": "Test dose log"
        }
        res_post_med = self.client.post(f"/api/patients/{patient_id}/events/medications", json=med_payload)
        self.assertEqual(res_post_med.status_code, 200)
        med_event_id = res_post_med.json()["event_id"]

        # 2. Add symptom event
        symp_payload = {
            "symptom": "Resting Tremor Increase",
            "severity": "MODERATE",
            "source": "USER_LOGGED",
            "notes": "Test observation"
        }
        res_post_symp = self.client.post(f"/api/patients/{patient_id}/events/symptoms", json=symp_payload)
        self.assertEqual(res_post_symp.status_code, 200)
        symp_event_id = res_post_symp.json()["event_id"]

        # 3. Retrieve events
        res_get_meds = self.client.get(f"/api/patients/{patient_id}/events/medications")
        self.assertEqual(res_get_meds.status_code, 200)
        med_ids = [m["event_id"] for m in res_get_meds.json()]
        self.assertIn(med_event_id, med_ids)

        # 4. Check longitudinal analytics contains events
        res_long = self.client.get(f"/api/analytics/longitudinal/{patient_id}")
        self.assertEqual(res_long.status_code, 200)
        long_data = res_long.json()
        self.assertIn("medication_events", long_data)
        self.assertIn("symptom_events", long_data)
        self.assertIn("timeline", long_data)

        # 5. Clean up by deleting events
        res_del_med = self.client.delete(f"/api/events/medications/{med_event_id}")
        self.assertEqual(res_del_med.status_code, 200)

        res_del_symp = self.client.delete(f"/api/events/symptoms/{symp_event_id}")
        self.assertEqual(res_del_symp.status_code, 200)

    def test_session_replay_endpoint(self):
        """Verify GET /api/sessions/{session_id}/replay returns readings and segments."""
        # Find any available session
        res_long = self.client.get("/api/analytics/longitudinal/P-101")
        sessions = res_long.json().get("sessions", [])
        if not sessions:
            self.skipTest("No sessions available for replay test")
            
        test_session_id = sessions[0]["session_id"]
        res_replay = self.client.get(f"/api/sessions/{test_session_id}/replay")
        self.assertEqual(res_replay.status_code, 200)
        replay_data = res_replay.json()
        
        self.assertEqual(replay_data["session_id"], test_session_id)
        self.assertIn("readings", replay_data)
        self.assertIn("segments", replay_data)
        self.assertIn("clinical_summary", replay_data)
        
        # Verify chronological ordering of readings
        readings = replay_data["readings"]
        if len(readings) > 1:
            timestamps = [r["t"] for r in readings]
            self.assertEqual(timestamps, sorted(timestamps), "Replay readings must preserve strictly chronological order")

if __name__ == "__main__":
    unittest.main()
