"use client";

import { FormEvent, useEffect, useState, useSyncExternalStore } from "react";
import {
  ApiError,
  AUTH_CHANGED_EVENT,
  SESSION_ENDED_EVENT,
  getAccessToken,
  login,
  register,
} from "@/lib/api";
import Link from "next/link";
import { Icon } from "./Icons";

function subscribe(listener: () => void) {
  window.addEventListener(AUTH_CHANGED_EVENT, listener);
  window.addEventListener(SESSION_ENDED_EVENT, listener);
  return () => {
    window.removeEventListener(AUTH_CHANGED_EVENT, listener);
    window.removeEventListener(SESSION_ENDED_EVENT, listener);
  };
}
const clientSnapshot = () => (getAccessToken() ? "signed-in" : "signed-out");
const serverSnapshot = () => "checking";

export function AuthGate({ children }: { children: React.ReactNode }) {
  // The server and hydration render share a deterministic snapshot. Browser
  // storage is only consulted by React after hydration, never during SSR.
  const session = useSyncExternalStore(
    subscribe,
    clientSnapshot,
    serverSnapshot,
  );
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const ended = () => setNotice("Your session ended. Sign in to continue.");
    window.addEventListener(SESSION_ENDED_EVENT, ended);
    return () => window.removeEventListener(SESSION_ENDED_EVENT, ended);
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      if (mode === "signin")
        await login(
          String(form.get("email") ?? "").trim(),
          String(form.get("password") ?? ""),
        );
      else
        await register(
          String(form.get("email") ?? "").trim(),
          String(form.get("password") ?? ""),
          String(form.get("display_name") ?? "").trim(),
          String(form.get("tenant_name") ?? "").trim(),
        );
      setNotice(null);
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "Could not reach your Jarvis API. Check the server and try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  if (session === "signed-in") return children;
  if (session === "checking")
    return (
      <main className="session-loading">
        <span className="brand-mark">J</span>
        <p>Opening your workspace…</p>
      </main>
    );
  return (
    <main className="auth-layout">
      <section className="auth-story">
        <Link className="brand" href="/">
          <span className="brand-mark">J</span>
          <span>
            <b>JARVIS</b>
            <small>YOUR PERSONAL AI OFFICE</small>
          </span>
        </Link>
        <div className="auth-story-copy">
          <span className="overline">
            <span className="tiny-dot" /> ONE WORKSPACE. MORE POSSIBILITIES.
          </span>
          <h1>
            A little more
            <br />
            intelligence.
            <br />
            <em>A lot more you.</em>
          </h1>
          <p>
            A manager to see the big picture. Specialists to get into the
            details. Your ideas, with room to grow.
          </p>
          <div className="auth-features">
            <span>
              <Icon name="team" /> Your AI team
            </span>
            <span>
              <Icon name="shield" /> You stay in control
            </span>
            <span>
              <Icon name="globe" /> Tamil + English
            </span>
          </div>
        </div>
        <span className="auth-footer">
          Thoughtful by design. Private by default.
        </span>
      </section>
      <section className="auth-form-side">
        <div className="signin">
          <span className="auth-icon">
            <Icon name="lock" size={24} />
          </span>
          <p className="eyebrow">YOUR WORKSPACE AWAITS</p>
          <h2>{mode === "signin" ? "Welcome back." : "Make it yours."}</h2>
          <p>
            {mode === "signin"
              ? "Sign in and pick up where your ideas left off."
              : "Create a secure home for your AI team."}
          </p>
          {notice && (
            <div className="form-notice" role="status">
              {notice}
            </div>
          )}
          <form onSubmit={submit}>
            <label>
              {mode === "signin" ? "Email or username" : "Email"}
              <input
                name="email"
                type={mode === "signin" ? "text" : "email"}
                autoComplete="username"
                placeholder="you@example.com"
                required
                disabled={busy}
              />
            </label>
            {mode === "signup" && (
              <>
                <label>
                  Your name
                  <input
                    name="display_name"
                    autoComplete="name"
                    required
                    maxLength={120}
                    disabled={busy}
                  />
                </label>
                <label>
                  Workspace name
                  <input
                    name="tenant_name"
                    autoComplete="organization"
                    required
                    maxLength={100}
                    disabled={busy}
                  />
                </label>
              </>
            )}
            <label>
              Password
              <input
                name="password"
                type="password"
                autoComplete={
                  mode === "signin" ? "current-password" : "new-password"
                }
                minLength={mode === "signup" ? 12 : undefined}
                placeholder={
                  mode === "signup" ? "At least 12 characters" : "Your password"
                }
                required
                disabled={busy}
              />
            </label>
            {error && (
              <div className="form-error" role="alert">
                {error}
              </div>
            )}
            <button className="primary-button" disabled={busy}>
              {busy
                ? "Please wait…"
                : mode === "signin"
                  ? "Enter workspace"
                  : "Create workspace"}
              <Icon name="arrow" />
            </button>
          </form>
          <button
            className="link-button auth-toggle"
            type="button"
            disabled={busy}
            onClick={() => {
              setMode((current) =>
                current === "signin" ? "signup" : "signin",
              );
              setError(null);
            }}
          >
            {mode === "signin"
              ? "New here? Create an account"
              : "Already have an account? Sign in"}
          </button>
          <p className="auth-security">
            <Icon name="shield" size={14} /> Credentials go directly to your
            Jarvis API.
          </p>
        </div>
      </section>
    </main>
  );
}
