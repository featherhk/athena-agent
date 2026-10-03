from .errors import UserError
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
import os
import sys
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal
from .config import Config, require_free_threading
from .engine import Engine
from .lock import InstanceLock
from .store import Store
from .auth import Auth, COOKIE, token_hash
from .metrics import summarize
from .errors import ServiceError
from .registry import initialize_registry
from .accounts import initialize_accounts, register_account_routes, pending_account_action
from .preferences import read_preferences, update_preferences, preference_schema
from .files import initialize_files, register_file_routes, attachment_info, list_session_files, list_files
from .user_help import list_help
from .component_feedback import register_component_routes
from .trace import register_trace_routes
from .client_version import register_client_version_route
from .frontend_state import register_frontend_routes, clear as clear_frontend_clients
from .app_controls import initialize_app_controls, list_app_actions, register_app_control_routes
from .voice import VoiceConfig, VoiceService, initialize_voice, register_voice_routes
from .activity import register_activity_routes


class Input(BaseModel):
    text: str = Field(default="", max_length=30000)
    file_ids: list[str] = Field(default_factory=list, max_length=10)
    mode: Literal["queue", "interrupt"] = "queue"


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=40, pattern=r"^[\w\-]+$")
    password: str = Field(min_length=8, max_length=128)


class DeveloperInput(BaseModel):
    enabled: bool


class HelpAnswer(BaseModel):
    answers: dict[str, str]
    file_ids: list[str] = Field(default_factory=list, max_length=10)


def public_message(message):
    result = {key: message[key] for key in ("id", "seq", "role", "content", "reasoning", "status", "run_id", "created_at") if key in message}
    result["attachments"] = [{k: v for k, v in item.items() if k != "workspace_path"} for item in json.loads(message.get("attachments") or "[]")]
    if message.get("help_reply"):
        result["help_reply"] = json.loads(message["help_reply"])
    from .task_events import message_origin
    origin = message_origin(message)
    if origin:
        result['origin'] = origin
    return result


