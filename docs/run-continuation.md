# Durable approval continuation and cancellation

Agent runs remain request-bound and serial. There is no background job queue,
automatic restart scheduler, or independently running workforce.

## Normal approval flow

A model-proposed write or command is recorded with its exact normalized
arguments and pauses for review. The run also saves a bounded, versioned
checkpoint containing the conversation, pending tool-call correlations, pinned
provider/model/privacy, context and remaining model-attempt budget.

Approving executes the recorded action once. Its actual result is committed
before the model continues synchronously. Denying records the decision and
continues with that result. Any later write or command needs its own approval;
one approval never authorizes a batch of additional actions. A batch can stop
at several independent approval boundaries without losing its tool-call IDs.

The tool response includes `run_status`. A model/provider failure after a
successful action does not turn that action into an unexecuted request. Inspect
the saved run and use Resume only when the server reports `can_resume: true`.
A repeated approval cannot execute an already-decided invocation again.

## Resume and cancellation

- `POST /runs/{id}/resume` resumes the owner's eligible saved checkpoint.
- `POST /runs/{id}/cancel` stops further work and denies pending actions.
- Run responses expose `can_resume`, `can_cancel`, `resume_blocked_reason`,
  `cancellation_requested`, `executing_tool_ids` and `uncertain_tool_ids`.

Current user/project permissions, available tool capabilities, and the pinned
provider/privacy boundary are rechecked. Revocation or a changed privacy
boundary cannot be bypassed using an old checkpoint. Concurrent operations are
serialized and fenced so an old worker cannot advance a newer execution.

Cancellation is not undo. An already-started external effect may finish; its
actual result is still recorded, while the cancelled run cannot start more
work. Inspect executing or uncertain effects before making a new request.

## Interruption and uncertainty

A 900-second execution lease exceeds the supported 600-second command timeout.
An active claim blocks competing resumes. Following an interruption, an expired
model-only checkpoint can be explicitly resumed, but there is no automatic
restart queue. A tool left executing is treated as uncertain and is never
automatically replayed. The user must inspect the actual effect before starting
a separate action. A database record cannot prove that an external effect did
not happen when execution was interrupted before its result was committed.

Legacy runs without a checkpoint cannot be reconstructed safely from partial
events. Their existing exact approvals remain usable, but the run then stays
paused with Resume unavailable. Start a new conversation turn when appropriate.

## Limits and deployment

Checkpoints are capped at 256 KB, model tool batches at 16 calls, and a run at
six model attempts. Attempts are charged before I/O and do not reset on resume.
Checkpoint contents are private server state and are not returned by run APIs.

Back up the database and run `alembic upgrade head` before deployment. Migration
0007 adds checkpoint/claim/cancellation metadata and widens run status to 32
characters; the old 16-character PostgreSQL column could not hold
`awaiting_approval` (17 characters). Existing rows and event history are retained.

Tests cover deterministic model interruptions and temporary-file effects. The
real PostgreSQL integration suite uses fresh migrated schemas and normal
bootstrap/login before testing approval, concurrent decisions, cancellation,
permission revocation and recovery. It does not establish live provider or
external email/SSH/Docker reliability.

## Desktop boundary

Run continuation does not grant access to a desktop. The separate
[desktop bridge foundation](desktop-bridge.md) remains disconnected, exposes no
pairing/listener/credential flow, and implements only a read-only POSIX adapter
and temporary-fixture demonstration. Browser/RPA and host activation remain
unavailable pending a separately reviewed implementation and user approval.
