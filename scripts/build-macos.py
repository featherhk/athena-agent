#!/usr/bin/env python3
"""Build an allowlisted, self-contained macOS preview. No installed Python/uv needed by users."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "desktop" / "macos"


def run(*args, **kwargs):
    print("+", " ".join(str(arg) for arg in args), flush=True)
    return subprocess.run([str(arg) for arg in args], check=True, cwd=ROOT, **kwargs)


def output(*args):
    return subprocess.check_output([str(arg) for arg in args], cwd=ROOT, text=True).strip()


def clean_copy(source, target):
    shutil.copytree(source, target, symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", ".env", ".env.*"))


def validated_cloud_url(value):
    if not value:
        return ""
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or any(character.isspace() for character in value)):
        raise ValueError("Cloud URL must be an HTTPS endpoint without credentials, query or fragment")
    return value.rstrip("/")


def validated_build_number(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 9 or not value.isascii() or not value.isdecimal() or not 0 < int(value) <= 999999999:
        raise ValueError("Build number must be a positive integer of at most nine digits")
    return str(int(value))


def copy_browser_runtime(resources, arch):
    """Only the pinned native executable/license, never node_modules or profiles."""
    package = ROOT / 'node_modules' / 'agent-browser'
    expected = json.loads((ROOT / 'package.json').read_text())['dependencies']['agent-browser']
    if json.loads((package / 'package.json').read_text())['version'] != expected:
        raise RuntimeError('Install the locked browser executor with npm ci before packaging')
    suffix = {'arm64': 'arm64', 'x86_64': 'x64'}[arch]
    target = resources / 'app' / 'browser-runtime'
    target.mkdir(parents=True)
    shutil.copy2(package / 'bin' / f'agent-browser-darwin-{suffix}', target / 'agent-browser')
    (target / 'agent-browser').chmod(0o755)
    shutil.copy2(package / 'LICENSE', target / 'LICENSE-agent-browser')
    (target / 'version.txt').write_text(expected + '\n')


def prepare_web_bundle(skip_web_build):
    if not skip_web_build:
        run("npm", "run", "build")
    elif not (ROOT / "dist" / "client-version.json").is_file():
        raise RuntimeError("Build the web client first")
    else:
        notice = ROOT / "dist" / "THIRD_PARTY_NOTICES.txt"
        expected = "Package-lock SHA-256: " + hashlib.sha256((ROOT / "package-lock.json").read_bytes()).hexdigest()
        if not notice.is_file() or expected not in notice.read_text().splitlines()[:3]:
            raise RuntimeError("Rebuild the web client: reused assets have no matching dependency notice provenance")
        # Reused web assets must still carry fresh notices for the locked runtime.
        run("node", ROOT / "scripts" / "generate-third-party-notices.mjs")
    if not (ROOT / "dist" / "client-version.json").is_file():
        raise RuntimeError("Build the web client first")
    if not (ROOT / "dist" / "THIRD_PARTY_NOTICES.txt").is_file():
        raise RuntimeError("Build is missing production dependency notices")


def verify_payload(app):
    resources = app / "Contents" / "Resources"
    for path in resources.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(app.resolve()):
            raise RuntimeError(f"Nonportable external symlink: {path}")
        if path.is_file() and (path.suffix in {".sqlite3", ".sqlite", ".db", ".key", ".p12", ".jsonl"}
                               or path.name in {".env", "auth.json", "credentials.json", "secrets.json"}):
            raise RuntimeError(f"Private runtime file in bundle: {path}")
        if path.is_file() and path.suffix == ".pem":
            text = path.read_text()
            if "PRIVATE KEY" in text or "-----BEGIN CERTIFICATE-----" not in text:
                raise RuntimeError(f"Unexpected PEM material in bundle: {path}")
    # Verify dependency imports with the isolated, relocated runtime.
    python = resources / "python" / "bin" / "python3.14t"
    check = "import sys;sys.path.insert(0,sys.argv[1]);from athena.doctor import main;main()"
    run(python, "-I", "-B", "-c", check, resources / "app" / "backend")
    run(resources / 'app' / 'browser-runtime' / 'agent-browser', '--version')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-web-build", action="store_true")
    parser.add_argument("--cloud-url", default="", help="Public HTTPS account/model gateway base URL; never a provider key")
    parser.add_argument("--identity", help="Developer ID Application identity; omit for an ad-hoc preview")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "macos")
    args = parser.parse_args()
    try:
        cloud_url = validated_cloud_url(args.cloud_url)
        build_number = validated_build_number(os.environ.get("ATHENA_BUILD_NUMBER", "1"))
    except ValueError as error:
        parser.error(str(error))
    if sys.platform != "darwin":
        parser.error("Build on macOS with Xcode command line tools and uv installed")
    try:
        prepare_web_bundle(args.skip_web_build)
    except RuntimeError as error:
        parser.error(str(error))
    base_python = Path(output("uv", "python", "find", "--managed-python", "3.14t")).resolve()
    base = base_python.parent.parent
    version = json.loads((ROOT / "package.json").read_text())["version"]
    arch = platform.machine()
    dest = args.output.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    # Build outside potentially synced project folders; sync software can add Finder metadata while signing.
    with tempfile.TemporaryDirectory(prefix="athena-macos-build-") as work:
        stage = Path(work)
        app = stage / "Athena.app"
        contents = app / "Contents"
        resources = contents / "Resources"
        (contents / "MacOS").mkdir(parents=True)
        resources.mkdir()
        clean_copy(base, resources / "python")
        # The uv runtime ships without project dependencies. Remove any locally-added site packages.
        lib = resources / "python" / "lib" / "python3.14t"
        site = lib / "site-packages"
        if site.exists(): shutil.rmtree(site)
        site.mkdir()
        requirements = stage / "requirements.txt"
        run("uv", "export", "--locked", "--no-dev", "--no-emit-project", "--no-annotate", "--no-header", "-o", requirements, stdout=subprocess.DEVNULL)
        run("uv", "pip", "install", "--python", resources / "python" / "bin" / "python3.14t",
            "--target", site, "--require-hashes", "-r", requirements)
        (resources / "app" / "backend").mkdir(parents=True)
        clean_copy(ROOT / "backend" / "athena", resources / "app" / "backend" / "athena")
        clean_copy(ROOT / "dist", resources / "app" / "dist")
        copy_browser_runtime(resources, arch)
        shutil.copy2(SOURCE / "launcher.py", resources / "launcher.py")
        (resources / "bundle-config.json").write_text(json.dumps({"version": 1, "cloud_url": cloud_url}, indent=2) + "\n")
        shutil.copy2(ROOT / "LICENSE", resources / "LICENSE")
        shutil.copy2(ROOT / "NOTICE", resources / "NOTICE")
        clean_copy(ROOT / "LICENSES", resources / "LICENSES")
        shutil.copy2(requirements, resources / "requirements.txt")
        with (SOURCE / "Info.plist").open("rb") as handle: info = plistlib.load(handle)
        info["CFBundleShortVersionString"] = version
        info["CFBundleVersion"] = build_number
        info["AthenaSourceRevision"] = output("git", "rev-parse", "HEAD")
        info["AthenaSourceDirty"] = bool(output("git", "status", "--porcelain"))
        locales = json.loads((SOURCE / "locales.json").read_text())
        info["CFBundleLocalizations"] = sorted(locales)
        with (contents / "Info.plist").open("wb") as handle: plistlib.dump(info, handle)
        for locale, strings in locales.items():
            folder = resources / f"{locale}.lproj"
            folder.mkdir()
            quote = lambda text: json.dumps(text, ensure_ascii=False)
            (folder / "Localizable.strings").write_text("\n".join(f"{quote(key)} = {quote(value)};" for key, value in strings.items()))
            (folder / "InfoPlist.strings").write_text(f'"NSMicrophoneUsageDescription" = {quote(strings["microphone"])};\n')
        iconset = stage / "Athena.iconset"
        cache = stage / "swift-cache"
        cache.mkdir()
        swift_env = {**os.environ, "CLANG_MODULE_CACHE_PATH": str(cache), "SWIFT_MODULECACHE_PATH": str(cache)}
        run("xcrun", "swift", "-module-cache-path", cache, SOURCE / "Icon.swift", iconset, env=swift_env)
        run("iconutil", "-c", "icns", iconset, "-o", resources / "Athena.icns")
        run("xcrun", "swiftc", "-O", "-parse-as-library", "-target", f"{arch}-apple-macos13.0", "-module-cache-path", cache,
            "-framework", "AppKit", "-framework", "WebKit", SOURCE / "Athena.swift", SOURCE / "UpdateCheck.swift", SOURCE / "WindowRecovery.swift", SOURCE / "DesktopCompanion.swift", SOURCE / "DesktopSceneHost.swift", SOURCE / "DesktopHitRegions.swift", SOURCE / "ComputerControl.swift", "-o", contents / "MacOS" / "Athena", env=swift_env)
        verify_payload(app)
        # copy2 may preserve Finder metadata from the developer machine; none belongs in a signed bundle.
        run("xattr", "-cr", app)
        # Every nested Mach-O must be signed before the outer bundle; --deep signing hides errors.
        identity = args.identity or "-"
        sign = ["codesign", "--force", "--sign", identity]
        if args.identity: sign += ["--options", "runtime", "--timestamp"]
        for path in sorted(resources.rglob("*"), key=lambda path: len(path.parts), reverse=True):
            if path.is_symlink() or not path.is_file(): continue
            with path.open("rb") as handle: magic = handle.read(4)
            if magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe"):
                run(*sign, path)
        run("xattr", "-cr", app)
        run(*sign, app)
        run("codesign", "--verify", "--deep", "--strict", app)
        image_root = stage / "image"
        image_root.mkdir()
        # Build the image before exposing the bundle to a synced output folder.
        # File Provider may attach FinderInfo as soon as an .app enters it.
        image_app = image_root / "Athena.app"
        run("ditto", "--norsrc", "--noextattr", app, image_app)
        run("codesign", "--verify", "--deep", "--strict", image_app)
        (image_root / "Applications").symlink_to("/Applications")
        label = f"Athena-{version}-macos-{arch}" + ("" if args.identity else "-preview")
        dmg = dest / f"{label}.dmg"
        if dmg.exists(): dmg.unlink()
        run("hdiutil", "create", "-volname", "Athena", "-srcfolder", image_root, "-ov", "-format", "UDZO", dmg)
        if args.identity: run(*sign, dmg)
        mount = stage / "mounted"
        mount.mkdir()
        run("hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, dmg)
        try:
            run("codesign", "--verify", "--deep", "--strict", mount / "Athena.app")
            if (mount / "Applications").readlink() != Path("/Applications"):
                raise RuntimeError("Disk image has no Applications installation link")
        finally:
            run("hdiutil", "detach", mount)
        final_app = dest / "Athena.app"
        if final_app.exists(): shutil.rmtree(final_app)
        shutil.move(app, final_app)
        checksum = hashlib.file_digest(dmg.open("rb"), "sha256").hexdigest()
        (dest / f"{label}.sha256").write_text(f"{checksum}  {dmg.name}\n")
        (dest / f"{label}.json").write_text(json.dumps({"version": version, "build": build_number, "architecture": arch,
            "minimum_macos": info["LSMinimumSystemVersion"], "sha256": checksum, "filename": dmg.name,
            "bytes": dmg.stat().st_size, "signed": bool(args.identity), "notarized": False,
            "channel": "preview", "cloud_url": cloud_url, "git_revision": info["AthenaSourceRevision"],
            "git_dirty": info["AthenaSourceDirty"]}, indent=2) + "\n")
        print(f"Built {dmg}\nThis is a preview; public release requires notarization and hosted-account verification.")


if __name__ == "__main__":
    main()
