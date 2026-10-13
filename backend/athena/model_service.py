"""Trusted model-provider composition with immutable, request-scoped bindings.

This is an in-process host API, not an agent-readable source-code loader. A failed
provider is never retried for the current request; only later bindings fall back.
"""
import asyncio
from dataclasses import dataclass, field
import inspect
import json
import re
from typing import Callable
from uuid import uuid4

from .errors import ServiceError
from . import model


PROTOCOLS = frozenset(("chat_completions", "chat_completions_json"))
SAFE_FAILURE_CODES = frozenset(("provider_auth_failed", "provider_balance_insufficient", "provider_access_denied",
    "provider_rate_limited", "provider_stream_error", "generation_incomplete", "stream_interrupted", "provider_contract_error"))


def safe_failure(error):
    # Even a trusted adapter can accidentally include a request/credential in an
    # exception. The engine persists exception text, so only host-owned prose and
    # a bounded diagnostic code may cross this boundary. Never preserve its cause.
    code = getattr(error, "code", None) if isinstance(error, ServiceError) else None
    if not isinstance(code, str) or (code not in SAFE_FAILURE_CODES and not re.fullmatch(r"provider_http_[45][0-9]{2}", code)):
        code = "provider_adapter_failed"
    return ServiceError(code, "The model request could not finish. Its partial work has been preserved.")


@dataclass(frozen=True)
class ModelProvider:
    id: str
    version: str
    protocol: str
    complete: Callable = field(repr=False, compare=False)
    contract: int = 1

    def validate(self):
        for value in (self.id, self.version):
            if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", value):
                raise ValueError("Use a short model-provider identity and version.")
        if self.protocol not in PROTOCOLS or self.contract != 1:
            raise ValueError("Unsupported model-provider contract.")
        if not inspect.iscoroutinefunction(self.complete):
            raise ValueError("A model provider must implement an async completion function.")
        # Validate before publishing, without executing candidate code or a request.
        inspect.signature(self.complete).bind(None, "", "", {}, None, None, request_id=None)


@dataclass(frozen=True)
class ModelBinding:
    host_id: str
    generation: int
    provider: ModelProvider
    fallback_from: str | None = None

    def snapshot(self):
        return {"host_id": self.host_id, "generation": self.generation,
                "provider_id": self.provider.id, "version": self.provider.version,
                "protocol": self.provider.protocol, "contract": self.provider.contract,
                "fallback_from": self.fallback_from}


def validate_result(result):
    """Reject incomplete protocol output before the engine can dispatch tools."""
    if not isinstance(result, dict) or not isinstance(result.get("message"), dict):
        raise ServiceError("provider_contract_error", "The model adapter returned an invalid result.")
    message = result["message"]
    if (not isinstance(message.get("content"), str)
            or not isinstance(message.get("reasoning_content"), str)
            or not isinstance(result.get("usage"), dict)):
        raise ServiceError("provider_contract_error", "The model adapter returned an invalid result.")
    calls = message.get("tool_calls", [])
    if not isinstance(calls, list):
        raise ServiceError("provider_contract_error", "The model adapter returned invalid tool calls.")
    identifiers = set()
    for call in calls:
        if not isinstance(call, dict):
            raise ServiceError("provider_contract_error", "The model adapter returned invalid tool calls.")
        function = call.get("function")
        identifier = call.get("id")
        if (not isinstance(identifier, str) or not identifier or identifier in identifiers
                or call.get("type") != "function" or not isinstance(function, dict)
                or not isinstance(function.get("name"), str) or not function["name"]
                or not isinstance(function.get("arguments"), str)):
            raise ServiceError("provider_contract_error", "The model adapter returned invalid tool calls.")
        identifiers.add(identifier)
    return result


class ModelService:
    def __init__(self, *, default_complete=None):
        self.owner = asyncio.get_running_loop()
        self.host_id = uuid4().hex
        self.generation = 0
        self.active: dict[str, ModelBinding] = {}
        self.fallbacks: dict[str, str] = {}
        self.versions: dict[tuple[str, str], ModelProvider] = {}
        # The composition root may supply an existing host/testing transport. It
        # is resolved once at bind(), never again during that request.
        self.default_complete = default_complete or (lambda: model.complete)

    def _check_owner(self):
        if asyncio.get_running_loop() is not self.owner:
            raise RuntimeError("Model service state belongs to its event loop.")

    def activate(self, provider: ModelProvider):
        self._check_owner()
        provider.validate()
        identity = (provider.id, provider.version)
        previous = self.versions.get(identity)
        if previous is not None and (previous.protocol != provider.protocol or previous.complete is not provider.complete):
            raise ValueError("A model-provider version is immutable; register a new version.")
        self.generation += 1
        binding = ModelBinding(self.host_id, self.generation, provider)
        self.versions[identity] = provider
        self.active[provider.protocol] = binding
        self.fallbacks.pop(provider.protocol, None)
        return binding.snapshot()

    def restore_default(self, protocol="chat_completions"):
        self._check_owner()
        if protocol not in PROTOCOLS:
            raise ValueError("Unsupported model protocol.")
        self.generation += 1
        self.active.pop(protocol, None)
        self.fallbacks.pop(protocol, None)

    def bind(self, protocol="chat_completions", *, hosted=False):
        self._check_owner()
        if protocol not in PROTOCOLS or (hosted and protocol != "chat_completions"):
            raise ServiceError("model_protocol_unavailable", "This model protocol is unavailable on this execution node.")
        selected = self.active.get(protocol)
        if selected is not None:
            return selected
        callback = self.default_complete() if protocol == "chat_completions" else model.complete_json
        provider = ModelProvider("builtin." + protocol, "1", protocol, callback)
        return ModelBinding(self.host_id, self.generation, provider, self.fallbacks.get(protocol))

    async def record(self, store, request_id, binding):
        self._check_owner()
        if binding.host_id != self.host_id:
            raise ValueError("A model binding belongs to a different host.")
        def save(db):
            db.execute("""CREATE TABLE IF NOT EXISTS model_provider_bindings (
                request_id TEXT PRIMARY KEY REFERENCES requests(id), snapshot TEXT NOT NULL)""")
            db.execute("INSERT INTO model_provider_bindings VALUES (?,?)",
                       (request_id, json.dumps(binding.snapshot(), sort_keys=True)))
        await store.call(save)

    async def complete(self, binding, client, endpoint, key, payload, on_text, on_reasoning, *, request_id=None):
        self._check_owner()
        if binding.host_id != self.host_id:
            raise ValueError("A model binding belongs to a different host.")
        try:
            keywords = {"request_id": request_id} if request_id is not None else {}
            result = await binding.provider.complete(client, endpoint, key, payload, on_text, on_reasoning, **keywords)
            return validate_result(result)
        except asyncio.CancelledError:
            # User Stop is not evidence that a provider is defective.
            raise
        except Exception as error:
            # Do not issue another request, replay streamed text, or rerun tools.
            # An older failed request must not displace a newer activation.
            protocol = binding.provider.protocol
            if self.active.get(protocol) is binding:
                self.active.pop(protocol)
                self.fallbacks[protocol] = binding.provider.id + "@" + binding.provider.version
                self.generation += 1
            raise safe_failure(error) from None
