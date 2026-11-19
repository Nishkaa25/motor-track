// Parkinson's Motor Assessment System - Clinical Telemetry Workstation
let ws = null;
let activePatientId = "P-101";
let activeTab = "view-live";
let isSimulating = false;
let voiceEnabled = true;
let phoneConnected = false;
let lastPacketTime = 0;

// Multi-Channel Acceleration Buffer (50Hz Multi-Channel Oscilloscope)
const MAX_STREAM_POINTS = 300;
const streamData = {
    dynSignal: new Array(MAX_STREAM_POINTS).fill(0.0),
    dynMag: new Array(MAX_STREAM_POINTS).fill(0.35),
    ax: new Array(MAX_STREAM_POINTS).fill(0.08),
    ay: new Array(MAX_STREAM_POINTS).fill(-0.06),
    az: new Array(MAX_STREAM_POINTS).fill(9.81),
};

// Oscilloscope Rendering Configuration & Auto-Gain State
let scopeMode = "DYNAMIC"; // "DYNAMIC", "AXES", or "SINE_TEST"
let scopeGainMode = "AUTO"; // "AUTO", "0.10", "0.25", "0.50", "1.00"
let smoothedYMax = 0.25; // Dynamic visual Y-axis scale limit in m/s²
let onlineGravityDC = 9.81; // Smooth online DC gravity estimation

// Real-Time Oscilloscope Instrumentation & HUD Telemetry
let lastPacketSeq = null;
let totalPacketsReceived = 0;
let latestPlottedSample = 0.0;
let renderFrameCounter = 0;
let lastRenderFpsTime = performance.now();
let renderFps = 60;


// Rolling Frequency vs. Real-Time Clock Timestamp Buffer (Past 20 Seconds)
const MAX_FREQ_POINTS = 20;
const freqTimeline = {
    timestamps: [],
    frequencies: [],
    amplitudes: []
};

// Initialize rolling clock timestamps with zero baselines
const initNow = new Date();
for (let i = MAX_FREQ_POINTS - 1; i >= 0; i--) {
    const t = new Date(initNow.getTime() - i * 1000);
    freqTimeline.timestamps.push(t.toTimeString().split(' ')[0]);
    freqTimeline.frequencies.push(0.0);
    freqTimeline.amplitudes.push(0.0);
}

// Spectrogram Waterfall Buffer (120 Time Slices x 32 Frequency Bins)
const SPEC_COLS = 120;
const SPEC_BINS = 32;
const specWaterfall = [];
for (let c = 0; c < SPEC_COLS; c++) {
    specWaterfall.push(new Float32Array(SPEC_BINS).fill(0.0));
}

// Live PSD Buffer (32 Frequency Bins 0-15 Hz)
const psdCurrent = {
    freqs: [0, 1, 2, 3, 3.8, 4.5, 5.2, 6.0, 7.0, 8.0, 9.5, 11.0, 13.0, 15.0],
    values: new Array(14).fill(0.0)
};

let currentDominantFreq = 0.0;
let currentRmsAmp = 0.0;
let currentBandRatio = 0.0;
let simWavePhase = 0;
let lastFpsTime = performance.now();
let sampleCount = 0;
let lastSpokenState = "";
let animationFrameId = null;

// Web Speech API (Calm speech at rate 0.82)
function speakVoice(text) {
    if (!voiceEnabled || !('speechSynthesis' in window)) return;
    if (phoneConnected) return; // Audio plays through mobile client if connected

    try {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 0.82;
        utterance.pitch = 0.95;
        utterance.volume = 1.0;
        window.speechSynthesis.speak(utterance);
    } catch (e) {}
}

// Visual Hand & Phone Placement Diagrams
const demoIllustrations = {
    "RESTING": {
        title: "Visual Demo: Stage 1 • Resting Hand on Lap",
        svg: `<path d="M20 120 Q60 100 100 115 Q130 125 150 110" stroke="#94a3b8" stroke-width="6" fill="none" stroke-linecap="round"/>
              <path d="M45 105 C60 100 90 95 110 102 C125 108 135 112 140 105" stroke="#2563eb" stroke-width="4" fill="none" stroke-linecap="round"/>
              <rect x="65" y="78" width="45" height="24" rx="4" fill="#0f172a" stroke="#2563eb" stroke-width="2.5" transform="rotate(-5 85 90)"/>
              <circle cx="88" cy="90" r="4" fill="#2563eb" class="animate-pulse"/>
              <path d="M110 102 Q125 98 135 102" stroke="#2563eb" stroke-width="3" fill="none"/>`,
        header: "How to position hand and phone for Resting Tremor:",
        steps: [
            "Sit back comfortably in your chair.",
            "Rest your entire forearm and hand flat on your lap.",
            "Place the smartphone flat in your open, relaxed palm.",
            "Keep your hand completely still and let it rest naturally."
        ]
    },
    "POSTURAL": {
        title: "Visual Demo: Stage 2 • Postural Outstretched Arm",
        svg: `<path d="M20 130 C40 110 55 90 60 70" stroke="#94a3b8" stroke-width="6" fill="none"/>
              <path d="M60 70 L130 70" stroke="#2563eb" stroke-width="5" stroke-linecap="round"/>
              <rect x="120" y="58" width="24" height="40" rx="4" fill="#0f172a" stroke="#2563eb" stroke-width="2.5"/>
              <circle cx="132" cy="78" r="4" fill="#2563eb" class="animate-pulse"/>
              <line x1="40" y1="95" x2="155" y2="95" stroke="#cbd5e1" stroke-dasharray="4 4" stroke-width="1.5"/>`,
        header: "How to position hand and phone for Postural Tremor:",
        steps: [
            "Raise your arm straight out in front of your chest.",
            "Hold your arm horizontal at shoulder height.",
            "Hold the phone steady with screen facing upward.",
            "Maintain this steady outstretched posture without moving."
        ]
    },
    "ACTION": {
        title: "Visual Demo: Stage 3 • Finger-to-Nose Action Motion",
        svg: `<circle cx="35" cy="55" r="16" stroke="#94a3b8" stroke-width="3" fill="none"/>
              <path d="M35 71 L35 120" stroke="#94a3b8" stroke-width="4"/>
              <path d="M42 60 Q85 30 135 65" stroke="#2563eb" stroke-width="2.5" stroke-dasharray="4 4" fill="none"/>
              <path d="M35 85 Q75 75 110 65" stroke="#2563eb" stroke-width="4" stroke-linecap="round"/>
              <rect x="105" y="52" width="22" height="36" rx="3" fill="#0f172a" stroke="#2563eb" stroke-width="2"/>
              <circle cx="116" cy="70" r="3" fill="#2563eb" class="animate-pulse"/>`,
        header: "How to perform the Finger-to-Nose Action motion:",
        steps: [
            "Hold the phone comfortably in your hand.",
            "Start with your arm extended in front of you.",
            "Slowly bring the phone towards your nose (approx. 2 seconds).",
            "Slowly extend your arm back out. Repeat steadily."
        ]
    }
};

function updateVisualDemo(stageKey) {
    const demo = demoIllustrations[stageKey] || demoIllustrations["RESTING"];
    document.getElementById("demoStageTitle").innerText = demo.title;
    document.getElementById("handDemoSvg").innerHTML = demo.svg;
    document.getElementById("demoInstructionHeader").innerText = demo.header;
    const listEl = document.getElementById("demoInstructionList");
    listEl.innerHTML = "";
    demo.steps.forEach(s => {
        const li = document.createElement("li");
        li.innerText = s;
        listEl.appendChild(li);
    });
}

// Initialize Application on Page Load
document.addEventListener("DOMContentLoaded", () => {
    initUI();
    startMasterRenderLoop();
    startContinuousTelemetryTicker();
    connectWebSocket();
    fetchPatients();
    fetchLongitudinalTrends(activePatientId);
    fetchReplaySessions();
    updateVisualDemo("RESTING");
});

