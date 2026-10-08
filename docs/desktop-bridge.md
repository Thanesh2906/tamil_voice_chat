# Desktop bridge foundation (disconnected)

The runtime remains **disabled, disconnected and unpaired**. Implemented means
there is tested adapter code; it does not mean a user computer is reachable.
`services.desktop_bridge.status.disconnected_status()` is the public status
contract used by the API. Every capability has `available: false`.

## What is implemented

- Protocol version 1 with exact owner, project, machine and session binding.
- Operation IDs with session-local replay rejection, deadlines, bounded session
  duration, cancellation and explicit capability allowlists.
- Real POSIX read-only directory listing, text reading and literal text search.
  Results come from actual file descriptors, not generated or simulated data.
- A one-frame stdio prototype and an isolated, disposable fixture demonstration.

Browser control, desktop RPA and shell execution are unimplemented and unavailable.
There is no pairing endpoint, network listener, persistent daemon, credential
creation, host grant, background process, or installer. The ordinary API imports
only the disconnected status function; it must not instantiate a host session.

## Trust and activation boundary

Request identifiers and owner/project strings are **not remote authentication**.
`SessionBinding` is trusted, in-process harness input. It cannot be established or
changed by a request frame. Replay records are in memory, bounded to 1,024 IDs,
and never evicted to make room: a full session fails closed. A process restart
does not preserve them. A future authenticated transport must establish a fresh
session and must not reuse an old session identity or rely on these strings as
credentials. The current prototype must not be exposed to a remote caller.

A future launcher needs a separate, explicitly reviewed activation design and
real user approval for the named machine, exact filesystem roots, allowed
capabilities and duration before constructing a session. It must authenticate
the caller and bind owner, project, machine and a fresh session, provide reliable
revocation, and account for replay protection across transport reconnects.
Approving a code implementation or a fixture test does not grant host access.
Connecting a laptop to dot is **not JARVIS pairing** and does not authorize
JARVIS to access that laptop. No laptop access is exercised by these tests.

## File boundaries and budgets

Roots must be explicit, nonempty, absolute canonical directories. There are no
cwd, home-directory, environment-variable or request-provided root defaults.
Requests require absolute canonical paths inside those roots. Shared
`packages.common.safe_paths` rules exclude dependency/VCS trees and the bridge
adds credential/secret patterns. The text-extension allowlist is reused from
`services.tools.adapters`.

POSIX descriptor-relative traversal uses `O_NOFOLLOW` for every path component
and root device/inode checks, so swapped roots or symlink components fail closed.
Symlinks, multiply-linked files and non-regular files are not read. Search and
listing omit sensitive entries. No writable file descriptors are opened by the
adapter. The fixture demo itself writes only its newly created temporary files.

Limits: 200 KB per read/file; 2 MB per search; 200 searched text files; 2,000
directory entries; 100 search matches; 16 nested directory descriptors; 16 KB
input frames; one active operation; 30-second maximum operation deadline;
ten-minute maximum session duration. Output is bounded by those limits and text
matches are capped at 300 characters. A bound may conservatively report
`truncated: true` even when no more results happen to exist.

Cancellation is cooperative between bounded reads/traversal steps and drops
partial results. Revocation cancels active work. Deadlines use asyncio's
monotonic timeout as well as wall-clock validity and session expiry checks.
This is not an OS sandbox or hard real-time I/O guarantee: an individual kernel
filesystem call can stall, for example on a blocked filesystem. The prototype
has no authenticated live cancellation transport; cancellation is exercised by
the in-process tests. Mount transitions and a privileged hostile host remain
outside its security guarantees. It must not be presented as production-ready
desktop isolation.

## Run the safe demonstrations

From the repository's Python development environment:

```sh
python -m services.desktop_bridge --status
python -m services.desktop_bridge --demo
pytest tests/test_desktop_bridge.py
```

`--status` prints disconnected status. `--demo` takes no root or session options,
creates its own temporary directory, performs actual list/read/search operations,
removes the fixture, and includes the still-disconnected runtime status. It
cannot select arbitrary host roots. It is not a pairing or activation command.

With no flags, the CLI reads at most one newline-delimited JSON frame, responds
once and exits. It always creates a disconnected bridge. Supplying correct-
looking IDs or paths cannot activate it. Invalid/oversized/duplicate-key frames
are rejected, and no stdin frame can inject roots or a session grant.

## Verification boundary

`tests/test_desktop_bridge.py` uses only temporary filesystem fixtures and
short-lived Python CLI subprocesses. It checks actual file results, all four
binding fields, capability denial, strict schemas, replay exhaustion, deadlines,
cancellation, root replacement, symlinks/hard links/FIFOs, secret exclusions,
bounded reads/traversal and disconnected CLI behavior. It does not test or claim
real laptop access, operating-system UI control, network transport, credential
handling, persistent pairing or production deployment.
