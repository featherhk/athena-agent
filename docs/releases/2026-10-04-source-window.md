# Source-window continuity — 2026-10-04

Conversation navigation and fixed-view actions now default to the browser window whose message started the run. The binding survives conversation navigation within that document. An expired, closed, reset or reauthenticated source never silently transfers control to another window. Each tab also restores its own conversation and draft after reload. Account preferences and registered components retain their existing scopes.

The browser waits for a committed conversation and an acknowledged observation before sending a message. Observation requests have a real five-second transport deadline, so a stalled request cannot permanently occupy the reporting channel. A submission rejected before dispatch retains its draft and says explicitly that it was not sent. An uncertain mutation response is not automatically retried.

## Acceptance and corrections

Real-engine fixtures with a deterministic provider verified first-message binding; two foreground windows showing the same conversation; navigation followed by another interface action in the originating window; preservation of the other window's draft after reload; and no fallback when the source disappeared. A server-side network black hole demonstrated bounded failure and successful retry without refreshing. Queueing and stopping still produced durable interrupted/cancelled states.

The first fault-recovery trial exposed a second race: an immediate model action could be invalidated by the composer's automatic clearing. The final client waits for submission cleanup to commit and report before claiming. The server permits one narrowly verified revision transition for source-owned, implicit-revision view actions: only the unsent-input flag may change from true to false. Explicit revisions, other semantic changes, downloads, sign-out and attachment operations remain strict. The private guard never enters model or browser output. Manual deletion of all draft text is indistinguishable from automatic clearing at this observation granularity; see [the exact contract](../frontend-state.md).

The final check passed **601 Python tests and 145 frontend tests**, TypeScript and production build. Seven opt-in native-browser tests were skipped; browser execution adapters were unchanged. Local Python 3.14.8T retained GIL disabled. Independent review covered claim boundaries and private metadata exclusion.

## Published web runtime

Source: `241ef5c226290d9809023594f8fae68c269f62af`.

Web image: `sha256:6488fc09b4ad43156e6a91b40ecf20263ce296a16e3ee91c48e1f6ab7d581b0f`.

Two idle checks preceded normal web shutdown with exit 0. The consistent backup and private audit are under `/var/backups/athena/20261004-241ef5c/`, with directory mode 0700 and files 0600. Schema 13→14 was initialized twice on an isolated copy: all original columns and rows in 44 tables, including settings, remained identical. Only an empty source-binding table was added. Historical messages were not assigned new browser authority.

HTTPS/local health, public HTML/JS/CSS hashes, anonymous authentication boundaries and the running Python 3.14.3T doctor passed. Runtime client version is `ab3ed00b2af14f16d5c5edba`. All other running containers retained their identities and start times, including the existing New API, execution services and credit gateway. Rollback requires preserving newly admitted work and using the paired database backup, not merely changing the image.

## Production model trial

A fresh weekend-planning request created a durable question. A second fresh conversation asked to find work awaiting a reply and return to the question. The model's first task-state query used `activity list`, then navigation obtained an applied semantic receipt. Answering the visible question resumed the original run and produced three suggestions. Both runs retained the same authenticated source client and completed; no help request was left waiting.

| Trial | Requests | Input tokens | Output tokens | Cached input tokens | Settled test-credit charge |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ask and resume | 3 | 5,099 | 428 | 3,072 | USD 0.001142 |
| Discover and navigate | 7 | 34,250 | 990 | 28,672 | USD 0.003038 |

All ten `deepseek-flash` requests settled, totalling USD 0.004180. The discovery run used seven requests versus nine in the earlier single trial, and replaced historical state reconstruction with activity discovery. It still read the target conversation and checked the action twice, with an intervening CLI wait. This is one bounded intent pair, not a general efficiency or quality benchmark. The broader [discovery cases](../discovery-acceptance.md) still require independent trials.

## Mac preview build 9

[Download](https://app.athena.featherhk.com/downloads/241ef5c/Athena-0.1.0-macos-arm64-preview.dmg) · [Checksum](https://app.athena.featherhk.com/downloads/241ef5c/Athena-0.1.0-macos-arm64-preview.sha256)

53,779,159 bytes; SHA-256 `2a1bbfd70aa6fc31d52c6d370c122d95ae2e667ae30c457ff1124d6abd1d2428`.

The clean source matches the web release. Read-only mounting, strict nested ad-hoc signatures, Apple Silicon/macOS 13 metadata, Applications link, byte equality of all 72 backend/skill files and frontend artifacts, private-data exclusion, GIL-disabled doctor, four desktop contracts and relocated sidecar authentication/Origin/restart/quit checks passed. An independent HTTPS download matched the hash; the live download page points to this immutable version.

This remains an ad-hoc signed, non-notarized test package. Real-account native login, microphone/provider acceptance and a second Mac were not repeated. Reload creates a new document identity; an older run needs an explicitly verified target to continue UI actions there. This release does not establish shared cloud/local files or a fully replaceable service graph.

## Next observed opportunities

- Replace model-driven receipt polling with a bounded harness wait that returns the actual UI outcome without extra provider turns; preserve cancellation and unknown-outcome semantics.
- Preserve recognized voice text when submission fails, distinguishing definitely unsent from unconfirmed delivery and avoiding automatic resend. The current call-ended view can hide that text.
- Give the download page a distinct loading state instead of briefly saying that the next build is being prepared while its manifest loads.
