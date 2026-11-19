import unittest
import time
from backend.app.protocols.state_machine import ProtocolStateMachine
from tests.fixtures.generator import generate_packet_stream

class TestProtocolStateMachine(unittest.TestCase):
    """
    Validates state transitions and segment management in the ProtocolStateMachine:
      1. Initial state is IDLE.
      2. Starting TREMOR_3TEST transitions to PREP_RESTING with target countdown.
      3. Process samples advance countdown and trigger automatic transitions.
      4. Reset cleans all buffers and returns to IDLE.
    """
    def setUp(self):
        self.sm = ProtocolStateMachine()

    def test_initial_state_idle(self):
        """State machine initializes in IDLE state."""
        self.assertEqual(self.sm.state, "IDLE")
        status = self.sm.get_status()
        self.assertEqual(status["state"], "IDLE")

    def test_protocol_start_and_countdown(self):
        """Starting protocol moves to PREP_RESTING with valid countdown."""
        self.sm.start_protocol("TREMOR_3TEST", patient_id="P-TEST")
        self.assertEqual(self.sm.state, "PREP_RESTING")
        self.assertEqual(self.sm.patient_id, "P-TEST")
        self.assertTrue(self.sm.session_id.startswith("SESS-"))
        
        status = self.sm.get_status()
        self.assertGreater(status["remaining_sec"], 0.0)

    def test_reset_protocol(self):
        """Reset clears active session and returns machine to IDLE."""
        self.sm.start_protocol("TREMOR_3TEST", patient_id="P-TEST")
        self.sm.reset()
        self.assertEqual(self.sm.state, "IDLE")
        self.assertIsNone(self.sm.session_id)
        self.assertEqual(len(self.sm.current_segment_readings), 0)
        self.assertEqual(len(self.sm.segment_summaries), 0)

    def test_stage_progression_with_samples(self):
        """Simulate time elapse to verify progression through stages."""
        self.sm.start_protocol("TREMOR_3TEST", patient_id="P-TEST")
        # Shorten stage target and advance monotonic clock
        self.sm.segment_duration_target = 0.05
        self.sm.segment_start_mono -= 0.10
        
        status = self.sm.process_sensor_sample(time.time(), 0.1, -0.1, 9.81, 0.0, 0.0, 0.0)
        
        # PREP_RESTING should have transitioned to RESTING
        self.assertEqual(self.sm.state, "RESTING")
        
        # Next stage transition: RESTING -> PREP_POSTURAL
        self.sm.segment_duration_target = 0.05
        self.sm.segment_start_mono -= 0.10
        self.sm.process_sensor_sample(time.time(), 0.1, -0.1, 9.81, 0.0, 0.0, 0.0)
        self.assertEqual(self.sm.state, "PREP_POSTURAL")

if __name__ == "__main__":
    unittest.main()
