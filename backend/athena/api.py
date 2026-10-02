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


class Input(BaseModel):
    text: str = Field(min_length=1, max_length=30000)
    mode: Literal["queue", "interrupt"] = "queue"


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=40, pattern=r"^[\w\-]+$")
    password: str = Field(min_length=8, max_length=128)


class DeveloperInput(BaseModel):
    enabled: bool


def public_message(message):
    return {key: message[key] for key in ("id", "seq", "role", "content", "status", "run_id", "created_at") if key in message}


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
        engine = Engine(config, store)
        await engine.models.ensure_default()
        app.state.engine, app.state.store = engine, store
        app.state.auth = Auth(store)
        scheduler = asyncio.create_task(engine.scheduler())
        try:
            yield
        finally:
            scheduler.cancel()
            await asyncio.gather(scheduler, return_exceptions=True)
            await engine.close()
            await store.close()
            pool.shutdown(wait=True)
            lock.close()
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def local_only(request, call_next):
        # Local prototype has no action approval workflow; block cross-site access to it.
        host = request.url.hostname
        if host not in ("127.0.0.1", "localhost", "::1", "testserver"):
            return JSONResponse({"error": "Only local requests are accepted"}, status_code=403)
        origin = request.headers.get("origin")
        allowed_origins = {f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (os.environ.get("ATHENA_PORT", "4317"), "5173")}
        if origin and origin not in allowed_origins:
            return JSONResponse({"error": "Cross-site requests are not allowed"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": "Cross-site requests are not allowed"}, status_code=403)
        if request.url.path.startswith('/api/'):
            request.state.user = await app.state.auth.current(request.cookies.get(COOKIE))
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

    def login_response(user, token):
        response = JSONResponse({"user": user})
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=30*86400, path="/")
        return response

    @app.get("/api/auth/me")
    async def me(request: Request):
        return {"user": request.state.user, "configured": bool(await app.state.engine.api_key()), "has_users": bool(await app.state.store.rows("SELECT id FROM users LIMIT 1"))}

    @app.post("/api/auth/register")
    async def register(body: Credentials):
        user, token = await app.state.auth.register(body.username, body.password)
        return login_response(user, token)

    @app.post("/api/auth/login")
    async def login(body: Credentials):
        user, token = await app.state.auth.login(body.username, body.password)
        return login_response(user, token)

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        await app.state.store.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash(request.cookies[COOKIE]),))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.post("/api/settings/developer")
    async def developer(request: Request, body: DeveloperInput):
        await app.state.store.execute("UPDATE users SET developer_mode=? WHERE id=?", (int(body.enabled), request.state.user["id"]))
        return {"enabled": body.enabled}

    @app.get("/api/developer/metrics")
    async def metrics(request: Request, session_id: str | None = None):
        if not request.state.user["developer_mode"]:
            raise HTTPException(403, "developer_required")
        args = [request.state.user["id"]]
        extra = ""
        if session_id:
            await exists(session_id, request)
            extra = " AND s.id=?"
            args.append(session_id)
        rows = await app.state.store.rows("SELECT q.* FROM requests q JOIN runs r ON r.id=q.run_id JOIN sessions s ON s.id=r.session_id WHERE s.user_id=?" + extra + " ORDER BY q.created_at", tuple(args))
        result = summarize(rows)
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
        return {"extensions": extensions, "components": components}

    @app.post("/api/reset")
    async def reset(request: Request):
        await app.state.engine.reset_user(request.state.user["id"])
        return {"ok": True, "preserved": ["messages", "files", "memories"]}

    @app.get("/api/health")
    async def health():
        return {"ok": True, "model": config.model, "configured": bool(await app.state.engine.api_key()), "gil_enabled": sys._is_gil_enabled()}

    @app.get("/api/sessions")
    async def sessions(request: Request):
        return await app.state.store.rows("SELECT s.*,(SELECT COUNT(*) FROM runs r WHERE r.session_id=s.id AND r.status IN ('queued','running')) AS pending FROM sessions s WHERE s.user_id=? ORDER BY created_at DESC", (request.state.user["id"],))

    @app.post("/api/sessions")
    async def new_session(request: Request):
        return {"id": await app.state.store.session(user_id=request.state.user["id"])}

    @app.get("/api/sessions/{session_id}")
    async def snapshot(session_id: str, request: Request):
        await exists(session_id, request)
        def snapshot(db):
            history = Store._history(db, session_id)
            return {
                "messages": [public_message(row) for row in history if row["role"] in ("user", "assistant") and row["content"]],
                "runs": [dict(row) for row in db.execute("SELECT * FROM runs WHERE session_id=? ORDER BY rowid DESC LIMIT 50", (session_id,))],
                "components": [dict(row) for row in db.execute("SELECT c.* FROM components c WHERE session_id=? AND version=(SELECT MAX(version) FROM components n WHERE n.session_id=c.session_id AND n.id=c.id) AND active=1", (session_id,))],
                "tasks": [dict(row) for row in db.execute("SELECT * FROM tasks WHERE session_id=? AND status!='done' ORDER BY due_at", (session_id,))],
                "cursor": db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE session_id=?", (session_id,)).fetchone()[0],
            }
        return await app.state.store.call(snapshot)

    @app.post("/api/sessions/{session_id}/messages")
    async def submit(session_id: str, body: Input, request: Request):
        await exists(session_id, request)
        if not body.text.strip():
            raise UserError("empty_message", "请写下你想做的事")
        return {"run_id": await app.state.engine.submit(session_id, body.text.strip(), body.mode)}

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
                while True:
                    rows = await app.state.store.rows("SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT 500", (session_id, cursor))
                    for row in rows:
                        row["payload"] = json.loads(row["payload"])
                        cursor = row["seq"]
                        yield encode(row)
                    if len(rows) < 500:
                        break
                while not await request.is_disconnected():
                    if not await app.state.auth.current(request.cookies.get(COOKIE)):
                        return
                    try:
                        event = await asyncio.wait_for(queue.get(), 15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if event is None:
                        return
                    if event["seq"] > cursor:
                        cursor = event["seq"]
                        yield encode(event)
            finally:
                engine.listeners.get(session_id, set()).discard(queue)
        return StreamingResponse(generate(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    dist = config.repo_dir / "dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    @app.get("/")
    async def home():
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return JSONResponse({"message": "Run npm run dev for the development UI, or npm run build first."})
    return app
