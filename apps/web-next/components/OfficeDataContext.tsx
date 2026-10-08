"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ApiError,
  getAgents,
  getDesktopStatus,
  getMe,
  getModels,
  getOfficeSnapshot,
  WORKSPACE_CHANGED_EVENT,
} from "@/lib/api";
import type {
  AgentsResponse,
  DesktopStatus,
  ModelsResponse,
  OfficeSnapshot,
  UserPublic,
} from "@/lib/contracts";

interface OfficeData {
  snapshot: OfficeSnapshot | null;
  models: ModelsResponse | null;
  directory: AgentsResponse | null;
  user: UserPublic | null;
  connection: "loading" | "connected" | "disconnected";
  error: string | null;
  modelsError: string | null;
  agentsError: string | null;
  desktop: DesktopStatus | null;
  desktopError: string | null;
  refreshing: boolean;
  refresh: () => Promise<void>;
}
const OfficeDataContext = createContext<OfficeData | null>(null);
const message = (reason: unknown, fallback: string) =>
  reason instanceof ApiError ? reason.message : fallback;

export function OfficeDataProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [snapshot, setSnapshot] = useState<OfficeSnapshot | null>(null);
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [directory, setDirectory] = useState<AgentsResponse | null>(null);
  const [user, setUser] = useState<UserPublic | null>(null);
  const [connection, setConnection] =
    useState<OfficeData["connection"]>("loading");
  const [error, setError] = useState<string | null>(null);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [agentsError, setAgentsError] = useState<string | null>(null);
  const [desktop, setDesktop] = useState<DesktopStatus | null>(null);
  const [desktopError, setDesktopError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const active = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setRefreshing(true);
    const signal = controller.signal;
    await Promise.all([
      getDesktopStatus(signal)
        .then((next) => {
          if (!signal.aborted) { setDesktop(next); setDesktopError(null); }
        })
        .catch((reason: unknown) => {
          if (!signal.aborted) setDesktopError(message(reason, "Desktop bridge status is unavailable. No computer access has been verified."));
        }),
      getOfficeSnapshot(signal)
        .then((next) => {
          if (signal.aborted) return;
          setSnapshot(next);
          setConnection("connected");
          setError(null);
        })
        .catch((reason: unknown) => {
          if (signal.aborted) return;
          setConnection("disconnected");
          setError(
            message(
              reason,
              "The office API is unavailable. Showing the last received data, if any.",
            ),
          );
        }),
      getModels(signal)
        .then((next) => {
          if (!signal.aborted) {
            setModels(next);
            setModelsError(null);
          }
        })
        .catch((reason: unknown) => {
          if (!signal.aborted)
            setModelsError(
              message(reason, "Provider configuration could not be loaded."),
            );
        }),
      getAgents(signal)
        .then((next) => {
          if (!signal.aborted) {
            setDirectory(next);
            setAgentsError(null);
          }
        })
        .catch((reason: unknown) => {
          if (!signal.aborted)
            setAgentsError(
              message(reason, "The agent directory could not be loaded."),
            );
        }),
    ]);
    if (!signal.aborted) setRefreshing(false);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    getMe(controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) setUser(next);
      })
      .catch(() => {});
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 8000);
    const changed = () => void refresh();
    window.addEventListener(WORKSPACE_CHANGED_EVENT, changed);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
      window.removeEventListener(WORKSPACE_CHANGED_EVENT, changed);
      controller.abort();
      active.current?.abort();
    };
  }, [refresh]);

  return (
    <OfficeDataContext.Provider
      value={{
        snapshot,
        models,
        directory,
        user,
        connection,
        error,
        modelsError,
        agentsError,
        desktop,
        desktopError,
        refreshing,
        refresh,
      }}
    >
      {children}
    </OfficeDataContext.Provider>
  );
}

export function useOfficeData() {
  const context = useContext(OfficeDataContext);
  if (!context)
    throw new Error("useOfficeData must be used inside OfficeDataProvider");
  return context;
}
