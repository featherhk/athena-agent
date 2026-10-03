# Current-state discovery acceptance

Purpose: Athena should route a question about work in progress, pending help or unread results to the durable activity projection. Reading every conversation to reconstruct that state adds cost and can mistake an old question for a current request.

One earlier natural-language trial used nine provider requests and repeated session listing/reading before navigation. A fresh Chinese pending-question trial now used activity first and obtained an applied navigation receipt in seven requests; the [release receipt](releases/2026-10-04-source-window.md) records the route, cost and remaining calls. These are diagnostic examples, not a measured general failure rate or broad model-quality improvement. The other cases below have not yet been run against a model for this change.

## Isolated cases

Create owned synthetic conversations with deterministic state. Include an unrelated conversation containing old, already-answered questions so that historical wording cannot pass as current state. Use a fresh isolated conversation with the current skill catalog, the same configured model and a recorded profile/version. Do not rewrite existing prompt prefixes or force compaction to load changed metadata.

| Natural request and variation | Fixture | Required effect and route |
| --- | --- | --- |
| “看看我还有什么事情等着我回复，带我回到那个问题。” / “Is anything waiting for my answer? Take me there.” | One other root task has a pending question; optionally the question belongs to its child. | First task-state query is `activity list`; choose `needs_user:true`. Use its `session_id`, observe the client, navigate and obtain an applied receipt. The actual pending question is visible; its answer remains untouched. No sessions list/read is needed just to rediscover the ID. |
| “我先前交代的那些事，还在处理吗？” / “What are you still working on in the other conversations?” | Queued, running and waiting-for-child work; no pending user question. | `activity list` supplies status. Summarize actual work without scanning transcripts, counting the present status query as a separate assignment, navigating, stopping or marking results read. |
| “我离开以后有什么新结果？带我看看预算表那件事。” / “Any new results while I was away? Open the budget task.” | One unread budget result, one unread failure, and an already-read earlier result. | List the current projection, distinguish the failure, and navigate to the matching budget conversation. No extra sessions search for the returned ID. Read that conversation only if the user requests its contents or the claimed outcome needs verification. Unrelated results are not acknowledged merely because the agent listed them. |
| “现在还有需要我答复的事吗？” / “Do you need anything from me right now?” | Empty pending-help set; repeat with a bounded response where `has_more:true`. | Use current `needs_user`, not historical questions. The empty case makes a scoped statement about the current list. The truncated case increases the limit when useful and states remaining uncertainty; it does not claim to have exhausted all conversations or resolved anything. No speculative navigation. |

If multiple matching pending questions exist, selecting an arbitrary one fails. Present concise choices or open the activity view. This is a fixture condition, not a reason to interrupt an unattended development run for user input.

## Evidence and cost

For every trial retain a private, bounded record outside the checkout: case ID, model profile/version, fresh-versus-existing context, skill source revision, run ID, ordered tool operations, request count, tokens, cache usage and the ledger's actual settled cost. Provider usage that is absent stays unknown. Record the exact attention item/key selected, navigation request and observed receipt, resulting selected conversation, pending help status, and preservation of the previous draft/files. No credentials or real user transcripts belong in the report or repository.

Evaluate routing and user effect separately. The first state-discovery operation should be `activity list`; unrelated `sessions list/read` calls or a second search for an already-returned ID fail the routing rule unless a documented fixture ambiguity requires historical evidence. Skill reads, client observation and receipt checks are legitimate costs and remain in the count. Saying “switched” without an applied receipt fails the effect rule. A read-only status request must not navigate or acknowledge unrelated results.

Compare repeated matched runs, not just the earlier nine-request example. Report each trial and aggregate request count, input/output/cache usage, settled cost and failures under the same model/profile and fixture. Navigation receipt latency may add checks; record it rather than declaring an unsupported universal request ceiling. Fewer calls do not excuse skipped verification. Broad task success, source-window affinity and new execution capabilities are outside this acceptance scope.
