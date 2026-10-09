# One conversation entrance, durable tasks, and recoverable context

Research and repository inspection: **2026-10-10**. This is an incremental design proposal, not a statement that the proposed features have shipped. The inspected checkout was based on `1e969cca` with concurrent desktop/lifecycle work in progress. No business code or user database was changed for this research. Existing test cases were read, not rerun.

The product decision is to hide session management from ordinary use while retaining execution boundaries internally. A single conversation entrance must not imply a single, permanently growing model prompt. Task selection, persistent memory, context projection, and provider caching solve different problems.

## What “Dream v3” refers to

The user has not supplied a URL, so the intended reference remains **unconfirmed**.

One verified candidate is [DREAM — Dynamic Retention Episodic Architecture for Memory](https://github.com/MatheusPereiraSilva/dream-architecture), by Matheus Pereira da Silva. Its v3 separates memory retention from permission to influence the current context, keeps provenance and transformation lineage, and records policy decisions. The author describes its evaluation as controlled and synthetic; it does not establish production conversational accuracy. Its playground does not call a live LLM. These are useful design ideas, not evidence that Athena should adopt the implementation wholesale. [Author's research page](https://codebymp.com/research/dream)

“Dreaming V3” also appears as an OpenAI-related name in secondary coverage. This pass did not locate a primary architecture specification under the searched OpenAI documentation domains, and the fetched [official personalization page](https://learn.chatgpt.com/docs/personalize) does not identify that version. This is a research limitation, not evidence that the feature does not exist. Do not conflate it with the independent DREAM project, infer proprietary internals, or substitute a similarly named reinforcement-learning project. The design below does not depend on resolving the name.

## What Athena already implements

| Area | Observed implementation | Boundary that remains |
| --- | --- | --- |
| Durable history | [`store.py`](../backend/athena/store.py) owns SQLite through one dedicated worker. [`event_log.py`](../backend/athena/event_log.py) preserves event sequence and restores compressed/delta message events. Streaming message state and its event are committed together. | This is not a complete event-sourced domain model. Some current-state tables are updated. Reconstructing a UI state must never mean re-executing external actions. |
| Task continuity | [`activity.py`](../backend/athena/activity.py) projects active work, help and unread results from existing runs. [`task_events.py`](../backend/athena/task_events.py) records scheduled-wake origin and occurrence. | The existing `tasks` table means timers/conditions, not every user goal. Work and stop scopes still largely follow sessions. There is no automatic general task router. |
| Memory | [`memory.py`](../backend/athena/memory.py) has account-scoped global/project/session records, append-only revisions, source run/session, optimistic version checks, forget/restore and fork lineage. | It does not perform general semantic contradiction resolution, embeddings or physical erasure. `forget` stops active recall; original chats, requests and historical versions remain. |
| Retrieval | Recall unions lexical matches, scope-local entries and recent entries. It bounds candidates to 80 entries/about 64,000 characters and injects whole entries within about 8,000 characters. A policy hook can select candidate IDs. | Chinese bigrams and literal terms do not imply semantic or cross-language recall. [`context_controls.py`](../backend/athena/context_controls.py) searches conversation titles and reads bounded history; it is not full-text search over all conversations. |
| Context projection | [`context_projection.py`](../backend/athena/context_projection.py) is shared by execution and inspection. It retains provider reasoning and assistant/tool-call pairing. Queued inputs enter model chronology only when admitted. | Every completed user/assistant message still grows the active projection unless an entire completed run is explicitly excluded. Repeated frozen memory notes also accumulate. |
| Recoverable clipping | [`introspection.py`](../backend/athena/introspection.py) deduplicates identical completed tool output and clips tool content with recovery IDs. Automatic clipping protects the newest tool round. Original messages remain intact. | Character-based token estimates are not tokenizer measurements. Clipping cannot guarantee a 20k target when preserved dialogue/reasoning dominates. `context_clips` holds the current replacement; a compaction row alone is not a versioned copy of every prior projection. |
| Prefix stability | [`engine.py`](../backend/athena/engine.py) persists the system/skill-catalog prefix by `(session_id, model_epoch, compaction boundary)`. New-turn memory is attached to that input, not inserted into an old system prefix. Requests save payload/profile/epoch and tool bindings. | Tool definitions are snapshotted per request, not frozen for an entire context epoch. A changed registered tool can legitimately change the provider prefix. There is no complete context-manifest explanation of why two requests diverged. |

Relevant existing tests include `test_memory.py`, `test_memory_recall.py`, `test_context_projection.py`, `test_context_controls.py`, `test_request_context_budget.py`, `test_event_log.py`, and `task-continuity.test.mjs`. Their presence is evidence of intended contracts, not a new test result from this research.

## Primary references and what to borrow

| Reference | Useful principle | Athena decision |
| --- | --- | --- |
| [MemGPT](https://arxiv.org/abs/2310.08560), 2023; revised 2024 | Bounded working context and larger external memory are separate tiers; the model can request movement between them. | Keep recoverable sources outside the prompt. Expose retrieval through existing runtime operations. Do not pretend a long context window equals reliable memory. |
| [LongMemEval](https://arxiv.org/abs/2410.10813), ICLR 2025 | Evaluates extraction, cross-session reasoning, time, updates and abstention; separates indexing, retrieval and reading. | Test those stages separately. A retrieved correct source can still be misread. Add task execution and cancellation tests beyond conversational QA. |
| [Zep/Graphiti](https://arxiv.org/html/2501.13956v1), 2025 | Distinguishes when a fact was valid from when the system learned it, and connects derived facts back to episodes. | Add validity/source fields before considering a graph database. “The appointment moved” must preserve history without treating both times as current. |
| [Hermes sessions](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/sessions.md), inspected 2026-10-10 | Search and scroll durable conversations through SQLite FTS5. | Add bounded, owner-filtered source search beside curated memory; no auxiliary summarizer is necessary for initial retrieval. |
| [OpenClaw memory](https://docs.openclaw.ai/concepts/memory), inspected 2026-10-10 | Background consolidation is distinct from foreground recall, with provenance checks and reviewable maintenance outcomes. | Maintenance must be bounded, observable and optional. Copy neither its file-backed canonical storage nor all its machinery into Athena's SQLite design. |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), inspected 2026-10-10 | Thread checkpoints and cross-thread durable memory have different scopes. | Keep task execution state independent of personal memory. No need to adopt another orchestration framework. |
| [Sleep-time Compute](https://arxiv.org/abs/2504.13171), 2025 | Useful work can be prepared before the next query. | Evaluate background indexing/consolidation only when it reduces total cost or improves task success. Offline work is not free and cannot be assumed useful for every user. |

Benchmark scores from these systems are not directly comparable product rankings: models, budgets, workloads and reader-facing context differ. Athena needs its own reproducible workload and ablations.

## Minimum product increment: one entrance over existing task contexts

Start with **one existing session as one internal work context**, exposed as a task when useful. Do not rename or migrate every table, merge unrelated histories, or remove the detailed window. A later task can span multiple execution contexts after this mapping proves inadequate.

1. Add a small task index over existing sessions/runs: goal/title, account, latest work state, last interaction, visible artifacts/components, and an explicit paused/finished intent where run state cannot express it. Derive running/help/error state from existing rows rather than creating a competing scheduler. Avoid calling this the existing scheduled `tasks` table.
2. The single composer carries an optional focused task target. An explicitly focused work panel or selected task is the strongest cue. “Add this too” can use a single unambiguous recent task; with two plausible tasks, ask one short question. Do not silently select a task solely because it is most recent.
3. Persist the routing decision with the input's existing idempotency identity, target context and evidence/reason before admission. Routing and enqueue must not create two copies during retries. The same input ID cannot silently route to a different task on retry. A correction becomes a new explicit routing event.
4. Show only enough routing context to prevent surprises: a task name beside an active input, and a small drawer of ongoing/waiting/completed work. Keep history/details available. Background results never steal focus or replace the user's draft.
5. Stop must bind to an actual task/run scope. “Stop comparing the quotes” stops that work; “stop that” asks if multiple targets are plausible. A separate fixed stop-all remains reachable. Pausing a task also prevents its pending wakeups from starting work until resumed; it does not erase evidence or replay completed actions.

Task routing is not memory retrieval. A retrieved conversation about a past trip is reference data, not authorization to resume its booking task. Source roles and origin metadata survive both routes.

## Memory: retain evidence, admit only useful current claims

Use the existing revision ledger; add fields only when the next acceptance case requires them:

- **Source:** message/event/file ID, source revision or content hash, source role, source span where practical. Existing source run/session remains useful but is too coarse to verify an individual claim.
- **Meaning:** preference, project fact, commitment, correction or verified procedure. A general “importance” float is not a substitute for these distinctions.
- **Validity:** observed/recorded time plus optional effective-from/effective-until. Unknown validity stays unknown. Expiry may exclude a memory from current advice without deleting its historical evidence.
- **Relations:** supersedes/contradicts/derived-from links. A newer incompatible explicit correction can supersede a prior claim. Different projects or different time periods need not conflict.
- **Admission:** accepted, candidate or withdrawn, with a compact reason/policy version. Do not promote quoted web text, model guesses or an old assistant's unsupported assertion into user preferences.

This is a proposal, not a claim that the current schema contains these fields. The first implementation can add a provenance relation table and a few revision fields, preserving current IDs and APIs.

Foreground recall should be: enforce ownership and scope → retrieve source candidates → apply current validity and correction rules → rank → fit a bounded evidence packet. A retrieved item is not automatically eligible for injection. Injection/retrieval counts must not increase factual confidence by themselves. A successful, independently observed outcome can supply evidence; repetition of the model's own claim cannot.

Start source retrieval with a rebuildable SQLite full-text index over approved message/file excerpts. Validate FTS/tokenization support on bundled Python; Chinese/Japanese search needs deliberate tests rather than assuming an English tokenizer works. Preserve literal/ID search. Embeddings and rerankers are optional providers added only after the multilingual evaluation exposes a useful gap. Never retrieve globally and filter ownership only after ranking or logging.

The model-facing packet should be short natural language with compact source IDs, dates and an explicit uncertainty/conflict when needed. Keep rich metadata in storage and inspect it on demand through `runtime`. Do not inject an entire knowledge graph or an audit ledger into system instructions.

Background consolidation should initially run only after a completed task or a bounded idle batch. Persist its input watermark, policy/model version, cost and proposed revisions. Validate sources and current versions before commit; a slow worker must not resurrect a forgotten entry or overwrite a later correction. Coalesce duplicate jobs and cap time, tokens and daily spend. On failure, keep the prior memory and source evidence usable. It need not wake the foreground assistant merely to report housekeeping.

“Forget for future recall” and “erase my stored data” need separate semantics. The latter requires descendant/index cleanup and an explicit backup-retention policy; it is not implemented by the current `forget` operation. Recoverable compression must not be used to override a deliberate future deletion request.

## Compression: a versioned projection, not destruction

Keep the current cheapest-first behavior: deduplicate equal tool output, clip old output, and fetch original spans on demand. Protect recent user intent, unresolved commitments, active tool-call groups and fresh recovery results.

When dialogue itself dominates, clipping tools alone is insufficient. The next increment should seal completed run ranges behind a **context manifest** rather than summarize every turn with another model:

```text
manifest_id, task_context_id, parent_manifest_id, boundary_reason
source ranges / message IDs and revisions
included ranges, clip replacements, excluded completed runs
checkpoint references: goal, open questions, artifacts, effect receipts
memory snapshot IDs/versions, profile ID/version, tool-schema hash
policy ID/version, source checksum, estimated size, created_at
```

This is proposed metadata, not a second copy of the complete transcript. Reuse `compactions`, `context_exclusions`, original messages and request snapshots. A manifest must reproduce a historical projection without running a tool. Atomically commit the manifest and its boundary before the next request; a failed commit leaves the previous projection active. Fork copies the selected boundary and fixed source references, not a pointer to a moving parent projection. Background jobs remain paused in forks.

A deterministic checkpoint can contain already-recorded goal/state/file/effect references. A model-written semantic summary is an optional later policy, with retained sources and visible uncertainty. If it omits a decision, source search can recover it. Never recursively summarize only an earlier summary while discarding the original evidence. A 20k request returns achieved/estimated/remaining obstacles honestly instead of silently removing a live call or claiming an exact tokenizer count.

Important distinction: restoring a context projection restores what the model can read; it does not revert files, undo a sent email, resume paused jobs, or replay commands. Execution receipts remain authoritative for effects and unknown outcomes.

## Stable prefixes and provider cache boundaries

All three relevant official references describe prefix-dependent reuse, but their mechanics differ: [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching), [DeepSeek context caching](https://api-docs.deepseek.com/guides/kv_cache/), and [Claude prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching). Matching content is necessary; availability, prefix units/breakpoints, expiry, routing and model settings can still affect a hit. Cache absence is not an application correctness failure.

Recommended logical order, encoded by each provider adapter:

```text
stable tool schemas + short Athena identity/capability metadata
frozen epoch material: task checkpoint + compact memory snapshot, when useful
append-only admitted conversation/tool rounds
current input + new/corrected memory evidence + current host facts
```

The second line is context data, not newly elevated instructions. Depending on protocol it belongs in a distinct data block/message after the stable instruction prefix. Role placement must be tested per adapter; the ASCII order is not a universal wire schema.

Preserve the current rule: do not rewrite previously sent notes on ordinary turns. Between boundaries, append only memory additions/corrections relevant to the new input. At compaction, task-context rotation, model/profile change or an intentional prefix refresh, consolidate a bounded current snapshot and start a new persisted manifest. Merely observing a cache miss does not prove expiry and must not automatically reorder all history. An urgent correction is appended immediately; correctness does not wait for cache optimization. A data-erasure or access-revocation operation may require immediate projection invalidation.

Tool registration needs an explicit policy: prefer the existing stable runtime envelope with extension instructions fetched progressively; if a provider-visible tool schema changes, record `tools_changed` as a boundary rather than pretending reuse is preserved. Never continue executing a revoked tool solely to keep a cache hot. Keep task timestamps, request IDs, changing UI state and current metrics out of the stable prefix.

Measure provider-reported cached input divided by eligible reported input, alongside total cost per successful task, uncached input, output/reasoning, compaction cost and first-response latency. Missing cache fields mean unknown. Do not average per-request hit percentages or claim a hit from a matching local hash alone. No fixed TTL, token threshold or price belongs in Athena's provider-neutral design; those are profile/adapter capabilities verified against the chosen service.

The minimum observability addition is a context-manifest ID and boundary reason on each request, plus stable hashes of the instruction/tool/history parts. Existing payload/profile snapshots are retained. This explains intentional misses without expanding the system prompt.

## Extensibility without another framework rewrite

Keep the three existing tool entrances. Put routing, memory and context operation details in the existing skill folders. The stable prompt only needs to teach the distinction between current task state, retrieved reference material and user instructions; it does not need this research document.

The smallest replaceable seam is a context planner: given owned source references, profile limits, current epoch and protected call groups, return a validated selection/clip/checkpoint plan. The host validates ownership, provenance, version matches, pairing and recoverability, then persists it. The existing clipping and memory-recall policies can be its initial implementation. A plugin proposes a plan; it does not receive arbitrary database access or remove independent stop/reset. Full service replacement remains a separate architecture goal, not a feature delivered by this document.

## Implementation order and acceptance

| Increment | Deliverable | Required user-visible proof |
| --- | --- | --- |
| 1. Task continuity | Task projection and durable input targeting over existing sessions; preserve detailed UI | Compare two quote sets concurrently, add a file to the intended one, stop one, leave the other running, and resume the correct task after restart. |
| 2. Source retrieval | Owned full-text search with stable source IDs and bounded scrolling | Find an older decision absent from the active prompt, cite its exact source, and recover a clipped table value without re-running the action. |
| 3. Context manifests | Versioned projections and complete boundary diagnostics | Long-run compaction, reconnect and fork keep call pairing, current goal and unresolved work; restoring a projection causes zero external effects. |
| 4. Memory lifecycle | Fine-grained provenance, effective time, correction and withdrawal checks | “Now use 80, not 50” overrides the old budget; a past date question still finds 50; forgetting during a slow recall/consolidation is not undone. |
| 5. Optional consolidation | Budgeted, source-validated background policy | Measured improvement over explicit memory + source search, including its own token cost and failure recovery. |

Use synthetic histories and repeatable provider fixtures for deterministic checks, then a separately budgeted real-model set. Include English/Chinese cross-language requests, rare important facts, misleading assistant claims, quoted prompt injection, conflicting dates, task-routing ambiguity, paused jobs, model switches, stale plugin outputs and cross-account leakage attempts. Keep a no-memory baseline and the current lexical baseline; evaluate indexing recall, evidence admitted, final answer/action correctness and abstention separately.

Do not infer readiness from a single “remembers my name” demo or a high cache ratio. The release criterion is that the user can continue the right work, understand what happened, correct Athena, and recover evidence without managing sessions.
