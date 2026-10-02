"""The native wrapper and embedded interpreter are part of the immutable core."""
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest
from athena.config import Config
from athena.protection import protected_command


def test_core_boundary_detects_complete_application_bundle(tmp_path):
    bundle = tmp_path / "Athena.app"
    repo = bundle / "Contents/Resources/app"
    repo.mkdir(parents=True)
    (bundle / "Contents/Info.plist").write_text("plist")
    config = Config(data_dir=tmp_path / "private", repo_dir=repo)
    assert config.core_dir == bundle
    assert Config(data_dir=tmp_path / "private", repo_dir=tmp_path / "checkout").core_dir == tmp_path / "checkout"


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sandbox-exec"), reason="macOS Seatbelt integration")
def test_agent_cannot_modify_native_shell_or_bundled_python(tmp_path):
    bundle = tmp_path / "Athena.app"
    repo = bundle / "Contents/Resources/app"
    repo.mkdir(parents=True)
    (bundle / "Contents/Info.plist").write_text("plist")
    native = bundle / "Contents/MacOS/Athena"
    python = bundle / "Contents/Resources/python/bin/python3.14t"
    for file in (native, python):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("original")
    config = Config(data_dir=tmp_path / "private", repo_dir=repo)
    config.prepare()
    workspace = config.data_dir / "files"
    for file in (native, python):
        command = f"printf changed > {shlex.quote(str(file))}"
        result = subprocess.run(protected_command(command, config, [workspace], [workspace]), capture_output=True)
        assert result.returncode != 0
        assert file.read_text() == "original"
    allowed = workspace / "result.txt"
    result = subprocess.run(protected_command(f"printf allowed > {shlex.quote(str(allowed))}", config, [workspace], [workspace]), capture_output=True)
    assert result.returncode == 0, result.stderr
    assert allowed.read_text() == "allowed"


def test_cloud_bundle_configuration_is_https_only(tmp_path, monkeypatch):
    import json
    import runpy
    root = Path(__file__).resolve().parents[1]
    validate = runpy.run_path(str(root / "scripts/build-macos.py"))["validated_cloud_url"]
    configure = runpy.run_path(str(root / "desktop/macos/launcher.py"))["configure_bundle"]
    import os
    monkeypatch.setenv("ATHENA_CLOUD_URL", "https://untrusted.invalid/v1")
    path = tmp_path / "bundle-config.json"
    valid = "https://app.featherhk.com/v1"
    assert validate(valid + "/") == valid
    path.write_text(json.dumps({"version": 1, "cloud_url": valid}))
    configure(tmp_path)
    assert os.environ["ATHENA_CLOUD_URL"] == valid
    for value in ("http://insecure.example", "https://user:secret@example.com", "https://example.com?key=secret", "https://example.com#key", "https:///v1", "https://example.com /v1"):
        with pytest.raises(ValueError):
            validate(value)
        path.write_text(json.dumps({"version": 1, "cloud_url": value}))
        with pytest.raises(ValueError):
            configure(tmp_path)
    # Malformed cloud config must fail instead of silently enabling local registration.
    path.write_text('{"cloud_url": ""}')
    with pytest.raises(ValueError):
        configure(tmp_path)


def test_bundle_browser_payload_contains_only_native_runtime_and_license(tmp_path):
    import runpy
    import platform
    root = Path(__file__).resolve().parents[1]
    copy = runpy.run_path(str(root / 'scripts/build-macos.py'))['copy_browser_runtime']
    copy(tmp_path, platform.machine())
    payload = tmp_path / 'app/browser-runtime'
    assert {path.name for path in payload.iterdir()} == {'agent-browser', 'LICENSE-agent-browser', 'version.txt'}
    assert (payload / 'version.txt').read_text().strip() == '0.38.2'
    assert (payload / 'agent-browser').stat().st_size > 1000000
    assert 'Apache License' in (payload / 'LICENSE-agent-browser').read_text()
