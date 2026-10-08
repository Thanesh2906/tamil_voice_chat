"""All connected-looking execution uses disposable filesystem/process fixtures."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from services.desktop_bridge import files, protocol
from services.desktop_bridge.protocol import DesktopBridge, SessionBinding
from services.desktop_bridge.status import IMPLEMENTED_CAPABILITIES, disconnected_status


@pytest.fixture
def bridge_fixture(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    note = root / "notes.txt"
    note.write_text("hello Tamil\nhello desktop\n")
    binding = SessionBinding("owner-a", "project-a", "machine-a", "session-a", (str(root),),
                             frozenset(IMPLEMENTED_CAPABILITIES), time.time() + 60)
    bridge = DesktopBridge(binding)
    yield bridge, binding, root, note
    bridge.close()


def request(binding, path, **changes):
    result = {
        "protocol_version": 1, "owner_id": binding.owner_id, "project_id": binding.project_id,
        "machine_id": binding.machine_id, "session_id": binding.session_id,
        "operation_id": "operation-0001", "deadline_at": time.time() + 5,
        "capability": "files.read_text", "arguments": {"path": str(path)},
    }
    result.update(changes)
    return result


def error(response, code):
    assert response["ok"] is False
    assert response["error"] == {"code": code}
    assert "result" not in response


def test_status_is_truthful_fresh_and_nonactivating():
    status = disconnected_status()
    assert status["state"] == "disconnected"
    assert status["paired"] is False
    assert status["machine_id"] is status["session_id"] is None
    assert status["platform_support"] == ["posix"]
    assert [item["name"] for item in status["capabilities"] if item["implemented"]] == list(IMPLEMENTED_CAPABILITIES)
    assert all(not item["available"] for item in status["capabilities"])
    status["capabilities"][0]["available"] = True
    assert not disconnected_status()["capabilities"][0]["available"]


async def test_disconnected_never_reads_a_path(bridge_fixture, monkeypatch):
    _, binding, _, note = bridge_fixture
    def forbidden(*args):
        pytest.fail("a disconnected request touched the filesystem")
    monkeypatch.setattr(files, "resolve_authorized", forbidden)
    error(await DesktopBridge().handle(request(binding, note)), "disconnected")


@pytest.mark.parametrize("field", ["owner_id", "project_id", "machine_id", "session_id"])
async def test_every_binding_is_enforced_before_execution(bridge_fixture, field):
    bridge, binding, _, note = bridge_fixture
    error(await bridge.handle(request(binding, note, **{field: "another"})), "binding_mismatch")
    assert not bridge.seen


async def test_real_read_and_replay_rejection(bridge_fixture):
    bridge, binding, _, note = bridge_fixture
    payload = request(binding, note)
    response = await bridge.handle(payload)
    assert response["result"] == {"path": str(note), "content": note.read_text(), "truncated": False}
    error(await bridge.handle(payload), "replay_rejected")


async def test_real_listing_and_search(bridge_fixture):
    bridge, binding, root, _ = bridge_fixture
    (root / ".env").write_text("hello secret")
    (root / "nested").mkdir()
    (root / "nested" / "other.txt").write_text("HELLO nested")
    listed = await bridge.handle(request(binding, root, capability="files.list"))
    assert [entry["name"] for entry in listed["result"]["entries"]] == ["nested", "notes.txt"]
    searched = await bridge.handle(request(binding, root, capability="files.search_text",
                                           operation_id="operation-0002",
                                           arguments={"path": str(root), "query": "hello"}))
    assert len(searched["result"]["matches"]) == 3
    assert all("secret" not in item["text"] for item in searched["result"]["matches"])


@pytest.mark.parametrize("capability", ["files.write", "shell.execute", "desktop.rpa", "browser.control"])
async def test_unimplemented_or_ungranted_capability_is_denied(bridge_fixture, capability):
    bridge, binding, _, note = bridge_fixture
    error(await bridge.handle(request(binding, note, capability=capability)), "capability_denied")


async def test_capability_subset_is_enforced(bridge_fixture):
    _, binding, root, _ = bridge_fixture
    bridge = DesktopBridge(replace(binding, capabilities=frozenset({"files.read_text"})))
    error(await bridge.handle(request(binding, root, capability="files.list")), "capability_denied")


@pytest.mark.parametrize("changes", [
    {"unexpected": True}, {"protocol_version": 2}, {"protocol_version": True}, {"operation_id": ""},
    {"deadline_at": float("nan")}, {"deadline_at": "10"},
    {"arguments": {"path": "relative.txt", "shell": True}},
    {"arguments": {"path": "relative.txt", "max_bytes": 200001}},
])
async def test_strict_request_and_argument_validation(bridge_fixture, changes):
    bridge, binding, _, note = bridge_fixture
    error(await bridge.handle(request(binding, note, **changes)), "invalid_request")


@pytest.mark.parametrize("seconds,code", [(-1, "deadline_exceeded"), (31, "deadline_out_of_bounds")])
async def test_deadline_rejected_before_io(bridge_fixture, seconds, code):
    bridge, binding, _, note = bridge_fixture
    error(await bridge.handle(request(binding, note, deadline_at=time.time() + seconds)), code)


async def test_expired_and_closed_sessions(bridge_fixture):
    bridge, binding, _, note = bridge_fixture
    bridge.clock = lambda: binding.expires_at + 1
    error(await bridge.handle(request(binding, note)), "session_expired")
    bridge.close()
    error(await bridge.handle(request(binding, note)), "disconnected")


@pytest.mark.parametrize("target", ["outside", "secret", "symlink", "hardlink", "fifo", "relative"])
async def test_paths_fail_closed(bridge_fixture, tmp_path, target):
    bridge, binding, root, note = bridge_fixture
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    secret = root / ".env.txt"
    secret.write_text("private")
    link = root / "link.txt"
    link.symlink_to(outside)
    hard = root / "hard.txt"
    hard.hardlink_to(outside)
    fifo = root / "pipe.txt"
    os.mkfifo(fifo)
    path = {"outside": outside, "secret": secret, "symlink": link,
            "hardlink": hard, "fifo": fifo, "relative": "notes.txt"}[target]
    error(await bridge.handle(request(binding, path)), "file_access_denied")
    assert note.read_text() == "hello Tamil\nhello desktop\n"


async def test_replaced_root_cannot_redirect_session(bridge_fixture):
    bridge, binding, root, note = bridge_fixture
    root.rename(root.with_name("old"))
    root.mkdir()
    note.write_text("replacement")
    error(await bridge.handle(request(binding, note)), "file_access_denied")


async def test_open_is_root_pinned_during_path_race(bridge_fixture, monkeypatch):
    bridge, binding, root, note = bridge_fixture
    original = bridge.files._resolve
    def replace_after_resolve(raw):
        path = original(raw)
        root.rename(root.with_name("old"))
        root.mkdir()
        note.write_text("replacement")
        return path
    monkeypatch.setattr(bridge.files, "_resolve", replace_after_resolve)
    error(await bridge.handle(request(binding, note)), "file_access_denied")


@pytest.mark.parametrize("replace_parent", [False, True])
async def test_component_symlink_swap_after_resolution_fails_closed(bridge_fixture, tmp_path, monkeypatch, replace_parent):
    bridge, binding, root, _ = bridge_fixture
    directory = root / "nested"
    directory.mkdir()
    note = directory / "notes.txt"
    note.write_text("inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.txt").write_text("private")
    original = bridge.files._resolve
    def swap(raw):
        path = original(raw)
        if replace_parent:
            directory.rename(root / "old")
            directory.symlink_to(outside, target_is_directory=True)
        else:
            note.unlink()
            note.symlink_to(outside / "notes.txt")
        return path
    monkeypatch.setattr(bridge.files, "_resolve", swap)
    error(await bridge.handle(request(binding, note)), "file_access_denied")


async def test_read_list_and_search_are_bounded(bridge_fixture, monkeypatch):
    bridge, binding, root, note = bridge_fixture
    note.write_text("hello\n" * 100)
    read = await bridge.handle(request(binding, note, arguments={"path": str(note), "max_bytes": 5}))
    assert read["result"]["content"] == "hello" and read["result"]["truncated"]
    monkeypatch.setattr(files, "MAX_ENTRIES", 1)
    (root / "other.txt").write_text("hello")
    listed = await bridge.handle(request(binding, root, capability="files.list", operation_id="operation-0002"))
    assert len(listed["result"]["entries"]) == 1 and listed["result"]["truncated"]
    searched = await bridge.handle(request(binding, root, capability="files.search_text", operation_id="operation-0003",
                                           arguments={"path": str(root), "query": "hello", "max_results": 1}))
    assert len(searched["result"]["matches"]) == 1 and searched["result"]["truncated"]


async def test_cancel_is_bound_replay_safe_and_stops_active_operation(bridge_fixture, monkeypatch):
    bridge, binding, _, note = bridge_fixture
    entered = asyncio.Event()
    async def delayed(args):
        entered.set()
        await asyncio.Event().wait()
        pytest.fail("cancelled read completed")
    monkeypatch.setattr(bridge.files, "read_text", delayed)
    running = asyncio.create_task(bridge.handle(request(binding, note)))
    await entered.wait()
    cancel = request(binding, note, operation_id="cancel-op-0001", action="cancel", capability=None,
                     arguments={}, target_operation_id="operation-0001")
    error(await bridge.handle({**cancel, "owner_id": "wrong"}), "binding_mismatch")
    assert not running.done()
    assert (await bridge.handle(cancel))["result"]["cancel_requested"]
    error(await running, "cancelled")
    error(await bridge.handle(cancel), "replay_rejected")
    assert not bridge.active


async def test_deadline_interrupts_running_operation(bridge_fixture, monkeypatch):
    bridge, binding, _, note = bridge_fixture
    async def delayed(args):
        await asyncio.Event().wait()
    monkeypatch.setattr(bridge.files, "read_text", delayed)
    error(await bridge.handle(request(binding, note, deadline_at=time.time() + 0.02)), "deadline_exceeded")
    assert not bridge.active


async def test_monotonic_deadline_rejects_late_sync_result_with_frozen_wall_clock(bridge_fixture, monkeypatch):
    bridge, binding, _, note = bridge_fixture
    frozen = time.time()
    bridge.clock = lambda: frozen
    async def delayed(args):
        time.sleep(0.02)
        return {"content": "must not be returned"}
    monkeypatch.setattr(bridge.files, "read_text", delayed)
    error(await bridge.handle(request(binding, note, deadline_at=frozen + 0.01)), "deadline_exceeded")


async def test_overlapping_execution_is_denied(bridge_fixture, monkeypatch):
    bridge, binding, _, note = bridge_fixture
    entered = asyncio.Event()
    async def delayed(args):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(bridge.files, "read_text", delayed)
    running = asyncio.create_task(bridge.handle(request(binding, note)))
    await entered.wait()
    error(await bridge.handle(request(binding, note, operation_id="operation-0002")), "operation_in_progress")
    bridge.close()
    error(await running, "cancelled")


async def test_close_cancels_active_operation(bridge_fixture, monkeypatch):
    bridge, binding, _, note = bridge_fixture
    entered = asyncio.Event()
    async def delayed(args):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(bridge.files, "read_text", delayed)
    running = asyncio.create_task(bridge.handle(request(binding, note)))
    await entered.wait()
    bridge.close()
    error(await running, "cancelled")


async def test_replay_cache_fails_closed_without_evicting(bridge_fixture, monkeypatch):
    bridge, binding, _, note = bridge_fixture
    monkeypatch.setattr(protocol, "MAX_OPERATIONS", 1)
    assert (await bridge.handle(request(binding, note)))["ok"]
    error(await bridge.handle(request(binding, note, operation_id="operation-0002")), "session_operation_limit")
    error(await bridge.handle(request(binding, note)), "replay_rejected")


@pytest.mark.parametrize("change", [{"roots": ()}, {"roots": (".",)},
                                    {"capabilities": frozenset({"shell.execute"})},
                                    {"expires_at": float("inf")}, {"expires_at": 0}, {"owner_id": ""}])
def test_session_requires_explicit_bounded_binding(bridge_fixture, change):
    _, binding, _, _ = bridge_fixture
    with pytest.raises((ValueError, OSError)):
        DesktopBridge(replace(binding, **change))


def run_stdio(*args, payload=None):
    return subprocess.run([sys.executable, "-m", "services.desktop_bridge", *args],
                          input=payload, text=True, capture_output=True, timeout=10, check=True)


def test_stdio_defaults_disconnected_even_with_forged_session(bridge_fixture):
    _, binding, _, note = bridge_fixture
    result = run_stdio(payload=json.dumps(request(binding, note)) + "\n")
    error(json.loads(result.stdout), "disconnected")
    assert result.stderr == ""


@pytest.mark.parametrize("payload", ["", "{bad\n", "{}" * 9000, '{"x":1,"x":2}\n', '{"x":NaN}\n'])
def test_stdio_rejects_invalid_frames(payload):
    error(json.loads(run_stdio(payload=payload).stdout), "invalid_frame")


def test_stdio_fixture_demo_is_real_and_removed():
    demo = json.loads(run_stdio("--demo").stdout)
    assert demo["mode"] == "temporary_fixture_only" and demo["fixture_removed"]
    assert demo["runtime_status"] == disconnected_status()
    assert all(operation["ok"] for operation in demo["operations"])
    assert "hello desktop bridge" in demo["operations"][1]["result"]["content"]
    assert not Path(demo["operations"][0]["result"]["path"]).exists()


def test_stdio_status_needs_no_input_or_host_roots():
    assert json.loads(run_stdio("--status").stdout) == disconnected_status()
