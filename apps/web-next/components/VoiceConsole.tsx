"use client";

import { useEffect, useRef, useState } from "react";
import { getModels, getVoiceAccessToken, voiceUrl } from "@/lib/api";
import type { ProviderInfo, VoiceEvent, VoiceState } from "@/lib/contracts";
import { JarvisOrb } from "./JarvisOrb";
import { useWorkspace } from "./WorkspaceContext";

type Envelope = VoiceEvent & { request_id?: string; session_id?: string };
type Session = {
  generation: number; requestId: string; sessionId: string; socket: WebSocket | null;
  media?: MediaStream; context?: AudioContext; source?: MediaStreamAudioSourceNode;
  processor?: AudioWorkletNode; player?: HTMLAudioElement; urls: string[];
  finished: boolean; recording: boolean; timer?: ReturnType<typeof setTimeout>;
};

function stopCapture(session: Session) {
  session.recording = false;
  session.processor?.disconnect(); session.source?.disconnect();
  session.media?.getTracks().forEach((track) => track.stop());
  if (session.context?.state !== "closed") void session.context?.close();
  session.processor = undefined; session.source = undefined; session.media = undefined; session.context = undefined;
}

function dispose(session: Session) {
  clearTimeout(session.timer);
  stopCapture(session);
  if (session.player) { session.player.onended = null; session.player.onerror = null; session.player.pause(); session.player.src = ""; }
  session.urls.forEach(URL.revokeObjectURL); session.urls = []; session.player = undefined;
  if (session.socket) { session.socket.onmessage = null; session.socket.onclose = null; session.socket.close(); }
}

