"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { signOut } from "@/lib/api";
import { ChatConsole } from "./ChatConsole";
import { OfficePanel } from "./OfficePanel";
import { AgentDirectory } from "./AgentDirectory";
import { ProvidersPanel } from "./ProvidersPanel";
import { ProjectsPanel } from "./ProjectsPanel";
import { DesktopPanel } from "./DesktopPanel";
import { RunsPanel } from "./RunsPanel";
import { VoiceConsole } from "./VoiceConsole";
import { useOfficeData } from "./OfficeDataContext";
import { useWorkspace } from "./WorkspaceContext";
import { Icon, agentIcon, type IconName } from "./Icons";

const navigation: {
  id: string;
  label: string;
  icon: IconName;
  title: string;
  description: string;
}[] = [
  {
    id: "workspace",
    label: "My workspace",
    icon: "office",
    title: "My workspace",
    description: "A little space for your next big idea.",
  },
  {
    id: "team",
    label: "AI team",
    icon: "team",
    title: "Meet your AI team",
    description: "Different strengths. One shared purpose.",
  },
  {
    id: "runs",
    label: "Tasks & runs",
    icon: "activity",
    title: "Work in motion",
    description: "A clear view of what happened, and what comes next.",
  },
  {
    id: "approvals",
    label: "Approvals",
    icon: "shield",
    title: "You’re in control",
    description: "A thoughtful pause before actions with consequences.",
  },
  {
    id: "projects",
    label: "Projects",
    icon: "folder",
    title: "Context makes the difference",
    description: "Give your team a place to find the right information.",
  },
  {
    id: "desktop",
    label: "Desktop access",
    icon: "terminal",
    title: "Your computer, your permission",
    description: "An honest view of the desktop bridge and its limits.",
  },
  {
    id: "providers",
    label: "Model providers",
    icon: "plug",
    title: "Your model connections",
    description: "An open office for the models you choose.",
  },
];
const readView = () => {
  const hash = window.location.hash.slice(1);
  return navigation.some((item) => item.id === hash) ? hash : "workspace";
};
function subscribeView(listener: () => void) {
  window.addEventListener("hashchange", listener);
  return () => window.removeEventListener("hashchange", listener);
}

function subscribeMobile(listener: () => void) {
  const media = window.matchMedia("(max-width: 800px)");
  media.addEventListener("change", listener);
  return () => media.removeEventListener("change", listener);
}
const mobileSnapshot = () => window.matchMedia("(max-width: 800px)").matches;

