"""Real filesystem, Git and process regressions for the tool security boundary."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from packages.common import get_settings
from packages.common.safe_paths import PathSecurityError, resolve_authorized_for_write
from services.tools import adapters, gateway
from services.tools.adapters import ToolExecutionError


def _call(name, **args):
    return gateway.validate(name, args)


@pytest.mark.parametrize("dangling", [False, True])
async def test_write_rejects_final_symlink_outside_root(tmp_path, dangling):
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    if not dangling:
        outside.write_text("original")
    link = root / "notes.txt"
    link.symlink_to(outside)
    with pytest.raises(PathSecurityError, match="outside"):
        resolve_authorized_for_write(str(link), [str(root)], [])
    with pytest.raises(ToolExecutionError, match="outside"):
        await gateway.execute(_call("write_file", path=str(link), content="changed"),
                              roots=[str(root)], sensitive_globs=[])
    assert outside.read_text() == "original" if not dangling else not outside.exists()


async def test_write_rejects_symlink_to_excluded_file_inside_root(tmp_path):
    target = tmp_path / ".env"
    target.write_text("original")
    link = tmp_path / "notes.txt"
    link.symlink_to(target)
    with pytest.raises(ToolExecutionError, match="sensitive"):
        await gateway.execute(_call("write_file", path=str(link), content="changed"),
                              roots=[str(tmp_path)], sensitive_globs=[".env*"])
    assert target.read_text() == "original"


@pytest.mark.parametrize("replace_parent", [False, True])
async def test_write_fails_closed_if_symlink_is_swapped_after_validation(tmp_path, monkeypatch, replace_parent):
    root = tmp_path / "allowed"
    root.mkdir()
    directory = root / "folder"
    directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = directory / "notes.txt"
    target.write_text("inside")
    protected = outside / "notes.txt"
    protected.write_text("original")
    original_resolve = adapters.resolve_authorized_for_write

    def swap(*args):
        resolved = original_resolve(*args)
        if replace_parent:
            directory.rename(root / "old-folder")
            directory.symlink_to(outside, target_is_directory=True)
        else:
            target.unlink()
            target.symlink_to(protected)
        return resolved

    monkeypatch.setattr(adapters, "resolve_authorized_for_write", swap)
    with pytest.raises(ToolExecutionError):
        await gateway.execute(_call("write_file", path=str(target), content="changed"),
                              roots=[str(root)], sensitive_globs=[])
    assert protected.read_text() == "original"


async def test_write_rejects_hard_link_to_another_file(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("original")
    root = tmp_path / "allowed"
    root.mkdir()
    link = root / "notes.txt"
    link.hardlink_to(outside)
    with pytest.raises(ToolExecutionError, match="multiply-linked"):
        await gateway.execute(_call("write_file", path=str(link), content="changed"),
                              roots=[str(root)], sensitive_globs=[])
    assert outside.read_text() == "original"


async def test_read_uses_a_bounded_stream_instead_of_read_bytes(tmp_path, monkeypatch):
    target = tmp_path / "large.txt"
    with target.open("wb") as handle:
        handle.write(b"hello")
        handle.truncate(100_000_000)

    def disallow_unbounded(*args, **kwargs):
        raise AssertionError("whole-file reads are forbidden")

    monkeypatch.setattr(Path, "read_bytes", disallow_unbounded)
    result = await gateway.execute(_call("read_file", path=str(target), max_bytes=5),
                                   roots=[str(tmp_path)], sensitive_globs=[])
    assert result["content"] == "hello"
    assert result["truncated"] is True


async def test_read_rejects_fifo_without_blocking(tmp_path):
    target = tmp_path / "pipe.txt"
    os.mkfifo(target)
    with pytest.raises(ToolExecutionError, match="regular file"):
        await gateway.execute(_call("read_file", path=str(target)), roots=[str(tmp_path)], sensitive_globs=[])


async def test_directory_listing_hides_sensitive_and_escaping_entries(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / ".env").write_text("hidden")
    (root / "notes.txt").write_text("visible")
    (root / "outside.txt").symlink_to(tmp_path / "private.txt")
    (tmp_path / "private.txt").write_text("hidden")
    result = await gateway.execute(_call("list_dir", path=str(root)), roots=[str(root)], sensitive_globs=[".env*"])
    assert [entry["name"] for entry in result["entries"]] == ["notes.txt"]


async def test_directory_listing_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(adapters, "MAX_DIRECTORY_ENTRIES", 2)
    for index in range(4):
        (tmp_path / f"{index}.txt").write_text("x")
    result = await gateway.execute(_call("list_dir", path=str(tmp_path)), roots=[str(tmp_path)], sensitive_globs=[])
    assert len(result["entries"]) == 2
    assert result["truncated"] is True


async def test_search_has_per_file_and_total_read_budgets(tmp_path, monkeypatch):
    monkeypatch.setattr(adapters, "MAX_SEARCH_FILE_BYTES", 32)
    monkeypatch.setattr(adapters, "MAX_SEARCH_BYTES", 48)
    for index in range(4):
        (tmp_path / f"{index}.txt").write_text("needle\n" * 100)
    original_open = adapters._open_regular_file
    reads = []

    @contextmanager
    def tracked_open(*args, **kwargs):
        with original_open(*args, **kwargs) as handle:
            class Tracked:
                def read(self, size):
                    reads.append(size)
                    return handle.read(size)

                def fileno(self):
                    return handle.fileno()
            yield Tracked()

    monkeypatch.setattr(adapters, "_open_regular_file", tracked_open)
    result = await gateway.execute(_call("search_text", path=str(tmp_path), query="needle"),
                                   roots=[str(tmp_path)], sensitive_globs=[])
    assert reads == [32, 16]
    assert result["truncated"] is True


@pytest.mark.parametrize("limit_name,limit", [("MAX_SEARCH_ENTRIES", 1), ("MAX_SEARCH_FILES", 1), ("MAX_SEARCH_SECONDS", 0)])
async def test_search_stops_at_traversal_file_or_time_budget(tmp_path, monkeypatch, limit_name, limit):
    monkeypatch.setattr(adapters, limit_name, limit)
    for index in range(3):
        (tmp_path / f"{index}.txt").write_text("needle")
    result = await gateway.execute(_call("search_text", path=str(tmp_path), query="needle"),
                                   roots=[str(tmp_path)], sensitive_globs=[])
    assert len(result["matches"]) <= 1
    assert result["truncated"] is True


async def test_search_excludes_sensitive_trees_and_symlinks(tmp_path):
    (tmp_path / "notes.txt").write_text("needle visible")
    for name in [".git", "node_modules", ".env-files"]:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "hidden.txt").write_text("needle hidden")
    (tmp_path / "alias.txt").symlink_to(tmp_path / ".env-files" / "hidden.txt")
    result = await gateway.execute(_call("search_text", path=str(tmp_path), query="needle"),
                                   roots=[str(tmp_path)], sensitive_globs=[".env*"])
    assert [match["text"] for match in result["matches"]] == ["needle visible"]


def _git(path, *args):
    return subprocess.run(["git", "-c", "user.name=Tool Test", "-c", "user.email=tool@example.invalid", *args],
                          cwd=path, check=True, capture_output=True, text=True)


def _repository(path):
    _git(path, "init", "--quiet")
    (path / "notes.txt").write_text("before\n")
    (path / ".env").write_text("PRIVATE=before\n")
    _git(path, "add", ".")
    _git(path, "commit", "--quiet", "-m", "fixture")
    (path / "notes.txt").write_text("after\n")
    (path / ".env").write_text("PRIVATE=after\n")


@pytest.mark.parametrize("tool", ["git_status", "git_diff"])
async def test_git_refuses_to_discover_repository_above_authorized_root(tmp_path, tool):
    _repository(tmp_path)
    root = tmp_path / "allowed"
    root.mkdir()
    with pytest.raises(ToolExecutionError, match="no Git repository"):
        await gateway.execute(_call(tool, path=str(root)), roots=[str(root)], sensitive_globs=[])


@pytest.mark.parametrize("tool", ["git_status", "git_diff"])
async def test_git_excludes_sensitive_files_and_stays_in_requested_subdirectory(tmp_path, tool):
    _repository(tmp_path)
    root = tmp_path / "subfolder"
    root.mkdir()
    (root / "safe.txt").write_text("original\n")
    (root / ".env").write_text("NESTED_PRIVATE=before\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "--quiet", "-m", "nested")
    (root / "safe.txt").write_text("changed\n")
    (root / ".env").write_text("NESTED_PRIVATE=after\n")
    (tmp_path / "notes.txt").write_text("outside requested subfolder\n")
    result = await gateway.execute(_call(tool, path=str(root)), roots=[str(tmp_path)], sensitive_globs=[".env*"])
    assert "safe.txt" in result["stdout"]
    assert ".env" not in result["stdout"]
    assert "PRIVATE" not in result["stdout"]
    assert "notes.txt" not in result["stdout"]


async def test_git_diff_disables_external_helpers_and_worktree_overrides(tmp_path):
    _repository(tmp_path)
    marker = tmp_path / "executed.txt"
    helper = tmp_path / "helper.sh"
    helper.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    helper.chmod(0o700)
    (tmp_path / ".gitattributes").write_text("*.txt diff=unsafe filter=unsafe\n")
    _git(tmp_path, "config", "filter.unsafe.clean", str(helper))
    _git(tmp_path, "config", "filter.unsafe.process", str(helper))
    _git(tmp_path, "config", "filter.unsafe.required", "true")
    _git(tmp_path, "config", "diff.unsafe.textconv", str(helper))
    _git(tmp_path, "config", "diff.external", str(helper))
    _git(tmp_path, "config", "core.fsmonitor", str(helper))
    _git(tmp_path, "config", "core.worktree", str(tmp_path.parent))
    result = await gateway.execute(_call("git_diff", path=str(tmp_path)), roots=[str(tmp_path)], sensitive_globs=[".env*"])
    assert "+after" in result["stdout"]
    assert "PRIVATE" not in result["stdout"]
    assert not marker.exists()


async def test_git_refuses_external_worktree_metadata(tmp_path):
    (tmp_path / ".git").write_text("gitdir: /outside/repository\n")
    with pytest.raises(ToolExecutionError, match="Git metadata"):
        await gateway.execute(_call("git_diff", path=str(tmp_path)), roots=[str(tmp_path)], sensitive_globs=[])


@pytest.mark.parametrize("tool,args", [
    ("run_command", {"command": "python", "args": ["-c", "print(42)"], "cwd": "."}),
    ("ssh_run", {"command": "git", "args": ["status"], "host": "example.com"}),
])
async def test_arbitrary_execution_requires_explicit_unsafe_opt_in(monkeypatch, tool, args):
    monkeypatch.setattr(get_settings(), "tools_unsafe_host_execution", False)
    with pytest.raises(ToolExecutionError, match="host execution is disabled"):
        await gateway.execute(_call(tool, **args), roots=["."], sensitive_globs=[])


async def test_docker_logs_require_container_allowlist(monkeypatch):
    monkeypatch.setattr(get_settings(), "docker_allowed_containers", [])
    with pytest.raises(ToolExecutionError, match="DOCKER_ALLOWED_CONTAINERS"):
        await gateway.execute(_call("docker_logs", container="private-db"), roots=[], sensitive_globs=[])


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
async def test_real_subprocess_output_overflow_is_an_error(tmp_path, monkeypatch, stream):
    monkeypatch.setattr(adapters, "MAX_OUTPUT_BYTES", 16_384)
    with pytest.raises(ToolExecutionError, match="output exceeded"):
        await adapters._exec([sys.executable, "-c", f"import sys;sys.{stream}.buffer.write(b'x'*1000000)"],
                             cwd=tmp_path, timeout_seconds=5)


async def test_real_subprocess_nonzero_exit_is_an_error(tmp_path):
    with pytest.raises(ToolExecutionError, match="exited with code 7: failure"):
        await adapters._exec([sys.executable, "-c", "import sys;print('failure',file=sys.stderr);sys.exit(7)"],
                             cwd=tmp_path, timeout_seconds=5)


async def test_real_subprocess_does_not_inherit_secrets_or_execution_overrides(tmp_path, monkeypatch):
    for key in ["JWT_SECRET", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK", "PYTHONPATH", "GIT_DIR"]:
        monkeypatch.setenv(key, "must-not-leak")
    result = await adapters._exec([sys.executable, "-c", "import json,os;print(json.dumps(dict(os.environ)))"],
                                 cwd=tmp_path, timeout_seconds=5)
    environment = json.loads(result["stdout"])
    assert "must-not-leak" not in result["stdout"]
    assert set(environment) <= set(adapters._subprocess_environment("placeholder"))
    assert not Path(environment["HOME"]).exists()


def _process_running(pid):
    proc = Path(f"/proc/{pid}/stat")
    try:
        return proc.read_text().split(") ", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _wait_for_file(path):
    async with asyncio.timeout(5):
        while not path.exists() or not path.read_text():
            await asyncio.sleep(0.01)


def _parent_with_child(pidfile, *, parent_exits=False):
    return (
        "import subprocess,sys,time,pathlib;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(child.pid));"
        + ("print('done')" if parent_exits else "time.sleep(60)")
    )


@pytest.mark.parametrize("reason", ["timeout", "cancel", "success"])
async def test_real_subprocess_cleans_up_process_group(tmp_path, reason):
    pidfile = tmp_path / "child.pid"
    task = asyncio.create_task(adapters._exec(
        [sys.executable, "-c", _parent_with_child(pidfile, parent_exits=reason == "success")],
        cwd=tmp_path, timeout_seconds=1 if reason == "timeout" else 10,
    ))
    await _wait_for_file(pidfile)
    child_pid = int(pidfile.read_text())
    try:
        if reason == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif reason == "timeout":
            with pytest.raises(ToolExecutionError, match="timed out"):
                await task
        else:
            result = await task
            assert result["stdout"] == "done\n"
        async with asyncio.timeout(3):
            while _process_running(child_pid):
                await asyncio.sleep(0.01)
    finally:
        if _process_running(child_pid):
            os.kill(child_pid, 9)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_git_read_rejects_external_config_includes(tmp_path):
    _repository(tmp_path)
    external = tmp_path / "external-config"
    external.write_text("[filter \"unsafe\"]\nclean = touch should-not-execute\n")
    _git(tmp_path, "config", "include.path", str(external))
    with pytest.raises(ToolExecutionError, match="include/worktree"):
        await gateway.execute(_call("git_diff", path=str(tmp_path)), roots=[str(tmp_path)], sensitive_globs=[])
