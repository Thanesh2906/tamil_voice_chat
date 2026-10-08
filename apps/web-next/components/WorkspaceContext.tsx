"use client";

import {
  ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ApiError, getProjects } from "@/lib/api";
import type { ProjectView } from "@/lib/contracts";

interface WorkspaceValue {
  projects: ProjectView[];
  projectId: string | null;
  projectName: string | null;
  setProjectId: (id: string | null) => void;
  refreshProjects: () => Promise<void>;
  loadError: string | null;
}

const WorkspaceContext = createContext<WorkspaceValue | null>(null);

/** Shares the selected project across Chat, Projects and Runs so picking a
 * project in one panel is what "coding"/"rag" mode chat actually searches. */
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<ProjectView[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const active = useRef<AbortController | null>(null);
  const refreshProjects = useCallback(() => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    return getProjects(controller.signal)
      .then((list) => {
        if (controller.signal.aborted) return;
        setProjects(list);
        setLoadError(null);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setLoadError(
          reason instanceof ApiError
            ? reason.message
            : "Could not load projects.",
        );
      });
  }, []);

  useEffect(() => {
    void refreshProjects();
    return () => active.current?.abort();
  }, [refreshProjects]);

  const projectName = useMemo(
    () => projects.find((project) => project.id === projectId)?.name ?? null,
    [projects, projectId],
  );

  const value: WorkspaceValue = {
    projects,
    projectId,
    projectName,
    setProjectId,
    refreshProjects,
    loadError,
  };
  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): WorkspaceValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx)
    throw new Error("useWorkspace must be used inside <WorkspaceProvider>");
  return ctx;
}
