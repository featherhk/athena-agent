"""Read a complete deployed frontend generation without caching across rebuilds."""
import asyncio
import json
import re


def client_version(config):
    try:
        path = config.repo_dir / "dist" / "client-version.json"
        if path.stat().st_size > 4096:
            return {"version": None}
        value = json.loads(path.read_text(encoding="utf-8"))
        version = value.get("version") if isinstance(value, dict) else None
        entry = value.get("entry") if isinstance(value, dict) else None
        if not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{16,64}", version):
            return {"version": None}
        if not isinstance(entry, str) or not re.fullmatch(r"assets/[^/\\]+\.js", entry):
            return {"version": None}
        dist = config.repo_dir / "dist"
        # During a rebuild, wait until the entry exists and index actually points
        # at it. A partially written output must not cause an old-client reload loop.
        if not (dist / entry).is_file() or entry not in (dist / "index.html").read_text(encoding="utf-8"):
            return {"version": None}
        return {"version": version}
    except (OSError, ValueError):
        # A dev server owns its HMR. An incomplete build never triggers a reload.
        return {"version": None}


def register_client_version_route(app, config):
    @app.get("/api/client-version")
    async def version():
        return await asyncio.to_thread(client_version, config)