// UI Event Handlers
function initUI() {
    // Oscilloscope Mode Selectors
    const btnDyn = document.getElementById("btnScopeModeDyn");
    const btnAxes = document.getElementById("btnScopeModeAxes");
    const btnSine = document.getElementById("btnScopeModeSine");

    const setScopeMode = (mode) => {
        scopeMode = mode;
        if (btnDyn && btnAxes && btnSine) {
            btnDyn.className = mode === "DYNAMIC"
                ? "px-2.5 py-1 rounded-md font-semibold bg-white text-blue-700 shadow-xs transition-all"
                : "px-2.5 py-1 rounded-md text-slate-600 hover:text-slate-900 transition-all";
            btnAxes.className = mode === "AXES"
                ? "px-2.5 py-1 rounded-md font-semibold bg-white text-blue-700 shadow-xs transition-all"
                : "px-2.5 py-1 rounded-md text-slate-600 hover:text-slate-900 transition-all";
            btnSine.className = mode === "SINE_TEST"
                ? "px-2.5 py-1 rounded-md font-semibold bg-amber-100 text-amber-900 shadow-xs transition-all"
                : "px-2.5 py-1 rounded-md text-amber-700 hover:text-amber-900 transition-all";
        }
    };

    if (btnDyn) btnDyn.addEventListener("click", () => setScopeMode("DYNAMIC"));
    if (btnAxes) btnAxes.addEventListener("click", () => setScopeMode("AXES"));
    if (btnSine) btnSine.addEventListener("click", () => setScopeMode("SINE_TEST"));

    // Visualization Gain Selector
    const selGain = document.getElementById("selScopeGain");
    if (selGain) {
        selGain.addEventListener("change", (e) => {
            scopeGainMode = e.target.value;
        });
    }

    const tabs = document.querySelectorAll(".nav-tab");
    tabs.forEach(tab => {
        tab.addEventListener("click", () => {
            tabs.forEach(t => {
                t.classList.remove("active", "border-blue-600", "text-blue-600");
                t.classList.add("border-transparent", "text-slate-600");
            });
            tab.classList.add("active", "border-blue-600", "text-blue-600");
            tab.classList.remove("border-transparent", "text-slate-600");

            const targetId = tab.getAttribute("data-target");
            activeTab = targetId;
            document.querySelectorAll(".tab-view").forEach(view => view.classList.add("hidden"));
            document.getElementById(targetId).classList.remove("hidden");

            if (typeof Plotly !== 'undefined' && targetId === "view-longitudinal") {
                Plotly.Plots.resize('longitudinalTrendsChart');
                Plotly.Plots.resize('symptomRadarChart');
            }
            if (targetId === "view-replay") {
                fetchReplaySessions();
            }
        });
    });

    const btnVoice = document.getElementById("btnToggleVoice");
    btnVoice.addEventListener("click", () => {
        voiceEnabled = !voiceEnabled;
        document.getElementById("voiceStatusText").innerText = `Voice: ${voiceEnabled ? 'ON' : 'OFF'}`;
        btnVoice.className = `flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${voiceEnabled ? 'bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100' : 'bg-slate-50 text-slate-500 border-slate-200'}`;
        if (voiceEnabled) speakVoice("Voice guidance enabled.");
    });

    const patientSelect = document.getElementById("patientSelect");
    patientSelect.addEventListener("change", (e) => {
        activePatientId = e.target.value;
        fetchLongitudinalTrends(activePatientId);
        fetchReplaySessions();
    });

    document.getElementById("btnStartProtocol").addEventListener("click", () => {
        const protocol = document.getElementById("protocolSelect").value;
        sendWsCommand({ action: "START_PROTOCOL", protocol, patient_id: activePatientId });
    });

    document.getElementById("btnResetProtocol").addEventListener("click", () => {
        sendWsCommand({ action: "RESET_PROTOCOL" });
    });

    // Session Replay Listeners
    const replaySelect = document.getElementById("replaySessionSelect");
    if (replaySelect) {
        replaySelect.addEventListener("change", (e) => {
            if (e.target.value) loadReplaySession(e.target.value);
        });
    }
    const btnRefreshReplay = document.getElementById("btnRefreshReplaySessions");
    if (btnRefreshReplay) {
        btnRefreshReplay.addEventListener("click", fetchReplaySessions);
    }
    const btnReplayPlay = document.getElementById("btnReplayPlay");
    if (btnReplayPlay) {
        btnReplayPlay.addEventListener("click", toggleReplay);
    }
    const btnReplayReset = document.getElementById("btnReplayReset");
    if (btnReplayReset) {
        btnReplayReset.addEventListener("click", resetReplay);
    }
    const btnSpeed1x = document.getElementById("btnReplaySpeed1x");
    const btnSpeed2x = document.getElementById("btnReplaySpeed2x");
    if (btnSpeed1x && btnSpeed2x) {
        btnSpeed1x.addEventListener("click", () => setReplaySpeed(1));
        btnSpeed2x.addEventListener("click", () => setReplaySpeed(2));
    }
    const scrubber = document.getElementById("replayScrubber");
    if (scrubber) {
        scrubber.addEventListener("input", (e) => seekReplay(e.target.value));
    }

    // Event Logging Modals
    const btnOpenMedEvent = document.getElementById("btnOpenLogMedEventModal");
    if (btnOpenMedEvent) {
        btnOpenMedEvent.addEventListener("click", () => {
            document.getElementById("modalLogMedEvent").classList.remove("hidden");
        });
    }
    const btnCloseMedEvent = document.getElementById("btnCloseLogMedEventModal");
    if (btnCloseMedEvent) {
        btnCloseMedEvent.addEventListener("click", () => {
            document.getElementById("modalLogMedEvent").classList.add("hidden");
        });
    }
    const btnSaveMedEvent = document.getElementById("btnSaveLogMedEvent");
    if (btnSaveMedEvent) {
        btnSaveMedEvent.addEventListener("click", saveLogMedEvent);
    }

    const btnOpenSympEvent = document.getElementById("btnOpenLogSympEventModal");
    if (btnOpenSympEvent) {
        btnOpenSympEvent.addEventListener("click", () => {
            document.getElementById("modalLogSympEvent").classList.remove("hidden");
        });
    }
    const btnCloseSympEvent = document.getElementById("btnCloseLogSympEventModal");
    if (btnCloseSympEvent) {
        btnCloseSympEvent.addEventListener("click", () => {
            document.getElementById("modalLogSympEvent").classList.add("hidden");
        });
    }
    const btnSaveSympEvent = document.getElementById("btnSaveLogSympEvent");
    if (btnSaveSympEvent) {
        btnSaveSympEvent.addEventListener("click", saveLogSympEvent);
    }

    // Modals
    document.getElementById("btnOpenQR").addEventListener("click", openQRModal);
    document.getElementById("btnCloseQR").addEventListener("click", () => {
        document.getElementById("modalQR").classList.add("hidden");
    });

    document.getElementById("btnOpenSimulator").addEventListener("click", () => {
        document.getElementById("modalSimulator").classList.remove("hidden");
    });
    document.getElementById("btnCloseSimulator").addEventListener("click", () => {
        document.getElementById("modalSimulator").classList.add("hidden");
    });

    document.getElementById("btnStartSim").addEventListener("click", () => {
        const profile = document.getElementById("simProfileSelect").value;
        sendWsCommand({ action: "START_SIMULATOR", profile });
        isSimulating = true;
        updateSimulatorBadge(true, profile);
        document.getElementById("modalSimulator").classList.add("hidden");
    });

    document.getElementById("btnStopSim").addEventListener("click", () => {
        sendWsCommand({ action: "STOP_SIMULATOR" });
        isSimulating = false;
        updateSimulatorBadge(false);
        document.getElementById("modalSimulator").classList.add("hidden");
    });

    document.getElementById("btnNewPatient").addEventListener("click", () => {
        document.getElementById("modalNewPatient").classList.remove("hidden");
    });
    document.getElementById("btnClosePatientModal").addEventListener("click", () => {
        document.getElementById("modalNewPatient").classList.add("hidden");
    });
    document.getElementById("btnSavePatient").addEventListener("click", saveNewPatient);

}

function switchTab(tabId) {
    const tabBtn = document.querySelector(`.nav-tab[data-target="${tabId}"]`);
    if (tabBtn) tabBtn.click();
}

function updateSimulatorBadge(running, profile = "") {
    const badgeText = document.getElementById("simStatusText");
    if (running) {
        badgeText.innerText = `Sim: ${profile.replace('_', ' ')}`;
        badgeText.parentElement.className = "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-amber-100 text-amber-900 border border-amber-300 shadow-sm";
    } else {
        badgeText.innerText = "Simulator: OFF";
        badgeText.parentElement.className = "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-amber-50 hover:bg-amber-100 text-amber-800 border border-amber-200";
    }
}

async function openQRModal() {
    try {
        const res = await fetch("/api/qr");
        const data = await res.json();
        document.getElementById("qrImageElement").src = data.image_data;
        document.getElementById("qrDirectUrlText").innerText = data.url;
        document.getElementById("modalQR").classList.remove("hidden");
    } catch (e) {}
}

// Continuous 1-Second Rolling Frequency Ticker
function startContinuousTelemetryTicker() {
    setInterval(() => {
        const now = new Date();
        const timeStr = now.toTimeString().split(' ')[0];

        const isLiveStreaming = (performance.now() - lastPacketTime) < 2000;
        if (!isLiveStreaming && !isSimulating) {
            currentDominantFreq = 0.0;
            currentRmsAmp = 0.0;
            currentBandRatio = 0.0;

            freqTimeline.timestamps.shift();
            freqTimeline.timestamps.push(timeStr);

            freqTimeline.frequencies.shift();
            freqTimeline.frequencies.push(0.0);

            freqTimeline.amplitudes.shift();
            freqTimeline.amplitudes.push(0.0);

            // Shift flat baseline into waterfall
            specWaterfall.shift();
            specWaterfall.push(new Float32Array(SPEC_BINS).fill(0.0));

            // Flat PSD values
            psdCurrent.values.fill(0.0);

            const peakBadge = document.getElementById("psdPeakBadge");
            if (peakBadge) peakBadge.innerText = "Peak: -- Hz";

            updateFeatureCards({
                dominant_freq: 0.0,
                rms_amp_tremor: 0.0,
                band_power_ratio: 0.0,
                tremor_detected: false,
                status: "INSUFFICIENT_QUALITY"
            });
            return;
        }

        freqTimeline.timestamps.shift();
        freqTimeline.timestamps.push(timeStr);

        freqTimeline.frequencies.shift();
        freqTimeline.frequencies.push(currentDominantFreq);

        freqTimeline.amplitudes.shift();
        freqTimeline.amplitudes.push(currentRmsAmp);

        const peakBadge = document.getElementById("psdPeakBadge");
        if (peakBadge) {
            peakBadge.innerText = currentDominantFreq > 0 ? `Peak: ${currentDominantFreq.toFixed(1)} Hz` : "Peak: -- Hz";
        }

    }, 1000);
}

// 60FPS Master Canvas Rendering Loop (Oscilloscope, Frequency vs Time, PSD, Waterfall)
function startMasterRenderLoop() {
    function renderFrame() {
        simWavePhase += 0.12;

        renderOscilloscope();
        renderFreqTimeCanvas();
        renderPsdCanvas();
        renderSpectrogramWaterfall();

        animationFrameId = requestAnimationFrame(renderFrame);
    }
    renderFrame();
}

