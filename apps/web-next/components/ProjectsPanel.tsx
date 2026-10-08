"use client";

import { FormEvent, useRef, useState } from "react";
import {
  ApiError,
  createProject,
  indexProject,
  notifyWorkspaceChanged,
} from "@/lib/api";
import { useWorkspace } from "./WorkspaceContext";
import { Icon } from "./Icons";

export function ProjectsPanel() {
  const { projects, projectId, setProjectId, refreshProjects, loadError } =
    useWorkspace();
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [paths, setPaths] = useState<Record<string, string>>({});
  const [indexingId, setIndexingId] = useState<string | null>(null);
  const [results, setResults] = useState<
    Record<string, { message: string; error?: boolean }>
  >({});
  const createBusy = useRef(false);
  const indexBusy = useRef(false);
  const project = projects.find((item) => item.id === projectId);
  const indexPaths = projectId ? (paths[projectId] ?? "") : "";

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (createBusy.current) return;
    const element = event.currentTarget;
    const form = new FormData(element);
    const name = String(form.get("name") ?? "").trim();
    if (!name) {
      setCreateError("Give your project a name.");
      return;
    }
    createBusy.current = true;
    setCreating(true);
    setCreateError(null);
    try {
      const created = await createProject(
        name,
        String(form.get("root_path") ?? "").trim(),
      );
      await refreshProjects();
      setProjectId(created.id);
      element.reset();
      notifyWorkspaceChanged();
    } catch (reason) {
      setCreateError(
        reason instanceof ApiError
          ? reason.message
          : "Could not create project.",
      );
    } finally {
      createBusy.current = false;
      setCreating(false);
    }
  };

  const runIndex = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!projectId || !indexPaths.trim() || indexBusy.current) return;
    const id = projectId;
    const requestedPaths = indexPaths
      .split(/[,\n]/)
      .map((path) => path.trim())
      .filter(Boolean);
    indexBusy.current = true;
    setIndexingId(id);
    setResults((current) => ({
      ...current,
      [id]: { message: "Indexing project sources…" },
    }));
    try {
      const result = await indexProject(id, requestedPaths);
      setResults((current) => ({
        ...current,
        [id]: {
          message: `Indexed ${result.chunks_indexed} chunk(s) from ${result.files_seen} file(s).${result.skipped.length ? ` Skipped ${result.skipped.length}: ${result.skipped.join(", ")}` : ""}`,
        },
      }));
      notifyWorkspaceChanged();
    } catch (reason) {
      setResults((current) => ({
        ...current,
        [id]: {
          message:
            reason instanceof ApiError
              ? reason.message
              : "Indexing could not finish. Check that the RAG service is running.",
          error: true,
        },
      }));
    } finally {
      indexBusy.current = false;
      setIndexingId(null);
    }
  };

  return (
    <section className="projects-panel panel" aria-labelledby="projects-title">
      <header className="section-heading">
        <div>
          <p className="eyebrow">GIVE YOUR IDEAS SOME CONTEXT</p>
          <h2 id="projects-title">Your projects</h2>
        </div>
        <span className="subtle-tag">{projects.length} projects</span>
      </header>
      <p className="section-description">
        Choose a project to use its indexed sources in chat and voice. Source
        files must already exist in an allowed directory on your API server.
      </p>
      {loadError && (
        <p className="form-notice" role="status">
          {loadError}
          <button
            type="button"
            className="text-action"
            onClick={() => void refreshProjects()}
          >
            Retry
          </button>
        </p>
      )}
      <div className="project-layout">
        <div>
          <div className="project-list">
            <button
              type="button"
              className={`project-row ${projectId === null ? "active" : ""}`}
              onClick={() => setProjectId(null)}
            >
              <Icon name="globe" size={19} />
              <span>
                Personal workspace
                <small>Chat without indexed project sources</small>
              </span>
              {projectId === null && <Icon name="check" size={15} />}
            </button>
            {projects.map((item) => (
              <button
                type="button"
                key={item.id}
                className={`project-row ${item.id === projectId ? "active" : ""}`}
                onClick={() => setProjectId(item.id)}
              >
                <Icon name="folder" size={19} />
                <span>
                  {item.name}
                  <small>{item.id}</small>
                </span>
                <b>{item.role}</b>
                {item.id === projectId && <Icon name="check" size={15} />}
              </button>
            ))}
          </div>
          {projectId && (
            <form className="side-form index-form" onSubmit={runIndex}>
              <h3>Sources for {project?.name ?? "this project"}</h3>
              <p className="project-help">
                Indexing reads server-side files inside RAG_ALLOWED_ROOTS. Your
                browser’s local files are not accessible here.
              </p>
              <label htmlFor="index-paths">
                Server paths, separated by commas
                <input
                  id="index-paths"
                  value={indexPaths}
                  onChange={(event) =>
                    setPaths((current) => ({
                      ...current,
                      [projectId]: event.target.value,
                    }))
                  }
                  placeholder="./src, ./docs"
                  required
                />
              </label>
              <button
                className="secondary-button"
                disabled={Boolean(indexingId) || !indexPaths.trim()}
              >
                <Icon name="search" size={15} />
                {indexingId === projectId
                  ? "Indexing sources…"
                  : "Index project sources"}
              </button>
              {results[projectId] && (
                <p
                  className={results[projectId].error ? "form-error" : "muted"}
                  role="status"
                >
                  {results[projectId].message}
                </p>
              )}
            </form>
          )}
        </div>
        <div>
          <form className="side-form" onSubmit={create}>
            <h3>A home for your next project.</h3>
            <p className="project-help">
              Create a project, add sources, and keep your team’s context
              focused.
            </p>
            <label>
              Project name
              <input
                name="name"
                placeholder="What are you working on?"
                required
                maxLength={160}
                disabled={creating}
              />
            </label>
            <label>
              Root path on the server (optional)
              <input
                name="root_path"
                placeholder="/workspace/my-project"
                disabled={creating}
              />
            </label>
            <button className="primary-button" disabled={creating}>
              {creating ? "Creating project…" : "Create project"}
              <Icon name="arrow" size={16} />
            </button>
            {createError && (
              <div className="form-error" role="alert">
                {createError}
              </div>
            )}
          </form>
          <div className="setup-note">
            <Icon name="lock" size={19} />
            <div>
              <h3>Context stays scoped.</h3>
              <p>
                Your selected project is shared across chat and voice. Access is
                checked by the server for each request.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
