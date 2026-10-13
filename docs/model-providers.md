# Request-bound model providers

The engine now sends each model request through a bound model provider. This is
the first non-policy service path. It is not a complete service graph and does
not expose arbitrary Python loading to agents or customers.

## Implemented boundary

`ModelService` is owned by the engine's event loop. A trusted host can activate a
`ModelProvider` with an ID, immutable version, protocol, contract version and
async completion implementation. Candidate signature/contract validation happens
before activation. Reusing an ID/version for different code is rejected. An
activation publishes a new generation; requests already bound to an old provider
continue with that exact callable. No provider metadata is inserted into the
model's stable system prefix.

The engine freezes the binding with the selected model profile, before obtaining
credentials or preparing its request. `model_provider_bindings` stores a snapshot
for each durable model request: host ID, generation, provider ID/version, protocol,
contract and fallback origin. Host IDs distinguish generations across restarts.
The table contains no keys or executable code. Model profile snapshots and the
existing canonical messages/tool receipts remain authoritative. A provider
replacement does not alter or rerun previous messages or tool calls.

The contract receives the host HTTP client, endpoint and credential through an
in-process call, the canonical request payload, text/reasoning callbacks and an
optional request ID. It returns canonical assistant content, provider reasoning,
tool calls, usage and returned model identity. The host validates the terminal
result before any tools can execute. Implementations are therefore trusted host
code with credential access, **not sandboxed agent plugins**.
Exception text and causes never cross into request/run records: the service
replaces them with host-owned prose and a recognized diagnostic code. This does
not make intentionally malicious in-process code safe; adapters must never emit
credentials through text callbacks, normal results or their own logging.

If an active candidate fails, the current request fails once and retains any
partial output and earlier completed tool effects. It is never retried with the
default adapter. Future requests bind the default adapter for that protocol and
audit the failed candidate. A late failure from an older generation cannot disable
a newer activation. Cancellation does not quarantine a provider. Restoring a
default or explicitly activating a corrected version changes future bindings
only. This layer does not add retry behavior inside third-party implementations;
trusted adapters must obey the same one-request/no-replay contract.

## Two actual transport implementations

An immutable operator profile selects `protocol`:

- `chat_completions` is the backward-compatible default. Existing SSE request
  bodies, reasoning preservation and tool-call pairing are unchanged.
- `chat_completions_json` sends one non-streaming Chat Completions request with
  `stream:false` and no `stream_options`, for compatible servers without SSE. It
  normalizes the final response to the same engine contract and emits its text
  and reasoning only after validating the result.

Switching to another profile uses the existing explicit model/context epoch
boundary. Missing `protocol` in old stored profiles means the streaming default.
The hosted account gateway currently requires SSE, so a non-streaming protocol
selection is rejected on that route rather than silently changing it. No
Responses or Anthropic wire-protocol adapter is included in this revision.

Operators select the JSON implementation with the existing `athena.admin
register-model` command's `--protocol chat_completions_json` argument. Credentials
continue to use the separate hidden/file-backed `provider-key` command. Existing
profile IDs cannot be overwritten; register a new profile and explicitly switch
the conversation to apply the protocol change.

```python
# Trusted composition code; never source supplied through a model tool.
engine.model_service.activate(ModelProvider(
    id="operator.compatible", version="2", protocol="chat_completions",
    complete=my_completion_adapter,
))
```

## Limits and acceptance

Activations are currently process-local host configuration. They are not persisted
plugin installations, have no dependency graph or resource lifecycle, and revert
to configured built-ins after restart. Loop, context construction, scheduling,
execution and SQLite remain concrete services. A custom callback must not retain
or mutate credentials/payloads outside its request; Python cannot enforce that
against trusted in-process code. The protected recovery path remains independent.

`tests/test_model_service.py` verifies a replacement during an in-flight request,
failed activation, late old-provider failure, cancellation, later-only fallback,
JSON HTTP normalization and errors, immutable profile snapshots, and actual
engine/database effects. The latter writes one memory, fails a subsequent model
request, and resumes with the default provider without repeating the write. These
are local synthetic providers, not an external vendor compatibility certification.
