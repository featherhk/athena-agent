# Input continuity and independent Stop

This change addresses ordinary interruptions: a person answers part of a question, switches chats, changes from speaking to typing, or stops work while a message is still being admitted. These paths must preserve intent without quietly starting or repeating work.

## Behavior

- Stop remains independently reachable while preparing a new message or an attachment, including the first message before the server has created its run. On narrow screens the active composer gives the text field its own row. Stopping invalidates local preparation before a first conversation exists; a late conversation response cannot send or move the draft. A late submission receipt after Stop cannot clear the retained words or files.
- A session admission generation invalidates older submissions still awaiting ownership, attachment, model or credit preflight. The generation is captured by the HTTP handler before its first await and checked under the session control lock. Network preflight does not hold that lock. A fresh message after Stop is accepted; an intentional interrupt validates first, stops older work and admits its own replacement under the same lock.
- Clarification answers share one account/conversation/request-scoped draft across typed and recognized speech input. Switching chats, hanging up and reloading the same tab retain partial answers. A valid matching answered receipt clears only the submitted revision; a late receipt cannot erase a later edit. Storage failure falls back to in-memory retention for that page.
- A spoken answer with an unknown receipt remains visible for review, including a single-question answer. Further speech is not an implicit retry of a completed form. The normal composer is not overwritten with the saved answer.
- File selection is copied before immediately clearing the native input. Count/size rejection and a late upload completion therefore do not prevent selecting the same file again or clear a newer selection.

These changes add no approval workflow, model request, default panel or permanent prompt text. Drafts remain local to the browser tab; they are not cross-device synchronization.

## Acceptance boundaries

Before the final voice/form unification, isolated browser acceptance used a real provider and disposable account to check login with a retained draft, simple material arithmetic, two-question help, switching conversations, partial-answer reload and continuation. A 390 × 844 viewport was inspected. These observations do not establish final-build visual, mobile keyboard, RTL or acoustic voice acceptance.

Permanent regressions exercise the real Engine/SQLite and HTTP admission paths, the actual React event handlers, draft storage/revisions, and form/composer bindings. Handler tests do not simulate React commits or the native file picker.

Final source validation passed **721 Python tests (7 opt-in/platform skips)**, **199 frontend tests**, TypeScript and the production build. The Python 3.14.8T doctor reports `gil_enabled: false` after dependency imports. The Stop suite includes 18 real Engine/HTTP cases. The changed native shell compiled with an actual Mach-O minimum of macOS 13; update comparison/transport tests and all 6 desktop packaging tests passed. Native result presentation also rechecks for an existing sheet after its asynchronous fetch, so it does not compete with a newly opened save or recovery dialog. No physical-device microphone or native-menu visual acceptance is implied.

The independent [product](../reviews/2026-10-04-product.md), [quality](../reviews/2026-10-04-quality.md) and [algorithm](../reviews/2026-10-04-algorithm.md) reviews distinguish reproduced issues from visual acceptance gaps. Normal message retries after an unknown server response still need durable submission idempotency; these fixes do not promise exactly-once external effects. Noise-triggered task cancellation, user timezone propagation, background-task visibility and complete context budgeting remain separate priorities.

The server admission fence begins when the message HTTP handler is entered. It cannot identify an older POST still delayed in transport, authentication middleware or request-body reading when Stop completes. A subsequent client-intent protocol needs an immutable submission ID and a durable session revision to cover that ordering and safe retry after restart.

## Publication

Source acceptance, hosted deployment and a packaged application are separate receipts. The hosted voice release remains identified by [its own frozen commit and image](2026-10-04-cloud-voice.md) until a later deployment receipt supersedes it. Native update discovery is documented in [Distribution](../distribution.md); previously installed builds do not acquire that menu without replacement.

The hosted candidate was built from clean commit `b1605ec8c34303745e99d008c10a89c28b277f60`: image `sha256:7f72ec6ee31a08129f6d65f2d248ba9ff567965cb5bd2ff62b1b0721b069a069`. All 76 runtime source files matched. Six authenticated HTTP admission/source cases passed as UID 10001 in an isolated container without network access; the bundled dependency doctor reported GIL disabled. Consistent production copies (41 gateway tables and 45 web tables) retained every original column/row hash through two candidate initializations, with schema 14 unchanged and no added table or setting.

At **2026-10-04 14:09:20 UTC**, the hosted web runtime still had one `waiting_user` run, with no running/queued/waiting-agent work, audio request or credit reservation. The candidate was **not promoted** and no service was restarted. Durable database preservation alone does not establish that an old client has saved a partly completed help form or that a browser handoff can be closed safely. Current remains `f817045`; protected services, audio configuration and balances were unchanged. The private candidate receipt is `/opt/athena/releases/b1605ec/release-audit.json`; it is preparation evidence, not a deployment receipt.
