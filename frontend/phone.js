// Parkinson's Assessment - Mobile Sensor Client with Slow Spoken Guidance & Visual Demos
let ws = null;
let sampleSeq = 0;
let lastEventMono = 0;
let measuredHz = 0;
let eventBuffer = [];

let currentAx = 0, currentAy = 0, currentAz = 0;
let currentGx = 0, currentGy = 0, currentGz = 0;

let tapCount = 0;
let tapTimestamps = [];
let audioCtx = null;
let wakeLock = null;

// Local High-Precision Timer State
let currentState = "IDLE";
let stateStartTime = 0;
let stateDuration = 15.0;
let timerAnimationId = null;
let lastSpokenPhoneState = "";

// Web Speech Synthesis (Slower, Calm, Natural Cadence: rate 0.82)
function speakPhoneVoice(text) {
    if (!('speechSynthesis' in window)) return;
    try {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 0.82; // Slower, calm, clear pace
        utterance.pitch = 0.95;
        utterance.volume = 1.0;
        window.speechSynthesis.speak(utterance);
    } catch (e) {}
}

// Mobile Visual Demo SVGs
const phoneDemoSvgData = {
    "RESTING": {
        svg: `<path d="M20 120 Q60 100 100 115 Q130 125 150 110" stroke="#334155" stroke-width="6" fill="none" stroke-linecap="round"/>
              <path d="M45 105 C60 100 90 95 110 102 C125 108 135 112 140 105" stroke="#38bdf8" stroke-width="4" fill="none" stroke-linecap="round"/>
              <rect x="65" y="78" width="45" height="24" rx="4" fill="#0f172a" stroke="#38bdf8" stroke-width="2.5" transform="rotate(-5 85 90)"/>
              <circle cx="88" cy="90" r="4" fill="#38bdf8" class="animate-pulse"/>`,
        text: "Place phone flat in your open palm resting relaxed on your lap. Do not grip."
    },
    "POSTURAL": {
        svg: `<path d="M20 130 C40 110 55 90 60 70" stroke="#334155" stroke-width="6" fill="none"/>
              <path d="M60 70 L130 70" stroke="#38bdf8" stroke-width="5" stroke-linecap="round"/>
              <rect x="120" y="58" width="24" height="40" rx="4" fill="#0f172a" stroke="#38bdf8" stroke-width="2.5"/>
              <circle cx="132" cy="78" r="4" fill="#38bdf8" class="animate-pulse"/>`,
        text: "Hold arm outstretched horizontally in front of chest at shoulder height."
    },
    "ACTION": {
        svg: `<circle cx="35" cy="55" r="14" stroke="#334155" stroke-width="3" fill="none"/>
              <path d="M35 69 L35 110" stroke="#334155" stroke-width="4"/>
              <path d="M42 60 Q85 30 130 65" stroke="#38bdf8" stroke-width="2" stroke-dasharray="3 3" fill="none"/>
              <path d="M35 85 Q75 75 105 65" stroke="#38bdf8" stroke-width="4" stroke-linecap="round"/>
              <rect x="100" y="52" width="20" height="32" rx="3" fill="#0f172a" stroke="#38bdf8" stroke-width="2"/>`,
        text: "Slowly bring the phone to touch your nose, then extend back out smoothly."
    },
    "GAIT": {
        svg: `<circle cx="80" cy="35" r="12" stroke="#38bdf8" stroke-width="3" fill="none"/>
              <path d="M80 47 L80 100" stroke="#38bdf8" stroke-width="4"/>
              <path d="M80 100 L65 145" stroke="#38bdf8" stroke-width="4"/>
              <path d="M80 100 L98 140" stroke="#38bdf8" stroke-width="4"/>
              <rect x="75" y="72" width="12" height="20" rx="2" fill="#38bdf8"/>`,
        text: "Place phone in your pocket or against your waist and walk naturally."
    }
};