// 1. Oscilloscope Renderer (Dynamic Auto-Gain, Multi-Mode, & Real-Time Instrumentation)
function renderOscilloscope() {
    const cvs = document.getElementById("liveStreamCanvas");
    if (!cvs) return;
    const c = cvs.getContext("2d");
    const w = cvs.width;
    const h = cvs.height;
    const halfH = h / 2;

    // Track RAF Rendering FPS
    renderFrameCounter++;
    const now = performance.now();
    if (now - lastRenderFpsTime >= 1000) {
        renderFps = Math.round(renderFrameCounter / ((now - lastRenderFpsTime) / 1000.0));
        renderFrameCounter = 0;
        lastRenderFpsTime = now;
        const hudFps = document.getElementById("hudRenderFps");
        if (hudFps) hudFps.innerText = `RENDER: ${renderFps} FPS`;
    }

    const N = MAX_STREAM_POINTS;
    const stepX = w / (N - 1);
    const isLiveStreaming = (now - lastPacketTime) < 2000;

    // Build or evaluate active signal buffer based on mode
    let activeSignal = [];
    let curPlottedVal = 0.0;

    if (scopeMode === "SINE_TEST") {
        // Mode 1: Synthetic Sine Test (Prism-clean 5.0 Hz, 0.20 m/s² sine wave for isolated canvas verification)
        const tSec = now / 1000.0;
        for (let i = 0; i < N; i++) {
            const ptTime = tSec - (N - 1 - i) * 0.02; // 50Hz sample spacing = 0.02s
            const sVal = 0.20 * Math.sin(2 * Math.PI * 5.0 * ptTime);
            activeSignal.push(sVal);
        }
        curPlottedVal = activeSignal[N - 1];
        latestPlottedSample = curPlottedVal;
    } else if (scopeMode === "DYNAMIC") {
        // Mode 2: Primary Filtered Dynamic Acceleration (Zero-mean tremor oscillation)
        activeSignal = streamData.dynSignal;
        curPlottedVal = activeSignal[N - 1] !== undefined ? activeSignal[N - 1] : 0.0;
        latestPlottedSample = curPlottedVal;
    } else {
        // Mode 3: 3-Axis IMU (Detrended Ax, Ay, Az)
        activeSignal = streamData.dynSignal;
        curPlottedVal = activeSignal[N - 1] !== undefined ? activeSignal[N - 1] : 0.0;
        latestPlottedSample = curPlottedVal;
    }

    // Dynamic Y-Axis Scaling (Auto-Gain)
    // Inspect recent buffer to calculate envelope
    let peakAbs = 0.0;
    if (scopeMode === "SINE_TEST") {
        peakAbs = 0.20;
    } else if (scopeMode === "AXES") {
        for (let i = Math.max(0, N - 60); i < N; i++) {
            const vDyn = Math.abs(streamData.dynSignal[i] || 0);
            const vAx = Math.abs(streamData.ax[i] || 0);
            const vAy = Math.abs(streamData.ay[i] || 0);
            const vAz = Math.abs((streamData.az[i] || 9.81) - 9.81);
            peakAbs = Math.max(peakAbs, vDyn, vAx, vAy, vAz);
        }
    } else {
        for (let i = Math.max(0, N - 60); i < N; i++) {
            const v = Math.abs(activeSignal[i] || 0);
            if (v > peakAbs) peakAbs = v;
        }
    }

    if (scopeGainMode === "AUTO") {
        // Adaptive scaling with headroom, clamped between 0.08 m/s² and 2.50 m/s²
        const targetYMax = Math.max(0.08, Math.min(2.50, peakAbs * 1.45));
        smoothedYMax = smoothedYMax * 0.95 + targetYMax * 0.05;
    } else {
        smoothedYMax = parseFloat(scopeGainMode) || 0.25;
    }

    // Visualization Gain (px per m/s²) - Visual transformation only; preserves raw telemetry numbers
    const visualGain = (halfH * 0.82) / Math.max(0.01, smoothedYMax);

    // Clear Canvas
    c.fillStyle = "#ffffff";
    c.fillRect(0, 0, w, h);

    // Vertical Time Gridlines (every 60px)
    c.strokeStyle = "rgba(226, 232, 240, 0.7)";
    c.lineWidth = 1;
    for (let x = 0; x < w; x += 60) {
        c.beginPath();
        c.moveTo(x, 0);
        c.lineTo(x, h);
        c.stroke();
    }

    // Horizontal Scale Gridlines
    const yTop = halfH - (smoothedYMax * visualGain);
    const yBot = halfH + (smoothedYMax * visualGain);
    const yMidTop = halfH - (smoothedYMax * 0.5 * visualGain);
    const yMidBot = halfH + (smoothedYMax * 0.5 * visualGain);

    c.strokeStyle = "rgba(241, 245, 249, 1.0)";
    c.setLineDash([4, 4]);
    c.beginPath(); c.moveTo(0, yMidTop); c.lineTo(w, yMidTop); c.stroke();
    c.beginPath(); c.moveTo(0, yMidBot); c.lineTo(w, yMidBot); c.stroke();
    c.setLineDash([]);

    c.strokeStyle = "rgba(203, 213, 225, 0.8)";
    c.beginPath(); c.moveTo(0, yTop); c.lineTo(w, yTop); c.stroke();
    c.beginPath(); c.moveTo(0, yBot); c.lineTo(w, yBot); c.stroke();

    // Center Zero Baseline (Darker solid line)
    c.strokeStyle = "rgba(100, 116, 139, 0.4)";
    c.lineWidth = 1.2;
    c.beginPath();
    c.moveTo(0, halfH);
    c.lineTo(w, halfH);
    c.stroke();

    // Left Y-Axis Scale Markers
    c.fillStyle = "#64748b";
    c.font = "9px JetBrains Mono, monospace";
    c.fillText(`+${smoothedYMax.toFixed(2)} m/s²`, 8, Math.max(14, yTop + 10));
    c.fillText(` 0.00 m/s²`, 8, halfH - 4);
    c.fillText(`-${smoothedYMax.toFixed(2)} m/s²`, 8, Math.min(h - 6, yBot - 4));

    // Render Waveforms According to Mode
    if (scopeMode === "AXES") {
        // Helper to draw single axis centered around zero baseline
        const drawAxis = (arr, color, dcOffset, lw = 1.3) => {
            c.strokeStyle = color;
            c.lineWidth = lw;
            c.beginPath();
            for (let i = 0; i < N; i++) {
                let v = arr[i] !== undefined ? arr[i] : 0.0;
                v = v - dcOffset;
                const x = i * stepX;
                const y = halfH - (v * visualGain);
                if (i === 0) c.moveTo(x, y);
                else c.lineTo(x, y);
            }
            c.stroke();
        };

        // Draw 3 detrended axes
        drawAxis(streamData.ax, "rgba(244, 63, 94, 0.8)", 0.0);
        drawAxis(streamData.ay, "rgba(16, 185, 129, 0.8)", 0.0);
        drawAxis(streamData.az, "rgba(99, 102, 241, 0.8)", 9.81);

        // Legend for Axes Mode
        c.font = "bold 10px Inter, sans-serif";
        c.fillStyle = "rgba(244, 63, 94, 1)"; c.fillText("Ax", w - 120, 20);
        c.fillStyle = "rgba(16, 185, 129, 1)"; c.fillText("Ay", w - 90, 20);
        c.fillStyle = "rgba(99, 102, 241, 1)"; c.fillText("Az (detrended)", w - 60, 20);

    } else {
        // DYNAMIC or SINE_TEST Mode
        const strokeColor = scopeMode === "SINE_TEST" ? "#d97706" : "#2563eb";
        const fillColor = scopeMode === "SINE_TEST" ? "rgba(217, 119, 6, 0.08)" : "rgba(37, 99, 235, 0.08)";

        // 1. Shaded Area Under Curve to Zero Baseline
        c.fillStyle = fillColor;
        c.beginPath();
        c.moveTo(0, halfH);
        for (let i = 0; i < N; i++) {
            const v = activeSignal[i] !== undefined ? activeSignal[i] : 0.0;
            const x = i * stepX;
            const y = halfH - (v * visualGain);
            c.lineTo(x, y);
        }
        c.lineTo(w, halfH);
        c.closePath();
        c.fill();

        // 2. Continuous Waveform Trace Line
        c.strokeStyle = strokeColor;
        c.lineWidth = 2.2;
        c.lineJoin = "round";
        c.lineCap = "round";
        c.beginPath();
        for (let i = 0; i < N; i++) {
            const v = activeSignal[i] !== undefined ? activeSignal[i] : 0.0;
            const x = i * stepX;
            const y = halfH - (v * visualGain);
            if (i === 0) c.moveTo(x, y);
            else c.lineTo(x, y);
        }
        c.stroke();

        // 3. Leading Head Marker (Pulse Dot on latest sample)
        const lastX = (N - 1) * stepX;
        const lastY = halfH - (curPlottedVal * visualGain);
        c.fillStyle = strokeColor;
        c.beginPath();
        c.arc(lastX, lastY, 4, 0, Math.PI * 2);
        c.fill();

        c.strokeStyle = "#ffffff";
        c.lineWidth = 1.5;
        c.beginPath();
        c.arc(lastX, lastY, 4, 0, Math.PI * 2);
        c.stroke();
    }

    // Top Canvas Diagnostic Badge
    c.fillStyle = "#0f172a";
    c.font = "bold 10px JetBrains Mono, monospace";
    const modeLabel = scopeMode === "SINE_TEST" ? "SYNTHETIC SINE TEST (5.0 Hz, 0.20 m/s²)" : (scopeMode === "AXES" ? "3-AXIS IMU (DETRENDED)" : "FILTERED DYNAMIC ACCELERATION");
    c.fillText(`${modeLabel} | LATEST: ${(curPlottedVal >= 0 ? '+' : '') + curPlottedVal.toFixed(3)} m/s²`, w - 380, 20);

    // Update Real-Time Instrumentation HUD Overlay
    const hudSeq = document.getElementById("hudPacketSeq");
    if (hudSeq) hudSeq.innerText = `SEQ: #${lastPacketSeq !== null ? lastPacketSeq : (isSimulating ? totalPacketsReceived : '--')}`;

    const hudBuf = document.getElementById("hudBufferLen");
    if (hudBuf) hudBuf.innerText = `BUF: ${activeSignal.length} pts (6.0s)`;

    const hudPlotted = document.getElementById("hudPlottedSample");
    if (hudPlotted) hudPlotted.innerText = `LATEST: ${(curPlottedVal >= 0 ? '+' : '') + curPlottedVal.toFixed(3)} m/s²`;

    const hudY = document.getElementById("hudYRange");
    if (hudY) hudY.innerText = `Y-RANGE: ±${smoothedYMax.toFixed(2)} m/s² (Gain: ${Math.round(visualGain)} px/m/s²)`;
}

