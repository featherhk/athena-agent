"""User-owned, append-only extension versions outside the application checkout."""
import asyncio
import base64
import json
from pathlib import Path, PurePosixPath
import re
import shutil

import jsonschema

from .store import now, uid


KINDS = {"tool": ("extensions", "user_id", "name"),
         "component": ("components", "session_id", "id"),
         "skill": ("user_skills", "user_id", "name"),
         "policy": ("harness_policies", "user_id", "name")}
BUILTIN_POLICY_CONSUMERS = ("memory_recall", "context_compression", "execution")
POLICY_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
POLICY_CONSUMERS = (
    {"name": "memory_recall", "description": "Select ids from the supplied memory candidates; the parent retains their original text."},
    {"name": "context_compression", "description": "Propose message-id to clipped-content mappings; the parent retains recoverable originals."},
    {"name": "execution", "description": "Transform command and timeout parameters before the parent performs the one actual execution."},
)
NAME = re.compile(r"user_[a-z][a-z0-9_]{0,47}\Z")
COMPONENT_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")
MAX_FILES, MAX_PACKAGE_BYTES = 64, 4 * 1024 * 1024


def policy_state(name, active=True, current=True):
    known = name in BUILTIN_POLICY_CONSUMERS
    return {"state": "historical" if not current else "disabled" if not active else "active" if known else "waiting_for_consumer",
            "effective": bool(known and active and current)}


