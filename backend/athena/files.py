"""Owned external files with bounded previews and no-follow disk access.

SQLite remains on Store's dedicated worker. Filesystem work runs off the event
loop; directory descriptors prevent path replacement from escaping a workspace.
"""
from contextlib import contextmanager
import asyncio
import hashlib
import mimetypes
import os
from pathlib import PurePosixPath
import re
import stat
import unicodedata
from urllib.parse import quote
import xml.etree.ElementTree as ET
import zipfile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from .store import now, uid
from .document_preview import docx_preview
from .spreadsheet_preview import xlsx_preview

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_BYTES = 512 * 1024
MAX_DISCOVERED_FILES = 1000
CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif"}
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".html", ".htm", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".py", ".js", ".ts", ".css", ".log", ".svg"}
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


async def initialize_files(store):
    def migrate(db):
        db.execute("""CREATE TABLE IF NOT EXISTS file_entries (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            session_id TEXT NOT NULL REFERENCES sessions(id), name TEXT NOT NULL,
            path TEXT NOT NULL, size INTEGER NOT NULL, media_type TEXT NOT NULL,
            source TEXT NOT NULL, created_at TEXT NOT NULL, content_revision TEXT,
            UNIQUE(user_id,session_id,path))""")
        if not any(row[1] == "content_revision" for row in db.execute("PRAGMA table_info(file_entries)")):
            db.execute("ALTER TABLE file_entries ADD COLUMN content_revision TEXT")
    await store.call(migrate)


def _content_revision(info):
    # An observed file change token, not an immutable content address. ctime and
    # inode cover same-size writes with restored mtime and atomic replacements.
    # Hash metadata only: polling the file area must not reread every large file.
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return "s1-" + hashlib.sha256(":".join(map(str, identity)).encode("ascii")).hexdigest()


