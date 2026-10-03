"""Owner-scoped, paged activity records; never expose provider request snapshots."""
import asyncio
import json
import os
from pathlib import Path
import stat
from typing import Literal

from fastapi import HTTPException, Query, Request


PREVIEW_CHARS = 1600


def _visible_run(db, session_id, run_id):
    """Resolve the immutable fork boundary without materializing chat contents."""
    visible, cursor, boundary = {}, session_id, None
    for _ in range(101):
        row = db.execute("SELECT parent_id,fork_seq FROM sessions WHERE id=?", (cursor,)).fetchone()
        if row is None:
            break
        visible[cursor] = boundary
        if row["parent_id"] is None:
            break
        boundary = row["fork_seq"] if boundary is None else min(boundary, row["fork_seq"])
        cursor = row["parent_id"]
    run = db.execute("SELECT id,session_id,status,phase,error,error_code,created_at,finished_at FROM runs WHERE id=?", (run_id,)).fetchone()
    if run is None or run["session_id"] not in visible:
        raise HTTPException(404, "run_not_found")
    cap = visible[run["session_id"]]
    if cap is not None and not db.execute("SELECT 1 FROM messages WHERE run_id=? AND seq<=?", (run_id, cap)).fetchone():
        raise HTTPException(404, "run_not_found")
    return dict(run), cap


def _message(db, session_id, run_id, message_id):
    run, cap = _visible_run(db, session_id, run_id)
    row = db.execute("SELECT * FROM messages WHERE id=? AND run_id=?", (message_id, run_id)).fetchone()
    if row is None or (cap is not None and row["seq"] > cap):
        raise HTTPException(404, "message_not_found")
    return dict(row), run


def _artifact_id(content):
    try:
        output = json.loads(content)
    except (ValueError, TypeError):
        return None
    return output.get("artifact_id") if isinstance(output, dict) and isinstance(output.get("artifact_id"), str) else None


def _preview(row):
    result = {key: row[key] for key in ("id", "seq", "role", "status", "tool_call_id", "created_at")}
    for field in ("content", "reasoning", "tool_calls"):
        value = row[field] or ""
        result[field] = value[:PREVIEW_CHARS]
        result[field + "_chars"] = len(value)
    result["artifact_id"] = _artifact_id(row["content"]) if row["role"] == "tool" else None
    from .task_events import task_origin
    origin = task_origin(row['origin']) if row['role'] == 'user' else None
    if origin:
        result['origin'] = origin
    return result


async def run_trace(store, session_id, run_id, after=0, limit=25):
    def read(db):
        run, cap = _visible_run(db, session_id, run_id)
        where, parameters = "run_id=?", [run_id]
        if cap is not None:
            where += " AND seq<=?"
            parameters.append(cap)
        total = db.execute(f"SELECT COUNT(*) FROM messages WHERE {where}", parameters).fetchone()[0]
        rows = db.execute(f"SELECT * FROM messages WHERE {where} AND seq>? ORDER BY seq LIMIT ?", [*parameters, after, limit + 1]).fetchall()
        page = rows[:limit]
        return {"run": run, "entries": [_preview(row) for row in page], "total": total,
                "next_after": page[-1]["seq"] if page else after, "has_more": len(rows) > limit}
    return await store.call(read)


async def trace_field(store, session_id, run_id, message_id, field, offset=0, limit=12000):
    if field not in {"content", "reasoning", "tool_calls"}:
        raise HTTPException(422, "invalid_input")
    def read(db):
        message, _ = _message(db, session_id, run_id, message_id)
        value = message[field] or ""
        return {"content": value[offset:offset + limit], "offset": offset, "total_chars": len(value)}
    return await store.call(read)


def _artifact_page(directory, name, offset, limit):
    # Artifact paths are generated filenames. Do not follow symlinks, special files,
    # or a forged database path when exposing an original tool output to the browser.
    if Path(name).name != name or name in {"", ".", ".."}:
        raise HTTPException(404, "artifact_not_found")
    descriptor = None
    try:
        descriptor = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise HTTPException(404, "artifact_not_found")
        with os.fdopen(descriptor, "r", encoding="utf-8", errors="replace") as source:
            descriptor = None
            length, pieces = 0, []
            while chunk := source.read(65536):
                start, end = max(0, offset - length), min(len(chunk), offset + limit - length)
                if end > start:
                    pieces.append(chunk[start:end])
                length += len(chunk)
            return {"content": "".join(pieces), "offset": offset, "total_chars": length}
    except OSError:
        raise HTTPException(404, "artifact_not_found") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


async def trace_artifact(store, config, session_id, run_id, message_id, offset=0, limit=12000):
    def lookup(db):
        message, run = _message(db, session_id, run_id, message_id)
        identifier = _artifact_id(message["content"]) if message["role"] == "tool" else None
        row = db.execute("SELECT path FROM artifacts WHERE id=? AND run_id=? AND session_id=?", (identifier, run_id, run["session_id"])).fetchone()
        if row is None:
            raise HTTPException(404, "artifact_not_found")
        return row["path"]
    name = await store.call(lookup)
    return await asyncio.to_thread(_artifact_page, config.data_dir / "artifacts", name, offset, limit)


def register_trace_routes(app, exists):
    @app.get("/api/sessions/{session_id}/runs/{run_id}/trace")
    async def trace(session_id: str, run_id: str, request: Request,
                    after: int = Query(default=0, ge=0), limit: int = Query(default=25, ge=1, le=50)):
        await exists(session_id, request)
        return await run_trace(app.state.store, session_id, run_id, after, limit)

    @app.get("/api/sessions/{session_id}/runs/{run_id}/trace/{message_id}")
    async def field(session_id: str, run_id: str, message_id: str, request: Request,
                    field: Literal["content", "reasoning", "tool_calls"] = "content",
                    offset: int = Query(default=0, ge=0), limit: int = Query(default=12000, ge=1, le=20000)):
        await exists(session_id, request)
        return await trace_field(app.state.store, session_id, run_id, message_id, field, offset, limit)

    @app.get("/api/sessions/{session_id}/runs/{run_id}/trace/{message_id}/artifact")
    async def artifact(session_id: str, run_id: str, message_id: str, request: Request,
                       offset: int = Query(default=0, ge=0), limit: int = Query(default=12000, ge=1, le=20000)):
        await exists(session_id, request)
        return await trace_artifact(app.state.store, app.state.engine.config, session_id, run_id, message_id, offset, limit)
