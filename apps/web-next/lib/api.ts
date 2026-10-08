import type {
  AgentsResponse,
  IngestResponse,
  ModelsResponse,
  OfficeSnapshot,
  ProjectView,
  RunOut,
  RunStreamEvent,
  RunSummary,
  TokenResponse,
  ToolInvocationOut,
  UserPublic,
} from "./contracts";

const configuredBase = process.env.NEXT_PUBLIC_JARVIS_API_URL?.replace(
  /\/$/,
  "",
);

export function apiBase(): string {
  if (configuredBase) return configuredBase;
  if (typeof window !== "undefined")
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  return "http://127.0.0.1:8000";
}

export function voiceUrl(): string {
  const url = new URL(apiBase());
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/voice/session";
  return url.toString();
}

const ACCESS_KEY = "jarvis_access_token";
const REFRESH_KEY = "jarvis_refresh_token";
export const AUTH_CHANGED_EVENT = "jarvis:auth-changed";

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(ACCESS_KEY);
}

function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(REFRESH_KEY);
}

function storeTokens(tokens: TokenResponse): void {
  window.sessionStorage.setItem(ACCESS_KEY, tokens.access_token);
  window.sessionStorage.setItem(REFRESH_KEY, tokens.refresh_token);
  window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
}

export function clearSession(): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(ACCESS_KEY);
  window.sessionStorage.removeItem(REFRESH_KEY);
}

/** Fired when the session is cleared (login expired, refresh failed, sign-out)
 * so the UI can fall back to the sign-in screen without a full page reload. */
export const SESSION_ENDED_EVENT = "jarvis:session-ended";

function announceSessionEnded(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(SESSION_ENDED_EVENT));
}

let refreshInFlight: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const response = await fetch(`${apiBase()}/auth/refresh`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (!response.ok) return false;
        // A sign-out or another sign-in may happen while refresh is pending.
        // Never restore an old session over the user’s newer decision.
        if (getRefreshToken() !== refreshToken)
          return Boolean(getAccessToken());
        storeTokens((await response.json()) as TokenResponse);
        return true;
      } catch {
        return false;
      } finally {
        refreshInFlight = null;
      }
    })();
  }
  return refreshInFlight;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

/** Every authenticated call goes through this: attaches the bearer token, and
 * on a 401 tries the refresh token exactly once before giving up and clearing
 * the session -- so a 15-minute access token doesn't silently kill the app. */
async function authFetch(
  path: string,
  init: RequestInit = {},
  signal?: AbortSignal,
): Promise<Response> {
  const token = getAccessToken();
  if (!token) throw new ApiError("Sign in is required.", 401);
  const withAuth = (bearer: string): RequestInit => ({
    ...init,
    signal,
    headers: { ...(init.headers ?? {}), Authorization: `Bearer ${bearer}` },
  });
  let response = await fetch(`${apiBase()}${path}`, withAuth(token));
  if (response.status === 401) {
    const refreshed = await tryRefresh();
    if (!refreshed) {
      clearSession();
      announceSessionEnded();
      throw new ApiError("Your session expired. Please sign in again.", 401);
    }
    const fresh = getAccessToken();
    if (!fresh)
      throw new ApiError("Your session expired. Please sign in again.", 401);
    response = await fetch(`${apiBase()}${path}`, withAuth(fresh));
  }
  return response;
}

