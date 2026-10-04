# Athena harness review — 2026-10-04

Scope: an independent read of the current engine, context, memory, model, tool and voice paths. This review used synthetic local databases and a fake model; it made no paid requests, read no customer conversations or credentials, changed no runtime code, and did not access a denied browser origin. Findings describe the checkout, not a verified production deployment.

The existing foundation is useful: SQLite has one worker; requests snapshot model profiles and custom tool versions; stop/restart preserve tool-result pairing without replaying uncertain actions; UI actions have durable acknowledgement; memory revisions, tool originals and fork boundaries are retained. The next improvements should make normal tasks survive longer use, rather than introduce more visible framework concepts.

## 1. P1 — Budget the payload actually sent, including admitted memory

**User symptom.** A conversation containing short messages can eventually refuse to continue with `context_limit`. Asking for compaction may not help, even though the visible conversation looks small.

**Mechanism and evidence.** `Engine.context()` counts message content, reasoning and tool calls, but omits `context_note`, attachment manifests, the prefix and tool definitions (`backend/athena/engine.py:224–246`). `prepare_memory()` writes a complete recall snapshot onto every new user turn (`252–267`); `project_messages()` appends every historical snapshot (`backend/athena/context_projection.py:9–22`). The later guard checks the complete JSON payload (`engine.py:292–298`). Consequently the trigger and the actual limit use different quantities. Compaction can currently shorten only completed tool content (`backend/athena/introspection.py:149–165`), so repeated memory snapshots cannot be reduced by its policy hook.

**Reproduction.** Six synthetic turns, two approximately 3,400-character saved preferences, a 30,000-character test profile: trigger counts **36** characters; frozen memory notes occupy **42,870**; the request without tools occupies **44,415**; automatic compactions: **0**. This does not claim the default profile fails after six turns; it establishes the sizing mismatch independently of window size.

**Smallest useful change.** Use the same projected payload and current tool schema for preflight and the hard guard. Report the reducible and non-reducible parts when clipping cannot meet a target. At a persisted compaction boundary, replace repeated identical memory snapshots with a versioned recall baseline plus changes; retain originals and withdrawal/version receipts. Do not silently delete user text or DeepSeek reasoning. Preserve the existing request prefix until that boundary.

**Acceptance and cost.** Test memory-heavy short chats, attachment manifests, schema growth, model switches and immutable replay. The preflight must detect every locally over-limit payload, and compaction must state `target_met=false` when it cannot help. Track payload characters by component and provider-reported prompt/cache tokens; deterministic deduplication adds no model call and reduces repeated input cost. Character counts remain guards, not exact token counts.

## 2. P1 — Recall relevant durable preferences beyond the latest 30 entries

**User symptom.** “Use my usual departure airport” stops working after unrelated preferences have been saved. The information still exists, but the user has to know to ask Athena to search its memory.

**Mechanism and evidence.** `current_memories()` defaults to the 30 most recently updated entries (`backend/athena/memory.py:33–46`). `prepare_memory()` supplies that set to the selection policy without the current task text (`engine.py:252–260`); the validator only accepts IDs in that set. `snapshot_note()` restricts selected IDs to another latest-30 set (`memory.py:64–72`). Thus registering a clever `memory_recall` policy cannot recover an older entry. Manual query/pagination and the `more_available` hint already exist; this is automatic recall coverage, not data loss.

**Reproduction.** Save an airport preference, then 30 unrelated preferences; begin a new turn about the usual airport. The automatic context lacks `SHA`, while `runtime memory action:list query:airport` returns it. A `more_available` hint is present.

**Smallest useful change.** Build a bounded candidate union of scope-relevant durable preferences, recent entries and lexical matches to the current task. Pass the task and candidate provenance to the policy. Resolve its selected IDs by account/scope and current revision in the final transaction, rather than reapplying the latest-30 cutoff. Keep manual search as recovery. A lexical baseline is sufficient to measure benefit before embeddings or another model are introduced.

**Acceptance and cost.** A synthetic corpus with 100+ preferences should test old relevant facts, correction, forgetting, conflicting scopes, forks and cross-account exclusion. Measure recall of the expected fact under a fixed note budget and downstream task success. No extra paid inference is needed for the first implementation; avoid raising every turn's memory budget to solve retrieval.

## 3. P1 — Separate suspected speech from cancellation of useful work

**User symptom.** A short noise, speaker echo or another person nearby can stop a flight search or document task. Empty recognition does not restore it. Independently, long gaps remain between spoken sentences.

**Mechanism and evidence.** `VoiceEndpoint` declares speech after 160 ms above an energy threshold and ends after 750 ms of silence (`src/voice-core.ts:2–19`). On `start`, `VoiceSession` ignores the current assistant messages and invokes the real interruption callback immediately, before recognition (`src/voice-session.ts:108–116`). If ASR later returns empty, only `holdOutput` is changed (`140–151`); cancelled execution and ignored output are not restored. A synthetic 180 ms, RMS 0.06 noise burst during playback produces `start` at **160 ms** and `end` at **940 ms**, despite containing no speech. This is a state-machine demonstration, not a measured field false-positive rate.

