"""Operator-registered, immutable Chat Completions profiles. No secrets in snapshots."""
from dataclasses import asdict, dataclass, field
import json
import re
from urllib.parse import urlparse
from .errors import ServiceError
from .store import now, uid


@dataclass(frozen=True)
class Profile:
    id: str
    provider: str
    model: str
    base_model: str
    api_base: str
    instruction_role: str = "system"
    preserve_reasoning: bool = False
    output_parameter: str = "max_tokens"
    max_output_tokens: int = 16384
    # Character guard, deliberately not advertised as a tokenizer measurement.
    context_chars: int = 180000
    context_window_tokens: int | None = None
    context_window_source: str | None = None
    options: dict = field(default_factory=dict)

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.id):
            raise ValueError("Invalid model profile id")
        for value in (self.provider, self.model, self.base_model):
            if not isinstance(value, str) or not value.strip() or len(value) > 120 or any(ord(c) < 32 for c in value):
                raise ValueError("Model identity must be a short, single-line label")
        url = urlparse(self.api_base)
        if url.username or url.password or url.query or url.fragment or not url.hostname:
            raise ValueError("Use an API base URL without credentials or query parameters")
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")):
            raise ValueError("Remote model endpoints require HTTPS")
        if self.instruction_role not in ("system", "developer") or self.output_parameter not in ("max_tokens", "max_completion_tokens"):
            raise ValueError("Unsupported Chat Completions profile convention")
        if not 1024 <= self.context_chars <= 2_000_000 or not 1 <= self.max_output_tokens <= 131072:
            raise ValueError("Invalid model limits")
        if self.context_window_tokens is not None and (type(self.context_window_tokens) is not int or self.context_window_tokens < 1024):
            raise ValueError("Invalid documented context window")
        allowed = {"thinking", "reasoning_effort", "temperature", "top_p"}
        if not isinstance(self.options, dict) or self.options.keys() - allowed:
            raise ValueError("Unsupported generation option; credentials and request fields cannot be overridden")

    def snapshot(self):
        return asdict(self)

    def payload(self, messages, tools):
        return {**self.options, "model": self.model, "messages": messages, "tools": tools,
                "stream": True, "stream_options": {"include_usage": True}, self.output_parameter: self.max_output_tokens}


class Models:
    def __init__(self, config, store):
        self.config, self.store = config, store

    async def ensure_default(self):
        profile = Profile(id="default", provider="DeepSeek", model=self.config.model,
                          base_model="DeepSeek V4.1 Flash", api_base=self.config.api_base,
                          preserve_reasoning=True, context_chars=1_200_000,
                          context_window_tokens=1_000_000 if self.config.model == "deepseek-flash" and self.config.api_base.rstrip("/") in ("https://api.deepseek.com", "https://api.deepseek.com/v1") else None,
                          context_window_source="https://api-docs.deepseek.com/quick_start/pricing/",
                          options={"thinking": {"type": "enabled"}, "reasoning_effort": "low"})
        await self.store.execute("INSERT OR IGNORE INTO model_profiles VALUES (?,?,1,?)", (profile.id, json.dumps(profile.snapshot()), now()))

    async def get(self, profile_id):
        await self.ensure_default()
        rows = await self.store.rows("SELECT definition FROM model_profiles WHERE id=? AND enabled=1", (profile_id,))
        if not rows:
            raise ServiceError("model_unavailable", "这段对话使用的服务暂不可用，请稍后再试。")
        definition = json.loads(rows[0]["definition"])
        # Add only documented metadata to older default profiles; generation
        # settings and existing request snapshots remain immutable.
        if (definition.get("model") == "deepseek-flash" and definition.get("api_base", "").rstrip("/") in ("https://api.deepseek.com", "https://api.deepseek.com/v1") and "context_window_tokens" not in definition):
            definition["context_window_tokens"] = 1_000_000
            definition["context_window_source"] = "https://api-docs.deepseek.com/quick_start/pricing/"
        return Profile(**definition)

    async def selected(self, session_id=None):
        if not session_id:
            return await self.get("default"), 0
        rows = await self.store.rows("SELECT model_profile_id,model_epoch FROM sessions WHERE id=?", (session_id,))
        if not rows:
            raise ValueError("Session not found")
        return await self.get(rows[0]["model_profile_id"]), rows[0]["model_epoch"]

    async def key(self, profile):
        if profile.id == "default" and self.config.api_key:
            return self.config.api_key
        key = "deepseek_api_key" if profile.id == "default" else f"provider_key:{profile.id}"
        rows = await self.store.rows("SELECT value FROM settings WHERE key=?", (key,))
        return rows[0]["value"] if rows else ""

    async def require_key(self, profile):
        key = await self.key(profile)
        if not key:
            raise ServiceError("provider_not_configured", "Athena 的服务尚未就绪。你写的内容还在，请稍后再试。")
        return key

    async def catalog(self):
        await self.ensure_default()
        profiles = [Profile(**json.loads(row["definition"])) for row in await self.store.rows("SELECT definition FROM model_profiles WHERE enabled=1 ORDER BY id")]
        return [{"id": p.id, "provider": p.provider, "model": p.model, "base_model": p.base_model, "configured": bool(await self.key(p))} for p in profiles]

    async def diagnostic(self, session_id=None):
        try:
            profile, epoch = await self.selected(session_id)
            configured = bool(await self.key(profile))
            return {"configured": configured, "code": "configured" if configured else "provider_not_configured",
                    "profile_id": profile.id, "provider": profile.provider, "model": profile.model,
                    "base_model": profile.base_model, "context_epoch": epoch}
        except ServiceError as exc:
            return {"configured": False, "code": exc.code}

    async def switch(self, session_id, profile_id, reason, run_id):
        if not reason.strip():
            raise ValueError("A model switch requires a reason")
        profile = await self.get(profile_id)
        await self.require_key(profile)
        def change(db):
            old = db.execute("SELECT model_profile_id,model_epoch FROM sessions WHERE id=?", (session_id,)).fetchone()
            if old is None:
                raise ValueError("Session not found")
            if old["model_profile_id"] == profile_id:
                return {"profile_id": profile_id, "context_epoch": old["model_epoch"], "changed": False}
            epoch = old["model_epoch"] + 1
            db.execute("UPDATE sessions SET model_profile_id=?,model_epoch=? WHERE id=?", (profile_id, epoch, session_id))
            db.execute("INSERT INTO model_switches VALUES (?,?,?,?,?,?,?,?,?)", (uid(), session_id, run_id, old["model_profile_id"], profile_id, epoch, reason, json.dumps(profile.snapshot()), now()))
            return {"profile_id": profile_id, "context_epoch": epoch, "changed": True, "effective": "next_model_request"}
        return await self.store.call(change)
