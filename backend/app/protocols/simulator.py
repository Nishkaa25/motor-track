import asyncio
import time
import math
import numpy as np
from typing import Optional, Callable
from backend.app.config import config

class PatientSensorSimulator:
    """
    Simulates high-fidelity 50Hz IMU sensor streams for different Parkinson's clinical profiles:
      - HEALTHY_CONTROL: Low resting noise, clean voluntary motion, fast tapping
      - MILD_PD: 5.4 Hz intermittent tremor (RMS ~0.15), mild tapping fatigue
      - SEVERE_PD: 4.8 Hz strong resting tremor (RMS ~0.75), high tremor band ratio
      - FOG_WALKING: 1.8 Hz walking rhythm -> sudden 5.5 Hz freeze episode at t=4s -> recovery
      - BRADYKINESIA_TAPPING: Progressive tap rate decay from 6.0 taps/s to 2.5 taps/s with irregularity
    """
    def __init__(self, callback: Optional[Callable] = None):
        self.callback = callback
        self.is_running = False
        self.profile = "SEVERE_PD"  # HEALTHY_CONTROL, MILD_PD, SEVERE_PD, FOG_WALKING, BRADYKINESIA_TAPPING
        self.task: Optional[asyncio.Task] = None
        self._start_time = 0.0

    def start(self, profile: str = "SEVERE_PD"):
        if self.is_running:
            self.stop()
            
        self.profile = profile
        self.is_running = True
        self._start_time = time.time()
        self.task = asyncio.create_task(self._simulation_loop())

    def stop(self):
        self.is_running = False
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None

    async def _simulation_loop(self):
        dt = 1.0 / config.sampling_rate_hz  # 0.02s for 50Hz
        t_sim = 0.0
        
        last_tap_time = 0.0
        tap_interval = 0.16  # Initial ~6 taps/s

        while self.is_running:
            start_loop = time.perf_counter()
            now = time.time()
            t_sim = now - self._start_time
            
            # Base gravity (z-axis ~9.81 m/s^2 when phone is flat on hand)
            gravity_x = 0.1 * math.sin(0.1 * t_sim)
            gravity_y = -0.2 * math.cos(0.1 * t_sim)
            gravity_z = 9.81 + 0.1 * math.sin(0.05 * t_sim)
            
            ax, ay, az = gravity_x, gravity_y, gravity_z
            gx, gy, gz = 0.01, -0.01, 0.02
            tap_event = False

            if self.profile == "HEALTHY_CONTROL":
                # Mild physiological noise
                noise_a = np.random.normal(0, 0.015, size=3)
                noise_g = np.random.normal(0, 0.02, size=3)
                ax += noise_a[0]
                ay += noise_a[1]
                az += noise_a[2]
                gx += noise_g[0]
                gy += noise_g[1]
                gz += noise_g[2]

            elif self.profile == "MILD_PD":
                # 5.4 Hz tremor with intermittent waxing/waning amplitude
                tremor_freq = 5.4
                tremor_amp = 0.18 * (0.6 + 0.4 * math.sin(0.5 * t_sim))  # Modulation
                
                # Oscillations along x and y with slight phase shift (pill-rolling)
                osc_x = tremor_amp * math.sin(2.0 * math.pi * tremor_freq * t_sim)
                osc_y = tremor_amp * math.cos(2.0 * math.pi * tremor_freq * t_sim + 0.3)
                osc_z = 0.3 * tremor_amp * math.sin(2.0 * math.pi * tremor_freq * t_sim + 0.6)
                
                ax += osc_x + np.random.normal(0, 0.02)
                ay += osc_y + np.random.normal(0, 0.02)
                az += osc_z + np.random.normal(0, 0.02)
                
                # Gyro oscillations
                gx += 0.8 * osc_y
                gy += -0.8 * osc_x
                gz += 0.2 * osc_x

            elif self.profile == "SEVERE_PD":
                # 4.8 Hz strong classical Parkinsonian resting tremor (high amplitude)
                tremor_freq = 4.8
                tremor_amp = 0.75
                
                osc_x = tremor_amp * math.sin(2.0 * math.pi * tremor_freq * t_sim)
                osc_y = tremor_amp * math.sin(2.0 * math.pi * tremor_freq * t_sim + math.pi/3)
                osc_z = 0.4 * tremor_amp * math.sin(2.0 * math.pi * tremor_freq * t_sim + math.pi/2)
                
                ax += osc_x + np.random.normal(0, 0.04)
                ay += osc_y + np.random.normal(0, 0.04)
                az += osc_z + np.random.normal(0, 0.04)
                
                gx += 2.5 * osc_y + np.random.normal(0, 0.05)
                gy += -2.5 * osc_x + np.random.normal(0, 0.05)
                gz += 0.9 * osc_z + np.random.normal(0, 0.05)

            elif self.profile == "FOG_WALKING":
                # Walking phase (0 to 4s and 8 to 15s): rhythmic 1.8 Hz locomotion
                # Freeze phase (4s to 8s): loss of locomotion, high-freq 5.5 Hz leg trembling
                is_in_freeze = (4.0 <= (t_sim % 15.0) <= 8.5)
                
                if not is_in_freeze:
                    # Normal walking step rhythm
                    step_freq = 1.8  # ~108 steps/min
                    walk_bounce = 3.5 * math.sin(2.0 * math.pi * step_freq * t_sim)
                    forward_acc = 1.2 * math.cos(2.0 * math.pi * step_freq * t_sim)
                    
                    az += walk_bounce + np.random.normal(0, 0.1)
                    ay += forward_acc + np.random.normal(0, 0.08)
                    ax += 0.5 * math.sin(2.0 * math.pi * (step_freq/2.0) * t_sim)
                    gx += 0.5 * math.sin(2.0 * math.pi * step_freq * t_sim)
                else:
                    # Leg trembling during Freezing of Gait (5.5 Hz in vertical axis, low locomotion)
                    freeze_freq = 5.5
                    freeze_tremble = 2.2 * math.sin(2.0 * math.pi * freeze_freq * t_sim)
                    az += freeze_tremble + np.random.normal(0, 0.2)
                    ay += 0.2 * np.random.normal(0, 0.1)  # Loss of forward motion
                    ax += 0.3 * math.sin(2.0 * math.pi * freeze_freq * t_sim + 0.4)
                    gx += 1.2 * math.sin(2.0 * math.pi * freeze_freq * t_sim)

            elif self.profile == "BRADYKINESIA_TAPPING":
                # Simulated tapping stream with fatigue
                # Tap interval lengthens over time: from 0.15s (6.6 taps/s) to 0.40s (2.5 taps/s)
                fatigue_factor = min(1.0, t_sim / 10.0)
                current_interval = 0.15 + 0.25 * fatigue_factor + np.random.normal(0, 0.03 * (1.0 + fatigue_factor))
                current_interval = max(0.12, current_interval)
                
                if (now - last_tap_time) >= current_interval:
                    tap_event = True
                    last_tap_time = now
                    # Tap impact acceleration
                    az += 4.0 + np.random.normal(0, 0.5)
                    ay += 1.0 * np.random.normal(0, 0.2)
                else:
                    az += np.random.normal(0, 0.05)

            # Build payload
            payload = {
                "t": round(now, 4),
                "ax": round(float(ax), 4),
                "ay": round(float(ay), 4),
                "az": round(float(az), 4),
                "gx": round(float(gx), 4),
                "gy": round(float(gy), 4),
                "gz": round(float(gz), 4),
                "tap_event": tap_event,
                "is_simulated": True,
                "profile": self.profile
            }

            if self.callback:
                try:
                    await self.callback(payload)
                except Exception as e:
                    pass

            # Maintain accurate 50Hz loop timing
            elapsed = time.perf_counter() - start_loop
            sleep_time = max(0.001, dt - elapsed)
            await asyncio.sleep(sleep_time)

simulator = PatientSensorSimulator()
