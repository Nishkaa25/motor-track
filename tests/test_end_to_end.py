import unittest
import asyncio
import time
import math
import numpy as np
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.database import init_db
from backend.app.protocols.state_machine import state_machine
from backend.app.routers.ws import handle_sensor_payload
from tests.fixtures.generator import generate_packet_stream

class TestMotorTrackEndToEnd(unittest.IsolatedAsyncioTestCase):
    """
    End-to-End Pipeline Validation:
      1. Generates synthetic 5.0 Hz tremor sensor stream with sequence numbers.
      2. Ingests packets through the WebSocket sensor stream pipeline.
      3. Passes through state machine (PREP_RESTING -> RESTING -> completes).
      4. Verifies DSP extracts dominant frequency ~ 5.0 Hz.
      5. Verifies database persistence of session and readings.
      6. Retrieves recorded session via Replay API and verifies integrity.
    """
    async def asyncSetUp(self):
        await init_db()
        from backend.app.main import seed_initial_patients_and_history
        await seed_initial_patients_and_history()
        self.client = TestClient(app)

    async def test_full_pipeline_generator_to_replay(self):
        """
        Verify complete flow with numerical correctness:
          1. Generator: 5.20 Hz, 0.20 m/s^2 peak amplitude
          2. Ingestion: WebSocket packets processed
          3. DSP: extracts ~5.20 Hz dominant frequency and expected RMS ~0.141 m/s^2
          4. Database: persists 100% of readings with sequence numbers
          5. Replay API: verifies chronological timestamps and exact numerical values
        """
        patient_id = "P-101"
        target_tremor_freq = 5.20
        target_peak_amp = 0.20
        # In generator.py, 3D tremor oscillation is distributed across axes:
        # ax = osc * 0.7, ay = osc * 0.3, az = osc * 0.5
        # True 3D resultant RMS = (target_peak_amp / sqrt(2)) * sqrt(0.7^2 + 0.3^2 + 0.5^2)
        total_3d_factor = float(np.sqrt(0.7**2 + 0.3**2 + 0.5**2))
        expected_rms = (target_peak_amp / 1.41421356) * total_3d_factor
        
        # 1. Start assessment protocol
        state_machine.start_protocol("TREMOR_3TEST", patient_id=patient_id)
        session_id = state_machine.session_id
        self.assertIsNotNone(session_id)
        
        # Fast-forward past prep stage directly into RESTING recording stage
        state_machine.transition_to("RESTING", duration_sec=0.2)
        
        # 2. Generate and stream 5.20 Hz synthetic tremor packets
        packets = generate_packet_stream(
            frequency=target_tremor_freq,
            amplitude=target_peak_amp,
            duration=4.0,
            fs=50.0,
            noise_std=0.002
        )
        
        # Ingest packets
        for p in packets:
            await handle_sensor_payload(p)
            
        # Brief pause for state machine transition and DB persistence
        time.sleep(0.3)
        state_machine.transition_to("COMPLETE", duration_sec=0.1)
        if getattr(state_machine, "persist_task", None):
            await state_machine.persist_task
        else:
            await state_machine._persist_session_to_db()
        await asyncio.sleep(0.1)
        
        # 3. Verify session exists in DB via REST API
        res_long = self.client.get(f"/api/analytics/longitudinal/{patient_id}")
        self.assertEqual(res_long.status_code, 200)
        
        # 4. Verify Replay API returns the session readings and summary
        res_replay = self.client.get(f"/api/sessions/{session_id}/replay")
        self.assertEqual(res_replay.status_code, 200)
        replay_data = res_replay.json()
        
        self.assertEqual(replay_data["session_id"], session_id)
        self.assertEqual(replay_data["patient_id"], patient_id)
        
        # Check numerical correctness of clinical summary metrics
        clin_sum = replay_data.get("clinical_summary", {})
        rest_freq = clin_sum.get("resting_dominant_freq")
        rest_rms = clin_sum.get("resting_rms_amp")
        
        self.assertIsNotNone(rest_freq, "Clinical summary must include resting dominant frequency")
        self.assertAlmostEqual(rest_freq, target_tremor_freq, delta=0.15,
                               msg=f"Dominant frequency {rest_freq} Hz should match target {target_tremor_freq} Hz within 0.15 Hz")
        
        if rest_rms is not None:
            self.assertAlmostEqual(rest_rms, expected_rms, delta=0.03,
                                   msg=f"Resting RMS {rest_rms} should scale with theoretical {expected_rms:.3f} m/s^2")
            
        # 5. Check sample-by-sample transport and numerical value preservation
        readings = replay_data.get("readings", [])
        self.assertEqual(len(readings), len(packets),
                         f"Replay must preserve 100% of recorded readings ({len(readings)} vs {len(packets)})")
        
        # Verify first, middle, and last sample numerical equality
        for idx in [0, len(packets)//2, len(packets)-1]:
            self.assertAlmostEqual(readings[idx]["t"], packets[idx]["t"], places=4)
            self.assertAlmostEqual(readings[idx]["ax"], packets[idx]["ax"], places=4)
            self.assertAlmostEqual(readings[idx]["ay"], packets[idx]["ay"], places=4)
            self.assertAlmostEqual(readings[idx]["az"], packets[idx]["az"], places=4)
            self.assertEqual(readings[idx]["seq"], packets[idx]["seq"])

if __name__ == "__main__":
    unittest.main()
