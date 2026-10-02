import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import shlex
import jsonschema
from .config import Config
from .prompt import read_skill
from .store import Store, now, uid
from .protection import protected_command
from .profiles import Models


def function(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}}


TOOLS = [
    function("exec", "执行本机命令，返回结果与完整输出的引用。", {
        "command": {"type": "string", "minLength": 1, "maxLength": 30000},
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
    }, ["command"]),
    function("update_frontend", "创建、更新或移除当前对话的交互组件。复杂用法见 interface skill。", {
        "op": {"type": "string", "enum": ["upsert", "remove"]},
        "id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,64}$"},
        "title": {"type": "string", "maxLength": 80},
        "html": {"type": "string", "maxLength": 100000},
    }, ["op", "id"]),
    function("runtime", "读取技能与状态，管理任务、记忆、扩展和模型。", {
        "op": {"type": "string", "enum": ["skill", "schedule", "tasks", "cancel_task", "remember", "recall", "read_artifact", "read_message", "register_tool", "remove_tool", "state", "models", "switch_model"]},
        "profile_id": {"type": "string", "maxLength": 64}, "reason": {"type": "string", "maxLength": 500},
        "name": {"type": "string"}, "title": {"type": "string", "maxLength": 100},
        "prompt": {"type": "string", "maxLength": 10000}, "due_at": {"type": "string"},
        "condition_command": {"type": "string", "maxLength": 30000},
        "interval_seconds": {"type": "integer", "minimum": 60}, "task_id": {"type": "string"},
        "scope": {"type": "string", "enum": ["global", "project", "session"]},
        "content": {"type": "string", "maxLength": 4000}, "query": {"type": "string", "maxLength": 300},
        "artifact_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
        "message_id": {"type": "string"},
        "description": {"type": "string", "maxLength": 300},
        "parameters": {"type": "object"}, "source": {"type": "string", "maxLength": 100000},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20000},
    }, ["op"]),
]


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit // 3
    return text[:head] + f"\n… 已省略 {len(text) - limit} 个字符；可用 read_artifact 恢复 …\n" + text[-(limit-head):]


