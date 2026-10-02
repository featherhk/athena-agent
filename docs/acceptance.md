# Requirement-driven acceptance

Athena is tested by the state a user's request should produce, not whether its final sentence says “done”. Deterministic tests exercise the harness; an opt-in real-provider evaluation checks intent routing. Neither should use customer conversations as fixtures.

| User request | Observable result | Main checks |
| --- | --- | --- |
| Change this app to Simplified Chinese | Account preference changes and open sessions receive the durable event | `test_preferences`, `test_requirements`, live evaluation |
| Keep menus Chinese; answer in Japanese from now on | UI stays Chinese; a sourced global preference is recalled in a new conversation | `test_requirements`, live evaluation |
| Use a serif font and ocean colors | Shell consumes saved settings without a page reload | `test_preferences`, live evaluation; Safari and in-app visual review |
| Make the app black, or use an arbitrary color | Any opaque hex color persists; text/control contrast adapts; reset removes custom styles | `test_preferences`, `appearance.test.mjs`, real-provider black/custom-color cases |
| Keep a weather card or clock in the top corner while scrolling | Explicit anchored registration stays outside the chat scroller; registration, mount, visibility and functional correctness are distinct | `test_component_feedback`, real-provider clock case; browser observation |
| Show today's weather, but no location is known | Athena requests the missing city instead of inventing readings; the run releases its stream while waiting | `test_user_help`, real-provider missing-information case |
| Answer later, refresh, upload a missing file, or stop a question | The question survives restart; answers resume once in the same run; stop cancels waiting and queued work; other accounts cannot answer | `test_user_help`, `test_help_api` |
| Give yourself a reusable rectangle-area tool | Registered code lives outside the checkout and appears in a subsequent model request | `test_evolution`, live evaluation |
| Restore the previous version of a broken tool or component | A new registration points to the restored content; historical registrations remain immutable | `test_evolution` |
| Reuse a skill containing a script and a reference | Only metadata is initially supplied; named resources load on demand | `test_skillpacks`, `test_evolution` |
| Change my name or avatar | Authenticated account data changes and propagates; unrelated accounts cannot access it | `test_accounts`, live display-name evaluation |
| Change my password | Chat opens a fixed secret-input form; the password does not enter model tools or request snapshots | `test_accounts` |
| Show a Markdown reply and reasoning while working | One trace per run exposes every persisted round, reasoning, tool call and result, with paged original recovery | `test_trace`, provider parser, Safari user-help resume and trace review |
| Read this attachment; give me a workbook or document | Files are associated with messages, scoped to their owner, previewable and downloadable | `test_files` |
| Stop, reconnect, switch chats, or restart | Partial content survives; browser disconnect does not cancel a run; side effects are not silently replayed | `test_runtime`, `test_requirements` |
| Keep long replies responsive and recover them after reconnecting | Batched writes retain pending reasoning/text on stop; lossless event encoding restores full frames from any cursor; archival preserves originals and a restorable backup | `test_event_log`, `test_shutdown`, private-copy migration/archive drill |
| Reset after a bad extension or model change | Fixed recovery restores defaults and disables extensions while keeping chats/files | `test_requirements`, `test_evolution` |
| Report context, cache hits, or the last hour's spending | Reported API usage is distinguished from estimates, unknowns and configured limits | `test_introspection` |
| Compact toward 20k tokens | Only tool projections are clipped; originals are recoverable; estimated target achievement is explicitly reported | `test_introspection` |

| Switch the visible conversation, open a view or preview a file | `runtime app` discovers real controls; a visible client claims once and returns an execution receipt | `test_app_controls`, `test_app_controls_integration`, `app-actions.test.mjs`, real-provider routing |
| Move an effect between messages or style individual characters | An isolated overlay receives bounded semantic geometry and can apply reversible text presentation | `effects.test.mjs`, real-provider registration, in-app overlay/text-style review |
| Read on a narrow screen or change to black | Input remains reachable, no horizontal overflow, readable theme tokens and a compact presence | 390px in-app viewport check; live theme change retains input |

## Activation and refresh

- Preferences and account state are stored before durable events. All open sessions catch up from SQLite in sequence; queues are wake signals, never the only copy of an event.
- Frontend registration increments a version. Structural snapshots merge independently of newer text chunks, so a continuous stream cannot starve component updates. A version replaces only its isolated frame; removal clears its text effects and retains the draft.
- Core frontend builds publish a checked version manifest. A safe client reload preserves the account/session draft; forms and active interactions defer reload. This is separate from component registration and does not cancel backend work.
- UI intents are account/session scoped, durably claimed before execution and acknowledged afterward. Uncertain dispatch is not replayed; opening a password form or starting a download is not proof of the subsequent external effect.
- A browser reports version-scoped mount/content/viewport observations and script/resource failures. An unobserved registration is not evidence that the user saw it; even a mounted frame is not proof its data or behavior is correct. Off-screen clients cannot claim visible success. Static and scripted widgets use the same mechanism.
- User help is a durable `waiting_user` state, not a busy loop. A single question uses the usual message composer; a short multi-question form is shown only when needed. Repeated identical answers are idempotent. Other input remains queued until an answer or explicit interruption. Pending help is not cloned into a fork.
- Backend extensions run in separate processes. A model request freezes its tool schema and code version together. A later registration affects the next request; disabling a tool blocks even previously frozen calls.
- Skill packages have immutable version directories. Core metadata and user skill metadata are captured at a persisted context boundary. Newly registered skills can be discovered immediately through the registry; stable prompt prefixes are not retroactively rewritten.
- Compaction persists a new projection boundary. A model switch creates an explicit context epoch. Request records retain their original profile, prompt and tool definitions.
- Core application deployments still require a controlled backend restart. Runtime extensions do not edit or hot-patch authentication, storage, the process supervisor or reset. A core restart interrupts active work and does not replay unknown side effects.

## Running the checks

```sh
npm run check
uv run --directory backend python -m athena.doctor
uv run --directory backend python ../scripts/evaluate_requirements.py --key-file /path/to/operator-key
```

The real-provider evaluation uses a temporary account/database and removes its files when finished. It prints verdicts and usage totals; failures may include a short synthetic reply and the selected operation names. Credentials and provider request recordings are never printed. It is an explicit paid check, excluded from CI.

## Honest limits

This is extensibility with recovery, not arbitrary safe self-rewriting of the application core. The new [managed-browser adapter](browser-execution.md) is separately tested on synthetic pages; it does not provide full desktop control. No claim of production billing, operating-system isolation across platforms, or exact local provider tokenization is made. XLSX previews show bounded cells and cached values, not a full spreadsheet engine. Safari and the in-app browser were used for the earlier visual acceptance; the earlier Chrome client-side block is distinct from the new executor's isolated Chrome tests. Browser observations are recorded separately from deterministic or model-routing checks. Generated sidebars and other components remain session scoped; they do not automatically become an account-wide shell. Live weather refresh still requires an explicit backend data/task implementation; registering a card does not prove automatic fresh data.
