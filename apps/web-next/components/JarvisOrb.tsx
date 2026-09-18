import type { VoiceState } from "@/lib/contracts";

const labels: Record<VoiceState, string> = {
  idle: "Ready",
  connecting: "Connecting",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  error: "Connection error",
};

export function JarvisOrb({ state }: { state: VoiceState }) {
  return (
    <div className="orb-stage" aria-label={`Jarvis is ${labels[state].toLowerCase()}`}>
      <div className={`orb orb--${state}`}>
        <span className="orb__ring orb__ring--one" />
        <span className="orb__ring orb__ring--two" />
        <span className="orb__core" />
      </div>
      <div className="orb-state"><span className="status-dot" />{labels[state]}</div>
    </div>
  );
}
