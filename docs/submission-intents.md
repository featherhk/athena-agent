# Durable submission and Stop receipts

**Implemented in the source tree; hosted release status is recorded separately.** This addresses quality-review finding 2: the server can accept a message, lose its HTTP response, and execute a manually retried message again. It also closes an ordering boundary in the current Stop admission fence. The guarantee concerns accepting a message and creating its run once, not exactly-once effects in external systems.

## One identity per deliberate send

At the initial click, create an immutable `client_submission_id` and payload containing the text, mode, ordered attachment IDs and expected durable session revision. Retain it separately from the editable draft, scoped to the account and conversation. An explicit retry preserves the ID and payload. A new deliberate send, even of identical words, creates a new ID. The existing window/client ID is a different identity and is not part of the content fingerprint.

An owner/session-scoped receipt table uses a unique submission ID, versioned canonical payload hash, run ID and timestamp. Create the receipt, run, user message and initial frontend source in the same SQLite transaction. Retain receipts until their conversation is deleted. Forks do not copy receipts, client intents or activation state.

After authentication and session ownership checks, resolve an existing receipt before attachment, model, credit or frontend-source checks:

- Matching payload: return the existing run and its current status, without stopping, enqueuing, charging or binding a second source.
- Different payload: return an explicit conflict without effects.
- No receipt: perform normal preflight, acquire the session control lock, then resolve the receipt again before any stop/enqueue operation. When concurrent preflight fails, check for an already accepted matching receipt before reporting the failure.

## Persist the Stop boundary

Sessions have a durable `submission_revision`. Stop and the stopping phase of interrupt advance it transactionally. A new client intent carries the revision it observed. This supplements the current in-process admission epoch; it does not replace it.

Ordering under the control lock is: resolve an accepted receipt, check the expected revision, perform any intentional interrupt stop, then atomically enqueue with its receipt. The existing single-Engine data-directory lock remains necessary; SQLite deduplication alone does not coordinate live model-task cancellation across processes.

| Situation | Required effect |
| --- | --- |
| Accepted input, then Stop, then retry | Return the original cancelled run; do not restart it |
| Old preflight finishes after Stop | Reject its old revision; no new run |
| Old POST reaches the handler only after Stop | Reject its original client revision, even if its server admission epoch is new |
| Process exits after interrupt stopped work but before enqueue | Old revision cannot execute another stop after restart |
| Process exits after enqueue committed | Resolve the durable receipt without repeating stop/enqueue |
| User deliberately sends after Stop | Use the revision from the Stop receipt and a new ID; accept normally |
| Stop response is lost or the request is still in flight | Resolve its exact durable Stop receipt before allowing another intent; a missing receipt or an unrelated snapshot is not completion proof |
| Existing run accepted before the account became unfunded | Return its receipt without another charge or new work |

Session snapshots expose the current revision. A Stop response identifies that operation and its original committed revision. An older snapshot cannot roll back a newer locally acknowledged revision. For immediate post-Stop sending, do not wait for an eventually arriving event snapshot or reuse the pre-Stop value.

## Recover without silently resending

A scoped, read-only receipt endpoint lets the client reconcile an unknown POST outcome after reload or returning to a chat. A missing receipt does not prove the POST is not still in flight. No reconciliation automatically sends a POST. An explicit retry uses the frozen ID/payload; a late receipt clears only the corresponding draft revision and never erases later words or files.

Unknown delivery needs accurate localized copy, distinct from confirmed rejection. Cross-account records are neither displayed nor queried. Restricted browser storage offers only in-page recovery. Older clients remain compatible without this new guarantee; the existing idempotent help-answer endpoint can stay unchanged.

## Stop is an identifiable operation too

A new client sends `{client_stop_id}` to `POST /api/sessions/{id}/stop`.
The cancellation transaction writes the exact Stop receipt and advances the revision
once. The engine owns completion of active-task cancellation even if the HTTP caller
disconnects. New submissions wait for the same session control lock; a receipt visible
during cancellation cannot allow work to overtake that cancellation.

`GET /api/sessions/{id}/stops/{client_stop_id}` returns `found:true` with its original
revision, or `found:false` with no completion claim. Repeating a Stop ID returns that
receipt without stopping a later task. No body and `{}` retain the old API behavior.

The client persists unresolved Stop IDs per account/conversation. It does not release
this local submission fence based on a generic snapshot, even a freshly fetched one:
the original Stop POST might still be delayed in transport. “Retry stop” explicitly
reuses the original ID. Reload/return/online recovery only reads receipts. Overlapping
Stops each need their own receipt. A revision read begun before Stop cannot restore
confidence after a lost Stop response. Stopping also preserves outstanding drafts;
a late message receipt cannot clear them.

## Client behavior

The normal composer gains no permanent controls. Only uncertain delivery shows a
scoped recovery notice with the original text, status lookup and explicit retry.
Retrying an unchanged draft uses its existing intent, including the original mode;
editing the draft creates a new deliberate send. Pending intents take precedence
over a later clarification, so an original message is never misused as its answer.
A confirmed late receipt only clears its exact unchanged draft revision. Reattaching
the same file after removal counts as a new draft and is preserved.

New clients require the server revision and receipt fields, rather than silently
falling back to unprotected sends. Updates should deploy the backend and its matching
web assets together. Legacy clients still work but lack these guarantees.
Session-storage restrictions retain only in-page recovery; tab closure is outside
the persistence guarantee. A receipt guarantees one admitted run, not exactly-once
external effects. Live-side-effect recovery must still verify the external system.

## Acceptance before release

Use real SQLite and Engine/HTTP fixtures. Twenty concurrent retries after a dropped successful response must produce one receipt, run and user message. Inject an exception between writes and require full rollback. Payload conflicts must leave all existing work intact. Test duplicate interrupt while a newer task runs, Stop/preflight and Stop/request-body ordering, both crash boundaries, exhausted credit after acceptance, and original frontend source retention.

Client tests must exercise actual handlers: reload and manual retry, account/session changes, late receipts, edited drafts, storage failure, an older snapshot arriving after a Stop receipt, and immediate post-Stop sending. A new ID with identical text must remain a valid new task. Verify schema migration and restart on disposable copies before touching hosted state.
