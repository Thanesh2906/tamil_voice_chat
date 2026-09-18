import type { OfficeSnapshot } from "./contracts";

const configuredBase = process.env.NEXT_PUBLIC_JARVIS_API_URL?.replace(/\/$/, "");

export function apiBase(): string {
  if (configuredBase) return configuredBase;
  if (typeof window !== "undefined") return `${window.location.protocol}//${window.location.hostname}:8000`;
  return "http://127.0.0.1:8000";
}

export function voiceUrl(): string {
  const url = new URL(apiBase());
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/voice/session";
  return url.toString();
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem("jarvis_access_token");
}

export async function getOfficeSnapshot(signal?: AbortSignal): Promise<OfficeSnapshot> {
  const token = getAccessToken();
  if (!token) throw new Error("Sign in is required to load live office data.");
  const response = await fetch(`${apiBase()}/api/v1/office/snapshot`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw new Error(`Office API unavailable (${response.status}).`);
  return response.json() as Promise<OfficeSnapshot>;
}
