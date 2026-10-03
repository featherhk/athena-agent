# Requirements and release readiness

Reviewed 2026-10-04 against the product requests, repository and recorded local/hosted acceptance. This is an acceptance inventory, not a marketing feature list. A code path, a passing synthetic test and a verified customer outcome are different levels of evidence.

## Preserve the product contract

- Athena is a general personal assistant. Start a conversation without choosing a model or entering a provider key. Show status and useful results progressively; keep stop, history and recovery in an independent shell.
- Athena can adapt its harness and communication surface through versioned registrations in the external file area. Default providers must remain recoverable. The target is all services, not just three policy hooks.
- A successful task and a comfortable user experience outrank self-modification. Do not redesign the interface while the requested flight, file or reminder remains unfinished.
- Users can ask for an application change, an external action or a reply preference. These must produce the appropriate durable effect rather than merely describing instructions.

## Current implemented capabilities and evidence

| Requirement | Current evidence | Remaining boundary |
| --- | --- | --- |
| Python 3.14T; safe concurrency | Doctor checks free threading after imports; event-loop ownership and dedicated SQLite worker; concurrency regressions | Local, hosted Linux and named Mac bundles have passed doctor checks; repeat for each release artifact |
| Database-backed projects, sessions, runs, requests, events | SQLite transaction and reconnect tests; live existing conversations survive frontend updates | Backup restoration and migration drills are still needed for releases |
| Minimal text interface and readable state | Shared classical typography/materials; fixed controls; Markdown; presence transitions; narrow-screen review | Ongoing user testing; generated fixed cards can compete for space |
| Nine app languages without flags | ar, en, es, fr, ja, ko, pt, zh-Hans, zh-Hant; system matching, English fallback, RTL, account preferences | Native OS language detection will need client adapters |
| Model identity and switching | Short Athena identity plus configured base-model name; versioned profiles and request snapshots; explicit cache boundary | Chat Completions is the integrated protocol; other provider protocols and automatic quality routing remain work |
| Stable prompt prefix; progressive skills | Directory packages with SKILL.md metadata and arbitrary resources; persisted prefix boundary | Measure actual cache effectiveness over long realistic tasks, not just prefix equality |
| Recoverable compression and memory layers | Tool clipping preserves originals; versioned user/project/session memories; correction, withdrawal and recovery; bounded recall; context inspection/exclusion; target-budget reports | Semantic conflict resolution, relevance ranking and retrieval evaluation are not mature; withdrawal does not erase historical transcripts |
| Queue, interrupt, stop and reconnect | Durable transitions and preserved partial replies; tool-call pairing; browser disconnect does not cancel | Cannot promise exactly-once external effects after an unknown remote outcome |
| Session forks and cross-session context | Immutable history boundary; copied jobs paused; owned-session lookup; context projection tests | Delegation has separate lineage and immutable result receipts; a fork starts no copied subtask |
| Arbitrary timed and script-triggered work | No-model condition checks wake the source conversation; task state stored in SQLite | Local node must be awake; cloud scheduling, notifications and executor idempotency need release validation |
| Agent operates application controls | runtime app discovery and bounded client observations; source-window binding in the current checkout, explicit multi-window targets; cross-session navigation, views, previews, attachments and stop/reset; semantic receipts and action-stream recovery | Source binding does not automatically follow a document reload; confirm the release artifact separately. Browser-only gestures, passwords and native permissions still need user interaction; observations are not pixel/content verification |
| User-help mechanism | Durable waiting state; questions, options, files, same-run resume; cancellation and ownership tests | Phone-call help and native handoff need the voice/client path |
| Arbitrary themes and task-specific surfaces | Custom hex colors with readable contrast, placements, iframe bridge, text effects, presence replacement, live registration | Full spreadsheet/video workspaces depend on real generated functionality; registration alone is not success |
| Accounts, profile and password form | Local and hosted credentials, owner scoping, name/avatar and fixed secret form; central model gateway and request reservation/settlement ledger for funded test accounts | No recharge or payments; account recovery, production reconciliation and tenant-isolation evaluation remain open |
| Developer observability | API usage, cache totals, estimated cost, run trace and recoverable outputs | Cost estimates are not a billing ledger; unsupported usage must remain unknown |
| Input/output files and previews | Owned attachments/outputs; Markdown, HTML, image and bounded XLSX preview; download | XLSX cached cells are not a calculation engine; broader document formats and large-file UX remain work |
| Code/data separation and reset | External immutable registrations; source-write protection; repository guards; reset preserves chats/files | Recovery of arbitrary future service providers still needs a service host |