class ToolRunner:
    def __init__(self, config: Config, store: Store):
        self.config, self.store = config, store
        self.models = Models(config, store)

    async def owner(self, session_id):
        rows = await self.store.rows("SELECT user_id,project_id FROM sessions WHERE id=?", (session_id,))
        return rows[0] if rows else {"user_id": None, "project_id": "personal"}

    async def registered(self, session_id):
        owner = await self.owner(session_id)
        return await self.store.rows("SELECT e.* FROM extensions e WHERE user_id=? AND version=(SELECT MAX(version) FROM extensions n WHERE n.user_id=e.user_id AND n.name=e.name) AND active=1 ORDER BY name", (owner["user_id"],))

    async def definitions(self, session_id):
        return TOOLS + [{"type": "function", "function": {"name": row["name"], "description": row["description"], "parameters": json.loads(row["parameters"])}} for row in await self.registered(session_id)]

    async def execute(self, name, args, session_id, run_id):
        schema = next((tool["function"]["parameters"] for tool in TOOLS if tool["function"]["name"] == name), None)
        if not schema:
            extension = next((row for row in await self.registered(session_id) if row["name"] == name), None)
            if not extension:
                raise ValueError("Unknown or disabled tool")
            jsonschema.validate(args, json.loads(extension["parameters"]))
            path = self.config.data_dir / extension["file_path"]
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(path))}"
            return await self.shell({"command": command}, session_id, run_id, json.dumps(args).encode())
        jsonschema.validate(args, schema)
        if name == "exec":
            return await self.shell(args, session_id, run_id)
        if name == "update_frontend":
            return await self.component(args, session_id)
        return await self.runtime(args, session_id, run_id)

    async def shell(self, args, session_id, run_id, stdin=None):
        owner = await self.owner(session_id)
        user = owner["user_id"] or "legacy"
        file_area = self.config.data_dir / "files" / user
        workspace = file_area / session_id
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        artifact_id = uid()
        path = self.config.data_dir / "artifacts" / f"{artifact_id}.txt"
        # Child commands do not inherit application/provider secrets.
        allowed = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "COMSPEC")
        env = {key: os.environ[key] for key in allowed if key in os.environ}
        env["ATHENA_WORKSPACE"] = str(workspace)
        extension_area = self.config.data_dir / "extensions" / user
        command = protected_command(args["command"], self.config, [file_area, extension_area], [file_area])
        process = await asyncio.create_subprocess_exec(*command, cwd=workspace, env=env, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        size, truncated, cancelled = 0, False, False
        async def read():
            nonlocal size, truncated
            if stdin is not None:
                try:
                    process.stdin.write(stdin)
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    process.stdin.close()
            with path.open("wb") as file:
                path.chmod(0o600)
                while chunk := await process.stdout.read(65536):
                    size += len(chunk)
                    file.write(chunk)
                    if size > 32 * 1024 * 1024:
                        truncated = True
                        raise ValueError("命令输出超过 32 MiB，执行已停止；已产生的输出仍可恢复。")
            await process.wait()
        error = None
        try:
            await asyncio.wait_for(read(), timeout=args.get("timeout_seconds", 120))
        except asyncio.CancelledError:
            cancelled = True
            raise
        except TimeoutError:
            error = "命令执行超时，已停止。"
        except ValueError as exc:
            error = str(exc)
        finally:
            if process.returncode is None:
                try:
                    if sys.platform == "win32":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            if path.exists():
                await self.store.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?)", (artifact_id, session_id, run_id, path.name, path.stat().st_size, now()))
        text = await asyncio.to_thread(path.read_text, errors="replace") if path.exists() else ""
        return {"exit_code": process.returncode, "output": clip(text, self.config.tool_output_chars), "artifact_id": artifact_id, "error": error, "output_limit_reached": truncated, "cancelled": cancelled}

    async def component(self, args, session_id):
        if args["op"] == "upsert" and not args.get("html"):
            raise ValueError("upsert requires html")
        owner = await self.owner(session_id)
        directory = self.config.data_dir / "extensions" / (owner["user_id"] or "legacy") / "frontend" / session_id
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / (uid() + ".html")
        if args["op"] == "upsert":
            await asyncio.to_thread(path.write_text, args["html"])
            path.chmod(0o600)
        def save(db):
            old = db.execute("SELECT * FROM components WHERE session_id=? AND id=? ORDER BY version DESC LIMIT 1", (session_id, args["id"])).fetchone()
            version = old["version"] + 1 if old else 1
            db.execute("INSERT INTO components(session_id,id,version,title,html,active,created_at,file_path) VALUES (?,?,?,?,?,?,?,?)", (session_id, args["id"], version, args.get("title", old["title"] if old else "小工具"), args.get("html", old["html"] if old else ""), int(args["op"] != "remove"), now(), str(path.relative_to(self.config.data_dir)) if args["op"] == "upsert" else old["file_path"] if old else None))
            return {"id": args["id"], "version": version, "status": args["op"]}
        return await self.store.call(save)

    async def runtime(self, args, session_id, run_id):
        op = args["op"]
        owner = await self.owner(session_id)
        user = owner["user_id"]
        if op == "models":
            return await self.models.catalog()
        if op == "switch_model":
            return await self.models.switch(session_id, args["profile_id"], args["reason"], run_id)
        if op == "state":
            return {"model": await self.models.diagnostic(session_id), "now_utc": now(),
                    "runs": await self.store.rows("SELECT id,status,error FROM runs WHERE session_id=? ORDER BY rowid DESC LIMIT 5", (session_id,)),
                    "tasks": await self.store.rows("SELECT id,title,status,due_at,last_error FROM tasks WHERE session_id=? AND status!='done'", (session_id,)),
                    "compactions": (await self.store.rows("SELECT COUNT(*) AS count FROM compactions WHERE session_id=?", (session_id,)))[0]["count"]}
        if op in ("register_tool", "remove_tool"):
            import re
            name = args["name"]
            if not user or not re.fullmatch(r"user_[a-z][a-z0-9_]{0,47}", name):
                raise ValueError("Custom tool names must start with user_ and use lowercase letters/digits/underscores")
            if op == "remove_tool":
                await self.store.execute("UPDATE extensions SET active=0 WHERE user_id=? AND name=?", (user, name))
                return {"status": "disabled", "name": name}
            parameters = args["parameters"]
            jsonschema.Draft202012Validator.check_schema(parameters)
            if parameters.get("type") != "object" or "$ref" in json.dumps(parameters):
                raise ValueError("Use an object parameter schema without external references")
            if len(await self.registered(session_id)) >= 20 and name not in {row["name"] for row in await self.registered(session_id)}:
                raise ValueError("最多同时注册 20 个自定义工具")
            compile(args["source"], name, "exec")
            directory = self.config.data_dir / "extensions" / user / "backend" / name
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = directory / (uid() + ".py")
            await asyncio.to_thread(path.write_text, args["source"])
            path.chmod(0o600)
            def register(db):
                version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM extensions WHERE user_id=? AND name=?", (user, name)).fetchone()[0]
                db.execute("INSERT INTO extensions VALUES (?,?,?,?,?,?,1,?)", (user, name, version, args["description"], json.dumps(parameters), str(path.relative_to(self.config.data_dir)), now()))
                return {"name": name, "version": version, "status": "registered"}
            return await self.store.call(register)
        if op == "skill":
            return {"content": read_skill(args["name"])}
        if op == "schedule":
            due = datetime.fromisoformat(args["due_at"])
            if due.tzinfo is None:
                raise ValueError("due_at must include a timezone")
            identifier = uid()
            if args.get("condition_command") and not args.get("interval_seconds"):
                raise ValueError("condition_command requires interval_seconds >= 60")
            await self.store.execute("INSERT INTO tasks(id,session_id,title,prompt,due_at,interval_seconds,status,created_at,condition_command) VALUES (?,?,?,?,?,?,'active',?,?)", (identifier, session_id, args["title"], args["prompt"], due.astimezone(timezone.utc).isoformat(), args.get("interval_seconds"), now(), args.get("condition_command")))
            return {"task_id": identifier, "status": "active", "due_at": due.isoformat()}
        if op == "tasks":
            return await self.store.rows("SELECT * FROM tasks WHERE session_id=? ORDER BY due_at", (session_id,))
        if op == "cancel_task":
            changed = await self.store.execute("UPDATE tasks SET status='paused' WHERE id=? AND session_id=?", (args["task_id"], session_id))
            return {"updated": bool(changed)}
        if op == "remember":
            scope = args.get("scope", "session")
            memory_owner = {"global": f"global:{user}" if user else "global", "project": owner["project_id"], "session": session_id}[scope]
            identifier = uid()
            await self.store.execute("INSERT INTO memories VALUES (?,?,?,?,?,?)", (identifier, scope, memory_owner, args["content"], run_id, now()))
            return {"memory_id": identifier, "scope": scope}
        if op == "recall":
            return await self.store.rows("SELECT * FROM memories WHERE owner_id IN (?,?,?) AND instr(lower(content),lower(?))>0 ORDER BY created_at DESC LIMIT 20", (f"global:{user}" if user else "global", owner["project_id"], session_id, args.get("query", "")))
        if op == "read_artifact":
            record = await self.store.artifact(session_id, args["artifact_id"])
            if not record:
                raise ValueError("Artifact not found in this session")
            text = await asyncio.to_thread((self.config.data_dir / "artifacts" / record["path"]).read_text, errors="replace")
            start, limit = args.get("offset", 0), args.get("limit", 12000)
            return {"content": text[start:start+limit], "offset": start, "total_chars": len(text)}
        if op == "read_message":
            record = next((item for item in await self.store.history(session_id) if item["id"] == args["message_id"]), None)
            if not record:
                raise ValueError("Message not found in this session")
            start, limit = args.get("offset", 0), args.get("limit", 12000)
            return {"content": record["content"][start:start+limit], "offset": start, "total_chars": len(record["content"])}
        raise ValueError("Unknown runtime operation")
