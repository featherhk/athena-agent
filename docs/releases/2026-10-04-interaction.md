# Observable interaction surfaces — 2026-10-04

This follows the [hosted execution release](2026-10-03-runtime.md). The frontend
is an interaction instrument: observe its current state, address a particular
client, apply a change, and verify the resulting state. The default interface
does not gain another dashboard or require users to manage these contracts.

## Published runtime

- Source: `c11218d3e549da366190825ad1b1a23e4a0b37c5`.
- Web: `athena-preview:c11218d`,
  `sha256:e4c8886ac847bc4c962b3f1e36e45178c9ea9e3e8310bdce82b7153c9d4bcad1`.
- Broker: `athena-execution-broker:ad42ed2`,
  `sha256:0ebaa50cfb03072128937563d56159b7dbd91e2ba9f73554f5ae2b1d59e11b3e`.
- Worker remains `athena-execution-worker:8971b1a`; gateway remains
  `athena-preview:5bcda5a`. Edge and New API were not changed.
- Current deployment: `/opt/athena/releases/c11218d`, using `compose.yaml`,
  `compose.execution.yaml`, then the release's `images.yaml`.
- Idle checks preceded normal web shutdown and consistent private SQLite
  backups. Final backup: `/var/backups/athena/20261004-c11218d/web.sqlite3`,
  mode 0600, SQLite integrity check passed. Deployment audit is in that directory.
- HTTPS health, configured model gateway, public asset hashes and anonymous
  authentication boundaries passed. Dependency imports retained GIL-disabled
  Python 3.14.3T on Linux and 3.14.8T locally.

## Interaction acceptance

The [frontend contract](../frontend-state.md) exposes bounded semantic state
through the existing runtime tool, with token-bound client identities, freshness,
revision checks and a separate client action stream. Reports omit draft text,
passwords, form values, DOM and clipboard contents. Registered components still
use the versioned external file area. Independent stop and reset remain reachable.

Real browser acceptance against an isolated account established:

- A visible registered sidebar reported its actual position and mounted state.
- Typing a unique unsent draft reported only a boolean, with no draft text in
  the stored observation.
- An action from conversation A navigated to B; a subsequent action from A
  opened its Markdown file in the same client. Both actions had durable applied
  receipts, and the preview reported ready after rendering.
- Returning to A restored the unsent draft. Reset removed custom components,
  rotated the observed client identity and retained conversation, file and draft.
- With two real foreground clients, omitting a target was rejected without
  creating an action. Explicitly targeting the second opened only its history
  pane; the first client's draft and views were unaffected.

Tests also cover stale/conflicting observations, late reports after reset,
authentication changes, cross-account isolation, duplicate receipts, preview
load/ACK races and slow-network report coalescing. Owned file discovery now
matches the account file menu and supports an explicit owned conversation after
navigation, without moving action auditing out of its originating conversation.

Final `npm run check`: 544 Python tests passed, seven opt-in native browser tests
skipped; 97 frontend tests, TypeScript and production build passed. Native browser
acceptance was run separately for the upload changes below.

## Deployment interruption recovery

The first production model-driven acceptance navigated correctly but never opened
its preview. Caddy recorded a 502 while the web process was replaced. The browser
kept reporting its surface but retained a permanently CLOSED EventSource, so the
independent client action stream could not deliver subsequent requests. The model
ended at its step limit rather than claiming completion.

The repaired client reconnects after a successful fresh observation using bounded
backoff. A real-browser fixture injected one 502 and observed exactly two stream
connection attempts; navigation followed by preview from the original conversation
both reached applied receipts, with file readiness confirmed and no page reload.
The failed task also exposed delayed pending intents, now dismissed transactionally
at unsuccessful finalization and rejected at claim for old persisted failures.
Unknown effects already dispatched are not described as rolled back.

After deploying the repair, a fresh real model conversation completed the natural
request to find a previous conversation, navigate there and open its spreadsheet.
No operator performed the intermediate navigation or preview action. Both effects
had applied receipts. The preview initially acknowledged loading; the model
inspected the subsequent ready state before completing, and browser acceptance
confirmed the actual table. All 14 model requests completed and settled, with a
recorded Athena test-credit charge of USD 0.009256. This verifies one concrete
intent, not a broad task-success rate.

## Linux browser upload correction

The first Linux acceptance exposed a false success: the native tool reported two
selected files, while the website received only the ASCII filename. Both staged
files were readable and correct; the browser container's POSIX locale was the
cause. Setting `LANG` and `LC_ALL` to `C.UTF-8` fixed the fresh real fixture.

The host now additionally checks the actual FileList names, sizes and count.
An ambiguous control is rejected before dispatch; missing files or a cleared
control after dispatch produce an unknown result without automatic replay.
This is a fixed read-only adapter, not arbitrary model-supplied JavaScript.

Final Linux QA with this adapter established one form receipt, exact download,
PNG screenshot, retained cookie after browser restart, one two-file selection,
two exact upload receipts (including a Chinese filename), no upload replay after
restart, released staging and preserved originals. Local native cases included
ordinary, absent and hostile quoted DOM IDs, including lost responses.

During the broker update, one production browser container with an old locale
was confirmed inactive after normal web shutdown and removed. Its profile and
file mounts were retained; no active browser was forcibly reclaimed.

## macOS preview

Build 7 uses the same `c11218d` source. Apple Silicon, minimum macOS 13:

`https://app.athena.featherhk.com/downloads/c11218d/Athena-0.1.0-macos-arm64-preview.dmg`

53,830,388 bytes; SHA-256:
`426d49df657797c5e60659c47ef02bdd5bcc514e0e57f4752b87ad41e9f0a9fa`.

The final read-only DMG passed strict nested signatures, Applications-link
inspection, byte comparison of 70 backend source files and frontend artifacts,
private-data exclusion, GIL-disabled doctor and relocated sidecar
startup/authentication/Origin/restart/shutdown checks. Four desktop contract tests
passed. The public HTTPS download independently matched the local SHA-256.
It is ad-hoc signed and not notarized; this is still a test distribution.
This build did not establish microphone, real-device iOS, clean-second-Mac or
real-account native acceptance.

## Remaining boundaries

Semantic observations are not pixel verification. Complete renderer replacement
and the remaining service-provider graph are still architecture work, not
implemented merely by this observation API. Source-client affinity is not yet
persisted with each user message: multiple foreground clients require explicit
targeting. Voice-provider setup and the other limitations in the previous release
receipt remain open.

The live case also exposed refinement opportunities: conversation search required
several discovery calls, and its final wording included internal client/status
terms that ordinary users do not need. Completion visibility after navigating
away from the originating task also needs a clearer cross-conversation treatment.
These are follow-up product work, not changes silently included in this artifact.