## Current report: Activity errors

The reported screenshot was reviewed against the live local database and authenticated HTTP API. Six sampled live trace requests returned HTTP 200. In a fresh in-app browser, existing activities of 2, 8 and 37 steps loaded reasoning, tool calls and outputs without console errors. The original failure has **not** been reproduced; a stale frontend was found in the existing browser, but that observation alone does not prove the cause.

The Activity viewer already retries on reopening. It now also retries a failed load when connectivity resumes or the page becomes visible, while preserving any previously loaded records. There is no background retry loop. A reproducible regression must retain the actual HTTP status and source build; a generic error screenshot is insufficient to infer lost data.

During that check, fixed generated cards obscured the workspace's return control. The return control now lives in the protected top bar. This is a distinct, reproduced interaction bug.

## Initial preview scope and historical validation

| Workstream | Release acceptance |
| --- | --- |
| Natural voice conversation | One call action; microphone permission; turn detection; automatic send; speech playback; interruption cancels playback and model work correctly; hang-up releases devices; reconnect does not duplicate a turn; text transcript survives; multilingual and accessibility fallback; real provider audio tested |
| Central accounts and model gateway | Client never receives platform provider credentials; existing identities migrate safely; only explicitly enabled accounts with positive balance can consume; unfunded accounts fail closed; registration cannot self-grant access; no recharge UI |
| Credit accounting | Server-side request reservation, bounded usage, idempotent settlement/release and uncertain-outcome handling; text and voice share enforcement; concurrent requests cannot overspend |
| Hosted web runtime | HTTPS and allowed-origin enforcement; account ownership; independent execution boundary; no untrusted agent command on the shared New API host; rate limits, logs, backups and rollback; preserve existing New API service |
| macOS distribution | Self-contained .app and drag-install DMG, no user Python/Node setup; local data outside bundle; clean exit/relaunch; Apple signing/notarization; microphone permission; real install and Gatekeeper test |
| Website and download distribution | Athena first product; same design language; remove unverified old company claims; truthful release labels; publish only tested download/build references; valid DNS/TLS and rollback |

The table states acceptance goals; it does not mark every row complete. The following early milestones are historical. The [initial preview](releases/2026-10-03-preview.md), [hosted execution update](releases/2026-10-03-runtime.md) and later release receipts identify the builds actually tested and supersede their earlier pending DNS, download and execution statements.

### Validation recorded in this update

- Full repository check: 313 Python tests, 41 frontend logic tests, TypeScript and production build passed. The local Python 3.14.8T and server Linux Python 3.14.3T both report GIL disabled after dependency imports.
- Local application data was backed up before an idle, graceful restart. Existing Activity records and continuation pages were read successfully in the updated browser; original report's cause remains unconfirmed.
- Central gateway and web containers are running on server loopback only. A real DeepSeek stream completed and settled from provider-reported usage, leaving no held credit. An explicitly authorized preview account received operator-approved test credit; new accounts have zero credit and cannot call models, and no recharge exists. Public DNS/TLS activation is awaiting confirmation.
- Voice UI and lifecycle tests pass, including unavailable-service behavior without opening the microphone. Audio credentials and an actual microphone/speaker call are still missing.
- An Apple Silicon test DMG was generated with a bundled free-threaded Python and non-secret cloud endpoint. Relocation, startup, authentication boundary, cross-site rejection, restart and shutdown smoke checks passed. Native window automation stalled; do not count native UI, clean-Mac installation, actual cloud login or real microphone behavior as verified. Apple signing/notarization remains deferred by the operator.
- The redesigned Feather website has been published, with Athena first and truthful private-preview labels. Download and web links remain hidden until their endpoints pass release checks.

