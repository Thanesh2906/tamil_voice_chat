// Jarvis minimal web client (Phase 1).
//
// Captures mic audio with the browser MediaRecorder, decodes it into
// raw PCM frames, and pushes them over the /voice/session WebSocket.
// Token / transcript / audio events update the UI in place.

const SAMPLE_RATE = 16000;
const WS_URL = `ws://${location.hostname}:8000/voice/session?token=dev`;

const statusEl = document.getElementById("status");
const transcriptEl = document.getElementById("transcript-text");
const answerEl = document.getElementById("answer-text");
const playerEl = document.getElementById("player");
const pttEl = document.getElementById("ptt");

let ws = null;
let mediaStream = null;
let audioCtx = null;
let sourceNode = null;
let pcmBuf = [];

function setStatus(s) {
  statusEl.textContent = s;
}

function connect() {
  ws = new WebSocket(WS_URL);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => setStatus("ready");
  ws.onclose = () => {
    setStatus("disconnected");
    setTimeout(connect, 1500);
  };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "ready") setStatus(`ready (${m.data?.language ?? "ta"})`);
    if (m.type === "partial" || m.type === "transcript") {
      transcriptEl.textContent = m.data?.text ?? transcriptEl.textContent;
    }
    if (m.type === "token") {
      answerEl.textContent += m.data?.text ?? "";
    }
    if (m.type === "audio") {
      playerEl.hidden = false;
      const bytes = new Uint8Array(
        (m.data?.hex ?? "").match(/.{1,2}/g).map((b) => parseInt(b, 16))
      );
      const blob = new Blob([bytes], { type: "audio/wav" });
      playerEl.src = URL.createObjectURL(blob);
      playerEl.play();
    }
    if (m.type === "final") {
      answerEl.textContent += "\n";
    }
  };
}

async function startTalking() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  mediaStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, sampleRate: SAMPLE_RATE } });
  audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
  sourceNode = audioCtx.createMediaStreamSource(mediaStream);
  const processor = audioCtx.createScriptProcessor(4096, 1, 1);
  sourceNode.connect(processor);
  processor.connect(audioCtx.destination);
  processor.onaudioprocess = (e) => {
    const f32 = e.inputBuffer.getChannelData(0);
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) i16[i] = Math.max(-32768, Math.min(32767, f32[i] * 32768));
    ws.send(i16.buffer);
  };
  ws.send(JSON.stringify({ type: "start" }));
}

function stopTalking() {
  ws?.send(JSON.stringify({ type: "stop" }));
  mediaStream?.getTracks().forEach((t) => t.stop());
  audioCtx?.close();
  mediaStream = null;
  audioCtx = null;
  sourceNode = null;
}

pttEl.addEventListener("pointerdown", startTalking);
pttEl.addEventListener("pointerup", stopTalking);
pttEl.addEventListener("pointerleave", stopTalking);

connect();