export function VoiceConsole() {
  const { projectId, projectName } = useWorkspace();
  const [state, setState] = useState<VoiceState>("idle");
  const [transcript, setTranscript] = useState("வணக்கம். Speak naturally in Tamil, English, or both.");
  const [response, setResponse] = useState("");
  const [language, setLanguage] = useState("auto");
  const [provider, setProvider] = useState("");
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [allowCloud, setAllowCloud] = useState(false);
  const [answeredBy, setAnsweredBy] = useState<{ provider: string; model: string } | null>(null);
  const active = useRef<Session | null>(null);
  const generation = useRef(0);
  const conversation = useRef<string | null>(null);

  useEffect(() => {
    const abort = new AbortController();
    getModels(abort.signal).then((data) => { setProviders(data.providers); setAllowCloud(data.allow_cloud); }).catch(() => {});
    return () => { abort.abort(); generation.current += 1; if (active.current) dispose(active.current); active.current = null; };
  }, []);

  const isCurrent = (session: Session) => active.current === session && generation.current === session.generation;
  const fail = (session: Session, detail: string) => {
    if (!isCurrent(session)) return;
    dispose(session); active.current = null; setTranscript(detail); setState("error");
  };
  const finishPlayback = (session: Session) => {
    if (isCurrent(session) && session.finished && !session.player && session.urls.length === 0) {
      dispose(session); active.current = null; setState("idle");
    }
  };
  const playNext = (session: Session) => {
    if (!isCurrent(session) || session.player) return;
    const url = session.urls[0];
    if (!url) { finishPlayback(session); return; }
    const player = new Audio(url); session.player = player; setState("speaking");
    const next = () => {
      URL.revokeObjectURL(url); session.urls.shift(); session.player = undefined;
      if (isCurrent(session)) playNext(session);
    };
    player.onended = next;
    player.onerror = () => fail(session, "Audio playback failed. Your text response is still available.");
    void player.play().catch(() => fail(session, "Your browser blocked audio playback. Your text response is still available."));
  };

  const begin = async () => {
    if (active.current) return;
    if (!navigator.mediaDevices?.getUserMedia) { setTranscript("Microphone access needs HTTPS or localhost and a supported browser."); setState("error"); return; }
    const session: Session = {
      generation: ++generation.current, requestId: crypto.randomUUID(),
      sessionId: conversation.current ?? crypto.randomUUID(), socket: null, urls: [], finished: false, recording: false,
    };
    conversation.current = session.sessionId; active.current = session;
    setState("connecting"); setResponse(""); setAnsweredBy(null);
    try {
      const token = await getVoiceAccessToken();
      if (!isCurrent(session)) return;
      const ws = new WebSocket(voiceUrl()); session.socket = ws; ws.binaryType = "arraybuffer";
      let authenticated: (() => void) | undefined;
      let ready: (() => void) | undefined;
      let rejectHandshake: ((reason: Error) => void) | undefined;
      const awaitEvent = (event: "authenticated" | "ready") => new Promise<void>((resolve, reject) => {
        rejectHandshake = reject;
        session.timer = setTimeout(() => reject(new Error("Voice service did not respond. Please try again.")), 12000);
        const done = () => { clearTimeout(session.timer); rejectHandshake = undefined; resolve(); };
        if (event === "authenticated") authenticated = done; else ready = done;
      });
      ws.onmessage = (message) => {
        if (!isCurrent(session) || typeof message.data !== "string") return;
        let event: Envelope;
        try { event = JSON.parse(message.data) as Envelope; } catch { fail(session, "Voice service sent an invalid response."); return; }
        if (event.type === "authenticated") { authenticated?.(); return; }
        if (event.request_id && event.request_id !== session.requestId) return;
        if (event.session_id && event.session_id !== session.sessionId) return;
        if (event.type === "ready") { ready?.(); return; }
        if (event.type === "partial" || event.type === "transcript") setTranscript(event.data?.text ?? "");
        if (event.type === "model" && event.data?.provider && event.data.model) setAnsweredBy({ provider: event.data.provider, model: event.data.model });
        if (event.type === "token") { if (!session.player) setState("thinking"); setResponse((text) => text + (event.data?.text ?? "")); }
        if (event.type === "audio" && event.data?.audio) {
          const hex = event.data.audio;
          if (hex.length > 16_000_000 || hex.length % 2 || !/^[0-9a-f]+$/i.test(hex)) { fail(session, "Voice service returned invalid audio."); return; }
          const bytes = Uint8Array.from(hex.match(/.{2}/g) ?? [], (pair) => parseInt(pair, 16));
          session.urls.push(URL.createObjectURL(new Blob([bytes], { type: event.data.media_type ?? "audio/wav" })));
          playNext(session);
        }
        if (event.type === "final") { if (event.data?.text) setResponse(event.data.text); session.finished = true; clearTimeout(session.timer); finishPlayback(session); }
        if (event.type === "error") { const detail = event.data?.detail ?? "Voice request failed."; rejectHandshake?.(new Error(detail)); fail(session, detail); }
      };
      ws.onclose = () => {
        if (!isCurrent(session)) return;
        rejectHandshake?.(new Error("Voice connection closed."));
        if (session.finished) finishPlayback(session); else fail(session, "Voice connection closed before the response finished.");
      };
      ws.onerror = () => { rejectHandshake?.(new Error("Cannot connect to voice service.")); fail(session, "Cannot connect to voice service."); };
      const auth = awaitEvent("authenticated");
      ws.onopen = () => { if (isCurrent(session)) ws.send(JSON.stringify({ type: "auth", access_token: token })); };
      await auth;
      if (!isCurrent(session)) return;
      const media = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      if (!isCurrent(session)) { media.getTracks().forEach((track) => track.stop()); return; }
      session.media = media;
      const context = new AudioContext({ sampleRate: 16000 }); session.context = context;
      await context.audioWorklet.addModule("/pcm-worklet.js");
      if (!isCurrent(session)) return;
      session.source = context.createMediaStreamSource(media);
      session.processor = new AudioWorkletNode(context, "pcm16-processor");
      session.processor.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (!isCurrent(session) || !session.recording || ws.readyState !== WebSocket.OPEN) return;
        if (ws.bufferedAmount >= 1024 * 1024) { fail(session, "Voice connection is too slow. Recording stopped safely; please try again."); return; }
        ws.send(event.data);
      };
      const started = awaitEvent("ready");
      ws.send(JSON.stringify({ type: "start", request_id: session.requestId, session_id: session.sessionId, sample_rate: context.sampleRate, language, provider: provider || undefined, project_id: projectId ?? undefined }));
      await started;
      if (!isCurrent(session)) return;
      session.source.connect(session.processor); session.processor.connect(context.destination);
      await context.resume();
      if (!isCurrent(session)) return;
      session.recording = true; setState("listening");
      session.timer = setTimeout(() => { if (isCurrent(session)) finish(); }, 55000);
    } catch (reason) { fail(session, reason instanceof Error ? reason.message : "Microphone could not start."); }
  };

  const finish = () => {
    const session = active.current; if (!session || !session.recording) return;
    clearTimeout(session.timer); stopCapture(session);
    if (session.socket?.readyState === WebSocket.OPEN) session.socket.send(JSON.stringify({ type: "stop", request_id: session.requestId, session_id: session.sessionId }));
    setState("thinking");
    session.timer = setTimeout(() => fail(session, "Voice response timed out. Please try again."), 120000);
  };
  const cancel = () => {
    const session = active.current; generation.current += 1; active.current = null;
    if (session) { if (session.socket?.readyState === WebSocket.OPEN) session.socket.send(JSON.stringify({ type: "barge_in" })); dispose(session); }
    setState("idle");
  };
  const busy = state !== "idle" && state !== "error";
  return (
    <section className="voice-console panel" aria-label="Voice conversation">
      <div className="voice-copy"><p className="eyebrow">Tamil + English · voice</p><h2>Talk to Jarvis</h2><p>Natural conversation, one thought at a time.</p></div>
      <JarvisOrb state={state} />
      <div className="transcript" aria-live="polite"><span>You</span><p>{transcript}</p>{response && <><span>Jarvis</span><p className="jarvis-response">{response}</p>{answeredBy && <span className="model-badge">Answered by <b>{answeredBy.provider}</b> · {answeredBy.model}</span>}</>}</div>
      <div className="voice-controls">
        <select value={language} onChange={(event) => setLanguage(event.target.value)} aria-label="Voice language" disabled={busy}><option value="auto">தமிழ் + English</option><option value="ta">தமிழ்</option><option value="en">English</option></select>
        <select value={provider} onChange={(event) => setProvider(event.target.value)} aria-label="Voice provider" disabled={busy}><option value="">Auto provider</option>{providers.filter((item) => item.configured && (item.privacy === "local" || allowCloud)).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</select>
        {state === "listening" ? <button className="mic active" onClick={finish}>Stop & send</button> : <button className="mic" onClick={() => void begin()} disabled={busy}>Start voice</button>}
        {busy && <button className="quiet-button" onClick={cancel}>Cancel</button>}
      </div>
      <p className="muted voice-boundary">{projectName ? `Project context: ${projectName}. ` : ""}Voice is conversational; use text for approved tool actions. Malaysian Tamil voice quality needs device testing.</p>
    </section>
  );
}