## Clarification and work-surface follow-up

Clarification replies now carry an immutable question/answer receipt on the original message. The UI displays a compact, expandable “Information provided” entry, retaining attachments. Model protocol content is unchanged; answered receipts survive forks without copying pending requests. The additive migration annotates only exact, unambiguous legacy matches. A copy of the local database migrated its one answered request while preserving a digest of all protocol message fields.

Work surfaces now have host-owned close/reopen, placement, drag and resize controls. The same effects are available through `runtime registry action:layout`, with a separate durable layout revision and optimistic concurrency check. Layout and its refresh event commit together; a stale browser cannot overwrite a newer source registration or layout. Content updates respect a user's hidden state. Forks copy layout independently; reset clears it while retaining source versions and files. `runtime app open/close target:surfaces` controls the management view.

Automatic floating panels fit their contents and use available margins without covering the reading column, each other, the top controls or composer. Overflow and narrow screens return to the conversation flow. Explicit user positioning may cover text but remains inside the available area. A workspace uses its full host area, regardless of previous floating width. Sidebar width, presence and full-workspace sizing remain host-managed.

Ordinary native form drafts survive hide/reopen and placement/conversation changes within the same page, using bounded account/session/version-scoped browser memory. Password, OTP, payment and file controls are excluded. This does not persist arbitrary JavaScript/Canvas state or preserve edits through a page reload; authored documents still need an explicit save.

Validation: 327 Python tests, 50 frontend logic tests, production build and free-threading doctor passed. Isolated browser acceptance exercised drag, keyboard resize, content-preserving hide/reopen, layout selection, full-workspace exit, expanded clarification receipts, durable page reload and a 390×844 mobile viewport. The fixture used a mock provider and disposable data, not live user commands. Public/cloud deployment and DMG validation above describe the previous release and must be repeated for a new distributed build.

## Context budget follow-up

Execution and context inspection now share one provider-message projection. The default compactor first folds exactly equal completed tool outputs, then retains as much original head/tail as the estimated budget permits. Both manual and automatic paths use that planner; raw messages, reasoning and tool-call slots remain intact. Automatic compaction protects the latest tool-call round, including freshly recovered evidence, from immediate re-clipping. Invalid plugin plans cannot override that protection. No-op compaction does not create another prefix boundary.

Offline evaluation on a disposable copy of the three largest local conversations reached the estimated 20k target in one case. Two remained above target because preserved user/assistant messages and reasoning already occupied the budget; none of these three samples contained exact duplicate outputs. This is evidence for the remaining limitation, not a claim that deduplication improved those conversations. Original data was unchanged and no model requests were made. Recoverable handoff summaries and memory lifecycle quality still need work.

The regression tests exercise the actual automatic execution path, exact inspection/payload agreement, immutable originals, attachments and memory notes, retained reasoning and call pairing, stable repeated projection, and protected recovery results when the requested budget is unattainable. The full check passed 331 Python tests, 50 frontend logic tests and the production build; the Python 3.14.8T doctor still reports GIL disabled.

## Graceful local updates

The CLI and macOS launcher now share a server adapter that drains open event streams before the HTTP server waits for connections to close. This fixes a reproduced five-second graceful-shutdown timeout with an idle browser attached. Store and engine teardown still happen after HTTP draining. Tests require clean shutdown logs and verify that active partial replies become durable interrupted records without automatic replay; disconnecting a view alone does not cancel its work.

## Memory lifecycle follow-up

The universal runtime operation now supports memory add/list/read/update/forget/history/restore. Mutations carry the expected current version; simultaneous corrections cannot silently overwrite each other. Same-scope exact additions are idempotent. Every revision retains its source conversation/run; restoration creates a new revision and the original row remains intact. Scope checks apply equally to reads, history and mutations. Forks inherit an independent snapshot of active session memories with parent/version provenance; forgotten entries are not reactivated.

