"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { ApiError, approveTool, cancelRun, denyTool, getPendingTools, getRun, notifyWorkspaceChanged, resumeRun, WORKSPACE_CHANGED_EVENT } from "@/lib/api";
import type { RunOut } from "@/lib/contracts";

type Action = "resume" | "cancel";
type Pending = Partial<Record<Action, boolean>>;
interface RunData {
  savedRuns: Record<string, RunOut>;
  runErrors: Record<string, string | null>;
  pendingRuns: Record<string, Pending>;
  refreshRun: (id: string) => Promise<void>;
  actOnRun: (id: string, action: Action) => Promise<void>;
  decidingId: string | null;
  decisionLocks: Record<string, boolean>;
  refreshDecisions: () => Promise<void>;
  decisionNotice: string | null;
  decisionError: string | null;
  decideTool: (id: string, decision: "approve" | "deny") => Promise<void>;
}
const RunDataContext = createContext<RunData | null>(null);
const errorText = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;

/** Auth-scoped state survives panel navigation. A mutation invalidates older
 * reads, and per-run guards prevent duplicate requests across every view. */
export function RunDataProvider({ children }: { children: React.ReactNode }) {
  const [savedRuns, setSavedRuns] = useState<Record<string, RunOut>>({});
  const [runErrors, setRunErrors] = useState<Record<string, string | null>>({});
  const [pendingRuns, setPendingRuns] = useState<Record<string, Pending>>({});
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [decisionLocks, setDecisionLocks] = useState<Record<string, boolean>>({});
  const [decisionNotice, setDecisionNotice] = useState<string | null>(null);
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const records = useRef<Record<string, RunOut>>({});
  const errors = useRef<Record<string, string | null>>({});
  const pending = useRef<Record<string, Pending>>({});
  const deciding = useRef<string | null>(null);
  const lockedDecisions = useRef<Record<string, boolean>>({});
  const generations = useRef<Record<string, number>>({});
  const reads = useRef<Record<string, AbortController>>({});
  const mounted = useRef(true);

  const accept = useCallback((run: RunOut) => {
    records.current[run.id] = run;
    errors.current[run.id] = null;
    setSavedRuns((current) => ({ ...current, [run.id]: run }));
    setRunErrors((current) => ({ ...current, [run.id]: null }));
  }, []);
  const fail = useCallback((id: string, text: string) => {
    errors.current[id] = text;
    setRunErrors((current) => ({ ...current, [id]: text }));
  }, []);
  const invalidate = useCallback((id: string) => {
    reads.current[id]?.abort();
    return (generations.current[id] = (generations.current[id] ?? 0) + 1);
  }, []);
  const refreshRun = useCallback(async (id: string) => {
    // A synchronous action can take time. Do not race its response with polling.
    if (pending.current[id]?.resume || pending.current[id]?.cancel) return;
    const generation = invalidate(id);
    const controller = new AbortController();
    reads.current[id] = controller;
    try {
      const next = await getRun(id, controller.signal);
      if (mounted.current && !controller.signal.aborted && generations.current[id] === generation) accept(next);
    } catch (reason) {
      if (mounted.current && !controller.signal.aborted && generations.current[id] === generation)
        fail(id, errorText(reason, "Saved status could not be loaded. Actions are disabled until it can be checked."));
    }
  }, [accept, fail, invalidate]);

  const actOnRun = useCallback(async (id: string, action: Action) => {
    const current = records.current[id];
    if (!current || errors.current[id] || pending.current[id]?.[action]) return;
    if (action === "resume" && (!current.can_resume || pending.current[id]?.cancel)) return;
    if (action === "cancel" && !current.can_cancel) return;
    const generation = invalidate(id);
    pending.current[id] = { ...pending.current[id], [action]: true };
    setPendingRuns({ ...pending.current });
    try {
      const next = await (action === "resume" ? resumeRun(id) : cancelRun(id));
      if (mounted.current && generations.current[id] === generation) accept(next);
    } catch (reason) {
      if (mounted.current && generations.current[id] === generation)
        fail(id, `${errorText(reason, "The action response was lost; the server may have acted.")} Refresh saved status before trying again.`);
    } finally {
      pending.current[id] = { ...pending.current[id], [action]: false };
      if (mounted.current) {
        setPendingRuns({ ...pending.current });
        // A later cancellation wins over an earlier resume response. Once both
        // settle, fetch the authoritative result rather than replaying either.
        if (generations.current[id] !== generation) void refreshRun(id);
        notifyWorkspaceChanged();
      }
    }
  }, [accept, fail, invalidate, refreshRun]);

  const decideTool = useCallback(async (id: string, decision: "approve" | "deny") => {
    if (deciding.current || lockedDecisions.current[id]) return;
    deciding.current = id;
    lockedDecisions.current[id] = true;
    setDecisionLocks({ ...lockedDecisions.current });
    setDecidingId(id);
    setDecisionError(null);
    setDecisionNotice(null);
    try {
      const result = await (decision === "approve" ? approveTool(id) : denyTool(id));
      if (!mounted.current) return;
      const outcome = result.status === "uncertain"
        ? "The tool outcome is uncertain. It will not be retried automatically."
        : result.status === "failed"
          ? `The approved tool failed: ${result.error ?? "Check the saved run."}`
          : decision === "deny"
            ? "Action denied. The tool was not run."
            : `Action ${result.status.replaceAll("_", " ")}.`;
      const continuation = result.run_status === "completed"
        ? " The run finished; its saved response is available in your conversation and Runs."
        : result.run_status === "awaiting_approval"
          ? " A new action needs a separate approval."
          : result.run_status
            ? ` Run status: ${result.run_status.replaceAll("_", " ")}. Check Runs for details.`
            : " Check Runs for its saved status.";
      setDecisionNotice(outcome + continuation);
      if (result.run_id) await refreshRun(result.run_id);
    } catch (reason) {
      if (mounted.current) setDecisionError(errorText(reason, "The decision could not be confirmed. Refresh to check its status before trying again."));
    } finally {
      deciding.current = null;
      if (mounted.current) {
        setDecidingId(null);
        notifyWorkspaceChanged();
      }
    }
  }, [refreshRun]);

  const refreshDecisions = useCallback(async () => {
    if (deciding.current) return;
    try {
      const pendingTools = await getPendingTools();
      if (!mounted.current || deciding.current) return;
      // Only an authoritative pending record permits an explicit retry after
      // an uncertain decision response. Completed decisions stay locked.
      for (const tool of pendingTools) {
        if (tool.status === "pending") lockedDecisions.current[tool.id] = false;
      }
      setDecisionLocks({ ...lockedDecisions.current });
      setDecisionError(null);
      notifyWorkspaceChanged();
    } catch (reason) {
      if (mounted.current) setDecisionError(errorText(reason, "Pending decisions could not be checked. No decision was retried."));
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    const activeReads = reads.current;
    const changed = () => {
      for (const id of Object.keys(records.current)) {
        // An uncertain mutation must be checked explicitly before another action.
        if (!errors.current[id]) void refreshRun(id);
      }
    };
    const timer = window.setInterval(() => {
      for (const run of Object.values(records.current)) {
        if (!errors.current[run.id] && (["running", "awaiting_approval"].includes(run.status) || run.executing_tool_ids?.length)) void refreshRun(run.id);
      }
    }, 8000);
    window.addEventListener(WORKSPACE_CHANGED_EVENT, changed);
    return () => {
      mounted.current = false;
      Object.values(activeReads).forEach((controller) => controller.abort());
      window.clearInterval(timer);
      window.removeEventListener(WORKSPACE_CHANGED_EVENT, changed);
    };
  }, [refreshRun]);

  return <RunDataContext.Provider value={{ savedRuns, runErrors, pendingRuns, refreshRun, actOnRun, decidingId, decisionLocks, refreshDecisions, decisionNotice, decisionError, decideTool }}>{children}</RunDataContext.Provider>;
}
export function useRunData() {
  const context = useContext(RunDataContext);
  if (!context) throw new Error("useRunData must be used inside RunDataProvider");
  return context;
}