function updatePhoneVisualDemo(key) {
    const demo = phoneDemoSvgData[key] || phoneDemoSvgData["RESTING"];
    const svgEl = document.getElementById("phoneHandDemoSvg");
    const txtEl = document.getElementById("phoneInstructionText");
    if (svgEl) svgEl.innerHTML = demo.svg;
    if (txtEl) txtEl.innerText = demo.text;
}

// Touch event lock
window.addEventListener('touchstart', (e) => {
    if (!e.target.closest('button') && !e.target.closest('#tapTarget') && !e.target.closest('select')) {
        if (e.touches.length > 1) {
            e.preventDefault();
        }
    }
}, { passive: false });

window.addEventListener('touchmove', (e) => {
    e.preventDefault();
}, { passive: false });

window.addEventListener('contextmenu', (e) => e.preventDefault());

// Wake Lock
async function requestScreenWakeLock() {
    try {
        if ('wakeLock' in navigator) {
            wakeLock = await navigator.wakeLock.request('screen');
        }
    } catch (e) {}
}

// Audio tone
function playTone(freq = 440, duration = 0.1) {
    try {
        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (audioCtx.state === 'suspended') {
            audioCtx.resume();
        }
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.frequency.value = freq;
        osc.type = 'sine';
        gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration);
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.start();
        osc.stop(audioCtx.currentTime + duration);
    } catch (e) {}
}