Automatic recall uses whole entries within an 8,000-character note budget. Omitted entries remain searchable and the note reports omissions or further candidates. A policy's selected IDs are resolved again in the database transaction before admission, so a slow plugin cannot resurrect a concurrently forgotten entry or its stale content. Recent content-free withdrawal receipts clarify older snapshots. Old messages and prompt prefixes remain unchanged.

“Forget” means stop using the entry for subsequent default retrieval and automatic recall. It is not physical erasure of chat, request or revision history. Semantic matching/merging and full data-erasure workflows remain unimplemented; the agent must identify and update the appropriate entry through the documented skill.

This follows the useful separation between bounded working memory and searchable history in [Hermes memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/), while retaining Athena's SQLite audit trail and concurrent multi-session ownership model.

Validation: 341 Python tests, 50 frontend logic tests, TypeScript/production build and the free-threading doctor passed. List/history responses are bounded and paginated without clipping individual entries. A private copy of the local database migrated to schema 8 with unchanged original message/memory fields; it contained no memory rows, so legacy memory provenance is additionally exercised with synthetic old-schema records.

## Managed browser follow-up

A pinned native browser adapter is connected to the universal runtime tool. Account-scoped persistent profiles, conversation tab ownership, latest-observation checks, recoverable full page evidence and browser action receipts are implemented. History remains queryable without starting a browser. Handoff uses the existing durable user-help wait; form contents are not duplicated into browser audit arguments. The host sends each webpage command once and records uncertain outcomes instead of relying on the CLI's automatic transport retries.

Validation: `npm run check` passed 357 Python tests and 50 frontend tests, TypeScript and production build. The GIL remains disabled on Python 3.14.8T. A separate 20-test run included real Chrome with a disposable profile and synthetic loopback site: one submitted form, resulting page text, exact downloaded CSV bytes, PNG screenshot and login-cookie persistence after shutdown/restart, plus the native bundle allowlist. This is local/headless executor verification, not live external login, complete computer use or a newly built/released DMG. Source and package instructions now include the pinned native binary/license; clean-machine browser installation is still missing.

## Event-storage follow-up

Repeated full snapshots were the main source of local database growth. Message writes now coalesce over a 50 ms interval, with immediate first reasoning/answer chunks and a final flush on completion, provider error or stop. Message updates and event insertion are atomic. The public stream/cursor contract is unchanged; checkpoints and exact prefix deltas are decoded on the server. Original messages, thinking, tool results and request inputs are retained.

An offline archive command uses the same exclusive lock as the runtime, creates a private backup and verifies each recovered old payload before committing a batch. On a private copy, 103,865 original events and 33 other tables remained identical; the active database file shrank from 1,012,076,544 to 93,134,848 bytes (90.8%). Backup copies occupy additional space. See [event storage and recovery](event-storage.md). Abrupt process failure can still lose the unflushed tail; normal stop/error flushing is separately tested.

Validation: 370 Python tests and 50 frontend tests passed, along with TypeScript, the production build and the Python 3.14.8T free-threading doctor. The opt-in native-browser test was not rerun for this storage-only change; its separate prior acceptance remains documented above.

## Background input provenance

Schema 11 adds an optional message origin. Scheduled and condition-triggered inputs snapshot their task ID, historical title, trigger type and occurrence in the same transaction as the queued run and wake record. The provider protocol role remains compatible; the source text and previously persisted context/request snapshots are not rewritten. A task waking an otherwise empty chat no longer takes over its automatic title.

The default view uses one expandable task/activity row instead of a user bubble containing the internal dispatch text. Actual assistant replies, including reminders, remain visible. Activity entries identify the input as a scheduled/condition trigger, and the universal tool returns structured provenance in session reads and original-message recovery. Other accounts cannot read it; forks inherit historical provenance without duplicating wake records or activating copied jobs.