The latency path is also explicitly buffered: whole utterances are uploaded, and `speak()` downloads and decodes a whole sentence, waits for playback to end, then requests the next (`voice-session.ts:127–156,173–195`). Native streaming and learned speech/turn detection are documented candidates, not integrated capabilities.

**Smallest useful change.** Add a reversible “suspected speech” state: duck/pause local playback promptly, while leaving backend execution and output cursors intact until speech is confirmed. Empty/noise input resumes the held output; confirmed speech uses the existing stop path and generation protection. Benchmark a learned VAD behind this small interface, with the energy detector as an explicit fallback. Separately, allow at most one next-sentence synthesis to overlap current playback; bound/cancel the prefetch and count unused billed audio. This can reduce gaps without pretending to be full streaming.

**Acceptance and cost.** Replay noise/echo fixtures and test genuine quiet interruption, delayed ASR, empty ASR, mute, hang-up and session navigation. Require zero backend stop for those negative fixtures and zero stale audio after confirmed interruption. Measure true speech-end to first audio, inter-sentence gaps and false stops on real Safari/WKWebView devices. Local VAD adds CPU/download cost, not per-call API cost; synthesis prefetch can waste at most the configured one-sentence window, which must be measured.

## 4. P1 — End bounded runs with a useful checkpoint instead of a generic failure

**User symptom.** A complex task works through many valid steps, then abruptly reports failure and requires “continue,” without delivering a clear account of the work already completed.

**Mechanism and evidence.** The default maximum is 20 model requests (`backend/athena/config.py:30`). Skill loading, observations and tool calls consume the same loop budget (`engine.py:274–279`); after the last tool result, the loop raises `step_limit` before the model can summarize (`378–380`). The failure path can also cancel outstanding delegated work (`393–400`). Preserving the total budget across user-help resumes is correct, but there is no reserved terminal-response opportunity or explicit budget checkpoint.

**Reproduction.** A fake provider emits 20 dependent, harmless `memory list` calls and is ready to give its final answer on request 21. The store contains **20 successful tool results**, **0 final answers**, and `status=failed,error_code=step_limit`. No external operation was performed.

**Smallest useful change.** Keep a hard cost/step bound, but reserve a final no-tool response or a deterministic durable checkpoint that lists completed artifacts, unresolved objectives and any uncertain effects. Expose a truthful budget-paused state. A continuation must bind to that checkpoint and retained receipts; do not replay completed actions or blindly requeue uncertain operations. Only add bounded automatic continuation after this checkpoint path is proven.

**Acceptance and cost.** Test the boundary on a completed last tool, a pending child, an uncertain side effect, a user-help resume and a restart. Every limit termination should yield a useful terminal/checkpoint result; completed action counts must remain unchanged after continuation. Reserving a response within the existing total cap adds no extra request budget but leaves fewer action rounds; allowing one extra response must be an explicit bounded cost decision.

## 5. P2 — Prove one replaceable non-policy service before expanding the registry

**User symptom.** Asking Athena to change its protocol or execution framework can still exceed the real plugin surface, despite policy registration succeeding.

**Mechanism and evidence.** `Engine` imports `complete`, instantiates `ToolRunner`, and owns the concrete loop/store directly (`backend/athena/engine.py:9–29,312–315`). `Profile.payload()` and `model.complete()` implement Chat Completions and its SSE vocabulary (`backend/athena/profiles.py:51–53`, `backend/athena/model.py:7–56`). Profile switching is real, request-bound configuration; it is not provider replacement. `PolicyRunner` is an isolated, bounded transform with real consumers for recall, clipping and command preparation (`backend/athena/policies.py:41–98`). It cannot become the model loop or storage service just by receiving another registration name. `docs/plugin-architecture.md:37–43` accurately acknowledges this gap.

**Smallest useful change.** Introduce a model-stream contract that returns canonical text, reasoning, tool-call and usage events, then route the existing Chat Completions implementation through it. Bind its version to each request and exercise a second synthetic adapter plus a failing candidate. The first milestone is request binding, cancellation, fallback and audit equivalence, not a rewrite of storage or an unrestricted in-process plugin loader. Keep secrets host-delivered and outside model-readable configuration.

**Acceptance and cost.** Replace an adapter while another request is in flight; the old request must finish against its original binding, a failed candidate must leave the active provider intact, and reset/reconnect/fork must work from the same durable history. This adds no required model calls. It creates a testable path for future cheaper providers and native streaming, without claiming those integrations exist today.

## Reproduction notes

- `/private/tmp/athena-algorithm-review.py`: disposable SQLite cases for payload sizing and recall. Run with `PYTHONPATH` pointing to the checkout's `backend` and `uv run --directory backend python`.
- `/private/tmp/athena-step-review.py`: disposable SQLite plus an in-memory fake `complete()` for the bounded-run case; the original callable is restored in `finally`.
- VAD: directly import `VoiceEndpoint` under Node's TypeScript stripping, feed nine 20 ms samples at RMS 0.06 followed by silence with `outputPlaying=true`.
- No synthetic data, audio, database or credentials were added to the checkout. These probes are review evidence, not a replacement for regression tests or real-device end-to-end acceptance.
