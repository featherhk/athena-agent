# Requirement-driven acceptance

Athena is tested by the state a user's request should produce, not whether its final sentence says “done”. Deterministic tests exercise the harness; an opt-in real-provider evaluation checks intent routing. Neither should use customer conversations as fixtures.

| User request | Observable result | Main checks |
| --- | --- | --- |
| Change this app to Simplified Chinese | Account preference changes and open sessions receive the durable event | `test_preferences`, `test_requirements`, live evaluation |
| Keep menus Chinese; answer in Japanese from now on | UI stays Chinese; a sourced global preference is recalled in a new conversation | `test_requirements`, live evaluation |
| Use a serif font and ocean colors | Shell consumes saved settings without a page reload | `test_preferences`, live evaluation; visual verification pending |
| Give yourself a reusable rectangle-area tool | Registered code lives outside the checkout and appears in a subsequent model request | `test_evolution`, live evaluation |
| Restore the previous version of a broken tool or component | A new registration points to the restored content; historical registrations remain immutable | `test_evolution` |
| Reuse a skill containing a script and a reference | Only metadata is initially supplied; named resources load on demand | `test_skillpacks`, `test_evolution` |
| Change my name or avatar | Authenticated account data changes and propagates; unrelated accounts cannot access it | `test_accounts`, live display-name evaluation |
| Change my password | Chat opens a fixed secret-input form; the password does not enter model tools or request snapshots | `test_accounts` |
| Show a Markdown reply and reasoning while working | Markdown is rendered; actual returned reasoning is separately collapsible; persisted phases distinguish thinking/reply/action | `test_requirements`, provider parser, TypeScript build |
| Read this attachment; give me a workbook or document | Files are associated with messages, scoped to their owner, previewable and downloadable | `test_files` |
| Stop, reconnect, switch chats, or restart | Partial content survives; browser disconnect does not cancel a run; side effects are not silently replayed | `test_runtime`, `test_requirements` |
| Reset after a bad extension or model change | Fixed recovery restores defaults and disables extensions while keeping chats/files | `test_requirements`, `test_evolution` |
| Report context, cache hits, or the last hour's spending | Reported API usage is distinguished from estimates, unknowns and configured limits | `test_introspection` |
| Compact toward 20k tokens | Only tool projections are clipped; originals are recoverable; estimated target achievement is explicitly reported | `test_introspection` |

## Activation and refresh

- Preferences and account state are stored before durable events. All open sessions catch up from SQLite in sequence; queues are wake signals, never the only copy of an event.
- Frontend registration increments a version. The next snapshot replaces the corresponding isolated frame without reloading the chat or stream.
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

The real-provider evaluation uses a temporary account/database and removes its files when finished. It prints verdicts and usage totals, not credentials, conversations or request recordings. It is an explicit paid check, excluded from CI.

## Honest limits

This is extensibility with recovery, not arbitrary safe self-rewriting of the application core. Browser/computer control is not bundled. No claim of production billing, operating-system isolation across platforms, or exact local provider tokenization is made. XLSX previews show bounded cells and cached values, not a full spreadsheet engine. Visual browser verification is pending because the available browser tool currently rejects the local URL.
