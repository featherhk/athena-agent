"""Loopback-only source onboarding; cloud accounts never enter this path.

The internal principal preserves ownership without a cloud login. Existing
accounts and pre-account history require their original explicit login flow.
Credentials use the existing private server store, and are never serialized.
"""
import asyncio
import ipaddress
import json
import secrets
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .auth import password_hash, public_user
from .profiles import Profile
from .store import now, uid

PRINCIPAL = "local_setup:principal"
PROFILE = "local_setup:profile"
REVISION = "local_setup:revision"


def _setting(db, key, fallback=None):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else fallback


def _set(db, key, value):
    db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def principal(db):
    identifier = _setting(db, PRINCIPAL)
    users = db.execute("SELECT * FROM users LIMIT 2").fetchall()
    if len(users) == 1 and users[0]["id"] == identifier:
        return dict(users[0])
    return None


def fresh(db):
    return not any(db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                   for table in ("users", "sessions", "memories")) and not _setting(db, PRINCIPAL)


def initial_profile(db, user_id):
    user = principal(db)
    identifier = _setting(db, PROFILE)
    if user and user["id"] == user_id and identifier and db.execute("SELECT 1 FROM model_profiles WHERE id=? AND enabled=1", (identifier,)).fetchone():
        return identifier
    return "default"


def _local_request(request, config):
    if not config.local_setup:
        raise HTTPException(404, "local_setup_unavailable")
    try:
        peer = ipaddress.ip_address(request.client.host) if request.client else None
    except ValueError:
        peer = None
    if not peer or not peer.is_loopback or request.url.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise HTTPException(403, "local_setup_unavailable")
    # Do not infer local authority from forwarding headers, even behind a local proxy.
    if any(name in request.headers for name in ("forwarded", "x-forwarded-for", "x-forwarded-host")):
        raise HTTPException(403, "local_setup_unavailable")


async def status(store, config):
    if not config.local_setup:
        return {"mode": "hosted" if config.cloud_url else "account"}
    def read(db):
        user = principal(db)
        return {"mode": "local" if user or fresh(db) else "account", "initialized": bool(user)}
    return await store.call(read)


async def registration_allowed(store, config):
    if config.local_setup and await store.call(principal):
        raise HTTPException(409, "local_identity_managed")


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)
    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=120)
    api_base: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr | None = None
    session_id: str | None = Field(default=None, max_length=128)


def _definition(body):
    official_deepseek = body.api_base.rstrip("/") in ("https://api.deepseek.com", "https://api.deepseek.com/v1")
    return Profile(id="local-" + uid(), provider=body.provider.strip(), model=body.model.strip(),
                   base_model=body.model.strip(), api_base=body.api_base.rstrip("/"),
                   preserve_reasoning=official_deepseek,
                   options={"thinking": {"type": "enabled"}} if official_deepseek else {})


