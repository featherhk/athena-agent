# Request budget correction — 2026-10-04

This change addresses the execution preflight mismatch from the algorithm review. It does not claim exact tokenization, deduplication of historical memory notes, or completion of the broader context/recall work.

## Observed failure

The prior `Engine.context()` trigger counted message bodies, reasoning and tool calls. The later send guard serialized the complete model payload. Admitted memory notes, attachment manifests, the stable instruction prefix and newly registered tool schemas contributed only to the latter. Compaction estimated schemas from the previous request, and its token-ratio target could exceed a small profile's character guard.

`tests/test_request_context_budget.py` reproduced a real run ending in `failed/context_limit` before any provider HTTP call, even though an older completed tool output could safely be clipped. The synthetic fixture has a 30,000-character profile guard, a 7,000-character admitted note, ten valid-length attachment names/paths, an 8,000-character registered schema description and a 13,000-character recoverable old tool result. No customer conversation or real provider was accessed.

## Change

- The engine snapshots definitions and version bindings before preparing context, and uses those definitions in compaction preflight, the final guard and the provider request.
- Automatic compaction measures the complete projected payload: the actual frozen profile, persisted prefix, every included message field, notes, attachment manifests and current tool schemas. Its character ceiling also constrains a target derived from the estimated token ratio.
- The existing prefix remains unchanged until a persisted compaction boundary. If that boundary admits a larger skill catalog, the engine rechecks the complete projection and makes at most one additional compaction pass. The final guard still prevents an oversized request from being sent.
- Latest tool-round observations, assistant reasoning, call/result pairing and all original rows remain intact. A failed clipping transaction cannot partially persist a projection.
- A model switch while preparing a request takes effect on the next request. The current preparation continues using its already selected profile and epoch.

No prompt, schema migration, new model call or public tool was added. The compression policy interface remains available; its input now includes the character ceiling during automatic compaction. A policy that cannot fit eligible content still leads to a truthful local `context_limit`, never an oversized provider call or silently deleted user content.

## Verification

Six new behavioral tests run the actual session actor and capture the serialized HTTP provider request through `httpx.MockTransport`, then inspect durable SQLite state. They cover successful clipping with schema growth; tool-version changes during preparation; irreducible payload failure without a provider call; prefix expansion at a persisted boundary; clipping transaction rollback; and a model switch during preparation. Originals are recovered through the runtime operation, not just compared against an in-memory expected string.

The context, introspection, model, policy-consumer and ownership/control regression selection passed **56 tests**:

```sh
uv run pytest tests/test_request_context_budget.py tests/test_context_projection.py tests/test_introspection.py tests/test_models.py tests/test_policy_consumers.py tests/test_context_controls.py -q
uv run --directory backend python -m athena.doctor
```

Doctor reported Python 3.14.8, a free-threaded build, `gil_enabled: false`, and SQLite thread safety 3. No live model request, user database access or production deployment was used for this verification.

An additional **67 tests** passed across `test_runtime.py`, `test_memory.py`, `test_requirements.py`, `test_agents.py`, `test_user_help.py` and `test_task_events.py`, covering the callers that depend on stable context, interruption, delegation and retained user input.

## Remaining limits

The character guard is an application safety limit, not the provider's tokenizer or a guaranteed token ceiling. Provider usage is still required for measured token/cache statistics. Standalone context inspection explicitly uses the previous request's schema when no live request snapshot exists. Repeated immutable memory notes, large user instructions, assistant reasoning and an inherently oversized active schema remain non-clippable. Their next improvements need explicit, recoverable context projections; they must not be hidden by deleting history or claiming that an estimated target was met.
