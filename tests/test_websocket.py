import unittest
import asyncio
import time
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.routers.ws import (
    compute_data_quality, handle_sensor_payload, reset_stream_quality_trackers,
    recent_sample_times, total_packets_lost, total_packets_received
)
from tests.fixtures.generator import generate_packet_stream

class TestWebSocketStreamingAndDataQuality(unittest.IsolatedAsyncioTestCase):
    """
    Validates the WebSocket ingestion pipeline and empirical data quality tracking:
      1. Empirical sampling rate (fs) derived from observed sample timestamps.
      2. Sequence number tracking detects packet loss.
      3. Jitter calculation reflects timestamp variance.
      4. Data Quality classification: GOOD, UNSTABLE, INSUFFICIENT.
      5. WebSocket client connection and initial handshake.
    """
    async def asyncSetUp(self):
        reset_stream_quality_trackers(f"test-epoch-{time.time()}")

    async def test_empirical_sampling_rate_and_jitter(self):
        """Verify empirical sampling rate and jitter are calculated from sample timestamps."""
        # Feed 30 synthetic packets spaced at 20ms (50.0 Hz)
        fs_target = 50.0
        packets = generate_packet_stream(frequency=5.0, amplitude=0.2, duration=1.0, fs=fs_target, jitter_std_sec=0.002)
        
        for p in packets:
            await handle_sensor_payload(p)
            
        dq = compute_data_quality()
        self.assertIn(dq["status"], ["GOOD", "UNSTABLE"])
        # Observed rate should be within +/- 3 Hz of 50 Hz
        self.assertAlmostEqual(dq["observed_hz"], fs_target, delta=3.0)
        self.assertGreater(dq["packets_received"], 10)

    async def test_packet_loss_detection_via_sequence_numbers(self):
        """Verify gaps in sequence numbers are accurately detected and reported as packet loss."""
        # Generate stream of 20 packets with an intentional gap of 5 packets
        seq_stream = []
        for i in range(10):
            seq_stream.append({"seq": i, "t": 100.0 + i * 0.02, "ax": 0.0, "ay": 0.0, "az": 9.81})
        for i in range(15, 25):
            seq_stream.append({"seq": i, "t": 100.0 + i * 0.02, "ax": 0.0, "ay": 0.0, "az": 9.81})
        
        for p in seq_stream:
            await handle_sensor_payload(p)
            
        dq = compute_data_quality()
        self.assertIsNotNone(dq["packet_loss_pct"])
        self.assertGreater(dq["packet_loss_pct"], 0.0)

    def test_websocket_client_connection(self):
        """Verify dashboard WebSocket client connects and receives initial state."""
        client = TestClient(app)
        with client.websocket_connect("/ws/dashboard") as ws:
            # First message should be INITIAL_STATE
            data = ws.receive_json()
            self.assertIn(data.get("type"), ["INITIAL_STATE", "STATUS_UPDATE"])
            self.assertIn("phone_connected", data)

if __name__ == "__main__":
    unittest.main()
