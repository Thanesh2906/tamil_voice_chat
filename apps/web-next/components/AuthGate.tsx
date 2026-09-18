"use client";

import { FormEvent, useEffect, useState } from "react";
import { ApiError, SESSION_ENDED_EVENT, getAccessToken, login, register } from "@/lib/api";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [authenticated, setAuthenticated] = useState(() => Boolean(getAccessToken()));
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const onSessionEnded = () => {
      setAuthenticated(false);
      setNotice("Your session expired. Please sign in again.");
    };
    window.addEventListener(SESSION_ENDED_EVENT, onSessionEnded);
    return () => window.removeEventListener(SESSION_ENDED_EVENT, onSessionEnded);
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "");
    const password = String(form.get("password") ?? "");
    try {
      if (mode === "signin") {
        await login(email, password);
      } else {
        const displayName = String(form.get("display_name") ?? "");
        const tenantName = String(form.get("tenant_name") ?? "");
        await register(email, password, displayName, tenantName);
      }
      setNotice(null);
      setAuthenticated(true);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  };

  if (authenticated) return children;
  return (
    <section className="signin panel">
      <p className="eyebrow">Secure workspace</p>
      <h1>{mode === "signin" ? "Enter your AI Office" : "Create your AI Office"}</h1>
      <p>Your credentials go directly to your configured Jarvis API.</p>
      {notice && <div className="form-notice">{notice}</div>}
      <form onSubmit={submit}>
        <label>
          Email<input name="email" type="email" autoComplete="username" required />
        </label>
        {mode === "signup" && (
          <>
            <label>
              Display name<input name="display_name" type="text" autoComplete="name" required maxLength={120} />
            </label>
            <label>
              Workspace name<input name="tenant_name" type="text" autoComplete="organization" required maxLength={100} />
            </label>
          </>
        )}
        <label>
          Password
          <input
            name="password"
            type="password"
            autoComplete={mode === "signin" ? "current-password" : "new-password"}
            minLength={mode === "signup" ? 12 : undefined}
            required
          />
        </label>
        {error && <div className="form-error" role="alert">{error}</div>}
        <button className="mic" disabled={busy}>
          {busy ? "Please wait…" : mode === "signin" ? "Sign in" : "Create account"}
        </button>
      </form>
      <button
        className="link-button"
        type="button"
        onClick={() => {
          setMode((current) => (current === "signin" ? "signup" : "signin"));
          setError(null);
        }}
      >
        {mode === "signin" ? "New here? Create an account" : "Already have an account? Sign in"}
      </button>
    </section>
  );
}