Legacy annotation requires a recorded wake, matching conversation/run and the exact historical input format. It extracts the historical title from that input, never from a potentially renamed task. Unknown formats and identical human-authored text are left alone. Recurring reply verbosity and delivery preferences remain a separate product issue; this change does not silence requested reminders or alter task schedules.

Validation: 379 Python tests and 52 frontend tests passed, along with TypeScript, the production build and the Python 3.14.8T free-threading doctor. The local upgrade created a private backup and annotated 10 historical wakes; all original columns across 38 tables remained identical. Browser acceptance verified collapsed task rows, expanded original activity, visible assistant replies and a 390 px layout without horizontal overflow. The existing workspace and developer panel were restored afterward. This release has not yet been deployed to the cloud or rebuilt into a DMG.

## Delegation and queue-order follow-up

Schema 12 adds durable child jobs and waits. Athena can spawn bounded independent tasks, work alongside them, wait without provider polling, collect referenced results and cancel a branch through the existing universal tool. Child questions and files appear in the main conversation; costs include child requests while context measurements remain local to each model window. Forks retain historical receipts without duplicating child execution. Restart reports an interrupted child instead of replaying it. Operating details and limits live in the [delegation skill and design](delegation.md).

The queue regression also exposed an existing context-order defect: a second message sent during the first answer could later be projected before that answer. A first-claim admission anchor now puts it after the prior run's output, preserving original message sequence/content and already-submitted prefixes.

Validation: 394 Python tests and 53 frontend tests passed, with TypeScript, production build and the Python 3.14.8T free-threading doctor. Tests cover actual loop concurrency, wait/finish and stop/spawn races, cancellation at a child's own tool boundary, limits, ownership, same-run user-help resumption, attachments, output bytes, usage, forks and restart. Isolated browser acceptance verified refresh during help, answer/resume, expanded raw child activity, Markdown preview and branch stop at desktop and 390 px widths. The live DeepSeek case passed with two completed children and a correct parent synthesis: six requests, 12,813 tokens. It is a single bounded intent sample, not broad quality evidence.

A private database copy passed repeated migration with unchanged original columns across all 38 existing tables. The local runtime was then gracefully stopped while idle, backed up, upgraded with the same full-column comparison and restarted. Its existing workspace and developer view reconnected successfully. Synthetic acceptance records remain outside the checkout and real chats. Cloud and DMG releases still require a separate rebuild/deployment and acceptance.

## Public preview deployment — 2026-10-03

The current chat origin, separate download origin and Feather marketing pages are served over valid HTTPS by Tencent Cloud. The deployed image includes delegation and the latest runtime fixes. The final Mac DMG contains the same runtime plus corrected packaging: mounted-image signature verification, real central login, native window startup/quit and the bundled browser fixture passed. Public downloads match the manifest hash. An unfunded new account receives HTTP 402 without a reservation. See the [release receipt](releases/2026-10-03-preview.md) for exact revisions and limits; this supersedes earlier pending-DNS and pending-DMG statements above.

## Observable interaction release — 2026-10-04

The frontend now reports bounded, authenticated semantic observations; runtime actions can address a particular client and verify their resulting state. Cross-conversation navigation followed by file preview passed real browser and production model acceptance. A deployment-induced 502 exposed a permanently closed action stream; bounded reconnect and failed-run intent cleanup now have regression coverage and a real fault-injection pass. The web and build 7 Mac DMG use the same runtime source. See the [release receipt](releases/2026-10-04-interaction.md) for the exact artifacts, failure history, billing evidence and remaining boundaries.

## Task continuity — 2026-10-04

Background work, requests for help and unread outcomes now remain reachable across conversations and inside file preview. A compact control appears only when relevant; it never navigates automatically or replaces a draft. The universal runtime exposes the same account-scoped discovery, acknowledgement and conversation stop. Durable exact-state read receipts prevent a stale acknowledgement from hiding a newer outcome. See [task continuity](task-continuity.md) for the contract and isolated real-engine browser acceptance, and the [published web/build 8 receipt](releases/2026-10-04-continuity.md) for real-model navigation/help resumption, exact charges and remaining discovery inefficiency. This is in-app continuity, not OS notification delivery or shared files across execution nodes.