// 2. Continuous Tremor Frequency vs. Clock Timestamp Canvas
function renderFreqTimeCanvas() {
    const cvs = document.getElementById("freqTimeCanvas");
    if (!cvs) return;
    const c = cvs.getContext("2d");
    const w = cvs.width;
    const h = cvs.height;

    c.fillStyle = "#ffffff";
    c.fillRect(0, 0, w, h);

    const padLeft = 45;
    const padBottom = 30;
    const plotW = w - padLeft - 20;
    const plotH = h - padBottom - 20;

    // Y Axis Grid (0 to 12 Hz)
    c.strokeStyle = "#f1f5f9";
    c.lineWidth = 1;
    c.fillStyle = "#64748b";
    c.font = "10px Inter, sans-serif";

    for (let f = 0; f <= 12; f += 2) {
        const y = h - padBottom - (f / 12.0) * plotH;
        c.beginPath(); c.moveTo(padLeft, y); c.lineTo(w - 20, y); c.stroke();
        c.fillText(`${f} Hz`, 10, y + 3);
    }

    // Shaded Parkinsonian Band (3.5 - 7.5 Hz)
    const yTopBand = h - padBottom - (7.5 / 12.0) * plotH;
    const yBotBand = h - padBottom - (3.5 / 12.0) * plotH;
    c.fillStyle = "rgba(217, 119, 6, 0.08)";
    c.fillRect(padLeft, yTopBand, plotW, yBotBand - yTopBand);

    c.strokeStyle = "#d97706";
    c.setLineDash([3, 3]);
    c.beginPath(); c.moveTo(padLeft, yTopBand); c.lineTo(w - 20, yTopBand); c.stroke();
    c.beginPath(); c.moveTo(padLeft, yBotBand); c.lineTo(w - 20, yBotBand); c.stroke();
    c.setLineDash([]);

    c.fillStyle = "#b45309";
    c.font = "bold 9px Inter, sans-serif";
    c.fillText("Diagnostic Parkinsonian Band (3.5 – 7.5 Hz)", padLeft + 15, yTopBand + 14);

    // Plot Frequency Curve
    const N = freqTimeline.frequencies.length;
    if (N < 2) return;

    const stepX = plotW / (N - 1);
    c.strokeStyle = "#2563eb";
    c.lineWidth = 2.5;
    c.beginPath();

    for (let i = 0; i < N; i++) {
        const f = freqTimeline.frequencies[i];
        const x = padLeft + i * stepX;
        const y = h - padBottom - (f / 12.0) * plotH;
        if (i === 0) c.moveTo(x, y);
        else c.lineTo(x, y);
    }
    c.stroke();

    // Plot Point Markers & X Time Labels
    for (let i = 0; i < N; i++) {
        const f = freqTimeline.frequencies[i];
        const x = padLeft + i * stepX;
        const y = h - padBottom - (f / 12.0) * plotH;

        c.fillStyle = "#2563eb";
        c.beginPath(); c.arc(x, y, 4.5, 0, Math.PI * 2); c.fill();

        // X Time Label (Every 3 points)
        if (i % 3 === 0 || i === N - 1) {
            c.fillStyle = "#64748b";
            c.font = "9px Inter, sans-serif";
            c.fillText(freqTimeline.timestamps[i] || "", x - 18, h - 10);
        }
    }

    // Active Pulse Indicator on Latest Point
    const lastX = padLeft + (N - 1) * stepX;
    const lastY = h - padBottom - (freqTimeline.frequencies[N - 1] / 12.0) * plotH;
    c.strokeStyle = "rgba(37, 99, 235, 0.5)";
    c.lineWidth = 2;
    c.beginPath(); c.arc(lastX, lastY, 7 + Math.sin(simWavePhase * 2) * 2, 0, Math.PI * 2); c.stroke();
}

// 3. Power Spectral Density (PSD) Canvas Renderer
function renderPsdCanvas() {
    const cvs = document.getElementById("psdCanvas");
    if (!cvs) return;
    const c = cvs.getContext("2d");
    const w = cvs.width;
    const h = cvs.height;

    c.fillStyle = "#ffffff";
    c.fillRect(0, 0, w, h);

    const padLeft = 35;
    const padBottom = 25;
    const plotW = w - padLeft - 15;
    const plotH = h - padBottom - 15;

    // Grid & Axis
    c.strokeStyle = "#f1f5f9";
    c.lineWidth = 1;
    c.fillStyle = "#64748b";
    c.font = "9px Inter, sans-serif";

    for (let f = 0; f <= 15; f += 3) {
        const x = padLeft + (f / 15.0) * plotW;
        c.beginPath(); c.moveTo(x, 15); c.lineTo(x, h - padBottom); c.stroke();
        c.fillText(`${f}Hz`, x - 8, h - 8);
    }

    // Shaded 3.5 - 7.5 Hz Band
    const xBandStart = padLeft + (3.5 / 15.0) * plotW;
    const xBandEnd = padLeft + (7.5 / 15.0) * plotW;
    c.fillStyle = "rgba(37, 99, 235, 0.08)";
    c.fillRect(xBandStart, 15, xBandEnd - xBandStart, plotH);

    // Draw Smooth Area Under PSD Curve
    const freqs = psdCurrent.freqs;
    const vals = psdCurrent.values;
    const maxVal = 5.5;

    c.beginPath();
    c.moveTo(padLeft, h - padBottom);
    for (let i = 0; i < freqs.length; i++) {
        const x = padLeft + (freqs[i] / 15.0) * plotW;
        const y = h - padBottom - (vals[i] / maxVal) * plotH;
        c.lineTo(x, y);
    }
    c.lineTo(padLeft + plotW, h - padBottom);
    c.closePath();

    c.fillStyle = "rgba(37, 99, 235, 0.15)";
    c.fill();

    // Outline Curve
    c.strokeStyle = "#2563eb";
    c.lineWidth = 2.2;
    c.beginPath();
    for (let i = 0; i < freqs.length; i++) {
        const x = padLeft + (freqs[i] / 15.0) * plotW;
        const y = h - padBottom - (vals[i] / maxVal) * plotH;
        if (i === 0) c.moveTo(x, y);
        else c.lineTo(x, y);
    }
    c.stroke();

    // Peak Pin at Dominant Freq
    const peakX = padLeft + (currentDominantFreq / 15.0) * plotW;
    const peakY = h - padBottom - (4.5 / maxVal) * plotH;
    c.fillStyle = "#2563eb";
    c.beginPath(); c.arc(peakX, peakY, 4.5, 0, Math.PI * 2); c.fill();
}

// 4. Waterfall Spectrogram (STFT Heatmap) Canvas Renderer
function renderSpectrogramWaterfall() {
    const cvs = document.getElementById("spectrogramCanvas");
    if (!cvs) return;
    const c = cvs.getContext("2d");
    const w = cvs.width;
    const h = cvs.height;

    c.fillStyle = "#ffffff";
    c.fillRect(0, 0, w, h);

    const padLeft = 30;
    const padBottom = 20;
    const plotW = w - padLeft - 10;
    const plotH = h - padBottom - 10;

    const numCols = specWaterfall.length;
    const colW = plotW / numCols;
    const binH = plotH / SPEC_BINS;

    for (let ci = 0; ci < numCols; ci++) {
        const colData = specWaterfall[ci];
        const x = padLeft + ci * colW;
        for (let bi = 0; bi < SPEC_BINS; bi++) {
            const intensity = colData[bi];
            const y = h - padBottom - (bi + 1) * binH;

            // Medical Heat Color Map
            let r, g, b;
            if (intensity < 0.25) {
                const t = intensity / 0.25;
                r = Math.floor(15 + t * 40);
                g = Math.floor(25 + t * 90);
                b = Math.floor(80 + t * 155);
            } else if (intensity < 0.6) {
                const t = (intensity - 0.25) / 0.35;
                r = Math.floor(55 + t * 180);
                g = Math.floor(115 + t * 120);
                b = Math.floor(235 - t * 160);
            } else {
                const t = (intensity - 0.6) / 0.4;
                r = Math.floor(235 + t * 20);
                g = Math.floor(235 - t * 160);
                b = Math.floor(75 - t * 50);
            }

            c.fillStyle = `rgb(${r},${g},${b})`;
            c.fillRect(x, y, colW + 0.6, binH + 0.6);
        }
    }

    // Frequency Tick Marks on Left
    c.fillStyle = "#64748b";
    c.font = "9px Inter, sans-serif";
    c.fillText("15Hz", 4, 18);
    c.fillText("7.5", 6, h / 2);
    c.fillText("0Hz", 4, h - padBottom - 2);
}

// Medication Management API
async function fetchPatientMedications(patientId) {
    try {
        const res = await fetch(`/api/patients/${patientId}/medications`);
        const meds = await res.json();
        const container = document.getElementById("medicationsListContainer");
        container.innerHTML = "";

        if (meds.length === 0) {
            container.innerHTML = `<div class="text-xs text-slate-500 italic p-4 text-center bg-slate-50 rounded-xl border border-slate-200">No active medications logged for this patient. Click 'Add Prescribed Medication' above.</div>`;
            return;
        }

        meds.forEach(m => {
            const card = document.createElement("div");
            card.className = "p-4 bg-slate-50 border border-slate-200 rounded-xl flex flex-wrap items-center justify-between gap-4 hover:border-slate-300 transition-all";
            
            let statusBadge = `<span class="px-2.5 py-0.5 rounded text-[10px] font-semibold bg-slate-100 text-slate-700 border border-slate-300">${m.time_since_dose || 'No recorded dose'}</span>`;

            card.innerHTML = `
                <div class="space-y-1">
                    <div class="flex items-center gap-2">
                        <h4 class="font-bold text-slate-900 text-sm">${m.name}</h4>
                        <span class="text-xs font-mono text-blue-700 font-semibold">(${m.dosage})</span>
                        ${statusBadge}
                    </div>
                    <p class="text-xs text-slate-600">${m.notes || 'Prescribed dose'}</p>
                    <div class="flex items-center gap-4 text-xs text-slate-600 pt-0.5">
                        <span>Scheduled: <strong class="text-slate-900">${m.scheduled_time}</strong></span>
                        <span>Last Taken: <strong class="text-blue-700 font-mono">${m.time_since_dose || 'Not logged today'}</strong></span>
                    </div>
                </div>
                <button onclick="logMedicationDose(${m.medication_id})" class="flex items-center gap-1.5 bg-white hover:bg-blue-50 text-blue-700 border border-blue-200 text-xs font-semibold px-4 py-2 rounded-lg shadow-sm active:scale-95 transition-all">
                    <i data-lucide="check-circle" class="w-4 h-4 text-blue-600"></i>
                    <span>Log Dose Taken Now</span>
                </button>
            `;
            container.appendChild(card);
        });

        if (typeof lucide !== 'undefined') lucide.createIcons();

        if (meds.length > 0 && meds[0].last_taken_at) {
            document.getElementById("medCurrentStatusText").innerText = meds[0].time_since_dose || "Recorded Dose";
            document.getElementById("doctorLiveMedState").innerText = meds[0].time_since_dose ? "Dose Logged" : "No Dose";
        }
    } catch (e) {}
}