async function authJson<T>(
  path: string,
  init: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  const response = await authFetch(
    path,
    {
      ...init,
      headers: { "content-type": "application/json", ...(init.headers ?? {}) },
    },
    signal,
  );
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new ApiError(
      (detail as { detail?: string } | null)?.detail ??
        `Request failed (${response.status}).`,
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

// ---- Auth ---------------------------------------------------------------

export async function login(email: string, password: string): Promise<void> {
  const response = await fetch(`${apiBase()}/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok)
    throw new ApiError(
      response.status === 401
        ? "Email or password is incorrect."
        : `Sign-in failed (${response.status}).`,
      response.status,
    );
  storeTokens((await response.json()) as TokenResponse);
}

export async function register(
  email: string,
  password: string,
  displayName: string,
  tenantName: string,
): Promise<void> {
  const response = await fetch(`${apiBase()}/auth/register`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      email,
      password,
      display_name: displayName,
      tenant_name: tenantName,
    }),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new ApiError(
      (detail as { detail?: string } | null)?.detail ??
        `Sign-up failed (${response.status}).`,
      response.status,
    );
  }
  storeTokens((await response.json()) as TokenResponse);
}

export function signOut(): void {
  clearSession();
  announceSessionEnded();
}

export function getMe(signal?: AbortSignal): Promise<UserPublic> {
  return authJson<UserPublic>("/me", {}, signal);
}

export async function getVoiceAccessToken(): Promise<string> {
  await getMe();
  const token = getAccessToken();
  if (!token) throw new ApiError("Sign in is required.", 401);
  return token;
}

export const WORKSPACE_CHANGED_EVENT = "jarvis:workspace-changed";
export function notifyWorkspaceChanged(): void {
  window.dispatchEvent(new Event(WORKSPACE_CHANGED_EVENT));
}

// ---- Office / approvals ---------------------------------------------------

export function getOfficeSnapshot(
  signal?: AbortSignal,
): Promise<OfficeSnapshot> {
  return authJson<OfficeSnapshot>("/api/v1/office/snapshot", {}, signal);
}

export function approveTool(invocationId: string): Promise<ToolInvocationOut> {
  return authJson<ToolInvocationOut>(`/tools/${invocationId}/approve`, {
    method: "POST",
  });
}

export function denyTool(invocationId: string): Promise<ToolInvocationOut> {
  return authJson<ToolInvocationOut>(`/tools/${invocationId}/deny`, {
    method: "POST",
  });
}

// ---- Models ---------------------------------------------------------------

export function getModels(signal?: AbortSignal): Promise<ModelsResponse> {
  return authJson<ModelsResponse>("/models", {}, signal);
}

export function getAgents(signal?: AbortSignal): Promise<AgentsResponse> {
  return authJson<AgentsResponse>("/agents", {}, signal);
}

// ---- Durable runs (chat) ---------------------------------------------------

export function createRun(
  message: string,
  options: {
    projectId?: string;
    provider?: string;
    model?: string;
    agentId?: string;
    sessionId?: string;
  } = {},
): Promise<RunOut> {
  return authJson<RunOut>("/runs", {
    method: "POST",
    body: JSON.stringify({
      message,
      project_id: options.projectId,
      provider: options.provider,
      model: options.model,
      agent_id: options.agentId ?? "manager",
      session_id: options.sessionId,
    }),
  });
}

/** Live counterpart to createRun(): opens POST /runs/stream and calls
 * `onEvent` for each frame the instant it arrives, instead of waiting for the
 * whole run to finish. Native EventSource can't do this (POST body, custom
 * auth header), so this reads the response body as a stream and parses SSE
 * frames by hand. Resolves once the stream naturally ends ("end"/"error"). */
export async function streamRun(
  message: string,
  options: {
    projectId?: string;
    provider?: string;
    model?: string;
    agentId?: string;
    sessionId?: string;
  } = {},
  onEvent: (event: RunStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const body = JSON.stringify({
    message,
    project_id: options.projectId,
    provider: options.provider,
    model: options.model,
    agent_id: options.agentId ?? "manager",
    session_id: options.sessionId,
  });
  const attempt = (bearer: string) =>
    fetch(`${apiBase()}/runs/stream`, {
      method: "POST",
      signal,
      headers: {
        "content-type": "application/json",
        Authorization: `Bearer ${bearer}`,
      },
      body,
    });

  const token = getAccessToken();
  if (!token) throw new ApiError("Sign in is required.", 401);
  let response = await attempt(token);
  if (response.status === 401) {
    const refreshed = await tryRefresh();
    if (!refreshed) {
      clearSession();
      announceSessionEnded();
      throw new ApiError("Your session expired. Please sign in again.", 401);
    }
    const fresh = getAccessToken();
    if (!fresh)
      throw new ApiError("Your session expired. Please sign in again.", 401);
    response = await attempt(fresh);
  }
  if (!response.ok || !response.body) {
    const detail = await response.json().catch(() => null);
    throw new ApiError(
      (detail as { detail?: string } | null)?.detail ??
        `Request failed (${response.status}).`,
      response.status,
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let sseEvent: string | null = null;
      let payload: Record<string, unknown> | null = null;
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) sseEvent = line.slice("event: ".length);
        else if (line.startsWith("data: ")) {
          try {
            payload = JSON.parse(line.slice("data: ".length));
          } catch {
            payload = null;
          }
        }
      }
      if (sseEvent) {
        const sequence =
          typeof payload?.sequence === "number" ? payload.sequence : null;
        const data =
          (payload?.data as Record<string, unknown> | undefined) ??
          payload ??
          {};
        onEvent({ sseEvent, sequence, data });
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

export function listRuns(signal?: AbortSignal): Promise<RunSummary[]> {
  return authJson<RunSummary[]>("/runs", {}, signal);
}

export function getRun(runId: string, signal?: AbortSignal): Promise<RunOut> {
  return authJson<RunOut>(`/runs/${runId}`, {}, signal);
}

// ---- Projects / RAG ---------------------------------------------------

export function getProjects(signal?: AbortSignal): Promise<ProjectView[]> {
  return authJson<ProjectView[]>("/projects", {}, signal);
}

export function createProject(
  name: string,
  rootPath?: string,
): Promise<ProjectView> {
  return authJson<ProjectView>("/projects", {
    method: "POST",
    body: JSON.stringify({ name, root_path: rootPath || undefined }),
  });
}

/** Indexes `paths` (server-side paths inside RAG_ALLOWED_ROOTS) into `projectId`
 * so chat's coding/rag mode has something to retrieve. Requires the RAG service
 * to be reachable from the API (its own container in the full Compose stack). */
export function indexProject(
  projectId: string,
  paths: string[],
  recursive = true,
): Promise<IngestResponse> {
  return authJson<IngestResponse>("/rag/index", {
    method: "POST",
    body: JSON.stringify({ project_id: projectId, paths, recursive }),
  });
}
