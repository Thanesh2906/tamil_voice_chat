"use client";

import { FormEvent, useState } from "react";
import { ApiError, createProject, indexProject } from "@/lib/api";
import { useWorkspace } from "./WorkspaceContext";

export function ProjectsPanel() {
  const { projects, projectId, setProjectId, refreshProjects, loadError } = useWorkspace();
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [indexPaths, setIndexPaths] = useState("");
  const [indexBusy, setIndexBusy] = useState(false);
  const [indexResult, setIndexResult] = useState<string | null>(null);

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setCreating(true);
    setCreateError(null);
    const form = new FormData(event.currentTarget);
    try {
      const project = await createProject(String(form.get("name") ?? ""), String(form.get("root_path") ?? ""));
      await refreshProjects();
      setProjectId(project.id);
      event.currentTarget.reset();
    } catch (reason) {
      setCreateError(reason instanceof ApiError ? reason.message : "Could not create project.");
    } finally {
      setCreating(false);
    }
  };

  const runIndex = async () => {
    if (!projectId || !indexPaths.trim()) return;
    setIndexBusy(true);
    setIndexResult(null);
    try {
      const paths = indexPaths.split(",").map((path) => path.trim()).filter(Boolean);
      const result = await indexProject(projectId, paths);
      setIndexResult(`Indexed ${result.chunks_indexed} chunk(s) from ${result.files_seen} file(s).`);
    } catch (reason) {
      setIndexResult(reason instanceof ApiError ? reason.message : "Indexing failed — is the RAG service running?");
    } finally {
      setIndexBusy(false);
    }
  };

  return (
    <section className="side-card panel" aria-labelledby="projects-title">
      <h3 className="section-label" id="projects-title">Projects</h3>

      {loadError && <p className="muted">{loadError}</p>}
      {!loadError && projects.length === 0 && <p className="muted">No projects yet.</p>}
      <div className="project-list">
        <button
          type="button"
          className={`project-row ${projectId === null ? "active" : ""}`}
          onClick={() => setProjectId(null)}
        >
          <span>No project (personal chat)</span>
        </button>
        {projects.map((project) => (
          <button
            type="button"
            key={project.id}
            className={`project-row ${project.id === projectId ? "active" : ""}`}
            onClick={() => setProjectId(project.id)}
          >
            <span>{project.name}</span>
            <b>{project.role}</b>
          </button>
        ))}
      </div>

      <form className="side-form" onSubmit={create}>
        <input name="name" placeholder="New project name" required maxLength={160} />
        <input name="root_path" placeholder="Root path on the server (optional)" />
        <button className="quiet-button" disabled={creating}>{creating ? "Creating…" : "Create project"}</button>
        {createError && <div className="form-error">{createError}</div>}
      </form>

      {projectId && (
        <div className="side-form index-form">
          <label className="muted" htmlFor="index-paths">Index paths into &quot;{projects.find((p) => p.id === projectId)?.name}&quot;</label>
          <input
            id="index-paths"
            value={indexPaths}
            onChange={(event) => setIndexPaths(event.target.value)}
            placeholder="./src, ./docs (comma-separated, inside RAG_ALLOWED_ROOTS)"
          />
          <button className="quiet-button" onClick={runIndex} disabled={indexBusy || !indexPaths.trim()} type="button">
            {indexBusy ? "Indexing…" : "Index now"}
          </button>
          {indexResult && <p className="muted">{indexResult}</p>}
        </div>
      )}
    </section>
  );
}
