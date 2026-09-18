const SAMPLE_RATE = 16000;
const API_BASE = globalThis.JARVIS_API_URL || `${location.protocol}//${location.hostname}:8000`;
const WS_BASE = API_BASE.replace(/^http/, "ws");

const statusEl = document.getElementById("status");
const transcriptEl = document.getElementById("transcript-text");
const answerEl = document.getElementById("answer-text");
const playerEl = document.getElementById("player");
const pttEl = document.getElementById("ptt");
const projectEl = document.getElementById("project");
const citationsEl = document.getElementById("citations");
const loginEl = document.getElementById("login-form");
const voiceEl = document.getElementById("voice");
const logoutEl = document.getElementById("logout");
const cancelEl = document.getElementById("cancel");

let ws;
let accessToken;
let refreshToken;
let mediaStream;
let audioCtx;
let sourceNode;
let processor;
let reconnectAttempt = 0;
let playbackUrl;
let sessionId = crypto.randomUUID();

function setStatus(value) { statusEl.textContent = value; }
function authHeaders() { return { Authorization: `Bearer ${accessToken}` }; }

async function refreshAccess() {
  if (!refreshToken) return false;
  const response = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!response.ok) return false;
  const tokens = await response.json();
  accessToken = tokens.access_token;
  refreshToken = tokens.refresh_token;
  return true;
}

async function loadProjects() {
  let response = await fetch(`${API_BASE}/projects`, { headers: authHeaders() });
  if (response.status === 401 && await refreshAccess()) {
    response = await fetch(`${API_BASE}/projects`, { headers: authHeaders() });
  }
  if (!response.ok) return;
  for (const project of await response.json()) {
    const option = document.createElement("option");
    option.value = project.id;
    option.textContent = `${project.name} (${project.role})`;
    projectEl.append(option);
  }
}

async function connect() {
  if (!accessToken) return;
  if (reconnectAttempt > 0 && !await refreshAccess()) return logout();
  ws = new WebSocket(`${WS_BASE}/voice/session`);
  ws.onopen = () => ws.send(JSON.stringify({ type: "auth", access_token: accessToken }));
  ws.onclose = () => {
    pttEl.disabled = true;
    setStatus("disconnected");
    if (accessToken) {
      const delay = Math.min(30000, 1000 * (2 ** reconnectAttempt++));
      setTimeout(connect, delay + Math.random() * 500);
    }
  };
  ws.onerror = () => setStatus("connection error");
  ws.onmessage = async (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "authenticated") {
      reconnectAttempt = 0;
      pttEl.disabled = false;
      cancelEl.disabled = false;
      setStatus("ready");
    }
    if (["partial", "transcript"].includes(message.type)) {
      transcriptEl.textContent = message.data?.text || transcriptEl.textContent;
    }
    if (message.type === "token") answerEl.textContent += message.data?.text || "";
    if (message.type === "citation") {
      if (citationsEl.firstElementChild?.textContent === "—") citationsEl.textContent = "";
      const item = document.createElement("li");
      item.textContent = `${message.data.file_path || "source"}:${message.data.line_start || "?"}-${message.data.line_end || "?"}`;
      citationsEl.append(item);
    }
    if (message.type === "audio") {
      const hex = message.data?.audio || "";
      const pairs = hex.match(/.{1,2}/g) || [];
      const bytes = Uint8Array.from(pairs, (pair) => parseInt(pair, 16));
      if (playbackUrl) URL.revokeObjectURL(playbackUrl);
      playbackUrl = URL.createObjectURL(new Blob([bytes], { type: message.data?.media_type || "audio/wav" }));
      playerEl.hidden = false;
      playerEl.src = playbackUrl;
      await playerEl.play();
    }
    if (message.type === "error") setStatus(message.data?.detail || "error");
  };
}

loginEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("signing in");
  const response = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: document.getElementById("email").value, password: document.getElementById("password").value }),
  });
  if (!response.ok) { setStatus("sign-in failed"); return; }
  const tokens = await response.json();
  accessToken = tokens.access_token;
  refreshToken = tokens.refresh_token;
  loginEl.hidden = true;
  voiceEl.hidden = false;
  await loadProjects();
  connect();
});

function logout() {
  accessToken = refreshToken = undefined;
  ws?.close(1000, "logout");
  ws = undefined;
  loginEl.hidden = false;
  voiceEl.hidden = true;
  projectEl.replaceChildren(new Option("Personal (no RAG)", ""));
  setStatus("signed out");
}

logoutEl.addEventListener("click", logout);
cancelEl.addEventListener("click", () => ws?.send(JSON.stringify({ type: "barge_in" })));

async function startTalking() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, sampleRate: SAMPLE_RATE } });
    audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
    sourceNode = audioCtx.createMediaStreamSource(mediaStream);
    await audioCtx.audioWorklet.addModule("./src/pcm-worklet.js");
    processor = new AudioWorkletNode(audioCtx, "pcm16-processor");
    sourceNode.connect(processor);
    processor.connect(audioCtx.destination);
    processor.port.onmessage = (audioEvent) => {
      if (ws.bufferedAmount < 1024 * 1024) ws.send(audioEvent.data);
    };
    answerEl.textContent = "";
    citationsEl.innerHTML = "<li>—</li>";
    ws.send(JSON.stringify({ type: "start", request_id: crypto.randomUUID(), session_id: sessionId,
      sample_rate: SAMPLE_RATE, language: "ta", project_id: projectEl.value || null }));
    setStatus("listening");
  } catch (error) { setStatus(`microphone unavailable: ${error.message}`); }
}

async function stopTalking() {
  if (!mediaStream) return;
  ws?.send(JSON.stringify({ type: "stop" }));
  processor?.disconnect();
  sourceNode?.disconnect();
  mediaStream.getTracks().forEach((track) => track.stop());
  await audioCtx?.close();
  mediaStream = audioCtx = sourceNode = processor = null;
  setStatus("thinking");
}

pttEl.addEventListener("pointerdown", startTalking);
pttEl.addEventListener("pointerup", stopTalking);
pttEl.addEventListener("pointerleave", stopTalking);
window.addEventListener("beforeunload", () => { if (playbackUrl) URL.revokeObjectURL(playbackUrl); });
