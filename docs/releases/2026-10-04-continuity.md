# Task continuity — 2026-10-04

Athena keeps unfinished work, requests for help and unread outcomes reachable when the user moves to another conversation or previews a file. This is a projection of existing durable runs, not another task scheduler. It does not ask a model to poll for progress.

The default shell adds no idle button. Updates do not navigate, overwrite drafts or answer on behalf of the user. Clicking an entry opens its actual run; only a matching visible result, or an explicit dismissal, creates a durable read receipt. Needs-reply, ready-to-view, failed and stopped outcomes are distinct. The universal runtime exposes the same account-scoped discovery, exact-state acknowledgement and conversation stop capabilities.

## Acceptance

The isolated real-engine browser fixture used a deterministic provider. It verified background completion while another chat held a draft; unread result survival across reload; navigation, read receipt and original draft preservation; a request for help reachable inside Markdown preview; one answer resuming the same run; failure and cross-conversation stopping; desktop, 390 px, Arabic RTL and keyboard dismissal. No real user data was used in that fixture.

A second code review found a response-order race: a GET begun during acknowledgement could capture the old result and return after the acknowledgement, briefly resurrecting it. The final client also invalidates reads at acknowledgement settlement. Same-account authentication refresh is reconciled before treating a late unauthorized response as a lost login; failed writes are not replayed. The final full check passed 557 Python tests and 115 frontend tests, TypeScript and production build. Seven opt-in native-browser tests were skipped; native browser code was unchanged. Local Python 3.14.8T retained GIL disabled.

## Scope

This is in-app continuity. OS notifications, source-window affinity, complete service-provider replacement, real microphone/provider voice acceptance and additional native platforms remain unfinished. The Mac package remains an ad-hoc signed, non-notarized preview. Existing broker, worker, gateway and New API services are outside this release's changes.

## Published runtime

Source `0dbcd4e0013823decd0c12b6bb67730a86d7f6c2`; web image
`sha256:7c3db87762c1f232d69c56e6aabf2fba8a730ef7f514627dac36fc9d58346a41`.
The current deployment is `/opt/athena/releases/0dbcd4e`.

Two idle checks preceded normal web shutdown. The consistent SQLite backup at
`/var/backups/athena/20261004-0dbcd4e/web.sqlite3` has mode 0600 in a 0700 directory.
Private-copy schema 12→13 validation preserved every original column and row
across 43 original tables and passed repeated initialization and integrity checks.
The database, migration evidence and deployment audit remain private on the server.
Rollback requires the paired database backup, not just the previous image.

HTTPS/local health, anonymous authentication boundaries, exact HTML/JS/CSS hashes
and Python 3.14.3T GIL-disabled doctor passed. Broker `ad42ed2`, worker `8971b1a`,
gateway `5bcda5a`, edge and existing New API services were not changed.

## Production model acceptance

A real model converted a request for a simple weekend plan into a durable question.
Leaving that conversation exposed its needs-reply entry. From a new conversation,
a natural-language request to return to the unanswered question caused the model
to discover the waiting conversation and navigate the actual browser there;
the action had an applied semantic receipt. Answering resumed the original run
and produced three relevant suggestions. The navigation task's unread result
remained accessible after it left its originating conversation.

Both runs completed, with one answered help request and all 12 model requests
settled. Recorded Athena test-credit charges totalled USD 0.005076: USD 0.000945
for asking/resuming and USD 0.004131 for discovery/navigation. This is one bounded
intent pair, not a broad success rate or a future price guarantee.

The discovery/navigation run used nine requests and searched conversation history
instead of choosing the new activity projection. It establishes the visible user
outcome and existing navigation path, not model adoption of the new operation.
The new activity operation has direct engine/ownership/stop regression coverage.
Skill routing and the number of discovery calls remain a concrete optimization
baseline; no efficiency improvement is claimed from this one model trace.

## Mac preview build 8

The same source is published at:

`https://app.athena.featherhk.com/downloads/0dbcd4e/Athena-0.1.0-macos-arm64-preview.dmg`

52,725,563 bytes; SHA-256:
`d42e4caaeabb391dff80568f86807622718d07907d146e64c37a8df0ecec94d0`.

The read-only DMG passed strict nested signatures, minimum macOS 13/Apple Silicon,
Applications-link inspection, byte equality of 72 backend/skill files and frontend
artifacts, private-data exclusion, GIL-disabled doctor, four desktop contracts and
relocated sidecar authentication/Origin/restart/quit checks. Independent public
HTTPS download matched the local hash, and browser acceptance confirmed the
updated download and checksum links. The unshipped `98e0adb` candidate was replaced
before publication after the response-order fix.

This remains ad-hoc signed and not notarized. Real-account native login, microphone,
full native-browser suite and a clean second Mac were not repeated for this build.
