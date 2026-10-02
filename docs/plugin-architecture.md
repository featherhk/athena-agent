# Evolving Athena without making users manage a framework

## Product contract

1. The harness is an instrument Athena can adapt. All harness capabilities should ultimately be services with replaceable providers: the agent loop, model/protocol adapters, context assembly, memory, execution, scheduling, storage, telemetry and interaction. Compression, memory selection and execution are first consumers, not the boundaries of the design.
2. The frontend is a communication surface. A useful interaction can become a card, warning, worksheet or a focused workspace. Defaults should be minimal and comfortable; stop and recovery must remain reachable when an extension fails.
3. Changes must serve the user's actual goal. Repainting an interface while failing to check a flight is a product failure. Self-modification is not a goal in itself, and consumes the same latency/token budget as the task it is meant to improve.

These are architecture and acceptance requirements, not proof that the current prototype already satisfies every one.

## Reference and decision

[DeepSeek Harness's official architecture](https://deepseek-harness.github.io/deepseek-harness/en/reference/) separates service definitions, providers and consumers, and composes even its loop and storage through Cordis plugins. Its reversible registration lifecycle and distinction between durable facts and live events are useful references. Athena retains Python 3.14T and SQLite; adopting these principles does not require adopting its TypeScript runtime or persistence format.

Athena's target is a composed service graph. A provider depends on contracts, not on another provider's concrete class. Each contract has a version, input/output or stream vocabulary, cancellation semantics, lifecycle and state-transfer requirements. Plugin manifests declare what they provide and require. Discovery must expose both installed and actually active capabilities.

Activation is a transaction: validate contracts and dependencies, stage a candidate, run contract checks, publish an activation generation, then drain and release the previous generation. Requests bind to an immutable generation. Failed preparation leaves the active graph untouched. Failed active providers fall back to a known working/default composition. Persistent registrations, timers and UI contributions need scoped disposal; unloading Python code alone is insufficient.

A stateless transformer can switch at a request boundary. A loop, scheduler or storage provider needs a quiescent checkpoint and a validated handoff; it cannot be treated as a fast function callback. Storage migration must preserve an independently recoverable source snapshot. Protocol adapters can change wire format while retaining canonical request/stream/tool-result audit semantics. Secret delivery belongs in the host's credential channel, not model-readable plugin configuration.

The launcher and independent recovery path form a minimal control plane, not a privileged implementation of all product capabilities. Reset must be usable when the normal loop, UI renderer or chosen provider is broken. Data preservation and ownership are invariants of replacement, not optional plugin promises.

## What this revision implements

- One small model-facing runtime envelope, with operating details loaded through directory skill packages.
- External, immutable versions of tools, components, skills and policy providers; explicit restoration creates a new version.
- Request-bound custom tool implementations and schema snapshots.
- Read-only process-isolated policy execution with validation, timeout/output bounds, failure audit and automatic fallback. Policy IDs are open rather than a fixed enumeration. Current consumers are context compression, memory recall and command preparation. An unknown registered service is reported as waiting for a consumer, not falsely advertised as effective.
- Inline, banner, workspace/fullscreen, sidebars, anchored corners, overlays and replaceable presence, with version-scoped browser loading/visibility reports. Components receive theme tokens and bounded message geometry, and can apply reversible text styles. Arbitrary hex themes derive readable shell colors; presets are shortcuts. The shell owns input, stop and reset.
- Discoverable application actions through the existing runtime tool: navigation, fixed views, file previews/downloads and attachment selection, with durable claim/acknowledgement. User clicks and agent requests reach the same underlying features; secrets remain in fixed forms.
- Durable requests for user information or manual assistance. Waiting releases the model stream, answers resume the same run once, and stop/reset cancels outstanding requests without deleting history.
- Durable events and context projection boundaries, recoverable originals, fixed model/default recovery, and account-scoped state.

## What remains before “everything is replaceable” is true

The current Engine and Store still implement the loop and persistence directly. They are not yet providers behind the service graph described above. Provider streaming, scheduling, authentication transport, tool protocol assembly and complete frontend rendering also need explicit service interfaces and lifecycle integration. Current policy callbacks cannot replace them merely by registering a new name.

The next architectural milestone must move a real vertical path (input → loop → model adapter → execution → log → UI) through the service host, then demonstrate replacement and recovery of each provider. A generic plugin registry with no consumers is not completion. Nor is an adapter switch that leaves sibling consumers calling the old concrete implementation.

Required proof: replace a non-policy provider during controlled operation; preserve an in-flight request's binding; restore after candidate failure; reconnect/fork from identical durable history; recover without the replaced provider. Only then expand provider families. Keep the default personal-assistant experience usable throughout the migration.
