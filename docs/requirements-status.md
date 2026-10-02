# Requirements and release readiness

Reviewed 2026-10-03 against the product requests, repository and live local runtime. This is an acceptance inventory, not a marketing feature list. A code path, a passing synthetic test and a verified customer outcome are different levels of evidence.

## Preserve the product contract

- Athena is a general personal assistant. Start a conversation without choosing a model or entering a provider key. Show status and useful results progressively; keep stop, history and recovery in an independent shell.
- Athena can adapt its harness and communication surface through versioned registrations in the external file area. Default providers must remain recoverable. The target is all services, not just three policy hooks.
- A successful task and a comfortable user experience outrank self-modification. Do not redesign the interface while the requested flight, file or reminder remains unfinished.
- Users can ask for an application change, an external action or a reply preference. These must produce the appropriate durable effect rather than merely describing instructions.

## Implemented and exercised locally

| Requirement | Current evidence | Remaining boundary |
| --- | --- | --- |
| Python 3.14T; safe concurrency | Doctor checks free threading after imports; event-loop ownership and dedicated SQLite worker; concurrency regressions | Production Linux and packaged runtime need separate doctor checks |
| Database-backed projects, sessions, runs, requests, events | SQLite transaction and reconnect tests; live existing conversations survive frontend updates | Backup restoration and migration drills are still needed for releases |
| Minimal text interface and readable state | Shared classical typography/materials; fixed controls; Markdown; presence transitions; narrow-screen review | Ongoing user testing; generated fixed cards can compete for space |
| Nine app languages without flags | ar, en, es, fr, ja, ko, pt, zh-Hans, zh-Hant; system matching, English fallback, RTL, account preferences | Native OS language detection will need client adapters |
| Model identity and switching | Short Athena identity plus configured base-model name; versioned profiles and request snapshots; explicit cache boundary | Chat Completions is the integrated protocol; other provider protocols and automatic quality routing remain work |
| Stable prompt prefix; progressive skills | Directory packages with SKILL.md metadata and arbitrary resources; persisted prefix boundary | Measure actual cache effectiveness over long realistic tasks, not just prefix equality |
| Recoverable compression and memory layers | Tool clipping preserves originals; versioned user/project/session memories; correction, withdrawal and recovery; bounded recall; context inspection/exclusion; target-budget reports | Semantic conflict resolution, relevance ranking and retrieval evaluation are not mature; withdrawal does not erase historical transcripts |
| Queue, interrupt, stop and reconnect | Durable transitions and preserved partial replies; tool-call pairing; browser disconnect does not cancel | Cannot promise exactly-once external effects after an unknown remote outcome |
| Session forks and cross-session context | Immutable history boundary; copied jobs paused; owned-session lookup; context projection tests | Subagent lineage is reserved, not implemented |
| Arbitrary timed and script-triggered work | No-model condition checks wake the source conversation; task state stored in SQLite | Local node must be awake; cloud scheduling, notifications and executor idempotency need release validation |
| Agent operates application controls | runtime app discovery, visible-session navigation, views, previews, attachments, stop/reset; durable claim and receipt | Browser-only gestures, passwords and native permissions still need user interaction; multi-window targeting can improve |
| User-help mechanism | Durable waiting state; questions, options, files, same-run resume; cancellation and ownership tests | Phone-call help and native handoff need the voice/client path |
| Arbitrary themes and task-specific surfaces | Custom hex colors with readable contrast, placements, iframe bridge, text effects, presence replacement, live registration | Full spreadsheet/video workspaces depend on real generated functionality; registration alone is not success |
| Accounts, profile and password form | Local hashed credentials, owner scoping, name/avatar, fixed secret form | This alone is not hosted identity, billing or tenant execution isolation |
| Developer observability | API usage, cache totals, estimated cost, run trace and recoverable outputs | Cost estimates are not a billing ledger; unsupported usage must remain unknown |
| Input/output files and previews | Owned attachments/outputs; Markdown, HTML, image and bounded XLSX preview; download | XLSX cached cells are not a calculation engine; broader document formats and large-file UX remain work |
| Code/data separation and reset | External immutable registrations; source-write protection; repository guards; reset preserves chats/files | Recovery of arbitrary future service providers still needs a service host |

## Current report: Activity errors

