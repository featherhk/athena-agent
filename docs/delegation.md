# Durable delegation

Athena can delegate bounded, independent work through the existing `runtime` tool. Users stay in their main conversation: progress is collapsed by default, questions appear at its composer, and generated files return for preview. Use delegation when its benefit exceeds the cost of another context and a final synthesis. Simple work should remain direct.

## Runtime contract

`runtime {op:"agents", args:{action:...}}` supports `spawn`, `list`, `read`, `wait` and `cancel`. The [delegation skill](../backend/athena/skills/delegation/SKILL.md) carries the detailed arguments and operating conventions; the core tool description remains short. New skill metadata appears at the next prompt-prefix boundary, without rewriting already persisted prefixes.

A spawn creates a fresh session and queued run in the same account and project. It does not copy the parent's history, components or timers. It has a separate file directory; the parent supplies only needed reference context. Same-account conversation lookup remains available explicitly. The child inherits the selected model profile unless another operator-registered profile is requested. Credentials never appear in job records or tool results.

The parent run and stable task name identify one job. An exact retry returns that job; a different definition under the same name fails. Current bounds are four unfinished children per account, eight jobs per parent run and three delegation levels. Completed batches release concurrency capacity but do not reset a run's total budget. Each child retains the existing model-step and credit constraints.

## Waiting and recovery

Schema 12 records lineage in `agent_jobs` and joins in `agent_waits`. A wait is prepared first, then activated with the saved tool result and the remaining tool slots marked unexecuted. It transitions the parent to `waiting_agents`, releasing its model request and actor. Readiness is checked again in the committing transaction to cover a child finishing during activation.

When all selected jobs finish, or any finishes for `mode:"any"`, one result receipt is appended and the same parent run is queued. Failure, interruption and cancellation are results with those statuses, never implied success. A normal final answer with unobserved children also waits or collects their results, so a premature model sign-off cannot silently abandon work. The result payload is bounded; full child messages, reasoning, activity and files remain retrievable by their references.

Stopping the main task durably cancels its waits, marks the branch for cancellation and stops active/queued/waiting descendants. Cancellation checks at claim and tool boundaries cover a spawn committed during stop. Partial output and tool slots remain recoverable. External side effects already performed are not undone.

Restart does not replay an interrupted child. A committed parent wait can resume with the child's interrupted result and decide what verification is needed. A queued orphan cannot start after its parent ends. A fork inherits immutable result receipts but creates no active child jobs, pending help or copied waits. Historical child traces remain accessible only to their account.

Queued human input is admitted to context after the preceding run's output. An additive `context_after_seq` anchor preserves the original message sequence/content and previously submitted prefixes while fixing future context ordering. Admission never changes a queued message that already entered a provider request.

## One conversation for the user

Child phase and completion events refresh ancestor views after persistence. A small expandable progress group exposes each child's actual activity and a stop control; the fixed main stop still stops the branch. Result messages carry explicit provenance rather than pretending to be human messages.

Pending child questions are routed to the main composer. Answers and attachments resume only the corresponding child; network retries are idempotent. The answer stays with that child's audit record, while the parent receives its eventual result. Output files appear alongside the main conversation's files without copying their contents. Completed-run file discovery is also recoverable through `agents read`.

Conversation token and estimated-cost totals include descendant requests. Context measurements remain specific to the selected conversation's own provider request; adding up separate child windows would be misleading. Account totals count each request once. Billing uses the existing gateway reservation/settlement path rather than these estimates.

## Evidence and limits

Deterministic tests cover concurrent idempotent spawn, account and branch ownership, budgets, completion/activation races, parallel execution, tool-slot preservation, automatic join, user help and files, cancellation, restart, forks, queue ordering and usage. Browser acceptance uses a separate temporary database and synthetic provider: login/send, refresh during a child question, answer/resume, expanded original activity, Markdown file preview and stop were exercised at desktop and 390 px widths.

A real DeepSeek acceptance case on 2026-10-03 delegated two arithmetic checks and combined their correct results: six requests and 12,813 provider-reported tokens including children. This verifies one bounded intent, not a general quality or latency claim. Repeat it with `scripts/evaluate_requirements.py --key-file PATH --case delegation`; it is opt-in and consumes API credit.

Children share the account's capabilities and host; context/file separation is not a separate security boundary. Running-child follow-up messages, cross-node delegation, automatic model selection and broad delegation-quality evaluations remain unfinished. Persistent scheduled work should be managed in the main conversation. Engine, Store and delegation are concrete integrations, not yet replaceable service providers. Cloud deployment and the previously generated DMG do not include this update until separately rebuilt and validated.