async function logMedicationDose(medId) {
    try {
        const res = await fetch(`/api/medications/${medId}/take`, { method: "POST" });
        if (res.ok) {
            speakVoice("Medication dose logged successfully.");
            await fetchPatientMedications(activePatientId);
        }
    } catch (e) {}
}

async function saveNewMedication() {
    const name = document.getElementById("inpMedName").value.trim();
    const dosage = document.getElementById("inpMedDosage").value.trim();
    const scheduled_time = document.getElementById("inpMedTime").value.trim();
    const notes = document.getElementById("inpMedNotes").value.trim();

    if (!name) {
        alert("Please enter the medication name.");
        return;
    }

    try {
        const res = await fetch(`/api/patients/${activePatientId}/medications`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, dosage, scheduled_time, notes })
        });
        if (res.ok) {
            document.getElementById("modalAddMedication").classList.add("hidden");
            await fetchPatientMedications(activePatientId);
        }
    } catch (e) {}
}

// Patients API
async function fetchPatients() {
    try {
        const res = await fetch("/api/patients");
        const patients = await res.json();
        const select = document.getElementById("patientSelect");
        select.innerHTML = "";
        patients.forEach(p => {
            const opt = document.createElement("option");
            opt.value = p.patient_id;
            opt.innerText = `${p.patient_id}: ${p.name} (${p.age || 'N/A'}y, ${p.gender || ''})`;
            select.appendChild(opt);
        });
        if (patients.length > 0) {
            activePatientId = patients[0].patient_id;
            select.value = activePatientId;
            document.getElementById("longPatientLabel").innerText = `${patients[0].patient_id} (${patients[0].name})`;
        }
    } catch (e) {}
}

async function saveNewPatient() {
    const patient_id = document.getElementById("inpPatientId").value.trim();
    const name = document.getElementById("inpPatientName").value.trim();
    const age = parseInt(document.getElementById("inpPatientAge").value) || null;
    const gender = document.getElementById("inpPatientGender").value;

    if (!patient_id || !name) {
        alert("Please provide both Patient ID and Name.");
        return;
    }

    try {
        const res = await fetch("/api/patients", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ patient_id, name, age, gender })
        });
        if (res.ok) {
            document.getElementById("modalNewPatient").classList.add("hidden");
            await fetchPatients();
        }
    } catch (e) {}
}