def _identity(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise HTTPException(404, "file_not_found")
    return value


def sanitize_filename(name):
    name = unicodedata.normalize("NFC", name).strip()
    if (not name or name in {".", ".."} or len(name.encode("utf-8")) > 240
            or any(char in name for char in "/\\")
            or any(unicodedata.category(char).startswith("C") for char in name)
            or name.startswith(".")):
        raise HTTPException(400, "invalid_filename")
    return name


def _parts(path):
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or "\\" in path or not candidate.parts or any(part in {".", ".."} for part in candidate.parts):
        raise HTTPException(404, "file_not_found")
    if any(not part or "\x00" in part for part in candidate.parts):
        raise HTTPException(404, "file_not_found")
    return candidate.parts


def _child_dir(parent, name, create=False):
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
    return os.open(name, DIRECTORY_FLAGS, dir_fd=parent)


@contextmanager
def _workspace(config, user_id, session_id, create=False):
    descriptors = []
    try:
        descriptors.append(os.open(config.data_dir, DIRECTORY_FLAGS))
        for part in ("files", _identity(user_id), _identity(session_id)):
            descriptors.append(_child_dir(descriptors[-1], part, create))
        yield descriptors[-1]
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _open_file(config, record, revision=None):
    descriptors = []
    try:
        with _workspace(config, record["user_id"], record["session_id"]) as workspace:
            current = workspace
            parts = _parts(record["path"])
            for part in parts[:-1]:
                current = _child_dir(current, part)
                descriptors.append(current)
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE_BYTES:
                os.close(descriptor)
                raise HTTPException(404, "file_not_found")
            # Older clients used a numeric size as a cache hint. Preserve that
            # URL format; only new observed tokens request conditional reads.
            if revision and not revision.isdigit() and revision != _content_revision(info):
                os.close(descriptor)
                raise HTTPException(409, "file_changed")
            return os.fdopen(descriptor, "rb"), info.st_size
    except OSError:
        raise HTTPException(404, "file_not_found") from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _media_type(name):
    suffix = PurePosixPath(name).suffix.lower()
    return IMAGE_TYPES.get(suffix) or mimetypes.guess_type(name)[0] or "application/octet-stream"


def public_file(record):
    result = {key: record[key] for key in ("id", "session_id", "name", "path", "size", "media_type", "source", "created_at")}
    result["content_url"] = f'/api/files/{record["id"]}/content'
    result["preview_url"] = f'/api/files/{record["id"]}/preview'
    result["content_revision"] = record["content_revision"] if "content_revision" in record.keys() else None
    return result


async def _session_owned(store, user_id, session_id):
    if not await store.rows("SELECT id FROM sessions WHERE id=? AND user_id=?", (session_id, user_id)):
        raise HTTPException(404, "session_not_found")


async def list_files(store, user_id, session_id=None):
    sql = "SELECT f.* FROM file_entries f JOIN sessions s ON s.id=f.session_id WHERE f.user_id=? AND s.user_id=?"
    args = [user_id, user_id]
    if session_id:
        sql += " AND f.session_id=?"
        args.append(session_id)
    return [public_file(row) for row in await store.rows(sql + " ORDER BY f.created_at DESC", tuple(args))]


def _discover(config, user_id, session_id):
    found = []
    visited = 0
    def walk(directory, prefix, depth):
        nonlocal visited
        if depth > 8:
            return
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > MAX_DISCOVERED_FILES:
                    return
                if entry.name.startswith(".") or entry.is_symlink():
                    continue
                path = str(PurePosixPath(prefix) / entry.name)
                try:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        child = _child_dir(directory, entry.name)
                        try:
                            walk(child, path, depth + 1)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= MAX_FILE_BYTES:
                        found.append((entry.name, path, info.st_size, _content_revision(info)))
                except OSError:
                    continue
    try:
        with _workspace(config, user_id, session_id) as workspace:
            walk(workspace, "", 0)
    except OSError:
        pass
    return found


async def discover_files(config, store, user_id, session_id):
    await _session_owned(store, user_id, session_id)
    found = await asyncio.to_thread(_discover, config, user_id, session_id)
    def register(db):
        for name, path, size, revision in found:
            if path.startswith("uploads/"):
                # Only the upload transaction can establish an upload's identity.
                # Discovery can run after its rename and before its INSERT.
                db.execute("""UPDATE file_entries SET size=?,media_type=?,content_revision=?
                    WHERE user_id=? AND session_id=? AND path=? AND source='upload'""",
                    (size, _media_type(name), revision, user_id, session_id, path))
                continue
            db.execute("""INSERT INTO file_entries(id,user_id,session_id,name,path,size,media_type,source,created_at,content_revision)
                VALUES (?,?,?,?,?,?,?,'output',?,?) ON CONFLICT(user_id,session_id,path)
                DO UPDATE SET size=excluded.size,media_type=excluded.media_type,content_revision=excluded.content_revision""",
                (uid(), user_id, session_id, name, path, size, _media_type(name), now(), revision))
    await store.call(register)
    return {path for _, path, _, _ in found}


async def list_session_files(store, config, user_id, session_id):
    paths = await discover_files(config, store, user_id, session_id)
    return [record for record in await list_files(store, user_id, session_id) if record["path"] in paths]


async def attachment_info(store, config, user_id, session_id, ids):
    await _session_owned(store, user_id, session_id)
    if len(ids) > 20 or len(ids) != len(set(ids)):
        raise HTTPException(400, "invalid_input")
    result = []
    for identifier in ids:
        records = await store.rows("SELECT * FROM file_entries WHERE id=? AND user_id=? AND session_id=?", (identifier, user_id, session_id))
        if not records:
            raise HTTPException(404, "file_not_found")
        record = records[0]
        handle, _ = await asyncio.to_thread(_open_file, config, record)
        await asyncio.to_thread(handle.close)
        result.append({**public_file(record), "workspace_path": str(config.data_dir / "files" / user_id / session_id / record["path"])})
    return result


def _start_upload(config, user_id, session_id, identifier):
    with _workspace(config, user_id, session_id, create=True) as workspace:
        uploads = _child_dir(workspace, "uploads", create=True)
        try:
            directory = _child_dir(uploads, identifier, create=True)
        finally:
            os.close(uploads)
        try:
            descriptor = os.open(".pending", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            return directory, os.fdopen(descriptor, "wb")
        except BaseException:
            os.close(directory)
            raise


def _finish_upload(directory, handle, name):
    handle.flush()
    os.fsync(handle.fileno())
    handle.close()
    os.rename(".pending", name, src_dir_fd=directory, dst_dir_fd=directory)
    return _content_revision(os.stat(name, dir_fd=directory, follow_symlinks=False))


def _cleanup_upload(directory, handle, name, success):
    handle.close()
    if not success:
        for target in (".pending", name):
            try:
                os.unlink(target, dir_fd=directory)
            except FileNotFoundError:
                pass
    os.close(directory)


def _xlsx_preview(data):
    return xlsx_preview(data)

def _preview(config, record, revision=None):
    handle, size = _open_file(config, record, revision)
    suffix = PurePosixPath(record["name"]).suffix.lower()
    with handle:
        if suffix in IMAGE_TYPES:
            return {"kind": "image", "url": f'/api/files/{record["id"]}/content'}
        if suffix == ".xlsx":
            try:
                return _xlsx_preview(handle.read(MAX_FILE_BYTES + 1))
            except (ValueError, KeyError, OSError, zipfile.BadZipFile, ET.ParseError, RuntimeError, OverflowError, NotImplementedError):
                return {"kind": "unsupported"}
        if suffix == ".docx":
            try:
                return docx_preview(handle.read(MAX_FILE_BYTES + 1))
            except (ValueError, KeyError, OSError, zipfile.BadZipFile, ET.ParseError, RuntimeError, OverflowError, NotImplementedError):
                return {"kind": "unsupported"}
        if suffix not in TEXT_SUFFIXES and not record["media_type"].startswith("text/"):
            return {"kind": "unsupported"}
        data = handle.read(MAX_TEXT_BYTES + 1)
        content = data[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")
        kind = "markdown" if suffix in {".md", ".markdown"} else "html" if suffix in {".html", ".htm"} else "text"
        return {"kind": kind, "content": content, "truncated": size > MAX_TEXT_BYTES}


def register_file_routes(app: FastAPI):
    def context(request):
        user = getattr(request.state, "user", None)
        if not user:
            raise HTTPException(401, "login_required")
        return app.state.store, app.state.engine.config, user["id"]

    async def owned(request, identifier):
        store, config, user_id = context(request)
        rows = await store.rows("SELECT f.* FROM file_entries f JOIN sessions s ON s.id=f.session_id WHERE f.id=? AND f.user_id=? AND s.user_id=?", (identifier, user_id, user_id))
        if not rows:
            raise HTTPException(404, "file_not_found")
        return config, rows[0]

    @app.get("/api/sessions/{session_id}/files")
    async def session_files(session_id: str, request: Request):
        store, config, user_id = context(request)
        return await list_session_files(store, config, user_id, session_id)

    @app.post("/api/sessions/{session_id}/files", status_code=201)
    async def upload(session_id: str, request: Request, name: str):
        store, config, user_id = context(request)
        await _session_owned(store, user_id, session_id)
        name = sanitize_filename(name)
        length = request.headers.get("content-length", "0")
        if not length.isdigit():
            raise HTTPException(400, "invalid_input")
        if int(length) > MAX_FILE_BYTES:
            raise HTTPException(413, "file_too_large")
        identifier = uid()
        try:
            directory, handle = await asyncio.to_thread(_start_upload, config, user_id, session_id, identifier)
        except OSError:
            raise HTTPException(400, "invalid_filename") from None
        size, success = 0, False
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise HTTPException(413, "file_too_large")
                await asyncio.to_thread(handle.write, chunk)
            revision = await asyncio.to_thread(_finish_upload, directory, handle, name)
            record = {"id": identifier, "user_id": user_id, "session_id": session_id, "name": name,
                      "path": f"uploads/{identifier}/{name}", "size": size, "media_type": _media_type(name), "source": "upload", "created_at": now(), "content_revision": revision}
            await store.execute("INSERT INTO file_entries(id,user_id,session_id,name,path,size,media_type,source,created_at,content_revision) VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(record.values()))
            success = True
            return public_file(record)
        finally:
            await asyncio.to_thread(_cleanup_upload, directory, handle, name, success)

    @app.get("/api/files/{file_id}/content")
    async def content(file_id: str, request: Request, download: bool = False, revision: str | None = None):
        config, record = await owned(request, file_id)
        handle, size = await asyncio.to_thread(_open_file, config, record, revision)
        suffix = PurePosixPath(record["name"]).suffix.lower()
        inline = not download and (suffix in IMAGE_TYPES or suffix in TEXT_SUFFIXES - {".html", ".htm", ".svg"})
        media_type = IMAGE_TYPES.get(suffix, "text/plain" if suffix in TEXT_SUFFIXES else "application/octet-stream")
        async def chunks():
            try:
                remaining = size
                while remaining > 0:
                    chunk = await asyncio.to_thread(handle.read, min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
            finally:
                await asyncio.to_thread(handle.close)
        headers = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                   "Content-Length": str(size), "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(record['name'], safe='')}"}
        return StreamingResponse(chunks(), media_type=media_type, headers=headers)

    @app.get("/api/files/{file_id}/preview")
    async def preview(file_id: str, request: Request, revision: str | None = None):
        config, record = await owned(request, file_id)
        return await asyncio.to_thread(_preview, config, record, revision)