def _initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS user_skills (
        user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
        version INTEGER NOT NULL, description TEXT NOT NULL,
        file_path TEXT NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(user_id,name,version))""")
    db.execute("""CREATE TABLE IF NOT EXISTS harness_policies (
        user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
        version INTEGER NOT NULL, description TEXT NOT NULL,
        file_path TEXT NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(user_id,name,version))""")
    db.execute("""CREATE TABLE IF NOT EXISTS policy_failures (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
        session_id TEXT NOT NULL REFERENCES sessions(id), name TEXT NOT NULL,
        version INTEGER NOT NULL, error_code TEXT NOT NULL,
        disabled INTEGER NOT NULL, created_at TEXT NOT NULL)""")


async def initialize_registry(store):
    await store.call(_initialize)


def reset_registry(db, user_id):
    """Called inside the reset transaction; old registrations remain unchanged."""
    _initialize(db)
    for table in ("extensions", "user_skills", "harness_policies"):
        rows = db.execute(f"SELECT e.* FROM {table} e WHERE user_id=? AND active=1 AND "
                          f"version=(SELECT MAX(version) FROM {table} n WHERE n.user_id=e.user_id AND n.name=e.name)",
                          (user_id,)).fetchall()
        for row in rows:
            if table == "extensions":
                db.execute("INSERT INTO extensions VALUES (?,?,?,?,?,?,0,?)",
                           (user_id, row["name"], row["version"] + 1, row["description"],
                            row["parameters"], row["file_path"], now()))
            else:
                db.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?,0,?)",
                           (user_id, row["name"], row["version"] + 1, row["description"], row["file_path"], now()))


def _resource_name(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Use a relative skill resource path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")) or ":" in value:
        raise ValueError("Use a relative skill resource path without traversal")
    if len(value) > 240:
        raise ValueError("Skill resource path is too long")
    return path


def _skill_metadata(text):
    # Shared core/user SKILL.md parser is provided by the skill loader.
    from .skillpacks import parse_skill_metadata
    return parse_skill_metadata(text)


def _package(args, name):
    files = args.get("files")
    if files is None:
        source = args.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("A skill requires files including SKILL.md, or source")
        if not source.startswith("---\n"):
            description = _description(args.get("description"))
            source = f"---\nname: {name}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---\n\n{source}"
        files = {"SKILL.md": source}
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES or "SKILL.md" not in files:
        raise ValueError("A skill package requires SKILL.md and at most 64 files")
    result, size = {}, 0
    for filename, content in files.items():
        relative = _resource_name(filename)
        if isinstance(content, str):
            data = content.encode("utf-8")
        elif isinstance(content, dict) and set(content) == {"encoding", "content"} and content["encoding"] == "base64":
            try:
                data = base64.b64decode(content["content"], validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError("Invalid base64 skill resource") from exc
        else:
            raise ValueError("Skill resources must be text or {encoding: base64, content: ...}")
        size += len(data)
        if size > MAX_PACKAGE_BYTES:
            raise ValueError("Skill package exceeds 4 MiB")
        result[str(relative)] = data
    for filename in result:
        if any(str(parent) in result for parent in PurePosixPath(filename).parents if str(parent) != "."):
            raise ValueError("A skill resource cannot also be a directory")
    try:
        metadata = _skill_metadata(result["SKILL.md"].decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("SKILL.md must be UTF-8") from exc
    if metadata["name"] != name:
        raise ValueError("SKILL.md name must match the registered user_ skill name")
    description = _description(metadata["description"])
    return result, description


def _description(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 300:
        raise ValueError("Description must contain 1–300 characters")
    return value


def _source(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 100000:
        raise ValueError("Source must contain 1–100000 characters")
    return value


class Registry:
    def __init__(self, config, store):
        self.config, self.store = config, store

    async def skill_catalog(self, session_id):
        await initialize_registry(self.store)
        rows = await self.store.rows("SELECT user_id FROM sessions WHERE id=?", (session_id,))
        if not rows or not rows[0]["user_id"]:
            return []
        return await self.store.rows("SELECT e.name,e.description,e.version FROM user_skills e WHERE user_id=? AND active=1 AND version=(SELECT MAX(version) FROM user_skills n WHERE n.user_id=e.user_id AND n.name=e.name) ORDER BY name", (rows[0]["user_id"],))

    async def _scope(self, session_id, kind):
        rows = await self.store.rows("SELECT user_id FROM sessions WHERE id=?", (session_id,))
        if not rows:
            raise ValueError("Session not found")
        user = rows[0]["user_id"]
        if kind != "component" and not user:
            raise ValueError("Custom tools, skills and policies require an account")
        return user or "legacy", session_id if kind == "component" else user

    @staticmethod
    def _record(db, kind, owner, name, version=None):
        table, column, key = KINDS[kind]
        sql, values = f"SELECT * FROM {table} WHERE {column}=? AND {key}=?", [owner, name]
        if version is not None:
            sql += " AND version=?"
            values.append(version)
        row = db.execute(sql + " ORDER BY version DESC LIMIT 1", values).fetchone()
        if row is None:
            raise ValueError("Extension version not found in this scope")
        return dict(row)

    def _path(self, row, user):
        root = self.config.data_dir / "extensions" / user
        path = self.config.data_dir / row["file_path"]
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Extension resource is outside its registered area")
        return path

    def _resource(self, row, user, path):
        root = self._path(row, user)
        relative = _resource_name(path)
        target = root.joinpath(*relative.parts)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Skill resources cannot be symlinks")
        if not target.resolve().is_relative_to(root.resolve()) or not target.is_file():
            raise ValueError("Skill resource not found")
        return target

    async def skill(self, session_id, name, path="SKILL.md", offset=0, limit=12000):
        result = await self.operate({"kind": "skill", "action": "read", "name": name,
                                     "path": path, "offset": offset, "limit": limit}, session_id)
        if not result["active"]:
            raise ValueError("Unknown or disabled skill")
        return result

    async def operate(self, args, session_id):
        kind, action = args.get("kind"), args.get("action")
        if kind not in KINDS or action not in ("list", "read", "register", "restore", "remove"):
            raise ValueError("Registry requires kind and action")
        await initialize_registry(self.store)
        user, owner = await self._scope(session_id, kind)
        key = KINDS[kind][2]
        name = args.get(key) or (args.get("name") if kind == "component" else None)
        if name is not None:
            if kind == "policy":
                if not isinstance(name, str) or not POLICY_NAME.fullmatch(name):
                    raise ValueError("Policy service id must contain lowercase letters, digits, underscores, dots or hyphens")
            elif not isinstance(name, str) or not (COMPONENT_ID if kind == "component" else NAME).fullmatch(name):
                raise ValueError("Use a valid component id or a lowercase user_ tool/skill name")
        if action != "list" and name is None:
            raise ValueError(f"Registry {action} requires {key}")
        version = args.get("version")
        if version is not None and (type(version) is not int or version < 1):
            raise ValueError("Version must be a positive integer")
        if action == "list":
            table, column, _ = KINDS[kind]
            limit = min(200, max(1, args.get("limit", 100)))
            offset = max(0, args.get("offset", 0))
            sql = (f"SELECT e.*, (e.version=(SELECT MAX(n.version) FROM {table} n "
                   f"WHERE n.{column}=e.{column} AND n.{key}=e.{key})) AS current FROM {table} e WHERE e.{column}=?")
            values = [owner]
            if name:
                sql += f" AND e.{key}=?"
                values.append(name)
            rows = await self.store.rows(sql + f" ORDER BY e.{key},e.version DESC LIMIT ? OFFSET ?", (*values, limit, offset))
            items = []
            for row in rows:
                item = {field: row[field] for field in (key, "version", "active", "current", "created_at")}
                item["description" if kind != "component" else "title"] = row["description" if kind != "component" else "title"]
                item["enabled"] = bool(row["active"] and row["current"])
                if kind == "component":
                    item["placement"] = row.get("placement", "inline")
                elif kind == "policy":
                    item.update(policy_state(name=row["name"], active=row["active"], current=row["current"]))
                items.append(item)
            result = {"kind": kind, "items": items, "offset": offset, "limit": limit}
            if kind == "policy":
                result["consumers"] = list(POLICY_CONSUMERS)
            return result
        if action == "read":
            row = await self.store.call(lambda db: self._record(db, kind, owner, name, version))
            result = {"kind": kind, key: name, "version": row["version"], "active": bool(row["active"])}
            if kind == "policy":
                latest = await self.store.call(lambda db: self._record(db, kind, owner, name))
                result.update(policy_state(name, row["active"], latest["version"] == row["version"]))
            if kind == "component":
                return {**result, "title": row["title"], "source": row["html"], "placement": row.get("placement", "inline")}
            result["description"] = row["description"]
            if kind in ("tool", "policy"):
                source = await asyncio.to_thread(self._path(row, user).read_text, encoding="utf-8")
                if kind == "tool":
                    result["parameters"] = json.loads(row["parameters"])
                return {**result, "source": source}
            path = args.get("path", "SKILL.md")
            resource = self._resource(row, user, path)
            data = await asyncio.to_thread(resource.read_bytes)
            offset, limit = max(0, args.get("offset", 0)), min(20000, max(1, args.get("limit", 12000)))
            try:
                text = data.decode("utf-8")
                content, encoding, total = text[offset:offset + limit], "utf-8", len(text)
            except UnicodeDecodeError:
                content, encoding, total = base64.b64encode(data[offset:offset + limit]).decode(), "base64", len(data)
            return {**result, "path": path, "source": content, "content": content, "encoding": encoding,
                    "offset": offset, "total": total, "has_more": offset + limit < total,
                    "total_chars" if encoding == "utf-8" else "total_bytes": total,
                    "next_offset": min(offset + limit, total)}
        if action == "remove":
            def remove(db):
                row = self._record(db, kind, owner, name)
                if not row["active"]:
                    return {"kind": kind, key: name, "version": row["version"], "status": "disabled"}
                self._insert(db, kind, owner, name, row["version"] + 1, row, active=False)
                return {"kind": kind, key: name, "version": row["version"] + 1, "status": "disabled"}
            result = await self.store.call(remove)
            return {**result, **policy_state(name, active=False)} if kind == "policy" else result
        if action == "restore":
            if version is None:
                raise ValueError("Restore requires an explicit historical version")
            row = await self.store.call(lambda db: self._record(db, kind, owner, name, version))
            if kind == "component":
                args = {**args, "source": row["html"], "title": row["title"], "placement": row.get("placement", "inline")}
            elif kind in ("tool", "policy"):
                source = await asyncio.to_thread(self._path(row, user).read_text, encoding="utf-8")
                args = {**args, "source": source, "description": row["description"]}
                if kind == "tool":
                    args["parameters"] = json.loads(row["parameters"])
            else:
                root = self._path(row, user)
                def read_package():
                    resources = {}
                    for path in root.rglob("*"):
                        if path.is_symlink():
                            raise ValueError("Skill resources cannot be symlinks")
                        if path.is_file():
                            resources[str(path.relative_to(root))] = {"encoding": "base64", "content": base64.b64encode(path.read_bytes()).decode()}
                    return resources
                args = {**args, "files": await asyncio.to_thread(read_package)}
        return await self._register(args, kind, user, owner, name, action)

    @staticmethod
    def _insert(db, kind, owner, name, version, record, active=True):
        if kind == "tool":
            db.execute("INSERT INTO extensions VALUES (?,?,?,?,?,?,?,?)", (owner, name, version,
                       record["description"], record["parameters"], record["file_path"], int(active), now()))
        elif kind == "component":
            db.execute("INSERT INTO components(session_id,id,version,title,html,active,created_at,file_path,placement) VALUES (?,?,?,?,?,?,?,?,?)",
                       (owner, name, version, record["title"], record["html"], int(active), now(), record["file_path"], record.get("placement", "inline")))
        else:
            db.execute(f"INSERT INTO {KINDS[kind][0]} VALUES (?,?,?,?,?,?,?)", (owner, name, version,
                       record["description"], record["file_path"], int(active), now()))

    async def _register(self, args, kind, user, owner, name, action):
        record, package = {}, None
        if kind == "skill":
            package, record["description"] = _package(args, name)
        else:
            source = _source(args.get("source"))
            if kind in ("tool", "policy"):
                if kind == "tool":
                    parameters = args.get("parameters")
                    if not isinstance(parameters, dict):
                        raise ValueError("A tool requires an object parameter schema")
                    jsonschema.Draft202012Validator.check_schema(parameters)
                    if parameters.get("type") != "object" or "$ref" in json.dumps(parameters):
                        raise ValueError("Use an object parameter schema without external references")
                    record["parameters"] = json.dumps(parameters)
                compile(source, name, "exec")
                record["description"] = _description(args.get("description"))
            else:
                old = await self.store.rows("SELECT title,placement FROM components WHERE session_id=? AND id=? ORDER BY version DESC LIMIT 1", (owner, name))
                title = args.get("title", old[0]["title"] if old else "小工具")
                placement = args.get("placement", old[0]["placement"] if old else "inline")
                if placement not in ("inline", "workspace", "banner"):
                    raise ValueError("Component placement must be inline, workspace or banner")
                if not isinstance(title, str) or len(title) > 80:
                    raise ValueError("Component title must be at most 80 characters")
                record.update(title=title, html=source, placement=placement)
        area = {"tool": "backend", "component": "frontend", "skill": "skills", "policy": "policies"}[kind]
        directory = self.config.data_dir / "extensions" / user / area / (owner if kind == "component" else name)
        path = directory / (uid() + ({"tool": ".py", "component": ".html", "skill": "", "policy": ".py"}[kind]))
        def write():
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if package is not None:
                path.mkdir(mode=0o700)
                for filename, data in package.items():
                    target = path / filename
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with target.open("xb") as output:
                        target.chmod(0o600)
                        output.write(data)
            else:
                with path.open("x", encoding="utf-8") as output:
                    path.chmod(0o600)
                    output.write(source)
        await asyncio.to_thread(write)
        record["file_path"] = str(path.relative_to(self.config.data_dir))
        def register(db):
            table, column, key = KINDS[kind]
            if kind == "tool":
                names = {row[0] for row in db.execute("SELECT e.name FROM extensions e WHERE user_id=? AND active=1 AND version=(SELECT MAX(version) FROM extensions n WHERE n.user_id=e.user_id AND n.name=e.name)", (owner,))}
                if len(names) >= 20 and name not in names:
                    raise ValueError("最多同时注册 20 个自定义工具")
            version = db.execute(f"SELECT COALESCE(MAX(version),0)+1 FROM {table} WHERE {column}=? AND {key}=?", (owner, name)).fetchone()[0]
            self._insert(db, kind, owner, name, version, record)
            result = {"kind": kind, key: name, "version": version, "status": "restored" if action == "restore" else "registered"}
            return {**result, **policy_state(name)} if kind == "policy" else result
        try:
            return await self.store.call(register)
        except Exception:
            if path.is_dir():
                await asyncio.to_thread(shutil.rmtree, path)
            else:
                await asyncio.to_thread(path.unlink, missing_ok=True)
            raise