// WebSocket Setup
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/dashboard`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log("[WS] Dashboard connected to server");
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleIncomingPacket(msg);
        } catch (e) {}
    };

    ws.onclose = () => {
        setTimeout(connectWebSocket, 1500);
    };
}

function sendWsCommand(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(obj));
    }
}

// Process Real-Time Incoming Sensor & DSP Stream
function handleIncomingPacket(msg) {
    if (msg.type === "INITIAL_STATE" || msg.type === "STATUS_UPDATE") {
        updatePhoneStatus(msg.phone_connected);
        if (msg.is_simulating) {
            isSimulating = true;
            updateSimulatorBadge(true, msg.simulation_profile);
        }
        if (msg.state) updateProtocolState(msg.state);
        return;
    }

    if (msg.type === "SENSOR_STREAM") {
        lastPacketTime = performance.now();
        updatePhoneStatus(msg.phone_connected);

        const s = msg.sample;
        if (s) {
            totalPacketsReceived++;
            if (s.seq !== undefined && s.seq !== null) {
                lastPacketSeq = s.seq;
            }

            const rawMag = s.raw_mag !== undefined ? s.raw_mag : Math.sqrt((s.ax || 0)**2 + (s.ay || 0)**2 + (s.az || 0)**2);
            onlineGravityDC = onlineGravityDC * 0.98 + rawMag * 0.02;

            let dynAcc = 0.0;
            if (s.dyn_acc !== undefined && s.dyn_acc !== null) {
                dynAcc = s.dyn_acc;
            } else if (s.dyn_mag !== undefined && s.dyn_mag !== null) {
                dynAcc = rawMag - onlineGravityDC;
            } else {
                dynAcc = rawMag - 9.81;
            }

            latestPlottedSample = dynAcc;

            streamData.dynSignal.shift();
            streamData.dynSignal.push(dynAcc);

            streamData.dynMag.shift();
            streamData.dynMag.push(s.dyn_mag !== undefined ? s.dyn_mag : Math.abs(dynAcc));

            streamData.ax.shift();
            streamData.ax.push(s.ax !== undefined ? s.ax : 0.0);

            streamData.ay.shift();
            streamData.ay.push(s.ay !== undefined ? s.ay : 0.0);

            streamData.az.shift();
            streamData.az.push(s.az !== undefined ? s.az : 9.81);

            sampleCount++;
            const now = performance.now();
            if (now - lastFpsTime >= 1000) {
                const fps = (sampleCount / ((now - lastFpsTime) / 1000.0)).toFixed(1);
                const fpsEl = document.getElementById("liveFpsText");
                if (fpsEl) fpsEl.innerText = `${fps} Hz`;
                sampleCount = 0;
                lastFpsTime = now;
            }
        }

        if (msg.features && Object.keys(msg.features).length > 0) {
            if (msg.features.status === "INSUFFICIENT_QUALITY") {
                currentDominantFreq = 0.0;
                currentRmsAmp = 0.0;
                currentBandRatio = 0.0;
            } else {
                currentDominantFreq = msg.features.dominant_freq !== undefined ? msg.features.dominant_freq : currentDominantFreq;
                currentRmsAmp = msg.features.rms_amp_tremor !== undefined ? msg.features.rms_amp_tremor : (msg.features.rms_amp || currentRmsAmp);
                currentBandRatio = msg.features.band_power_ratio !== undefined ? msg.features.band_power_ratio : currentBandRatio;
            }
            if (msg.features.psd_freqs && msg.features.psd_values && msg.features.psd_values.length > 0) {
                psdCurrent.freqs = msg.features.psd_freqs;
                psdCurrent.values = msg.features.psd_values;
            }
            updateFeatureCards(msg.features);
        }

        if (msg.spectrogram && msg.spectrogram.spectrogram && msg.spectrogram.spectrogram.length > 0) {
            const specMat = msg.spectrogram.spectrogram;
            const nFreqs = specMat.length;
            const nTimes = specMat[0].length;
            if (nTimes > 0) {
                const latestCol = new Float32Array(SPEC_BINS);
                for (let b = 0; b < SPEC_BINS; b++) {
                    const freqIdx = Math.min(nFreqs - 1, Math.floor((b / (SPEC_BINS - 1)) * nFreqs));
                    latestCol[b] = (specMat[freqIdx][nTimes - 1] || 0.0) / 100.0;
                }
                specWaterfall.shift();
                specWaterfall.push(latestCol);
            }
        }

        if (msg.data_quality) {
            updateDataQualityBadge(msg.data_quality);
        }

        if (msg.state) {
            updateProtocolState(msg.state);
        }
    }
}

function updateDataQualityBadge(dq) {
    const badge = document.getElementById("dataQualityBadge");
    const dot = document.getElementById("dataQualityDot");
    const text = document.getElementById("dataQualityText");
    const statusText = document.getElementById("liveQualityStatusText");
    const detailText = document.getElementById("liveQualityDetailText");

    // Session Quality & Recording Validity card in Live View
    const sqBadge = document.getElementById("sessionQualityBadge");
    const sqDetail = document.getElementById("sessionQualityDetail");
    const sqVerdict = document.getElementById("sessionQualityVerdict");

    if (!badge || !text) return;

    const hz = dq.observed_hz !== undefined ? dq.observed_hz.toFixed(1) : "50.0";
    const loss = dq.packet_loss_percent !== undefined ? dq.packet_loss_percent.toFixed(1) : "0.0";
    const jitter = dq.jitter_ms !== undefined ? dq.jitter_ms.toFixed(1) : "1.2";

    text.innerText = `Sampling: ${hz} Hz | Loss: ${loss}% | Jitter: <${Math.max(1, Math.ceil(dq.jitter_ms || 1))}ms`;

    if (dq.status === "GOOD") {
        badge.className = "flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-50 border border-emerald-200 text-xs font-mono font-bold text-emerald-800";
        dot.className = "w-2 h-2 rounded-full bg-emerald-500 animate-pulse";
        if (statusText) {
            statusText.innerText = "RELIABLE";
            statusText.className = "text-xl font-black text-emerald-700";
        }
        if (detailText) detailText.innerText = `Observed ${hz} Hz • ${loss}% loss • ${jitter}ms jitter`;

        if (sqBadge) {
            sqBadge.innerText = "RELIABLE";
            sqBadge.className = "px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-100 text-emerald-800 border border-emerald-300";
        }
        if (sqDetail) {
            sqDetail.innerText = `Empirical Sampling: ${hz} Hz • Packet Loss: ${loss}% • Inter-Sample Jitter: <${jitter} ms • Valid baseline noise floor`;
        }
        if (sqVerdict) {
            sqVerdict.innerText = "Signal Quality Validated";
            sqVerdict.className = "text-[11px] text-emerald-800 font-semibold bg-white px-3 py-1 rounded-md border border-emerald-200 shadow-xs";
        }
    } else if (dq.status === "UNSTABLE") {
        badge.className = "flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-50 border border-amber-200 text-xs font-mono font-bold text-amber-800";
        dot.className = "w-2 h-2 rounded-full bg-amber-500";
        if (statusText) {
            statusText.innerText = "UNSTABLE";
            statusText.className = "text-xl font-black text-amber-700";
        }
        if (detailText) detailText.innerText = `Elevated jitter (${jitter}ms) or packet drop (${loss}%)`;

        if (sqBadge) {
            sqBadge.innerText = "UNSTABLE";
            sqBadge.className = "px-2 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-800 border border-amber-300";
        }
        if (sqDetail) {
            sqDetail.innerText = `Observed Sampling: ${hz} Hz • Packet Loss: ${loss}% • Inter-Sample Jitter: ${jitter} ms (Jitter elevated)`;
        }
        if (sqVerdict) {
            sqVerdict.innerText = "Elevated Jitter Detected";
            sqVerdict.className = "text-[11px] text-amber-800 font-semibold bg-white px-3 py-1 rounded-md border border-amber-200 shadow-xs";
        }
    } else {
        badge.className = "flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-50 border border-rose-200 text-xs font-mono font-bold text-rose-800";
        dot.className = "w-2 h-2 rounded-full bg-rose-500";
        if (statusText) {
            statusText.innerText = "INSUFFICIENT";
            statusText.className = "text-xl font-black text-rose-700";
        }
        if (detailText) detailText.innerText = "Awaiting reliable signal to compute metrics";
        const domEl = document.getElementById("liveDomFreq");
        if (domEl) domEl.innerText = "--";
        const rmsEl = document.getElementById("liveRmsAmp");
        if (rmsEl) rmsEl.innerText = "--";
        const ratEl = document.getElementById("livePowerRatio");
        if (ratEl) ratEl.innerText = "--";
        const sevEl = document.getElementById("doctorLiveSeverity");
        if (sevEl) sevEl.innerText = "Awaiting reliable signal";

        if (sqBadge) {
            sqBadge.innerText = "INSUFFICIENT";
            sqBadge.className = "px-2 py-0.5 rounded text-[10px] font-bold bg-rose-100 text-rose-800 border border-rose-300";
        }
        if (sqDetail) {
            sqDetail.innerText = `Observed Sampling: ${hz} Hz • Insufficient sample cadence or excessive packet drop`;
        }
        if (sqVerdict) {
            sqVerdict.innerText = "Data Unreliable";
            sqVerdict.className = "text-[11px] text-rose-800 font-semibold bg-white px-3 py-1 rounded-md border border-rose-200 shadow-xs";
        }
    }
}

function updatePhoneStatus(connected) {
    phoneConnected = !!connected;
    const dot = document.getElementById("phoneStatusDot");
    const text = document.getElementById("phoneStatusText");
    if (connected) {
        dot.className = "w-2 h-2 rounded-full bg-emerald-500";
        text.innerText = "Phone Streaming (Voice on Phone)";
        text.className = "text-emerald-700 font-semibold";
    } else {
        dot.className = "w-2 h-2 rounded-full bg-slate-400";
        text.innerText = "Awaiting Phone";
        text.className = "text-slate-600";
        document.getElementById("liveFpsText").innerText = "0 Hz";
    }
}

function updateFeatureCards(f) {
    const isInsufficient = f.status === "INSUFFICIENT_QUALITY" || f.status === "INSUFFICIENT_DATA" || (!phoneConnected && !isSimulating);

    const domEl = document.getElementById("liveDomFreq");
    const rmsEl = document.getElementById("liveRmsAmp");
    const ratioEl = document.getElementById("livePowerRatio");
    const sevBadge = document.getElementById("doctorLiveSeverity");

    if (isInsufficient) {
        if (domEl) domEl.innerText = "--";
        if (rmsEl) rmsEl.innerText = "--";
        if (ratioEl) ratioEl.innerText = "--";
        if (sevBadge) {
            sevBadge.innerText = "Awaiting Signal";
            sevBadge.className = "text-xs font-bold px-3 py-1 rounded-full bg-slate-100 text-slate-500 border border-slate-200";
        }
        return;
    }

    const domF = (f.dominant_freq !== undefined ? f.dominant_freq : currentDominantFreq).toFixed(1);
    const rmsA = (f.rms_amp_tremor !== undefined ? f.rms_amp_tremor : (f.rms_amp || currentRmsAmp)).toFixed(3);
    const ratio = Math.round((f.band_power_ratio !== undefined ? f.band_power_ratio : currentBandRatio) * 100);

    if (domEl) domEl.innerText = domF;
    if (rmsEl) rmsEl.innerText = rmsA;
    if (ratioEl) ratioEl.innerText = `${ratio}%`;

    if (sevBadge) {
        if (f.tremor_detected) {
            sevBadge.innerText = "Within monitored band";
            sevBadge.className = "text-xs font-bold px-3 py-1 rounded-full bg-blue-50 text-blue-700 border border-blue-200";
        } else {
            sevBadge.innerText = "Baseline";
            sevBadge.className = "text-xs font-bold px-3 py-1 rounded-full bg-slate-100 text-slate-600";
        }
    }
}

// Protocol State Controller Sync & Voice Guidance
function updateProtocolState(st) {
    const state = st.state || "IDLE";
    const remaining = st.remaining_sec !== undefined ? st.remaining_sec : 0.0;
    const target = st.target_duration_sec || 20.0;
    const intSec = Math.max(0, Math.ceil(remaining));
    const progress = Math.min(100, Math.max(0, (1.0 - (remaining / Math.max(1.0, target))) * 100.0));

    if (state !== lastSpokenState) {
        lastSpokenState = state;
        if (state === "PREP_RESTING") {
            updateVisualDemo("RESTING");
            speakVoice("Please take your time. For Stage One, sit comfortably. Place your phone flat in your open palm, resting relaxed on your lap. We will begin in a few moments.");
        } else if (state === "RESTING") {
            updateVisualDemo("RESTING");
            speakVoice("Begin resting test. Keep your hand completely still and relaxed.");
        } else if (state === "PREP_POSTURAL") {
            updateVisualDemo("POSTURAL");
            speakVoice("Stage one is complete. For Stage Two, Postural Tremor: raise your arm and hold it straight out in front of your chest, at shoulder height. We will begin in a few moments.");
        } else if (state === "POSTURAL") {
            updateVisualDemo("POSTURAL");
            speakVoice("Begin postural test. Hold your outstretched arm steady.");
        } else if (state === "PREP_ACTION") {
            updateVisualDemo("ACTION");
            speakVoice("Stage two is complete. For Stage Three, Action Tremor: slowly and steadily bring the phone towards your nose, and then extend your arm back out. We will begin in a few moments.");
        } else if (state === "ACTION") {
            updateVisualDemo("ACTION");
            speakVoice("Begin action test. Move your arm slowly and steadily.");
        } else if (state === "COMPLETE") {
            speakVoice("Assessment complete. Processing biomechanical features and updating your clinical summary.");
        }
    }

    const pill = document.getElementById("protocolStatePill");
    pill.classList.remove("hidden");
    pill.innerText = state.replace('PREP_', 'PREP: ');

    const controllerName = document.getElementById("controllerStageName");
    const subLabel = document.getElementById("controllerSubLabel");
    const phasePill = document.getElementById("controllerPhasePill");
    
    if (state.startsWith("PREP_")) {
        const cleanName = state.replace('PREP_', '');
        controllerName.innerText = `Get Ready: ${cleanName} Test`;
        subLabel.innerText = "Preparation Countdown";
        phasePill.innerText = `PREPARING (${intSec}s)`;
        phasePill.className = "text-[11px] font-bold px-3 py-1 rounded-full bg-amber-50 text-amber-800 border border-amber-200 uppercase tracking-wider";
    } else if (state === "IDLE") {
        controllerName.innerText = "Ready to Begin Assessment";
        subLabel.innerText = "Seconds";
        phasePill.innerText = "IDLE (READY)";
        phasePill.className = "text-[11px] font-bold px-3 py-1 rounded-full bg-slate-100 text-slate-700 uppercase tracking-wider";
    } else if (state === "COMPLETE") {
        controllerName.innerText = "Assessment Complete";
        subLabel.innerText = "Finished";
        phasePill.innerText = "RECORDING DONE";
        phasePill.className = "text-[11px] font-bold px-3 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 uppercase tracking-wider";
    } else {
        controllerName.innerText = `Recording: ${state} Tremor`;
        subLabel.innerText = "Recording Countdown";
        phasePill.innerText = `RECORDING (${intSec}s)`;
        phasePill.className = "text-[11px] font-bold px-3 py-1 rounded-full bg-blue-50 text-blue-700 border border-blue-200 uppercase tracking-wider";
    }

    document.getElementById("controllerSecondsLeft").innerText = intSec;

    const circle = document.getElementById("timerProgressCircle");
    const circumference = 264;
    const offset = circumference - (progress / 100.0) * circumference;
    circle.style.strokeDashoffset = offset;

    const cardRest = document.getElementById("stepCardRest");
    const cardPost = document.getElementById("stepCardPostural");
    const cardAct = document.getElementById("stepCardAction");

    const updateCard = (card, active, prep, completed) => {
        const badge = card.querySelector(".step-badge");
        if (active) {
            card.className = "p-4 rounded-xl border-2 border-blue-600 bg-blue-50/50 shadow-sm";
            badge.className = "step-badge text-[10px] font-bold px-2 py-0.5 rounded bg-blue-600 text-white";
            badge.innerText = "RECORDING";
        } else if (prep) {
            card.className = "p-4 rounded-xl border-2 border-amber-500 bg-amber-50/40";
            badge.className = "step-badge text-[10px] font-bold px-2 py-0.5 rounded bg-amber-500 text-white";
            badge.innerText = "GET READY";
        } else if (completed) {
            card.className = "p-4 rounded-xl border border-emerald-200 bg-emerald-50/30";
            badge.className = "step-badge text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 border border-emerald-200";
            badge.innerText = "DONE";
        } else {
            card.className = "p-4 rounded-xl border border-slate-200 bg-slate-50";
            badge.className = "step-badge text-[10px] font-bold px-2 py-0.5 rounded bg-slate-200 text-slate-600";
            badge.innerText = "WAITING";
        }
    };

    updateCard(cardRest, state === "RESTING", state === "PREP_RESTING", ["PREP_POSTURAL", "POSTURAL", "PREP_ACTION", "ACTION", "COMPLETE"].includes(state));
    updateCard(cardPost, state === "POSTURAL", state === "PREP_POSTURAL", ["PREP_ACTION", "ACTION", "COMPLETE"].includes(state));
    updateCard(cardAct, state === "ACTION", state === "PREP_ACTION", state === "COMPLETE");

    if (state === "COMPLETE") {
        if (st.summaries && Object.keys(st.summaries).length > 0) {
            populateSessionSummary(st.summaries, st.session_id);
        }
        setTimeout(() => {
            fetchReplaySessions();
            fetchLongitudinalTrends(activePatientId);
        }, 800);
    }
}

function populateSessionSummary(sums, sessId) {
    document.getElementById("summarySessionId").innerText = `${sessId || 'SESS-RECENT'} • ${document.getElementById("patientSelect").options[document.getElementById("patientSelect").selectedIndex].text}`;
    document.getElementById("btnDownloadHtmlReport").href = `/api/reports/${sessId}/html`;

    const rest = sums["RESTING"] || {};
    const post = sums["POSTURAL"] || {};
    const act = sums["ACTION"] || {};

    document.getElementById("sumRestFreq").innerText = `${(rest.dominant_freq || 0).toFixed(1)} Hz`;
    document.getElementById("sumRestRms").innerText = `${(rest.rms_amp_tremor || 0).toFixed(3)} m/s²`;
    document.getElementById("sumRestRatio").innerText = `${Math.round((rest.band_power_ratio || 0) * 100)}%`;

    document.getElementById("sumPostFreq").innerText = `${(post.dominant_freq || 0).toFixed(1)} Hz`;
    document.getElementById("sumPostRms").innerText = `${(post.rms_amp_tremor || 0).toFixed(3)} m/s²`;
    document.getElementById("sumPostRatio").innerText = `${Math.round((post.band_power_ratio || 0) * 100)}%`;

    document.getElementById("sumActFreq").innerText = `${(act.dominant_freq || 0).toFixed(1)} Hz`;
    document.getElementById("sumActRms").innerText = `${(act.rms_amp_tremor || 0).toFixed(3)} m/s²`;
    document.getElementById("sumActRatio").innerText = `${Math.round((act.band_power_ratio || 0) * 100)}%`;
}

// Fetch and Render Longitudinal Trends (Tab 3)
async function fetchLongitudinalTrends(patientId) {
    try {
        const res = await fetch(`/api/analytics/longitudinal/${patientId}`);
        const data = await res.json();
        const tl = data.timeline;
        if (!tl || tl.dates.length === 0) return;

        const traces = [
            {
                name: 'Resting Tremor',
                x: tl.dates, y: tl.resting_amps,
                mode: 'lines+markers',
                line: { color: '#2563eb', width: 2.5 },
                marker: { size: 7, color: '#2563eb' }
            },
            {
                name: 'Postural Tremor',
                x: tl.dates, y: tl.postural_amps,
                mode: 'lines+markers',
                line: { color: '#10b981', width: 2, dash: 'dot' },
                marker: { size: 6, color: '#10b981' }
            },
            {
                name: 'Action Tremor',
                x: tl.dates, y: tl.action_amps,
                mode: 'lines+markers',
                line: { color: '#8b5cf6', width: 2, dash: 'dash' },
                marker: { size: 6, color: '#8b5cf6' }
            }
        ];

        // Temporal Medication Overlay Markers
        if (data.medication_events && data.medication_events.length > 0) {
            const medTrace = {
                name: 'Medication Intake',
                x: data.medication_events.map(m => m.time_str),
                y: data.medication_events.map(() => 0.05),
                mode: 'markers+text',
                text: data.medication_events.map(m => `💊 ${m.name} (${m.dose})`),
                textposition: 'top center',
                marker: { symbol: 'diamond', size: 11, color: '#d97706' }
            };
            traces.push(medTrace);
        }

        const layout = {
            paper_bgcolor: '#ffffff',
            plot_bgcolor: '#ffffff',
            margin: { l: 45, r: 15, t: 25, b: 35 },
            legend: { orientation: 'h', y: 1.15, font: { color: '#475569', size: 10, family: "'Inter', sans-serif" } },
            xaxis: { color: '#64748b', gridcolor: '#f1f5f9' },
            yaxis: { title: { text: 'RMS Amp (m/s²)', font: { size: 10, color: '#64748b', family: "'Inter', sans-serif" } }, color: '#64748b', gridcolor: '#f1f5f9' }
        };

        if (typeof Plotly !== 'undefined') {
            Plotly.newPlot('longitudinalTrendsChart', traces, layout, { displayModeBar: false, responsive: true });
        }

        // Populate Table of Recorded Motor Assessments
        const tbody = document.getElementById("historyTableBody");
        if (tbody) {
            tbody.innerHTML = "";
            const sessList = data.sessions || [];
            for (let i = sessList.length - 1; i >= 0; i--) {
                const s = sessList[i];
                const tr = document.createElement("tr");
                tr.className = "hover:bg-slate-50";
                const sev = s.severity || 'BASELINE';
                const sevBadgeClass = sev === 'MODERATE' ? 'bg-amber-50 text-amber-800 border border-amber-200' :
                                      sev === 'SEVERE' ? 'bg-rose-50 text-rose-800 border border-rose-200' :
                                      'bg-blue-50 text-blue-700 border border-blue-200';
                tr.innerHTML = `
                    <td class="py-2.5 px-3 font-semibold text-slate-800">${s.date_str || s.session_id}</td>
                    <td class="py-2.5 px-3 text-blue-700 font-bold font-mono">${s.dominant_freq ? s.dominant_freq.toFixed(1) + ' Hz' : '--'}</td>
                    <td class="py-2.5 px-3 text-slate-700 font-mono">${s.resting_amp ? s.resting_amp.toFixed(3) + ' m/s²' : '--'}</td>
                    <td class="py-2.5 px-3"><span class="px-2 py-0.5 rounded text-[10px] font-bold ${sevBadgeClass}">${sev}</span></td>
                    <td class="py-2.5 px-3 text-slate-600 font-medium text-xs">${s.time_since_medication || 'No prior dose logged'}</td>
                    <td class="py-2.5 px-3 flex items-center gap-2">
                        <button onclick="switchTab('view-replay'); loadReplaySession('${s.session_id}');" class="text-xs font-semibold px-2 py-1 rounded bg-emerald-50 text-emerald-700 border border-emerald-200 hover:bg-emerald-100 transition-all">Replay</button>
                        <a href="/api/reports/${s.session_id}/html" target="_blank" class="text-xs text-blue-600 hover:underline font-semibold">Report</a>
                    </td>
                `;
                tbody.appendChild(tr);
            }
        }
    } catch (e) {
        console.error("Failed to fetch longitudinal trends:", e);
    }
}

// --- Session Replay Engine (Tab 2) ---
let replayReadings = [];
let replaySegments = [];
let replayIndex = 0;
let replayPlaying = false;
let replaySpeed = 1.0;
let replayInterval = null;
let currentReplaySession = null;

async function fetchReplaySessions() {
    try {
        const res = await fetch(`/api/sessions?patient_id=${activePatientId}`);
        const sessions = await res.json();
        const select = document.getElementById("replaySessionSelect");
        if (!select) return;
        select.innerHTML = "";
        if (!sessions || sessions.length === 0) {
            select.innerHTML = '<option value="">No recorded sessions found</option>';
            return;
        }
        sessions.forEach((s) => {
            const opt = document.createElement("option");
            opt.value = s.session_id;
            const dateStr = s.started_at ? new Date(s.started_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : s.session_id;
            const sev = s.summary && s.summary.overall_severity ? ` • ${s.summary.overall_severity}` : "";
            opt.innerText = `${s.session_id} (${s.session_type || 'Assessment'}${sev}) — ${dateStr}`;
            select.appendChild(opt);
        });

        if (sessions.length > 0 && (!currentReplaySession || currentReplaySession.patient_id !== activePatientId)) {
            loadReplaySession(sessions[0].session_id);
        }
    } catch (e) {
        console.error("Failed to fetch replay sessions:", e);
    }
}

async function loadReplaySession(sessionId) {
    if (!sessionId) return;
    try {
        pauseReplay();
        const res = await fetch(`/api/sessions/${sessionId}/replay`);
        const data = await res.json();
        currentReplaySession = data;
        replayReadings = data.readings || [];
        replaySegments = data.segments || [];
        replayIndex = 0;

        const replaySelect = document.getElementById("replaySessionSelect");
        if (replaySelect && replaySelect.value !== sessionId) {
            replaySelect.value = sessionId;
        }

        // Update replayed stage summaries
        const restSummary = replaySegments.find(s => s.test_type === "RESTING")?.summary || {};
        const postSummary = replaySegments.find(s => s.test_type === "POSTURAL")?.summary || {};
        const actSummary = replaySegments.find(s => s.test_type === "ACTION")?.summary || {};

        const restFreqEl = document.getElementById("replayRestFreq");
        if (restFreqEl) restFreqEl.innerText = restSummary.dominant_freq ? `${restSummary.dominant_freq.toFixed(1)} Hz` : "-- Hz";
        const restRmsEl = document.getElementById("replayRestRms");
        if (restRmsEl) restRmsEl.innerText = `RMS: ${(restSummary.rms_amp_tremor || restSummary.rms_amp || 0).toFixed(3)} m/s²`;
        const restSevEl = document.getElementById("replayRestSeverity");
        if (restSevEl) restSevEl.innerText = restSummary.tremor_detected ? "ELEVATED" : "BASELINE";

        const postFreqEl = document.getElementById("replayPostFreq");
        if (postFreqEl) postFreqEl.innerText = postSummary.dominant_freq ? `${postSummary.dominant_freq.toFixed(1)} Hz` : "-- Hz";
        const postRmsEl = document.getElementById("replayPostRms");
        if (postRmsEl) postRmsEl.innerText = `RMS: ${(postSummary.rms_amp_tremor || postSummary.rms_amp || 0).toFixed(3)} m/s²`;

        const actFreqEl = document.getElementById("replayActFreq");
        if (actFreqEl) actFreqEl.innerText = actSummary.dominant_freq ? `${actSummary.dominant_freq.toFixed(1)} Hz` : "-- Hz";
        const actRmsEl = document.getElementById("replayActRms");
        if (actRmsEl) actRmsEl.innerText = `RMS: ${(actSummary.rms_amp_tremor || actSummary.rms_amp || 0).toFixed(3)} m/s²`;

        updateReplayScrubber();
        renderReplayFrame();
    } catch (e) {
        console.error("Failed to load session replay:", e);
    }
}

function toggleReplay() {
    if (replayPlaying) {
        pauseReplay();
    } else {
        startReplay();
    }
}

function startReplay() {
    if (replayReadings.length === 0) return;
    if (replayIndex >= replayReadings.length - 1) {
        replayIndex = 0;
    }
    replayPlaying = true;
    const playBtnText = document.getElementById("replayPlayBtnText");
    if (playBtnText) playBtnText.innerText = "Pause";

    clearInterval(replayInterval);
    const stepInterval = Math.max(5, Math.round(20 / replaySpeed));
    replayInterval = setInterval(() => {
        if (replayIndex < replayReadings.length - 1) {
            replayIndex++;
            updateReplayScrubber();
            renderReplayFrame();
        } else {
            pauseReplay();
        }
    }, stepInterval);
}

function pauseReplay() {
    replayPlaying = false;
    clearInterval(replayInterval);
    const playBtnText = document.getElementById("replayPlayBtnText");
    if (playBtnText) playBtnText.innerText = "Play";
}

function resetReplay() {
    pauseReplay();
    replayIndex = 0;
    updateReplayScrubber();
    renderReplayFrame();
}

function setReplaySpeed(speed) {
    replaySpeed = speed;
    const btn1x = document.getElementById("btnReplaySpeed1x");
    const btn2x = document.getElementById("btnReplaySpeed2x");
    if (btn1x && btn2x) {
        if (speed === 1) {
            btn1x.className = "px-2 py-1 rounded font-bold bg-emerald-100 text-emerald-800";
            btn2x.className = "px-2 py-1 rounded font-bold bg-white text-slate-600 border border-slate-200";
        } else {
            btn2x.className = "px-2 py-1 rounded font-bold bg-emerald-100 text-emerald-800";
            btn1x.className = "px-2 py-1 rounded font-bold bg-white text-slate-600 border border-slate-200";
        }
    }
    if (replayPlaying) {
        startReplay();
    }
}

function seekReplay(pct) {
    if (replayReadings.length === 0) return;
    replayIndex = Math.min(replayReadings.length - 1, Math.floor((pct / 100.0) * replayReadings.length));
    updateReplayScrubber();
    renderReplayFrame();
}

function updateReplayScrubber() {
    if (replayReadings.length === 0) return;
    const scrubber = document.getElementById("replayScrubber");
    const timeDisplay = document.getElementById("replayTimeDisplay");
    const pct = (replayIndex / Math.max(1, replayReadings.length - 1)) * 100;
    if (scrubber) scrubber.value = pct;

    const startT = replayReadings[0].t;
    const curT = replayReadings[replayIndex].t;
    const endT = replayReadings[replayReadings.length - 1].t;

    const curElapsedSec = Math.max(0, curT - startT);
    const totalSec = Math.max(0, endT - startT);

    const fmt = (s) => {
        const m = Math.floor(s / 60);
        const sec = Math.floor(s % 60);
        return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
    };

    if (timeDisplay) {
        timeDisplay.innerText = `Replay: ${fmt(curElapsedSec)} / ${fmt(totalSec)}`;
    }

    if (replaySegments.length > 0) {
        let activeStage = "RESTING";
        for (const seg of replaySegments) {
            if (curT >= seg.start_t && curT <= seg.end_t) {
                activeStage = seg.test_type;
                break;
            }
        }
        const badge = document.getElementById("replayActiveStageBadge");
        if (badge) {
            badge.innerText = `STAGE: ${activeStage}`;
        }

        const pillRest = document.getElementById("replayPillRest");
        const pillPost = document.getElementById("replayPillPost");
        const pillAct = document.getElementById("replayPillAct");
        if (pillRest && pillPost && pillAct) {
            pillRest.className = activeStage === "RESTING" ? "px-2.5 py-0.5 rounded-full bg-emerald-600 text-white shadow-xs font-semibold" : "px-2.5 py-0.5 rounded-full bg-slate-200 text-slate-600";
            pillPost.className = activeStage === "POSTURAL" ? "px-2.5 py-0.5 rounded-full bg-emerald-600 text-white shadow-xs font-semibold" : "px-2.5 py-0.5 rounded-full bg-slate-200 text-slate-600";
            pillAct.className = activeStage === "ACTION" ? "px-2.5 py-0.5 rounded-full bg-emerald-600 text-white shadow-xs font-semibold" : "px-2.5 py-0.5 rounded-full bg-slate-200 text-slate-600";
        }
    }
}

function renderReplayFrame() {
    const cvs = document.getElementById("replayCanvas");
    if (!cvs || replayReadings.length === 0) return;
    const c = cvs.getContext("2d");
    const w = cvs.width;
    const h = cvs.height;
    const halfH = h / 2;

    c.fillStyle = "#ffffff";
    c.fillRect(0, 0, w, h);

    // Gridlines
    c.strokeStyle = "rgba(0, 0, 0, 0.05)";
    c.lineWidth = 1;
    for (let y = 20; y < h; y += 30) {
        c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke();
    }
    for (let x = 0; x < w; x += 50) {
        c.beginPath(); c.moveTo(x, 0); c.lineTo(x, h); c.stroke();
    }
    c.strokeStyle = "rgba(0, 0, 0, 0.12)";
    c.beginPath(); c.moveTo(0, halfH); c.lineTo(w, halfH); c.stroke();

    const windowSize = 250;
    const startIdx = Math.max(0, replayIndex - windowSize);
    const windowReadings = replayReadings.slice(startIdx, replayIndex + 1);
    if (windowReadings.length < 2) return;

    const stepX = w / (windowSize - 1);
    const startXOffset = (windowSize - windowReadings.length) * stepX;

    // Dynamic auto-scaling for replay window
    let maxReplayMag = 0.1;
    for (const r of windowReadings) {
        const mag = r.filtered_mag !== undefined ? r.filtered_mag : Math.abs(Math.sqrt(r.ax * r.ax + r.ay * r.ay + r.az * r.az) - 9.81);
        if (mag > maxReplayMag) maxReplayMag = mag;
    }
    const replayScale = (halfH * 0.8) / Math.max(0.15, Math.min(2.0, maxReplayMag * 1.3));

    // Ax Trace
    c.strokeStyle = "rgba(244, 63, 94, 0.5)";
    c.lineWidth = 1.0;
    c.beginPath();
    for (let i = 0; i < windowReadings.length; i++) {
        const r = windowReadings[i];
        const x = startXOffset + i * stepX;
        const y = halfH - (r.ax * (replayScale * 0.5));
        if (i === 0) c.moveTo(x, y);
        else c.lineTo(x, y);
    }
    c.stroke();

    // Ay Trace
    c.strokeStyle = "rgba(16, 185, 129, 0.5)";
    c.beginPath();
    for (let i = 0; i < windowReadings.length; i++) {
        const r = windowReadings[i];
        const x = startXOffset + i * stepX;
        const y = halfH - (r.ay * (replayScale * 0.5));
        if (i === 0) c.moveTo(x, y);
        else c.lineTo(x, y);
    }
    c.stroke();

    // Filtered Dynamic Magnitude Trace (Centerline zero baseline)
    c.fillStyle = "rgba(37, 99, 235, 0.08)";
    c.beginPath();
    c.moveTo(startXOffset, halfH);
    for (let i = 0; i < windowReadings.length; i++) {
        const r = windowReadings[i];
        const x = startXOffset + i * stepX;
        const mag = r.filtered_mag !== undefined ? r.filtered_mag : Math.abs(Math.sqrt(r.ax * r.ax + r.ay * r.ay + r.az * r.az) - 9.81);
        const y = halfH - (mag * replayScale);
        c.lineTo(x, y);
    }
    c.lineTo(startXOffset + (windowReadings.length - 1) * stepX, halfH);
    c.closePath();
    c.fill();

    c.strokeStyle = "#2563eb";
    c.lineWidth = 2.2;
    c.beginPath();
    for (let i = 0; i < windowReadings.length; i++) {
        const r = windowReadings[i];
        const x = startXOffset + i * stepX;
        const mag = r.filtered_mag !== undefined ? r.filtered_mag : Math.abs(Math.sqrt(r.ax * r.ax + r.ay * r.ay + r.az * r.az) - 9.81);
        const y = halfH - (mag * replayScale);
        if (i === 0) c.moveTo(x, y);
        else c.lineTo(x, y);
    }
    c.stroke();

    // Instantaneous Readout
    const cur = replayReadings[replayIndex];
    c.fillStyle = "#0f172a";
    c.font = "bold 11px JetBrains Mono, monospace";
    const elapsed = (cur.t - replayReadings[0].t).toFixed(2);
    const magVal = (cur.filtered_mag !== undefined ? cur.filtered_mag : Math.abs(Math.sqrt(cur.ax*cur.ax + cur.ay*cur.ay + cur.az*cur.az) - 9.81)).toFixed(3);
    c.fillText(`REPLAY T: ${elapsed}s | SAMPLE: #${cur.seq || replayIndex} | DYN MAG: ${magVal} m/s²`, 15, 20);
}