export function OfficeDashboard() {
  const view = useSyncExternalStore(subscribeView, readView, () => "workspace");
  const [agentId, setAgentId] = useState("manager");
  const [mobileOpen, setMobileOpen] = useState(false);
  const mobile = useSyncExternalStore(
    subscribeMobile,
    mobileSnapshot,
    () => false,
  );
  const sidebar = useRef<HTMLElement | null>(null);
  const { snapshot, models, user, directory, connection, desktop, desktopError } = useOfficeData();
  const { projectId, projects, setProjectId } = useWorkspace();
  const current = navigation.find((item) => item.id === view) ?? navigation[0];
  const pending = snapshot?.approvals.length ?? 0;
  const active = snapshot?.tasks.filter(
    (task) => task.status === "running",
  ).length;
  const configured = models?.providers.filter(
    (provider) => provider.configured !== false,
  ).length;
  const go = (next: string) => {
    if (readView() !== next) {
      window.history.pushState(null, "", `#${next}`);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    }
    setMobileOpen(false);
  };
  const openChat = (next: string) => {
    setAgentId(next);
    go("workspace");
  };
  const initials = (user?.display_name || user?.email || "You")
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
  useEffect(() => {
    if (!mobileOpen || !mobile) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    sidebar.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileOpen(false);
      if (event.key !== "Tab") return;
      const controls = Array.from(
        sidebar.current?.querySelectorAll<HTMLElement>(
          "a[href], button:not(:disabled), select, input",
        ) ?? [],
      ).filter((element) => element.offsetParent !== null);
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    window.addEventListener("keydown", keyboard);
    return () => {
      window.removeEventListener("keydown", keyboard);
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus();
    };
  }, [mobileOpen, mobile]);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [view]);

  return (
    <div className="app-layout">
      <a
        href="#main-content"
        className="skip-link"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        Skip to workspace
      </a>
      {mobileOpen && (
        <button
          type="button"
          className="sidebar-backdrop"
          aria-label="Close navigation"
          onClick={() => setMobileOpen(false)}
        />
      )}
      <aside
        ref={sidebar}
        className={`sidebar ${mobileOpen ? "sidebar--open" : ""}`}
        inert={mobile && !mobileOpen}
        aria-hidden={mobile && !mobileOpen ? true : undefined}
        role={mobile && mobileOpen ? "dialog" : undefined}
        aria-modal={mobile && mobileOpen ? true : undefined}
        aria-label="Workspace navigation"
      >
        <button
          type="button"
          className="sidebar-close"
          aria-label="Close menu"
          onClick={() => setMobileOpen(false)}
        >
          <Icon name="close" size={18} />
        </button>
        <a
          className="brand"
          href="#workspace"
          onClick={() => setMobileOpen(false)}
        >
          <span className="brand-mark">
            J<span />
          </span>
          <span>
            <b>JARVIS</b>
            <small>PERSONAL AI OFFICE</small>
          </span>
        </a>
        <button
          className="workspace-switch"
          type="button"
          onClick={() => go("projects")}
        >
          <span className="workspace-logo">W</span>
          <span>
            <b>Personal workspace</b>
            <small>Your private AI office</small>
          </span>
          <Icon name="chevron" size={15} />
        </button>
        <p className="nav-section-label">WORKSPACE</p>
        <nav className="main-nav" aria-label="Main navigation">
          {navigation.map((item) => (
            <a
              key={item.id}
              href={`#${item.id}`}
              aria-current={view === item.id ? "page" : undefined}
              className={view === item.id ? "active" : ""}
              onClick={() => setMobileOpen(false)}
            >
              <Icon name={item.icon} size={19} />
              <span>{item.label}</span>
              {item.id === "approvals" && pending > 0 && (
                <b className="nav-count">{pending}</b>
              )}
            </a>
          ))}
        </nav>
        <div className="sidebar-team">
          <div className="nav-section-heading">
            <p className="nav-section-label">YOUR TEAM</p>
            <span>{directory?.agents.length ?? "—"}</span>
          </div>
          {directory?.agents.map((agent) => (
            <button
              type="button"
              key={agent.id}
              onClick={() => openChat(agent.id)}
              className={
                view === "workspace" && agentId === agent.id ? "selected" : ""
              }
            >
              <span className={`mini-avatar agent-avatar--${agent.id}`}>
                <Icon name={agentIcon(agent.id)} size={14} />
              </span>
              <span>{agent.name}</span>
              <Icon name="chevron" size={13} />
            </button>
          ))}
          {!directory && (
            <p className="sidebar-hint">
              Your registered agents will appear here.
            </p>
          )}
        </div>
        <div className="sidebar-bottom">
          <div className="executor-status">
            <span className="executor-icon">
              <Icon name="terminal" size={17} />
            </span>
            <div>
              <strong>Desktop bridge</strong>
              <span>
                <i /> {desktopError ? "Status unavailable" : desktop ? "Disconnected" : "Checking status"}
              </span>
            </div>
          </div>
          <p className="executor-explanation">
            Tools run on the API server. JARVIS has no verified desktop access.
          </p>
          <button type="button" className="text-action executor-details" onClick={() => go("desktop")}>View desktop status <Icon name="arrow" size={12} /></button>
          <div className="sidebar-profile">
            <span className="profile-avatar">{initials}</span>
            <span>
              <strong>{user?.display_name || "Your account"}</strong>
              <small>{user?.email || "Private workspace"}</small>
            </span>
            <button
              type="button"
              aria-label="Sign out"
              title="Sign out"
              onClick={signOut}
            >
              <Icon name="logout" size={16} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="app-topbar">
          <div className="topbar-left">
            <button
              type="button"
              className="mobile-menu"
              aria-label="Open navigation"
              aria-expanded={mobileOpen}
              onClick={() => setMobileOpen(true)}
            >
              <Icon name="menu" />
            </button>
            <span className="breadcrumb">
              Your office <Icon name="chevron" size={12} />{" "}
              <strong>{current.label}</strong>
            </span>
          </div>
          <div className="topbar-right">
            <span className={`connection-indicator ${connection}`}>
              <span className="tiny-dot" />
              {connection === "connected"
                ? "API connected"
                : connection === "loading"
                  ? "Connecting to API"
                  : "API disconnected"}
            </span>
            <button
              type="button"
              className="approval-shortcut"
              aria-label={`Open approvals, ${pending} pending`}
              onClick={() => go("approvals")}
            >
              <Icon name="shield" size={19} />
              {pending > 0 && <span>{pending}</span>}
            </button>
            <span className="topbar-avatar">{initials}</span>
          </div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}>
          <header className="page-heading">
            <div>
              <p className="eyebrow">
                {view === "workspace"
                  ? "WELCOME TO YOUR OFFICE"
                  : "JARVIS / PERSONAL WORKSPACE"}
              </p>
              <h1>
                {view === "workspace" && user?.display_name
                  ? `Make room for what’s next, ${user.display_name.split(" ")[0]}.`
                  : current.title}
              </h1>
              <p>{current.description}</p>
            </div>
            {view === "workspace" && (
              <label className="project-picker">
                <Icon name="folder" size={16} />
                <select
                  aria-label="Active project"
                  value={projectId ?? ""}
                  onChange={(event) => setProjectId(event.target.value || null)}
                >
                  <option value="">Personal workspace</option>
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </header>
          {view === "workspace" && (
            <div className="overview-metrics">
              <button type="button" onClick={() => go("team")}>
                <span className="metric-icon metric-icon--cyan">
                  <Icon name="team" />
                </span>
                <span>
                  <small>Your AI team</small>
                  <strong>
                    {directory?.agents.length ?? "—"} <span>personas</span>
                  </strong>
                </span>
                <Icon name="chevron" size={15} />
              </button>
              <button type="button" onClick={() => go("runs")}>
                <span className="metric-icon metric-icon--purple">
                  <Icon name="activity" />
                </span>
                <span>
                  <small>
                    {connection === "disconnected"
                      ? "Last known active runs"
                      : "Active runs"}
                  </small>
                  <strong>
                    {active ?? "—"}
                    <span>
                      {active === 0 ? "a fresh canvas" : "in progress"}
                    </span>
                  </strong>
                </span>
                <Icon name="chevron" size={15} />
              </button>
              <button type="button" onClick={() => go("providers")}>
                <span className="metric-icon metric-icon--lime">
                  <Icon name="plug" />
                </span>
                <span>
                  <small>Model providers</small>
                  <strong>
                    {configured ?? "—"}
                    <span>configured</span>
                  </strong>
                </span>
                <Icon name="chevron" size={15} />
              </button>
            </div>
          )}
          <div className="workspace-grid" hidden={view !== "workspace"}>
            <ChatConsole
              agentId={agentId}
              onAgentChange={setAgentId}
              onApprovals={() => go("approvals")}
            />
            <div className="workspace-rail">
              {view === "workspace" && <VoiceConsole key={projectId ?? "personal"} />}
              <OfficePanel onApprovals={() => go("approvals")} />
            </div>
          </div>
          {view === "team" && <AgentDirectory onChat={openChat} />}
          {view === "runs" && (
            <div className="runs-view">
              <RunsPanel />
              <OfficePanel view="tasks" />
            </div>
          )}
          {view === "approvals" && <OfficePanel view="approvals" />}
          {view === "projects" && <ProjectsPanel />}
          {view === "providers" && <ProvidersPanel />}
          {view === "desktop" && <DesktopPanel />}
          <footer className="app-footer">
            <span>
              <Icon name="lock" size={12} /> Your workspace. Your decisions.
            </span>
            <span>Built for thinking clearly and doing thoughtfully.</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