The reported screenshot was reviewed against the live local database and authenticated HTTP API. Six sampled live trace requests returned HTTP 200. In a fresh in-app browser, existing activities of 2, 8 and 37 steps loaded reasoning, tool calls and outputs without console errors. The original failure has **not** been reproduced; a stale frontend was found in the existing browser, but that observation alone does not prove the cause.

The Activity viewer already retries on reopening. It now also retries a failed load when connectivity resumes or the page becomes visible, while preserving any previously loaded records. There is no background retry loop. A reproducible regression must retain the actual HTTP status and source build; a generic error screenshot is insufficient to infer lost data.

During that check, fixed generated cards obscured the workspace's return control. The return control now lives in the protected top bar. This is a distinct, reproduced interaction bug.

## New release scope, currently being built

| Workstream | Release acceptance |
| --- | --- |
| Natural voice conversation | One call action; microphone permission; turn detection; automatic send; speech playback; interruption cancels playback and model work correctly; hang-up releases devices; reconnect does not duplicate a turn; text transcript survives; multilingual and accessibility fallback; real provider audio tested |
| Central accounts and model gateway | Client never receives platform provider credentials; existing identities migrate safely; only an explicitly enabled preview account with positive balance can consume; all other users fail closed; registration cannot self-grant access; no recharge UI |
| Credit accounting | Server-side request reservation, bounded usage, idempotent settlement/release and uncertain-outcome handling; text and voice share enforcement; concurrent requests cannot overspend |
| Hosted web runtime | HTTPS and allowed-origin enforcement; account ownership; independent execution boundary; no untrusted agent command on the shared New API host; rate limits, logs, backups and rollback; preserve existing New API service |
| macOS distribution | Self-contained .app and drag-install DMG, no user Python/Node setup; local data outside bundle; clean exit/relaunch; Apple signing/notarization; microphone permission; real install and Gatekeeper test |
| Website and download distribution | Athena first product; same design language; remove unverified old company claims; truthful release labels; publish only tested download/build references; valid DNS/TLS and rollback |

A patch under construction is not an integrated pass. Refer to the final validation record and distribution documentation for the delivered state.

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

## Remaining release gaps

1. **Complete service-provider replacement.** Engine, Store, model transport, scheduler and authentication remain concrete integrations. Implement one end-to-end provider graph with immutable in-flight bindings, staged activation and independent recovery before declaring the harness self-evolving.
2. **Integrated browser and computer control.** Ad-hoc commands installing a browser do not constitute a packaged, supported executor. A usable product needs managed browser identity, login handoff, capability discovery, screenshots/semantic reads, cancellation and native platform adapters. Browser automation usually benefits from DOM/accessibility; desktop visual control needs a vision-capable model/provider.
3. **Four-platform clients.** macOS packaging does not deliver iOS, Android or Windows. Separate central identity/credits, persistent execution nodes and view clients; mobile OS background limits need explicit product behavior.
4. **System notifications and offline operation.** A paused browser should not be the only place a completed background job can report. Cloud and local wake-up guarantees must be distinguished from computer sleep and provider outages.
5. **Subagents, connectors and extension sharing.** Lineage, cancellation, fork semantics, credentials and installation provenance still need implementation. These are not implicit properties of the registry.
6. **Optional fast decision models.** JEV-like admission/routing models and mu-inspired improvements are research optimizations, not dependencies for first task success. Do not claim configured models or cheap official endpoints without verified sources.
7. **Broad requirement-driven quality.** The Codex-command minimum and personal-assistant cases cover mechanics and selected intents. Add real independent cases: ambiguous dates, unavailable provider, insufficient balance, language switching during speech, overlapping scheduled jobs, stale clients, malicious files, extension failure, multi-window navigation and uninstall/reinstall. Record observed effects and recovery, not self-reported success.
8. **Production operations.** Account recovery, abuse response, retention/deletion policy, export, signed updates, observability redaction, schema rollback and restoration drills remain release responsibilities. Avoid introducing payment collection before pricing/refund rules and reconciliation exist.

## Documentation consistency

README and older product notes describe different implementation snapshots. The older statements that preferences are browser-only, browser navigation cannot be verified, and all visuals await browser review are stale: preferences are account-backed and Safari/in-app navigation and visuals have been exercised. Conversely, no current document should upgrade policy hooks to complete service replacement, a local account to production billing, or an unsigned artifact to a publicly trusted macOS release.