// --- Event Logging Handlers (USER_LOGGED) ---
async function saveLogMedEvent() {
    const medName = document.getElementById("inpEventMedName")?.value.trim() || "Levodopa / Carbidopa";
    const dose = document.getElementById("inpEventMedDose")?.value.trim() || "100/25 mg";
    const notes = document.getElementById("inpEventMedNotes")?.value.trim() || "";

    try {
        const res = await fetch(`/api/patients/${activePatientId}/events/medications`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                medication_name: medName,
                dose: dose,
                timestamp: new Date().toISOString(),
                source: "USER_LOGGED",
                notes: notes
            })
        });
        if (res.ok) {
            document.getElementById("modalLogMedEvent")?.classList.add("hidden");
            speakVoice("Medication dose logged.");
            await fetchLongitudinalTrends(activePatientId);
        }
    } catch (e) {
        console.error("Failed to save medication event:", e);
    }
}

async function saveLogSympEvent() {
    const symp = document.getElementById("inpEventSympName")?.value || "Tremor";
    const severity = document.getElementById("inpEventSympSeverity")?.value || "MILD";
    const notes = document.getElementById("inpEventSympNotes")?.value.trim() || "";

    try {
        const res = await fetch(`/api/patients/${activePatientId}/events/symptoms`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                symptom: symp,
                severity: severity,
                timestamp: new Date().toISOString(),
                source: "USER_LOGGED",
                notes: notes
            })
        });
        if (res.ok) {
            document.getElementById("modalLogSympEvent")?.classList.add("hidden");
            speakVoice("Symptom observation logged.");
            await fetchLongitudinalTrends(activePatientId);
        }
    } catch (e) {
        console.error("Failed to save symptom event:", e);
    }
}