## Source-window affinity — current checkout

Messages now wait for the target conversation to commit in the frontend and for its observation to be accepted before binding a source client to the run. Default UI actions use that source after navigation; a lost, stale or invalid source produces an error instead of operating another window. Inputs without source metadata retain the unique-foreground-client fallback. Explicit client targeting remains available after inspecting the current client state. The source summary is exposed through runtime discovery without rewriting the stable prompt prefix.

A help reply resumes the original run with its original source, even when answered in another window. Forks, scheduled inputs and new child runs do not inherit that binding. Reloading a document restores its tab-local conversation selection and drafts, but creates a new client identity; continuing an older run's UI interaction then requires a verified explicit target. A fresh tab may seed its initial selection from shared storage, while existing tabs keep their own place. This is browser-local continuity, not cross-device conversation or file synchronization.

These changes are implemented in the checkout. They do not certify the currently published web image or DMG; record artifact-specific validation and deployment in a release receipt. See [frontend state](frontend-state.md) for targeting and recovery boundaries.

## Remaining release gaps

1. **Complete service-provider replacement.** Engine, Store, model transport, scheduler and authentication remain concrete integrations. Implement one end-to-end provider graph with immutable in-flight bindings, staged activation and independent recovery before declaring the harness self-evolving.
2. **Browser completion and computer control.** Concrete macOS and optional Linux container adapters support persistent identity, owned tabs, semantic reads, forms and screenshots/downloads. The native fixtures verify a single form submission, downloaded bytes and cookies across restart. See [browser execution](browser-execution.md) and [Linux execution](linux-execution.md). Cloud browsers have no remotely accessible login window; handoff reports that limitation instead of creating an impossible wait. Observed file-input uploads have passed local native fixtures and isolated Linux acceptance, including exact Chinese filenames and no replay after restart; see the [interaction and browser receipt](releases/2026-10-04-interaction.md). Custom file choosers/popup ownership, headed external login acceptance, vision input and full desktop control remain unfinished.
3. **Four-platform clients.** The iOS WKWebView host builds for Simulator and unsigned iPhone, with lifecycle recovery and queued native download sharing. Real-device signing/install, microphone and native UI acceptance remain open; see [iOS](ios.md). Android and Windows are not delivered. Mac local sessions/files are separate from the cloud node; shared login alone does not synchronize them.
4. **System notifications and offline operation.** A paused browser should not be the only place a completed background job can report. Cloud and local wake-up guarantees must be distinguished from computer sleep and provider outages.
5. **Delegation depth, connectors and extension sharing.** Bounded subtask lineage, waiting, cancellation, main-conversation help/files and fork receipts are implemented; running-child follow-up messages, cross-node delegation and broad quality evaluation remain work. Connector credentials and installation provenance are not implicit properties of the registry.
6. **Optional fast decision models.** JEV-like admission/routing models and mu-inspired improvements are research optimizations, not dependencies for first task success. Do not claim configured models or cheap official endpoints without verified sources.
7. **Broad requirement-driven quality.** The Codex-command minimum and personal-assistant cases cover mechanics and selected intents. Add real independent cases: ambiguous dates, unavailable provider, insufficient balance, language switching during speech, overlapping scheduled jobs, stale clients, malicious files, extension failure, multi-window navigation and uninstall/reinstall. Record observed effects and recovery, not self-reported success.
8. **Production operations.** Account recovery, abuse response, retention/deletion policy, export, signed updates, observability redaction, schema rollback and restoration drills remain release responsibilities. Avoid introducing payment collection before pricing/refund rules and reconciliation exist.

## Documentation consistency

Release receipts describe named artifacts and preserve historical outcomes; a later web deployment does not silently certify an older or newly rebuilt DMG. Account-backed preferences, directed browser UI actions and recorded visual acceptance are implemented. None establishes complete service replacement, production billing, real audio-call acceptance or an Apple-notarized release.
