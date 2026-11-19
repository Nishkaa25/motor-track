import datetime
from sqlalchemy import Column, Integer, Float, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from backend.app.database import Base

class Patient(Base):
    __tablename__ = "patients"

    patient_id = Column(String(64), primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    age = Column(Integer, nullable=True)
    gender = Column(String(32), nullable=True)
    diagnosis_year = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    notes = Column(Text, nullable=True)

    sessions = relationship("Session", back_populates="patient", cascade="all, delete-orphan")
    medications = relationship("Medication", back_populates="patient", cascade="all, delete-orphan")
    medication_events = relationship("MedicationEvent", back_populates="patient", cascade="all, delete-orphan")
    symptom_events = relationship("SymptomEvent", back_populates="patient", cascade="all, delete-orphan")


class Medication(Base):
    __tablename__ = "medications"

    medication_id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(String(64), ForeignKey("patients.patient_id"), nullable=False)
    name = Column(String(128), nullable=False)  # e.g. Levodopa / Carbidopa
    dosage = Column(String(64), default="100/25 mg")
    scheduled_time = Column(String(32), default="08:00 AM")
    last_taken_at = Column(DateTime, nullable=True)
    source = Column(String(32), default="USER_LOGGED")  # USER_LOGGED, SIMULATED, IMPORTED
    notes = Column(String(256), nullable=True)

    patient = relationship("Patient", back_populates="medications")


class MedicationEvent(Base):
    __tablename__ = "medication_events"

    event_id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(String(64), ForeignKey("patients.patient_id"), nullable=False, index=True)
    medication_name = Column(String(128), nullable=False)
    dose = Column(String(64), default="100/25 mg")
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    source = Column(String(32), default="USER_LOGGED")  # USER_LOGGED, SIMULATED, IMPORTED
    notes = Column(Text, nullable=True)

    patient = relationship("Patient", back_populates="medication_events")


class SymptomEvent(Base):
    __tablename__ = "symptom_events"

    event_id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(String(64), ForeignKey("patients.patient_id"), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    symptom = Column(String(128), nullable=False)  # e.g. Resting Tremor Increase, Stiffness, Wearing Off
    severity = Column(String(32), default="MILD")  # MILD, MODERATE, SEVERE
    source = Column(String(32), default="USER_LOGGED")  # USER_LOGGED, SIMULATED
    notes = Column(Text, nullable=True)

    patient = relationship("Patient", back_populates="symptom_events")



class Session(Base):
    __tablename__ = "sessions"

    session_id = Column(String(64), primary_key=True, index=True)
    patient_id = Column(String(64), ForeignKey("patients.patient_id"), nullable=False, index=True)
    session_type = Column(String(64), default="TREMOR_3TEST")
    started_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None))
    completed_at = Column(DateTime, nullable=True)
    provenance = Column(String(32), default="REAL_DEVICE")  # REAL_DEVICE, SIMULATED, TEST, UNKNOWN_LEGACY
    is_simulated = Column(Boolean, default=False)
    device_info = Column(String(256), nullable=True)
    quality_status = Column(String(32), default="RELIABLE")
    empirical_hz = Column(Float, nullable=True)
    packet_loss_pct = Column(Float, nullable=True)
    jitter_ms = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)

    patient = relationship("Patient", back_populates="sessions")
    segments = relationship("TestSegment", back_populates="session", cascade="all, delete-orphan")
    clinical_summary = relationship("ClinicalSummary", back_populates="session", uselist=False, cascade="all, delete-orphan")


class TestSegment(Base):
    __tablename__ = "test_segments"

    segment_id = Column(String(64), primary_key=True, index=True)
    session_id = Column(String(64), ForeignKey("sessions.session_id"), nullable=False, index=True)
    test_type = Column(String(32), nullable=False)  # RESTING, POSTURAL, ACTION, GAIT_WALK, TAPPING
    start_t = Column(Float, nullable=False)
    end_t = Column(Float, nullable=True)
    summary_json = Column(Text, nullable=True)

    session = relationship("Session", back_populates="segments")
    readings = relationship("Reading", back_populates="segment", cascade="all, delete-orphan")


class Reading(Base):
    __tablename__ = "readings"

    reading_id = Column(Integer, primary_key=True, autoincrement=True)
    segment_id = Column(String(64), ForeignKey("test_segments.segment_id"), nullable=False, index=True)
    seq_num = Column(Integer, nullable=True)
    stream_epoch = Column(String(64), nullable=True)
    timestamp = Column(Float, nullable=False)
    t_mono = Column(Float, nullable=True)  # Monotonic acquisition timestamp

    # Raw IMU channels (SI units: m/s^2 for acc, rad/s for gyro)
    ax = Column(Float, nullable=False)
    ay = Column(Float, nullable=False)
    az = Column(Float, nullable=False)
    gx = Column(Float, nullable=False)
    gy = Column(Float, nullable=False)
    gz = Column(Float, nullable=False)
    
    # Processed features
    filtered_mag = Column(Float, nullable=True)
    dominant_freq = Column(Float, nullable=True)
    rms_amp = Column(Float, nullable=True)
    band_power_ratio = Column(Float, nullable=True)
    spectral_entropy = Column(Float, nullable=True)
    zero_crossing_rate = Column(Float, nullable=True)
    tremor_detected = Column(Boolean, default=False)
    fog_index = Column(Float, nullable=True)

    segment = relationship("TestSegment", back_populates="readings")


class ClinicalSummary(Base):
    __tablename__ = "clinical_summaries"

    summary_id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), ForeignKey("sessions.session_id"), unique=True, nullable=False)
    
    # Tremor Metrics (Derived synchronously from 3.5 - 7.5 Hz band)
    resting_dominant_freq = Column(Float, nullable=True)
    resting_rms_amp = Column(Float, nullable=True)
    resting_tremor_ratio = Column(Float, nullable=True)
    resting_tremor_detected = Column(Boolean, default=False)
    
    postural_dominant_freq = Column(Float, nullable=True)
    postural_rms_amp = Column(Float, nullable=True)
    postural_tremor_ratio = Column(Float, nullable=True)
    postural_tremor_detected = Column(Boolean, default=False)
    
    action_dominant_freq = Column(Float, nullable=True)
    action_rms_amp = Column(Float, nullable=True)
    action_tremor_ratio = Column(Float, nullable=True)
    action_tremor_detected = Column(Boolean, default=False)
    
    overall_tremor_severity = Column(String(32), default="NONE")  # NONE, MILD, MODERATE, SEVERE
    
    # FOG & Gait Metrics
    fog_episodes_count = Column(Integer, default=0)
    total_freeze_duration_sec = Column(Float, default=0.0)
    max_fog_index = Column(Float, default=0.0)
    gait_cadence_spm = Column(Float, nullable=True)  # Steps per minute
    gait_shuffle_score = Column(Float, nullable=True)
    gait_symmetry_ratio = Column(Float, nullable=True)
    
    # Bradykinesia Finger Tapping Metrics
    tap_rate_per_sec = Column(Float, nullable=True)
    tap_irregularity_cv = Column(Float, nullable=True)  # % CV of ITI
    tap_fatigue_slope = Column(Float, nullable=True)  # Rate decay slope
    
    # ML Confidence (Experimental research layer)
    ml_tremor_confidence = Column(Float, nullable=True)  # 0.0 to 1.0
    overall_status = Column(String(64), default="NORMAL")
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None))

    session = relationship("Session", back_populates="clinical_summary")
