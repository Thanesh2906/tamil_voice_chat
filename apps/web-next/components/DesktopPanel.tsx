"use client";

import { useOfficeData } from "./OfficeDataContext";
import { Icon } from "./Icons";

const capabilityNames: Record<string, string> = {
  "files.list": "List files",
  "files.read_text": "Read text files",
  "files.search_text": "Search text files",
  "browser.control": "Browser control",
  "desktop.rpa": "Desktop app automation",
  "shell.execute": "Run shell commands",
};

export function DesktopPanel() {
  const { desktop, desktopError, refresh, refreshing } = useOfficeData();
  return (
    <section className="desktop-panel panel" aria-labelledby="desktop-title">
      <header className="section-heading">
        <div><p className="eyebrow">A SEPARATE ACCESS BOUNDARY</p><h2 id="desktop-title">Desktop bridge</h2></div>
        <button type="button" className="secondary-button" disabled={refreshing} onClick={() => void refresh()}><Icon name="refresh" size={15} />Refresh desktop status</button>
      </header>
      <div className="desktop-state">
        <span className="empty-icon"><Icon name="terminal" size={26} /></span>
        <div><h3>{desktopError ? "Status unavailable" : desktop ? "Disconnected · not paired" : "Checking desktop status…"}</h3><p>{desktopError ? "No active desktop session has been verified." : desktop?.reason ?? "Waiting for the server’s desktop status."}</p></div>
      </div>
      {desktopError && <p className="form-notice" role="status">{desktop ? "Last received status: disconnected. " : ""}{desktopError}</p>}
      <p className="section-description">The API connection only reaches your JARVIS server. It does not connect your computer. Connections granted to dot or another assistant do not grant JARVIS access.</p>
      <p className="section-description">This build has no desktop pairing or permission-grant flow. No machine or desktop session is attached, and desktop actions are unavailable.</p>
      {desktop && <>
        <div className="desktop-meta"><span>Foundation platform</span><strong>{desktop.platform_support.map((platform) => platform.toUpperCase()).join(", ")}</strong><span>Machine / session</span><strong>None / none</strong></div>
        <h3 className="section-label">Capability availability</h3>
        {desktopError && <p className="muted">Last received capability information</p>}
        <div className="desktop-capabilities">{desktop.capabilities.map((capability) => (
          <div className="desktop-capability" key={capability.name}>
            <span><strong>{capabilityNames[capability.name] ?? capability.name}</strong><small>{capability.name}</small></span>
            <span className="run-status">{capability.implemented ? "Built · unavailable" : "Not implemented"}</span>
          </div>
        ))}</div>
        <p className="run-safety-note">“Built” describes the isolated bridge foundation. It does not mean the server can read your computer. File access would require a separate reviewed session and explicitly allowed folders.</p>
      </>}
    </section>
  );
}
