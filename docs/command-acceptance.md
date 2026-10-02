# AI-native minimum acceptance suite

Baseline: [official Codex developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli), checked 2026-10-03. Commands such as status, compaction, model selection, new/resumed chats, forks and skills expose harness operations. Athena should perform the corresponding operation through `runtime`, without requiring a user to learn slash syntax. This is a capability test baseline, not a requirement to clone a coding interface.

## Minimum assertions

| Intent family | Natural request | Required evidence |
| --- | --- | --- |
| Status | What is your model/window, current context and cache rate? | Actual selected profile; documented window vs character guard vs measured/estimated usage clearly separated |
| Compact | Reduce this conversation to about 20k tokens | Durable projection boundary, recoverable original, before/after estimates, honest target achievement |
| Model | Use a different configured model for this conversation | Next-request profile/epoch changes; previous actions do not execute again |
| New | Create a separate conversation | New owned session exists; old history and jobs remain intact |
| Resume | Continue the conversation about my budget | Owned session is located/read; reconnect restores its durable state |
| Fork | Branch from this conversation | Immutable inherited boundary; duplicated jobs paused; independent context projections |
| Rename | Name this conversation “Home budget” | Persisted session title changes |
| Skills/plugins | What can you change? Add a new capability and undo it | Discovery distinguishes active from merely installed; versioned change and restoration have visible effects |
| Context inspection | Find what I said in another conversation | Same-account ownership, bounded results, source session/message IDs and continuation cursor |
| Context editing | Stop including that completed attempt; restore it later | Whole-run exclusion/restoration; original UI history remains; tool pairs cannot be split |

Status, compaction and model tests already have deterministic harness coverage. New/resume/fork flows also need a browser-level pass; a returned ID alone does not demonstrate that navigation happened. The present browser restriction prevents claiming that pass.

## Personal-assistant scenarios

Language vs reply preference; font and colors; display name/avatar/password; uploading and delivering documents; Markdown and returned reasoning; timers and arbitrary condition scripts; independent foreground/background work; file previews; custom workspace and warning; plugin crash recovery; inspecting the last hour's costs; finding earlier decisions; returning to an interrupted stream; reset after a broken provider.

Expand the corpus with ambiguous wording, unsupported capabilities, empty data, stale browser builds, concurrent sessions, account switches, stopped requests, plugin updates during execution, missing usage and estimated target failures. For each case record expected state, permitted side effects, recovery behavior and the evidence that determines pass/fail. Reassuring prose is never the oracle.

## Explicit gaps, not passes

Codex-specific Git review/diff and terminal conveniences need task-specific equivalents, not obligatory default buttons. Approval commands conflict with the prototype's explicitly chosen no-approval workflow and are not silently introduced. Cloud execution, goals, subagents, connector lifecycle and full service-provider replacement remain separate milestones. Missing features must be marked unsupported or pending; explaining them is not a successful execution.

The live evaluation script covers a small, real-provider intent sample. Unit/integration tests cover deterministic mechanics. Neither justifies claiming arbitrary task success or an already-complete self-evolving harness.
