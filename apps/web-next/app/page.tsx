import { ChatConsole } from "@/components/ChatConsole";
import { OfficePanel } from "@/components/OfficePanel";
import { ProjectsPanel } from "@/components/ProjectsPanel";
import { RunsPanel } from "@/components/RunsPanel";
import { VoiceConsole } from "@/components/VoiceConsole";
import { AuthGate } from "@/components/AuthGate";
import { WorkspaceProvider } from "@/components/WorkspaceContext";

export default function Home() {
  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#"><span className="brand-mark">J</span><span><b>JARVIS</b><small>Personal AI Office</small></span></a>
        <nav aria-label="Primary"><a className="active" href="#voice">Voice</a><a href="#chat">Chat</a><a href="#office">Office</a></nav>
        <button className="profile" aria-label="Open profile"><span>TS</span><span><b>Thanesh</b><small>Owner</small></span></button>
      </header>
      <AuthGate>
        <WorkspaceProvider>
          <div className="shell">
            <section id="voice"><VoiceConsole /></section>
            <section id="office"><OfficePanel /></section>
          </div>
          <div className="chat-shell chat-shell--split">
            <section id="chat"><ChatConsole /></section>
            <div className="workspace-sidebar">
              <ProjectsPanel />
              <RunsPanel />
            </div>
          </div>
        </WorkspaceProvider>
      </AuthGate>
      <footer><span>Private workspace</span><span>Actions require policy checks and approval</span></footer>
    </main>
  );
}