def create_app(config: Config | None = None):
    config = config or Config.from_env()
    @asynccontextmanager
    async def lifespan(app):
        require_free_threading()
        config.prepare()
        lock = InstanceLock(config.data_dir / "instance.lock")
        pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="athena-worker")
        asyncio.get_running_loop().set_default_executor(pool)
        store = Store(config.data_dir / "athena.sqlite3")
        await store.initialize()
        await initialize_files(store)
        await initialize_registry(store)
        await initialize_accounts(store)
        await initialize_app_controls(store)
        await initialize_voice(store)
        app.state.voice = VoiceService(VoiceConfig.from_env(), store)
        engine = Engine(config, store)
        await engine.models.ensure_default()
        app.state.engine, app.state.store = engine, store
        app.state.auth = Auth(store)
        app.state.cloud = None
        if config.cloud_url:
            from .cloud_client import CloudAccounts
            cloud = CloudAccounts(store, config.cloud_url)
            await cloud.initialize()
            app.state.cloud = engine.cloud = cloud
            app.state.auth = cloud
            await app.state.voice.close()
            from .voice_gateway import CloudVoiceService
            app.state.voice = CloudVoiceService(cloud)
        scheduler = asyncio.create_task(engine.scheduler())
        try:
            yield
        finally:
            scheduler.cancel()
            await asyncio.gather(scheduler, return_exceptions=True)
            await engine.close()
            await app.state.voice.close()
            if app.state.cloud:
                await app.state.cloud.http.aclose()
            await store.close()
            pool.shutdown(wait=True)
            lock.close()
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def local_only(request, call_next):
        # Local prototype has no action approval workflow; block cross-site access to it.
        host = request.url.hostname
        public = urlparse(config.public_origin) if config.public_origin else None
        if public and (public.scheme != 'https' or not config.cloud_url):
            return JSONResponse({'code':'hosted_configuration_invalid'}, status_code=503)
        if host not in ("127.0.0.1", "localhost", "::1", "testserver", public.hostname if public else None):
            return JSONResponse({"error": "Only local requests are accepted"}, status_code=403)
        origin = request.headers.get("origin")
        allowed_origins = {f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (os.environ.get("ATHENA_PORT", "4317"), "5173")}
        if public:
            allowed_origins = {config.public_origin}
        if origin and origin not in allowed_origins:
            return JSONResponse({"error": "Cross-site requests are not allowed"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": "Cross-site requests are not allowed"}, status_code=403)
        if request.url.path.startswith('/api/'):
            try:
                request.state.user = await app.state.auth.current(request.cookies.get(COOKIE))
            except ServiceError as exc:
                return JSONResponse({'code':exc.code},status_code=exc.http_status)
            public = {"/api/health", "/api/auth/me", "/api/auth/login", "/api/auth/register"}
            if request.url.path not in public and not request.state.user:
                return JSONResponse({"error": "请先登录", "code": "login_required"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"error": str(exc), "code": getattr(exc, "code", "invalid_input")}, status_code=400)

    @app.exception_handler(ServiceError)
    async def unavailable(request, exc):
        return JSONResponse({"error": str(exc), "code": exc.code}, status_code=exc.http_status)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"code": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo credentials in Pydantic's rejected input representation.
        return JSONResponse({"code": "invalid_input"}, status_code=422)

    async def exists(session_id, request):
        if not await app.state.store.rows("SELECT id FROM sessions WHERE id=? AND user_id=?", (session_id, request.state.user["id"])):
            raise HTTPException(404, "session_not_found")

    async def login_response(user, token):
        response = JSONResponse({"user": user, "preferences": await read_preferences(app.state.store, user["id"])})
        response.set_cookie(COOKIE, token, httponly=True, secure=bool(config.public_origin), samesite="strict", max_age=30*86400, path="/")
        return response

    @app.get("/api/auth/me")
    async def me(request: Request):
        return {"user": request.state.user, "preferences": await read_preferences(app.state.store, request.state.user["id"]) if request.state.user else None, "configured": bool(await app.state.engine.api_key()), "has_users": bool(config.cloud_url) or bool(await app.state.store.rows("SELECT id FROM users LIMIT 1"))}

    @app.post("/api/auth/register")
    async def register(body: Credentials):
        user, token = await app.state.cloud.authenticate('register',body.username,body.password) if app.state.cloud else await app.state.auth.register(body.username, body.password)
        return await login_response(user, token)

    @app.post("/api/auth/login")
    async def login(body: Credentials):
        user, token = await app.state.cloud.authenticate('login',body.username,body.password) if app.state.cloud else await app.state.auth.login(body.username, body.password)
        return await login_response(user, token)

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        if app.state.cloud:
            await app.state.cloud.logout(request.cookies[COOKIE])
        def revoke(db):
            hashed = token_hash(request.cookies[COOKIE])
            clear_frontend_clients(db, request.state.user["id"], hashed)
            db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (hashed,))
        await app.state.store.call(revoke)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get('/api/account/credit')
    async def credit(request: Request):
        if not app.state.cloud:
            return {'managed':False}
        result=await app.state.cloud.call('/auth/me',token=await app.state.cloud.token(request.state.user['id']))
        return {'managed':True,**result['credit']}

    @app.post("/api/settings/developer")
    async def developer(request: Request, body: DeveloperInput):
        await update_preferences(app.state.store, request.state.user["id"], {"developer_mode": body.enabled})
        return {"enabled": body.enabled}

    @app.get("/api/settings/preferences")
    async def preferences(request: Request):
        return {"settings": await read_preferences(app.state.store, request.state.user["id"]), "schema": preference_schema()}

    @app.post("/api/settings/preferences")
    async def set_preferences(request: Request, body: dict):
        return await update_preferences(app.state.store, request.state.user["id"], body)

    @app.get("/api/developer/metrics")
    async def metrics(request: Request, session_id: str | None = None):
        if not request.state.user["developer_mode"]:
            raise HTTPException(403, "developer_required")
        args = [request.state.user["id"]]
        extra = ""
        if session_id:
            await exists(session_id, request)
            from .agents import usage_sessions
            identifiers = await app.state.store.call(lambda db: usage_sessions(db, session_id))
            extra = ' AND s.id IN (' + ','.join('?' for _ in identifiers) + ')'
            args.extend(identifiers)
        rows = await app.state.store.rows("SELECT q.* FROM requests q JOIN runs r ON r.id=q.run_id JOIN sessions s ON s.id=r.session_id WHERE s.user_id=?" + extra + " ORDER BY q.created_at", tuple(args))
        result = summarize(rows)
        result['includes_delegated_work'] = True
        result["service"] = await app.state.engine.models.diagnostic(session_id)
        return result

    @app.get("/api/service")
    async def service(request: Request, session_id: str | None = None):
        if session_id:
            await exists(session_id, request)
        diagnostic = await app.state.engine.models.diagnostic(session_id)
        return {"configured": diagnostic["configured"], "message": "" if diagnostic["configured"] else "Athena 的服务尚未就绪。你可以先写下想做的事，稍后再发。"}

    @app.get("/api/files")
    async def files(request: Request):
        uid = request.state.user["id"]
        extensions = await app.state.store.rows("SELECT name,version,file_path,active,created_at FROM extensions WHERE user_id=? ORDER BY created_at DESC", (uid,))
        components = await app.state.store.rows("SELECT c.id,c.title,c.version,c.file_path,c.active FROM components c JOIN sessions s ON s.id=c.session_id WHERE s.user_id=? ORDER BY c.created_at DESC", (uid,))
        return {"extensions": extensions, "components": components, "files": await list_files(app.state.store, uid)}

    @app.post("/api/reset")
    async def reset(request: Request):
        await app.state.engine.reset_user(request.state.user["id"])
        return {"ok": True, "preserved": ["messages", "files", "memories"]}

    @app.get("/api/health")
    async def health():
        return {"ok": True, "model": config.model, "configured": bool(await app.state.engine.api_key()), "gil_enabled": sys._is_gil_enabled()}

    @app.get("/api/sessions")
    async def sessions(request: Request):
        return await app.state.store.rows("SELECT s.*,(SELECT COUNT(*) FROM runs r WHERE r.session_id=s.id AND r.status IN ('queued','running','waiting_user','waiting_agents')) AS pending FROM sessions s WHERE s.user_id=? AND NOT EXISTS(SELECT 1 FROM agent_jobs j WHERE j.child_session_id=s.id) ORDER BY created_at DESC", (request.state.user["id"],))

    @app.post("/api/sessions")
    async def new_session(request: Request):
        return {"id": await app.state.store.session(user_id=request.state.user["id"])}

    @app.get("/api/sessions/{session_id}")
    async def snapshot(session_id: str, request: Request):
        await exists(session_id, request)
        def snapshot(db):
            from .component_views import active_components
            from .agents import branch, public_job
            history = Store._history(db, session_id)
            return {
                "messages": [public_message(row) for row in history if row["role"] in ("user", "assistant")],
                "runs": [dict(row) for row in db.execute("SELECT * FROM runs WHERE session_id=? ORDER BY rowid DESC LIMIT 50", (session_id,))],
                "components": active_components(db, session_id),
                "tasks": [dict(row) for row in db.execute("SELECT * FROM tasks WHERE session_id=? AND status!='done' ORDER BY due_at", (session_id,))],
                "cursor": db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE session_id=?", (session_id,)).fetchone()[0],
                "agents": [public_job(job) for job in branch(db, session_id)],
            }
        result = await app.state.store.call(snapshot)
        result["account_action"] = await pending_account_action(app.state.store, request.state.user["id"])
        result["preferences"] = await read_preferences(app.state.store, request.state.user["id"])
        result["files"] = await list_session_files(app.state.store, config, request.state.user["id"], session_id)
        result["help_requests"] = await list_help(app.state.store, session_id, request.state.user["id"])
        from .agents import delegated_help
        result['help_requests'] += await delegated_help(app.state.store, session_id, request.state.user['id'])
        from .files import list_files
        for job in result['agents']:
            result['files'] += [file for file in await list_files(app.state.store, request.state.user['id'], job['child_session_id']) if file['source'] == 'output']
        result["app_actions"] = await list_app_actions(app.state.store, session_id, request.state.user["id"])
        return result

    @app.post("/api/sessions/{session_id}/help/{help_id}/answer")
    async def answer_help(session_id: str, help_id: str, body: HelpAnswer, request: Request):
        await exists(session_id, request)
        attachments = await attachment_info(app.state.store, config, request.state.user["id"], session_id, body.file_ids)
        from .agents import help_target
        target = await help_target(app.state.store, session_id, request.state.user['id'], help_id)
        return await app.state.engine.answer_help(target, request.state.user["id"], help_id, body.answers, attachments)

    @app.post('/api/sessions/{session_id}/agents/{agent_id}/cancel')
    async def cancel_agent(session_id: str, agent_id: str, request: Request):
        await exists(session_id, request)
        result = await app.state.engine.tools.agents.operate(session_id, None, {'action': 'cancel', 'agent_id': agent_id})
        await app.state.engine.reconcile_agents()
        await app.state.engine.emit(session_id, 'agents_changed', {})
        return result

    @app.post("/api/sessions/{session_id}/messages")
    async def submit(session_id: str, body: Input, request: Request):
        await exists(session_id, request)
        if not body.text.strip() and not body.file_ids:
            raise UserError("empty_message", "请写下你想做的事")
        attachments = await attachment_info(app.state.store, config, request.state.user["id"], session_id, body.file_ids)
        return {"run_id": await app.state.engine.submit(session_id, body.text.strip(), body.mode, attachments)}

    @app.post("/api/sessions/{session_id}/stop")
    async def stop(session_id: str, request: Request):
        await exists(session_id, request)
        await app.state.engine.stop(session_id)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/fork")
    async def fork(session_id: str, request: Request):
        await exists(session_id, request)
        return {"id": await app.state.store.fork(session_id)}

    @app.post("/api/sessions/{session_id}/tasks/{task_id}/pause")
    async def pause(session_id: str, task_id: str, request: Request):
        await exists(session_id, request)
        await app.state.store.execute("UPDATE tasks SET status='paused' WHERE id=? AND session_id=?", (task_id, session_id))
        await app.state.engine.emit(session_id, "refresh", {})
        return {"ok": True}

    @app.get("/api/sessions/{session_id}/events")
    async def events(session_id: str, request: Request, after: int = 0):
        await exists(session_id, request)
        engine = app.state.engine
        try:
            initial_cursor = max(0, after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise ValueError("无效的消息位置")
        async def generate():
            cursor = initial_cursor
            queue = asyncio.Queue(maxsize=256)
            engine.listeners.setdefault(session_id, set()).add(queue)
            def encode(event):
                return f"id: {event['seq']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            try:
                idle_ticks = 0
                while not engine.closing and not await request.is_disconnected():
                    if not await app.state.auth.current(request.cookies.get(COOKIE)):
                        return
                    # Queues only wake the reader. Always catch up from the log so
                    # transactional events from preferences/registries cannot be skipped.
                    rows = await app.state.store.events(session_id, cursor)
                    for row in rows:
                        cursor = row["seq"]
                        yield encode(row)
                    if rows:
                        continue
                    try:
                        event = await asyncio.wait_for(queue.get(), 1)
                        if event is None:
                            return
                        idle_ticks = 0
                    except TimeoutError:
                        idle_ticks += 1
                        if idle_ticks >= 15:
                            yield ": keepalive\n\n"
                            idle_ticks = 0
            finally:
                engine.listeners.get(session_id, set()).discard(queue)
        return StreamingResponse(generate(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    register_file_routes(app)
    register_account_routes(app)
    register_component_routes(app, exists)
    register_trace_routes(app, exists)
    register_voice_routes(app, exists)
    register_client_version_route(app, config)
    register_app_control_routes(app, exists)
    register_frontend_routes(app)
    register_activity_routes(app)
    dist = config.repo_dir / "dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    @app.get("/")
    async def home():
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return JSONResponse({"message": "Run npm run dev for the development UI, or npm run build first."})
    return app
