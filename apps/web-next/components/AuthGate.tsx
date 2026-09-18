"use client";

import { FormEvent, useState } from "react";
import { apiBase, getAccessToken } from "@/lib/api";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [authenticated, setAuthenticated] = useState(() => Boolean(getAccessToken()));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const signIn = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError(null);
    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch(`${apiBase()}/auth/login`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
      });
      if (!response.ok) throw new Error(response.status === 401 ? "Email or password is incorrect." : `Sign-in failed (${response.status}).`);
      const tokens = await response.json() as { access_token: string; refresh_token: string };
      sessionStorage.setItem("jarvis_access_token", tokens.access_token);
      sessionStorage.setItem("jarvis_refresh_token", tokens.refresh_token);
      setAuthenticated(true);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Sign-in failed."); }
    finally { setBusy(false); }
  };

  if (authenticated) return children;
  return (
    <section className="signin panel">
      <p className="eyebrow">Secure workspace</p><h1>Enter your AI Office</h1>
      <p>Your credentials go directly to your configured Jarvis API.</p>
      <form onSubmit={signIn}>
        <label>Email<input name="email" type="email" autoComplete="username" required /></label>
        <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
        {error && <div className="form-error" role="alert">{error}</div>}
        <button className="mic" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </section>
  );
}
