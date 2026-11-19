"""
MotorTrack SQLite Database Migration & Integrity Tool.
Performs non-destructive schema migration, foreign key verification,
and legacy quarantine tracking with consistent online backup.
"""
import sqlite3
import shutil
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "parkinson_engine.db"

def run_migration():
    if not DB_PATH.exists():
        print(f"[MIGRATION] Database {DB_PATH} not found. Skipping migration.")
        return

    # Step 1: Create consistent backup
    t = int(time.time())
    backup_path = DATA_DIR / f"parkinson_engine_migration_backup_{t}.db"
    src_conn = sqlite3.connect(DB_PATH)
    dst_conn = sqlite3.connect(backup_path)
    src_conn.backup(dst_conn)
    dst_conn.close()
    print(f"[MIGRATION] Backup created: {backup_path}")

    cur = src_conn.cursor()

    # Step 2: PRAGMA integrity check
    cur.execute("PRAGMA integrity_check")
    integrity = cur.fetchall()
    print(f"[MIGRATION] PRAGMA integrity_check: {integrity}")

    # Step 3: Check columns in sessions
    cur.execute("PRAGMA table_info(sessions)")
    sess_cols = [r[1] for r in cur.fetchall()]
    
    if "provenance" not in sess_cols:
        print("[MIGRATION] Adding 'provenance' column to sessions...")
        cur.execute("ALTER TABLE sessions ADD COLUMN provenance VARCHAR(32) DEFAULT 'REAL_DEVICE'")
    if "device_info" not in sess_cols:
        print("[MIGRATION] Adding 'device_info' column to sessions...")
        cur.execute("ALTER TABLE sessions ADD COLUMN device_info VARCHAR(256)")
    if "quality_status" not in sess_cols:
        print("[MIGRATION] Adding 'quality_status' column to sessions...")
        cur.execute("ALTER TABLE sessions ADD COLUMN quality_status VARCHAR(32) DEFAULT 'RELIABLE'")
    if "empirical_hz" not in sess_cols:
        print("[MIGRATION] Adding 'empirical_hz' column to sessions...")
        cur.execute("ALTER TABLE sessions ADD COLUMN empirical_hz FLOAT")
    if "packet_loss_pct" not in sess_cols:
        print("[MIGRATION] Adding 'packet_loss_pct' column to sessions...")
        cur.execute("ALTER TABLE sessions ADD COLUMN packet_loss_pct FLOAT")
    if "jitter_ms" not in sess_cols:
        print("[MIGRATION] Adding 'jitter_ms' column to sessions...")
        cur.execute("ALTER TABLE sessions ADD COLUMN jitter_ms FLOAT")

    # Step 4: Check columns in readings
    cur.execute("PRAGMA table_info(readings)")
    reading_cols = [r[1] for r in cur.fetchall()]
    if "stream_epoch" not in reading_cols:
        print("[MIGRATION] Adding 'stream_epoch' column to readings...")
        cur.execute("ALTER TABLE readings ADD COLUMN stream_epoch VARCHAR(64)")
    if "t_mono" not in reading_cols:
        print("[MIGRATION] Adding 't_mono' column to readings...")
        cur.execute("ALTER TABLE readings ADD COLUMN t_mono FLOAT")

    # Step 5: Quarantine table for unresolved orphan foreign keys
    cur.execute("""
        CREATE TABLE IF NOT EXISTS legacy_orphaned_sessions (
            session_id VARCHAR(64) PRIMARY KEY,
            missing_patient_id VARCHAR(64),
            reason TEXT,
            archived_at DATETIME
        )
    """)

    # Identify orphan sessions
    cur.execute("""
        SELECT session_id, patient_id FROM sessions 
        WHERE patient_id NOT IN (SELECT patient_id FROM patients)
    """)
    orphans = cur.fetchall()
    if orphans:
        print(f"[MIGRATION] Found {len(orphans)} sessions referencing missing patient identities.")
        # Ensure default Demo Participant P-101 exists so legitimate demo sessions can link
        cur.execute("SELECT patient_id FROM patients WHERE patient_id = 'P-101'")
        if not cur.fetchone():
            print("[MIGRATION] Creating default research participant 'P-101' for demo sessions...")
            cur.execute("""
                INSERT INTO patients (patient_id, name, age, gender, diagnosis_year, notes)
                VALUES ('P-101', 'Demo Participant · Synthetic Data', 68, 'Non-specified', 2021, 'Research prototype evaluation record')
            """)

        # Re-check remaining orphans
        cur.execute("""
            SELECT session_id, patient_id FROM sessions 
            WHERE patient_id NOT IN (SELECT patient_id FROM patients)
        """)
        remaining_orphans = cur.fetchall()
        for s_id, p_id in remaining_orphans:
            cur.execute("""
                INSERT OR IGNORE INTO legacy_orphaned_sessions (session_id, missing_patient_id, reason, archived_at)
                VALUES (?, ?, 'Foreign key violation: patient does not exist', datetime('now'))
            """, (s_id, p_id))

    # Mark sessions without sequence evidence appropriately
    cur.execute("""
        UPDATE sessions SET provenance = 'UNKNOWN_LEGACY'
        WHERE session_id IN (
            SELECT DISTINCT s.session_id FROM sessions s
            JOIN test_segments seg ON s.session_id = seg.session_id
            JOIN readings r ON seg.segment_id = r.segment_id
            WHERE r.seq_num IS NULL
        ) AND provenance = 'REAL_DEVICE'
    """)

    # Step 6: PRAGMA foreign_key_check
    cur.execute("PRAGMA foreign_key_check")
    fk_errors = cur.fetchall()
    print(f"[MIGRATION] PRAGMA foreign_key_check: {fk_errors}")

    src_conn.commit()
    src_conn.close()
    print("[MIGRATION] Migration and repair complete!")

if __name__ == "__main__":
    run_migration()