// WebSocket Connection
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/phone`;
    
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        document.getElementById("connDot").className = "w-2 h-2 rounded-full bg-emerald-400";
        document.getElementById("connText").innerText = "Streaming to Laptop";
        document.getElementById("connText").className = "text-emerald-300 font-serif italic";
        requestScreenWakeLock();
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === "CONNECTED_ACK" || data.type === "STATE_UPDATE" || data.type === "STATUS_UPDATE") {
                if (data.state) {
                    syncStateFromServer(data.state);
                }
            }
        } catch (e) {}
    };

    ws.onclose = () => {
        document.getElementById("connDot").className = "w-2 h-2 rounded-full bg-rose-500";
        document.getElementById("connText").innerText = "Reconnecting...";
        document.getElementById("connText").className = "text-rose-400 italic";
        setTimeout(connectWebSocket, 1500);
    };

    ws.onerror = () => {
        try { ws.close(); } catch(e) {}
    };
}

// Event-driven batch transmission loop (flushes queued hardware events at ~50Hz)
function startTransmissionLoop() {
    setInterval(() => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        if (eventBuffer.length === 0) return;

        const batch = eventBuffer.splice(0);
        ws.send(JSON.stringify({
            type: "BATCH",
            samples: batch
        }));
    }, 20);
}

function setupSensorListeners() {
    window.addEventListener('devicemotion', (event) => {
        const acc = event.accelerationIncludingGravity;
        const rot = event.rotationRate;
        if (!acc) return;

        // Preserve genuine 0.0 values without falsy substitution or 9.81 fallback
        const ax = (acc.x !== null && acc.x !== undefined) ? acc.x : 0.0;
        const ay = (acc.y !== null && acc.y !== undefined) ? acc.y : 0.0;
        const az = (acc.z !== null && acc.z !== undefined) ? acc.z : 0.0;

        const gx = (rot && rot.alpha !== null && rot.alpha !== undefined) ? rot.alpha * (Math.PI / 180.0) : 0.0;
        const gy = (rot && rot.beta !== null && rot.beta !== undefined) ? rot.beta * (Math.PI / 180.0) : 0.0;
        const gz = (rot && rot.gamma !== null && rot.gamma !== undefined) ? rot.gamma * (Math.PI / 180.0) : 0.0;

        currentAx = ax;
        currentAy = ay;
        currentAz = az;
        currentGx = gx;
        currentGy = gy;
        currentGz = gz;

        const nowMono = performance.now();
        const nowWall = Date.now() / 1000.0;
        const seq = sampleSeq++;

        // Measure actual hardware sampling interval
        if (lastEventMono > 0) {
            const dt = (nowMono - lastEventMono) / 1000.0;
            if (dt > 0.001) {
                const instHz = 1.0 / dt;
                measuredHz = measuredHz <= 0 ? instHz : (measuredHz * 0.95 + instHz * 0.05);
                const liveHzEl = document.getElementById("liveHz");
                if (liveHzEl) liveHzEl.innerText = `${measuredHz.toFixed(1)} Hz`;
            }
        }
        lastEventMono = nowMono;

        eventBuffer.push({
            t: nowWall,
            t_mono: nowMono,
            seq: seq,
            ax: ax,
            ay: ay,
            az: az,
            gx: gx,
            gy: gy,
            gz: gz,
            tap_event: false
        });

        const axEl = document.getElementById("valAx");
        const ayEl = document.getElementById("valAy");
        const azEl = document.getElementById("valAz");
        if (axEl) axEl.innerText = ax.toFixed(2);
        if (ayEl) ayEl.innerText = ay.toFixed(2);
        if (azEl) azEl.innerText = az.toFixed(2);
    }, true);
}

async function requestSensorPermissions() {
    playTone(520, 0.1);
    requestScreenWakeLock();
    
    if (typeof DeviceMotionEvent !== 'undefined' && typeof DeviceMotionEvent.requestPermission === 'function') {
        try {
            const permission = await DeviceMotionEvent.requestPermission();
            if (permission === 'granted') {
                onSensorsApproved();
            } else {
                alert("Sensor access permission was not granted. Please check iOS settings.");
            }
        } catch (e) {
            console.warn("Sensor permission request error:", e);
            onSensorsApproved();
        }
    } else {
        onSensorsApproved();
    }
}

function onSensorsApproved() {
    document.getElementById("permissionCard").classList.add("hidden");
    setupSensorListeners();
    startTransmissionLoop();
}

// Local Countdown & Slow Spoken Guidance Engine
function syncStateFromServer(stateInfo) {
    const newState = stateInfo.state || "IDLE";
    const targetSec = stateInfo.target_duration_sec || 15.0;
    const remaining = stateInfo.remaining_sec !== undefined ? stateInfo.remaining_sec : targetSec;

    if (newState !== currentState) {
        currentState = newState;
        stateDuration = targetSec;
        stateStartTime = performance.now() - (targetSec - remaining) * 1000.0;
        
        if (currentState !== lastSpokenPhoneState) {
            lastSpokenPhoneState = currentState;
            if (currentState === "PREP_RESTING") {
                updatePhoneVisualDemo("RESTING");
                speakPhoneVoice("Please take your time. For Stage One, sit comfortably. Place your phone flat in your open palm, resting relaxed on your lap. We will begin in a few moments.");
            } else if (currentState === "RESTING") {
                updatePhoneVisualDemo("RESTING");
                speakPhoneVoice("Begin resting test. Keep your hand completely still and relaxed.");
            } else if (currentState === "PREP_POSTURAL") {
                updatePhoneVisualDemo("POSTURAL");
                speakPhoneVoice("Stage one is complete. For Stage Two, Postural Tremor: raise your arm and hold it straight out in front of your chest, at shoulder height. We will begin in a few moments.");
            } else if (currentState === "POSTURAL") {
                updatePhoneVisualDemo("POSTURAL");
                speakPhoneVoice("Begin postural test. Hold your outstretched arm steady.");
            } else if (currentState === "PREP_ACTION") {
                updatePhoneVisualDemo("ACTION");
                speakPhoneVoice("Stage two is complete. For Stage Three, Action Tremor: slowly and steadily bring the phone towards your nose, and then extend your arm back out. We will begin in a few moments.");
            } else if (currentState === "ACTION") {
                updatePhoneVisualDemo("ACTION");
                speakPhoneVoice("Begin action test. Move your arm slowly and steadily.");
            } else if (currentState === "PREP_GAIT") {
                updatePhoneVisualDemo("GAIT");
                speakPhoneVoice("Prepare for the walking test. Place your phone in your pocket or at your waist. We will begin in a few moments.");
            } else if (currentState === "GAIT_WALK") {
                updatePhoneVisualDemo("GAIT");
                speakPhoneVoice("Begin walking at your normal, comfortable pace.");
            } else if (currentState === "PREP_TAPPING") {
                speakPhoneVoice("Prepare for the finger tapping test. Rest your phone on the table. We will begin in a few moments.");
            } else if (currentState === "TAPPING") {
                speakPhoneVoice("Begin tapping. Tap the circle as rapidly and steadily as you can.");
            } else if (currentState === "COMPLETE") {
                speakPhoneVoice("Assessment complete. Check your dashboard for the clinical summary.");
            }
        }
    }

    renderStateUI(currentState, remaining, targetSec);
}

function startLocalTimerLoop() {
    function tick() {
        if (currentState !== "IDLE" && currentState !== "COMPLETE") {
            const elapsedSec = (performance.now() - stateStartTime) / 1000.0;
            const remaining = Math.max(0.0, stateDuration - elapsedSec);
            renderStateUI(currentState, remaining, stateDuration);
        }
        timerAnimationId = requestAnimationFrame(tick);
    }
    tick();
}

function renderStateUI(state, remaining, targetSec) {
    const phoneSeconds = document.getElementById("phoneSeconds");
    const shieldSecondsLeft = document.getElementById("shieldSecondsLeft");
    const stagePill = document.getElementById("stagePill");
    const stageTitle = document.getElementById("stageTitle");
    const protocolCard = document.getElementById("protocolCard");
    const tappingZone = document.getElementById("tappingZone");
    const timerSub = document.getElementById("phoneTimerSub");
    const circle = document.getElementById("phoneTimerProgress");

    const intSec = Math.max(0, Math.ceil(remaining));

    if (state === "COMPLETE") {
        phoneSeconds.innerText = "DONE";
        if (shieldSecondsLeft) shieldSecondsLeft.innerText = "DONE";
    } else {
        phoneSeconds.innerText = intSec;
        if (shieldSecondsLeft) shieldSecondsLeft.innerText = intSec;
    }

    const progress = Math.min(100, Math.max(0, (1.0 - (remaining / Math.max(1.0, targetSec))) * 100.0));
    const circumference = 264;
    const offset = circumference - (progress / 100.0) * circumference;
    circle.style.strokeDashoffset = offset;

    if (state === "TAPPING") {
        protocolCard.classList.add("hidden");
        tappingZone.classList.remove("hidden");
    } else {
        tappingZone.classList.add("hidden");
        protocolCard.classList.remove("hidden");

        if (state.startsWith("PREP_")) {
            stagePill.innerText = `GET READY (${intSec}s)`;
            stagePill.className = "text-[10px] font-bold px-3 py-1 rounded-full bg-amber-50 text-amber-800 border border-amber-200 uppercase tracking-wider";
            stageTitle.innerText = "Prepare Posture";
            timerSub.innerText = "PREP SEC";
            circle.setAttribute("class", "text-amber-500 fill-none transition-all duration-300");
        } else if (state === "RESTING") {
            stagePill.innerText = `RECORDING (${intSec}s)`;
            stagePill.className = "text-[10px] font-bold px-3 py-1 rounded-full bg-blue-50 text-blue-700 border border-blue-200 uppercase tracking-wider";
            stageTitle.innerText = "Rest Hand on Lap";
            timerSub.innerText = "REC SEC";
            circle.setAttribute("class", "text-blue-600 fill-none transition-all duration-300");
        } else if (state === "POSTURAL") {
            stagePill.innerText = `RECORDING (${intSec}s)`;
            stagePill.className = "text-[10px] font-bold px-3 py-1 rounded-full bg-teal-50 text-teal-700 border border-teal-200 uppercase tracking-wider";
            stageTitle.innerText = "Hold Arm Outstretched";
            timerSub.innerText = "REC SEC";
            circle.setAttribute("class", "text-teal-600 fill-none transition-all duration-300");
        } else if (state === "ACTION") {
            stagePill.innerText = `RECORDING (${intSec}s)`;
            stagePill.className = "text-[10px] font-bold px-3 py-1 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-200 uppercase tracking-wider";
            stageTitle.innerText = "Finger-to-Nose Motion";
            timerSub.innerText = "REC SEC";
            circle.setAttribute("class", "text-indigo-600 fill-none transition-all duration-300");
        } else if (state === "COMPLETE") {
            stagePill.innerText = "SESSION COMPLETE";
            stagePill.className = "text-[10px] font-bold px-3 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 uppercase tracking-wider";
            stageTitle.innerText = "Assessment Finished!";
            circle.setAttribute("class", "text-emerald-600 fill-none transition-all duration-300");
        } else {
            stagePill.innerText = "IDLE (READY)";
            stagePill.className = "text-[10px] font-bold px-3 py-1 rounded-full bg-slate-100 text-slate-600 uppercase tracking-wider";
            stageTitle.innerText = "Ready to Begin";
            timerSub.innerText = "READY";
            circle.setAttribute("class", "text-blue-600 fill-none transition-all duration-300");
        }
    }
}

function handleTap(e) {
    if (e) e.preventDefault();
    tapCount++;
    const now = Date.now() / 1000.0;
    const nowMono = performance.now();
    tapTimestamps.push(now);
    
    const countDisplay = document.getElementById("tapCountDisplay");
    if (countDisplay) countDisplay.innerText = tapCount;
    const target = document.getElementById("tapTarget");
    if (target) {
        target.classList.add("tap-active-anim");
        setTimeout(() => target.classList.remove("tap-active-anim"), 120);
    }

    playTone(680, 0.04);
    if (navigator.vibrate) navigator.vibrate(15);

    if (tapTimestamps.length >= 3) {
        const recent = tapTimestamps.slice(-5);
        const dt = recent[recent.length - 1] - recent[0];
        const rate = (recent.length - 1) / Math.max(0.1, dt);
        const rateEl = document.getElementById("tapRateLive");
        if (rateEl) rateEl.innerText = `${rate.toFixed(1)} taps/s`;
    }

    if (ws && ws.readyState === WebSocket.OPEN) {
        // Dedicated tap event payload without synthesizing +5.0 m/s² IMU spikes
        ws.send(JSON.stringify({
            type: "TAP_EVENT",
            t: now,
            t_mono: nowMono,
            tap_event: true,
            tap_count: tapCount
        }));
    }
}

function togglePalmShield(show) {
    const overlay = document.getElementById("palmShieldOverlay");
    if (show) {
        overlay.classList.remove("hidden");
        if (document.documentElement.requestFullscreen) {
            document.documentElement.requestFullscreen().catch(() => {});
        }
    } else {
        overlay.classList.add("hidden");
    }
}

document.getElementById("btnGrantSensors").addEventListener("click", requestSensorPermissions);
document.getElementById("btnPalmLock").addEventListener("click", () => togglePalmShield(true));
document.getElementById("btnUnlockPalmShield").addEventListener("click", () => togglePalmShield(false));

document.getElementById("btnPhoneStart").addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: "START_PROTOCOL", protocol: "TREMOR_3TEST" }));
    }
});

// Single pointerdown listener to prevent double triggers on mobile devices
const tapTarget = document.getElementById("tapTarget");
if (tapTarget) {
    tapTarget.addEventListener("pointerdown", handleTap);
}

window.addEventListener("DOMContentLoaded", () => {
    connectWebSocket();
    startLocalTimerLoop();
    updatePhoneVisualDemo("RESTING");
});
