import { OfficePanel } from "@/components/OfficePanel";
import { VoiceConsole } from "@/components/VoiceConsole";
import { AuthGate } from "@/components/AuthGate";

export default function Home() {
  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#"><span className="brand-mark">J</span><span><b>JARVIS</b><small>Personal AI Office</small></span></a>
        <nav aria-label="Primary"><a className="active" href="#voice">Voice</a><a href="#office">Office</a><a href="#tasks">Tasks</a></nav>
        <button className="profile" aria-label="Open profile"><span>TS</span><span><b>Thanesh</b><small>Owner</small></span></button>
      </header>
      <AuthGate><div className="shell">
        <section id="voice"><VoiceConsole /></section>
        <section id="office"><OfficePanel /></section>
      </div></AuthGate>
      <footer><span>Private workspace</span><span>Actions require policy checks and approval</span></footer>
    </main>
  );
}
