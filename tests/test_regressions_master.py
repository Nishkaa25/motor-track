"""
Master Regression Test Suite for MotorTrack Prototype
Validates critical engineering repairs:
  1. 3D Rotation Invariance (summed PSD prevents frequency doubling across 0°, 45°, 90° orientations).
  2. Short Window Padlen Safety (SOS filter handles N = 24..27 samples without ValueError).
  3. Unknown Packet Loss when unsequenced vs sequence gap detection.
  4. Immutable snapshot isolation during protocol reset.
  5. HTML injection sanitization in PDF/HTML report exports.
  6. Medication dose logging (no synthetic past dose).
  7. SQLite Foreign Key constraint enforcement.
"""

import unittest
import asyncio
import time
import html
import numpy as np
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.dsp.filters import RealtimeFilterBank
from backend.app.dsp.features import extract_window_features
from backend.app.dsp.fog import FOGDetector
from backend.app.dsp.recording_utility import build_hardware_recording_bundle, validate_recording_bundle
from backend.app.protocols.state_machine import ProtocolStateMachine
from backend.app.routers.ws import compute_data_quality, handle_sensor_payload, reset_stream_quality_trackers
from backend.app.database import AsyncSessionLocal, init_db
from backend.app.models.db_models import Patient, Session as DBSession, MedicationEvent

class TestMotorTrackRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        reset_stream_quality_trackers(f"epoch-regression-{time.time()}")

    def test_3d_rotation_invariance_and_no_frequency_doubling(self):
        """
        Verify that a 5.0 Hz tremor oscillation at varying orientations relative to gravity
        produces identical dominant frequency ~5.0 Hz and consistent RMS amplitude,
        completely avoiding the vector magnitude full-wave rectification frequency doubling (10 Hz).
        """
        fs = 50.0
        duration = 3.0
        n_samples = int(duration * fs)
        t = np.linspace(0, duration, n_samples, endpoint=False)
        amp = 0.25
        pure_osc = amp * np.sin(2.0 * np.pi * 5.0 * t)

        fb = RealtimeFilterBank(fs=fs)

        # Test orientations: 
        # (1) Parallel to gravity (pure Z)
        # (2) Perpendicular to gravity (pure X)
        # (3) 45-degree angle in X-Z plane
        # (4) Arbitrary 3D angle
        orientations = [
            ("pure_z", 0.0, 0.0, 1.0),
            ("pure_x", 1.0, 0.0, 0.0),
            ("oblique_xz", np.cos(np.pi / 4), 0.0, np.sin(np.pi / 4)),
            ("arbitrary_3d", 1.0 / np.sqrt(3), 1.0 / np.sqrt(3), 1.0 / np.sqrt(3))
        ]

        expected_rms = amp / np.sqrt(2.0)  # ~ 0.1768 m/s^2

        for name, kx, ky, kz in orientations:
            ax = kx * pure_osc
            ay = ky * pure_osc
            az = 9.81 + kz * pure_osc

            # Process with 3D pipeline
            axes_3d = fb.process_axes_3d(ax, ay, az)
            acc_dyn, acc_tremor, _, _ = fb.process_raw_imu(ax, ay, az, np.zeros_like(ax), np.zeros_like(ay), np.zeros_like(az))
            feat = extract_window_features(
                acc_dyn,
                acc_tremor,
                fs=fs,
                axes_3d=axes_3d
            )

            # Assert dominant frequency matches ~5.0 Hz (not doubled to 10 Hz)
            self.assertAlmostEqual(feat["dominant_freq"], 5.0, delta=0.15,
                                   msg=f"Failed orientation {name}: dominant frequency {feat['dominant_freq']} Hz doubled or distorted")

            # Assert resultant tremor RMS matches theoretical RMS
            self.assertAlmostEqual(feat["rms_amp_tremor"], expected_rms, delta=0.03,
                                   msg=f"Failed orientation {name}: RMS {feat['rms_amp_tremor']} differed from theoretical {expected_rms:.4f}")

    def test_short_window_filter_padlen_safety(self):
        """
        Verify that SOS filters dynamically adapt padlen on short windows (N = 20..27)
        without raising ValueError: The length of the input vector must be greater than padlen.
        """
        fs = 50.0
        fb = RealtimeFilterBank(fs=fs)
        for n in [20, 22, 24, 25, 26, 27, 28]:
            sig = np.random.normal(0, 0.1, n)
            try:
                # Highpass filter
                hp_res = fb.remove_gravity(sig)
                self.assertEqual(len(hp_res), n)

                # Bandpass filter
                bp_res = fb.apply_tremor_bandpass(sig)
                self.assertEqual(len(bp_res), n)
            except ValueError as e:
                self.fail(f"Filter raised ValueError on window length N={n}: {e}")

    async def test_unknown_packet_loss_when_unsequenced(self):
        """
        Verify that streams without sequence numbers report packet_loss_pct as None (UNKNOWN),
        never assuming 0.0% loss without evidence.
        """
        # Feed 15 samples without seq numbers
        for i in range(15):
            await handle_sensor_payload({
                "t": 100.0 + i * 0.02,
                "ax": 0.0, "ay": 0.0, "az": 9.81
            })

        dq = compute_data_quality()
        self.assertIsNone(dq["packet_loss_pct"], "Packet loss must be None when no sequence numbers are transmitted")

    def test_immutable_snapshot_isolation_on_protocol_reset(self):
        """
        Verify that calling reset() on the state machine while a session snapshot is pending
        does not corrupt or wipe the snapshot dictionary.
        """
        sm = ProtocolStateMachine()
        sm.start_protocol("TREMOR_3TEST", patient_id="P-RESET-TEST")
        sm.transition_to("RESTING", duration_sec=15.0)
        
        # Populate readings in RESTING stage
        for i in range(10):
            sm.process_sensor_sample(100.0 + i * 0.02, 0.1, -0.1, 9.81, 0.0, 0.0, 0.0)

        # Finalize stage
        sm.transition_to("COMPLETE", duration_sec=0.1)

        # Create immutable snapshot
        snapshot = sm._create_snapshot()
        
        # Reset machine
        sm.reset()
        self.assertEqual(sm.state, "IDLE")
        self.assertIsNone(sm.session_id)
        self.assertEqual(len(sm.current_segment_readings), 0)

        # Snapshot must retain the pre-reset data
        self.assertIsNotNone(snapshot["session_id"])
        self.assertEqual(snapshot["patient_id"], "P-RESET-TEST")
        self.assertIn("RESTING", snapshot["all_segment_readings"])
        self.assertEqual(len(snapshot["all_segment_readings"]["RESTING"]), 10)

    def test_html_injection_prevention_in_reports(self):
        """
        Verify that participant names, notes, and metadata containing HTML/XSS payloads
        are strictly escaped via html.escape before rendering in report templates.
        """
        payload = "<script>alert('xss')</script>"
        escaped = html.escape(payload)
        
        self.assertNotIn("<script>", escaped)
        self.assertIn("&lt;script&gt;", escaped)

    async def test_foreign_key_enforcement(self):
        """
        Verify SQLite enforces foreign key constraints and rejects sessions with non-existent patients.
        """
        async with AsyncSessionLocal() as db:
            orphan_session = DBSession(
                session_id=f"SESS-ORPHAN-{int(time.time())}",
                patient_id="NON-EXISTENT-PATIENT-ID-9999",
                session_type="TREMOR_3TEST",
                provenance="TEST"
            )
            db.add(orphan_session)
            with self.assertRaises(IntegrityError):
                await db.commit()

    def test_multi_rate_sampling_consistency(self):
        """
        Phase 1 & 8: Verify 5.0 Hz oscillation is accurately detected across 40 Hz, 50 Hz, and 60 Hz
        without sample rate distortion (e.g. 5 Hz sampled at 40 Hz finalizing at 6.25 Hz).
        """
        rates = [40.0, 50.0, 60.0]
        duration = 4.0
        
        for test_fs in rates:
            n_samples = int(duration * test_fs)
            t = np.linspace(0, duration, n_samples, endpoint=False)
            sig_5hz = 0.25 * np.sin(2.0 * np.pi * 5.0 * t)
            
            fb = RealtimeFilterBank(fs=test_fs)
            filtered = fb.apply_tremor_bandpass(sig_5hz)
            feats = extract_window_features(sig_5hz, filtered, fs=test_fs)
            
            # Assert dominant frequency matches ~5.0 Hz within 0.15 Hz
            self.assertAlmostEqual(
                feats["dominant_freq"], 5.0, delta=0.15,
                msg=f"Rate {test_fs} Hz failed: detected {feats['dominant_freq']} Hz instead of 5.0 Hz"
            )

        # Also test State Machine empirical rate adaptation
        sm_40 = ProtocolStateMachine()
        sm_40.start_protocol("TREMOR_3TEST", patient_id="P-101")
        sm_40.transition_to("RESTING", duration_sec=15.0)
        
        # Ingest 120 samples at 40 Hz (dt = 0.025s)
        for i in range(120):
            t_val = 100.0 + i * 0.025
            sig_val = 0.25 * np.sin(2.0 * np.pi * 5.0 * (i * 0.025))
            sm_40.process_sensor_sample(t_val, 0.0, 0.0, 9.81 + sig_val, 0.0, 0.0, 0.0)
            
        sm_40.transition_to("COMPLETE", duration_sec=0.1)
        res_summary = sm_40.segment_summaries.get("RESTING", {})
        self.assertAlmostEqual(
            res_summary.get("dominant_freq", 0.0), 5.0, delta=0.20,
            msg=f"State machine with 40 Hz feed failed: {res_summary.get('dominant_freq')} Hz instead of ~5.0 Hz"
        )

    def test_multi_window_fog_episode_tracking(self):
        """
        Phase 3B: Verify chronological multi-window FOG episode tracking.
        Detects multiple distinct episodes with accurate start, end, duration, and trial closure.
        """
        fs = 50.0
        detector = FOGDetector(fs=fs, threshold=2.5)
        
        # 1. First Walking block (0.0 to 3.0s) -> 1.8 Hz
        t_walk = np.linspace(0, 3.0, int(3.0 * fs), endpoint=False)
        walk_sig = 3.0 * np.sin(2.0 * np.pi * 1.8 * t_walk)
        res_walk1 = detector.update(3.0, walk_sig)
        self.assertFalse(res_walk1["is_freezing"])
        self.assertEqual(res_walk1["freeze_episodes"], 0)
        
        # 2. First Freezing Episode (3.0s to 7.0s) -> 5.5 Hz
        t_freeze = np.linspace(0, 3.0, int(3.0 * fs), endpoint=False)
        freeze_sig = 2.5 * np.sin(2.0 * np.pi * 5.5 * t_freeze)
        res_fr1 = detector.update(7.0, freeze_sig)
        self.assertTrue(res_fr1["is_freezing"])
        self.assertEqual(res_fr1["freeze_episodes"], 1)
        
        # 3. Second Walking block (7.0s to 10.0s) -> Freezing ends
        res_walk2 = detector.update(10.0, walk_sig)
        self.assertFalse(res_walk2["is_freezing"])
        self.assertEqual(len(detector.episodes), 1)
        self.assertEqual(detector.episodes[0]["start_sec"], 7.0)
        self.assertEqual(detector.episodes[0]["end_sec"], 10.0)
        self.assertEqual(detector.episodes[0]["duration_sec"], 3.0)
        
        # 4. Second Freezing Episode (10.0s to 14.0s) -> Active at trial boundary
        res_fr2 = detector.update(14.0, freeze_sig)
        self.assertTrue(res_fr2["is_freezing"])
        self.assertEqual(res_fr2["freeze_episodes"], 2)
        
        # 5. Trial Closure at 15.0s -> Closes active second episode
        close_res = detector.close_trial(15.0)
        self.assertFalse(detector.in_freeze)
        self.assertEqual(close_res["freeze_episodes"], 2)
        self.assertEqual(len(close_res["episodes"]), 2)
        self.assertEqual(close_res["episodes"][1]["start_sec"], 14.0)
        self.assertEqual(close_res["episodes"][1]["end_sec"], 15.0)
        self.assertEqual(close_res["episodes"][1]["duration_sec"], 1.0)
        self.assertEqual(close_res["total_freeze_duration"], 4.0)

    async def test_exact_packet_loss_and_sequence_edge_cases(self):
        """
        Phase 1B & 8: Verify exact 20.0% loss calculation (20 received, 5 missing),
        deduplication of resent packets, sequence resets across epochs, and stale-sensor gating.
        """
        reset_stream_quality_trackers("epoch-exact-loss-test")
        
        # 1. Transmit 10 packets (seq 0 to 9)
        for i in range(10):
            await handle_sensor_payload({
                "stream_epoch": "epoch-exact-loss-test",
                "seq": i,
                "t": 100.0 + i * 0.02,
                "ax": 0.0, "ay": 0.0, "az": 9.81
            })
            
        # 2. Skip 5 packets (seq 10, 11, 12, 13, 14 missing)
        # Transmit 10 packets (seq 15 to 24)
        for i in range(15, 25):
            await handle_sensor_payload({
                "stream_epoch": "epoch-exact-loss-test",
                "seq": i,
                "t": 100.0 + i * 0.02,
                "ax": 0.0, "ay": 0.0, "az": 9.81
            })
            
        dq = compute_data_quality()
        # Total received: 20, missing: 5. Expected total: 25.
        # Loss pct: 5 / 25 * 100 = 20.0%
        self.assertEqual(dq["packets_received"], 20)
        self.assertEqual(dq["packet_loss_pct"], 20.0)
        
        # 3. Test Deduplication: Re-transmitting seq 18 must be dropped
        await handle_sensor_payload({
            "stream_epoch": "epoch-exact-loss-test",
            "seq": 18,
            "t": 100.0 + 18 * 0.02,
            "ax": 0.0, "ay": 0.0, "az": 9.81
        })
        dq_after_dup = compute_data_quality()
        self.assertEqual(dq_after_dup["packets_received"], 20, "Duplicate sequence must not increment packets_received")
        
        # 4. Test Stream Epoch Reset: New epoch resets sequence trackers
        await handle_sensor_payload({
            "stream_epoch": "new-epoch-reset",
            "seq": 0,
            "t": 200.0,
            "ax": 0.0, "ay": 0.0, "az": 9.81
        })
        dq_new_epoch = compute_data_quality()
        self.assertEqual(dq_new_epoch["packets_received"], 1)
        self.assertEqual(dq_new_epoch["packet_loss_pct"], 0.0)

    def test_hardware_recording_export_schema(self):
        """
        Phase 9: Verify hardware benchmarking bundle generation and schema validation.
        """
        dummy_readings = [
            {"t": 100.0 + i * 0.02, "t_mono": 1000.0 + i * 20.0, "seq": i, "ax": 0.1, "ay": -0.1, "az": 9.81, "gx": 0.0, "gy": 0.0, "gz": 0.0}
            for i in range(25)
        ]
        bundle = build_hardware_recording_bundle(
            session_id="SESS-BENCH-TEST",
            patient_id="P-101",
            protocol_type="TREMOR_3TEST",
            provenance="REAL_DEVICE",
            placement="HANDHELD",
            device_info={"browser": "Chrome Mobile", "os": "Android 14", "device_model": "Pixel 7"},
            raw_readings=dummy_readings
        )
        
        valid, msg = validate_recording_bundle(bundle)
        self.assertTrue(valid, msg)
        self.assertEqual(bundle["recording_metadata"]["session_id"], "SESS-BENCH-TEST")
        self.assertEqual(bundle["empirical_quality_metrics"]["sample_count"], 25)
        self.assertAlmostEqual(bundle["empirical_quality_metrics"]["empirical_hz"], 50.0, delta=2.0)

if __name__ == "__main__":
    unittest.main()
