import json

import httpx

from athena.api import create_app
from athena.client_version import client_version


def write_build(config, version, entry="assets/index-test.js"):
    dist = config.repo_dir / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / entry).write_text("// compiled code")
    (dist / "index.html").write_text(f'<script type="module" src="/{entry}"></script>')
    (dist / "client-version.json").write_text(json.dumps({"version": version, "entry": entry}))


def test_incomplete_or_invalid_build_does_not_trigger_refresh(config):
    assert client_version(config) == {"version": None}
    write_build(config, "1234567890abcdef")
    assert client_version(config) == {"version": "1234567890abcdef"}
    dist = config.repo_dir / "dist"
    (dist / "index.html").write_text('<script src="/assets/index-old.js"></script>')
    assert client_version(config) == {"version": None}
    (dist / "client-version.json").write_text('{"version":')
    assert client_version(config) == {"version": None}
    (dist / "client-version.json").write_text(json.dumps({"version": "invalid value"}))
    assert client_version(config) == {"version": None}
    (dist / "client-version.json").write_text(" " * 5000)
    assert client_version(config) == {"version": None}


async def test_client_generation_observes_completed_rebuild_without_server_restart(config):
    write_build(config, "1234567890abcdef")
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:4317") as client:
            await client.post("/api/auth/register", json={"username": "version-owner", "password": "test-password"})
            first = await client.get("/api/client-version")
            assert first.status_code == 200
            assert first.json() == {"version": "1234567890abcdef"}
            assert first.headers["cache-control"] == "no-store"
            write_build(config, "abcdef1234567890", "assets/index-new.js")
            assert (await client.get("/api/client-version")).json() == {"version": "abcdef1234567890"}
