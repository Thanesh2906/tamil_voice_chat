"use client";

import { useEffect, useRef, useState } from "react";
import { getAccessToken, voiceUrl } from "@/lib/api";
import type { VoiceEvent, VoiceState } from "@/lib/contracts";
import { JarvisOrb } from "./JarvisOrb";

function eventText(event: VoiceEvent): string | undefined {
  if (event.type === "partial" || event.type === "transcript" || event.type === "token" || event.type === "final") return event.data?.text;
  return undefined;
}

export function VoiceConsole() {
  const [state, setState] = useState<VoiceState>("idle");
  const [transcript, setTranscript] = useState("வணக்கம். Press the mic and speak in Tamil or English.");
  const [response, setResponse] = useState("");
  const [language, setLanguage] = useState("auto");
  const socket = useRef<WebSocket | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const context = useRef<AudioContext | null>(null);
  const source = useRef<MediaStreamAudioSourceNode | null>(null);
  const processor = useRef<AudioWorkletNode | null>(null);

  const stopMedia = () => {
    processor.current?.disconnect(); source.current?.disconnect();
    stream.current?.getTracks().forEach((track) => track.stop());
    void context.current?.close();
    processor.current = null; source.current = null; stream.current = null; context.current = null;
  };

  useEffect(() => () => { stopMedia(); socket.current?.close(); }, []);

  const begin = async () => {
    const token = getAccessToken();
    if (!token) { setTranscript("Sign in first. The access token is never placed in the WebSocket URL."); setState("error"); return; }
    setState("connecting"); setResponse("");
    try {
      const ws = new WebSocket(voiceUrl());
      ws.binaryType = "arraybuffer";
      socket.current = ws;
      ws.onmessage = (message) => {
        if (typeof message.data !== "string") return;
        const event = JSON.parse(message.data) as VoiceEvent;
        const text = eventText(event);
        if (event.type === "partial" || event.type === "transcript") setTranscript(text ?? "");
        if (event.type === "token") { setState("thinking"); setResponse((current) => current + (text ?? "")); }
        if (event.type === "audio") {
          setState("speaking");
          const hex = event.data?.audio;
          if (hex) {
            const pairs = hex.match(/.{1,2}/g) ?? [];
            const bytes = Uint8Array.from(pairs, (pair) => Number.parseInt(pair, 16));
            const url = URL.createObjectURL(new Blob([bytes], { type: event.data?.media_type ?? "audio/wav" }));
            const player = new Audio(url);
            player.addEventListener("ended", () => URL.revokeObjectURL(url), { once: true });
            player.addEventListener("error", () => URL.revokeObjectURL(url), { once: true });
            void player.play();
          }
        }
        if (event.type === "final") { if (text) setResponse(text); setState("idle"); }
        if (event.type === "error") { setTranscript(event.data?.detail ?? "Voice request failed."); setState("error"); stopMedia(); }
      };
      ws.onclose = () => { stopMedia(); setState((current) => current === "error" ? current : "idle"); };
      await new Promise<void>((resolve, reject) => { ws.onopen = () => resolve(); ws.onerror = () => reject(new Error("Cannot connect to voice service.")); });
      ws.send(JSON.stringify({ type: "auth", access_token: token }));
      const media = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      stream.current = media;
      const audioContext = new AudioContext({ sampleRate: 16000 });
      context.current = audioContext;
      const input = audioContext.createMediaStreamSource(media);
      await audioContext.audioWorklet.addModule("/pcm-worklet.js");
      const node = new AudioWorkletNode(audioContext, "pcm16-processor");
      source.current = input; processor.current = node;
      node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (ws.readyState !== WebSocket.OPEN) return;
        if (ws.bufferedAmount < 1024 * 1024) ws.send(event.data);
      };
      input.connect(node); node.connect(audioContext.destination);
      ws.send(JSON.stringify({ type: "start", request_id: crypto.randomUUID(), session_id: crypto.randomUUID(), sample_rate: audioContext.sampleRate, language }));
      setState("listening");
    } catch (reason) {
      stopMedia(); socket.current?.close();
      setTranscript(reason instanceof Error ? reason.message : "Microphone could not start."); setState("error");
    }
  };

  const finish = () => {
    stopMedia();
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify({ type: "stop" }));
    setState("thinking");
  };

  const cancel = () => {
    stopMedia();
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify({ type: "barge_in" }));
    socket.current?.close(); setState("idle");
  };

  return (
    <section className="voice-console panel">
      <div className="voice-copy"><p className="eyebrow">Voice channel · Tamil / English</p><h1>Good morning, Thanesh.</h1><p>What are we building today?</p></div>
      <JarvisOrb state={state} />
      <div className="transcript" aria-live="polite"><span>You</span><p>{transcript}</p>{response && <><span>Jarvis</span><p className="jarvis-response">{response}</p></>}</div>
      <div className="voice-controls">
        <select value={language} onChange={(event) => setLanguage(event.target.value)} aria-label="Voice language" disabled={state !== "idle" && state !== "error"}>
          <option value="auto">Auto · தமிழ் + English</option><option value="ta">தமிழ்</option><option value="en">English</option>
        </select>
        {state === "listening" ? <button className="mic active" onClick={finish}>Stop & send</button> : <button className="mic" onClick={begin} disabled={state === "connecting" || state === "thinking" || state === "speaking"}>Start voice</button>}
        {(state === "thinking" || state === "speaking") && <button className="quiet-button" onClick={cancel}>Cancel</button>}
      </div>
    </section>
  );
}