def register_routes(app, config, login_response):
    @app.get("/api/local/setup")
    async def setup_status(request: Request):
        if config.local_setup:
            _local_request(request, config)
        return await status(app.state.store, config)

    @app.post("/api/local/bootstrap")
    async def bootstrap(request: Request):
        _local_request(request, config)
        # Not a shared default password. KDF work stays outside the DB worker.
        encoded = await asyncio.to_thread(password_hash, secrets.token_urlsafe(48))
        def create(db):
            user = principal(db)
            if user:
                return public_user(user)
            if not fresh(db):
                raise HTTPException(409, "local_existing_accounts")
            identifier = uid()
            db.execute("INSERT INTO users(id,username,password_hash,role,created_at) VALUES (?,?,?,'owner',?)",
                       (identifier, "local-" + identifier[:12], encoded, now()))
            db.execute("INSERT INTO projects VALUES (?, '我的事情')", ("personal:" + identifier,))
            _set(db, PRINCIPAL, identifier)
            return public_user(dict(db.execute("SELECT * FROM users WHERE id=?", (identifier,)).fetchone()))
        user = await app.state.store.call(create)
        return await login_response(user, await app.state.auth.issue(user["id"]))

    def owned(db, user_id):
        user = principal(db)
        if not user or user["id"] != user_id:
            raise HTTPException(403, "local_setup_unavailable")

    @app.get("/api/local/provider")
    async def provider(request: Request):
        _local_request(request, config)
        def read(db):
            owned(db, request.state.user["id"])
            identifier = initial_profile(db, request.state.user["id"])
            row = db.execute("SELECT definition FROM model_profiles WHERE id=?", (identifier,)).fetchone()
            definition = json.loads(row[0])
            key = "deepseek_api_key" if identifier == "default" else "provider_key:" + identifier
            configured = bool((config.api_key if identifier == "default" else "") or _setting(db, key))
            return {"revision": int(_setting(db, REVISION, "0")), "configured": configured,
                    "profile": {key: definition[key] for key in ("id", "provider", "model", "api_base")}}
        return await app.state.store.call(read)

    @app.post("/api/local/provider")
    async def save_provider(body: ProviderInput, request: Request):
        _local_request(request, config)
        profile = _definition(body)
        key = body.api_key.get_secret_value().strip() if body.api_key is not None else ""
        if len(key) > 8192 or any(ord(c) < 32 for c in key):
            raise HTTPException(400, "local_provider_key_invalid")
        owner = request.state.user["id"]
        def save(db):
            owned(db, owner)
            revision = int(_setting(db, REVISION, "0"))
            if body.revision != revision:
                raise HTTPException(409, "local_setup_conflict")
            previous_id = initial_profile(db, owner)
            previous = json.loads(db.execute("SELECT definition FROM model_profiles WHERE id=?", (previous_id,)).fetchone()[0])
            credential = key
            if not credential and previous["api_base"].rstrip("/") == profile.api_base:
                credential = (config.api_key if previous_id == "default" else "") or _setting(db, "deepseek_api_key" if previous_id == "default" else "provider_key:" + previous_id, "")
            has_existing_credential = bool(credential)
            if not credential:
                if urlparse(profile.api_base).hostname not in ("127.0.0.1", "localhost", "::1"):
                    raise HTTPException(400, "local_provider_key_required")
                credential = "local-no-key"
            session = None
            if body.session_id:
                session = db.execute("SELECT * FROM sessions WHERE id=? AND user_id=?", (body.session_id, owner)).fetchone()
                if session is None:
                    raise HTTPException(404, "session_not_found")
                if db.execute("SELECT 1 FROM runs WHERE session_id=? AND status IN ('queued','running','waiting_user','waiting_agents')", (body.session_id,)).fetchone():
                    raise HTTPException(409, "local_model_busy")
            same_connection = has_existing_credential and not key and all(previous[name] == getattr(profile, name)
                                              for name in ("provider", "model", "api_base"))
            selected = previous if same_connection else profile.snapshot()
            selected_id = selected["id"]
            definition = json.dumps(selected)
            if not same_connection:
                db.execute("INSERT INTO model_profiles VALUES (?,?,1,?)", (selected_id, definition, now()))
                _set(db, "provider_key:" + selected_id, credential)
                _set(db, PROFILE, selected_id)
                revision += 1
                _set(db, REVISION, revision)
            session_changed = bool(session and session["model_profile_id"] != selected_id)
            if session_changed:
                epoch = session["model_epoch"] + 1
                db.execute("UPDATE sessions SET model_profile_id=?,model_epoch=? WHERE id=?", (selected_id, epoch, body.session_id))
                db.execute("INSERT INTO model_switches VALUES (?,?,?,?,?,?,?,?,?)", (uid(), body.session_id, None,
                           session["model_profile_id"], selected_id, epoch, "Local model settings", definition, now()))
            return {"configured": True, "revision": revision,
                    "profile": {key: selected[key] for key in ("id", "provider", "model", "api_base")}}, session_changed
        result, session_changed = await app.state.store.call(save)
        if session_changed:
            await app.state.engine.emit(body.session_id, "model_changed", {"profile_id": result["profile"]["id"]})
        return result
