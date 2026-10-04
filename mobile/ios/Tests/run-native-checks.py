"""Build a separate iOS Simulator fixture app; no website or user account is accessed."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import plistlib
import runpy
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
BUILD = runpy.run_path(str(ROOT / 'scripts/build-ios.py'))

DOCUMENT = b'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<style>body{margin:20px;font:18px Georgia}textarea{width:100%;height:100px;box-sizing:border-box}</style></head>
<body><h1>Offline native acceptance</h1><label for="draft">Unsent draft</label><textarea id="draft"></textarea>
<a id="inline-note" href="/api/files/inline-note/content" target="_blank" rel="noopener noreferrer">Open saved notes</a>
<script>window.backgroundEvents=0;window.addEventListener('athena:background',()=>window.backgroundEvents++);</script></body></html>'''


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        request_receipt = {'method': 'GET', 'path': self.path}
        self.server.receipts.append(request_receipt)
        path = urlsplit(self.path).path
        if path == '/offline':
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        downloading = path in ['/file', '/missing-file']
        inline_note = path == '/api/files/inline-note/content'
        authenticated = f'athena_native_fixture={self.server.fixture_cookie}' in self.headers.get('Cookie', '')
        if inline_note:
            request_receipt['fixture_session_present'] = authenticated
        body = b'Synthetic saved notes\n' if inline_note else b'Athena synthetic file\n' if path == '/file' else DOCUMENT
        self.send_response((200 if authenticated else 401) if inline_note else 200 if path in ['/', '/file'] else 404)
        self.send_header('Content-Type', 'text/plain; charset=utf-8' if inline_note else 'application/octet-stream' if downloading else 'text/html; charset=utf-8')
        if inline_note:
            self.send_header('Content-Disposition', 'inline; filename="notes.txt"')
        if path == '/':
            self.send_header('Set-Cookie', f'athena_native_fixture={self.server.fixture_cookie}; HttpOnly; SameSite=Lax; Path=/')
        if downloading:
            self.send_header('Content-Disposition', 'attachment; filename="report.txt"')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.server.receipts.append({'method': self.command, 'path': self.path})
        self.send_error(405)

    do_PUT = do_PATCH = do_DELETE = do_POST


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xcode', required=True)
    parser.add_argument('--simulator', required=True, help='Exact UDID; a disposable Simulator is recommended')
    args = parser.parse_args()
    env, _, _ = BUILD['toolchain'](args.xcode)
    inventory = json.loads(BUILD['capture'](['xcrun', 'simctl', 'list', 'devices', 'available', '--json'], env=env))
    device = BUILD['simulator_device'](inventory, args.simulator)
    output = Path(tempfile.mkdtemp(prefix='athena-ios-native-checks-'))
    bundle = output / 'AthenaNativeChecks.app'
    bundle.mkdir()
    sdk = BUILD['capture'](['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-path'], env=env)
    sources = ROOT / 'mobile/ios'
    executable = bundle / 'AthenaNativeChecks'
    subprocess.run(['xcrun', '--sdk', 'iphonesimulator', 'swiftc', '-sdk', sdk,
                    '-target', 'arm64-apple-ios17.0-simulator', '-swift-version', '5', '-g',
                    '-D', 'DEBUG', '-D', 'ATHENA_NATIVE_CHECKS', '-o', str(executable),
                    str(sources / 'Athena/AthenaApp.swift'), str(sources / 'Athena/HostPolicy.swift'),
                    str(sources / 'Athena/DownloadQueue.swift'), str(sources / 'Tests/NativeChecks.swift')],
                   env=env, check=True, timeout=180)
    identifier = 'com.featherhk.athena.nativechecks'
    info = {'CFBundleExecutable': executable.name, 'CFBundleIdentifier': identifier,
            'CFBundleName': 'Athena Native Checks', 'CFBundlePackageType': 'APPL',
            'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0', 'MinimumOSVersion': '17.0',
            'CFBundleSupportedPlatforms': ['iPhoneSimulator'], 'LSRequiresIPhoneOS': True,
            'UIDeviceFamily': [1], 'UILaunchScreen': {},
            'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
            'UIApplicationSceneManifest': {'UIApplicationSupportsMultipleScenes': False, 'UISceneConfigurations': {}}}
    (bundle / 'Info.plist').write_bytes(plistlib.dumps(info))
    catalog = json.loads((sources / 'Athena/Localizable.xcstrings').read_text())
    for language in ['ar', 'en', 'es', 'fr', 'ja', 'ko', 'pt', 'zh-Hans', 'zh-Hant']:
        directory = bundle / f'{language}.lproj'
        directory.mkdir()
        strings = {key: entry['localizations'][language]['stringUnit']['value'] for key, entry in catalog['strings'].items()}
        (directory / 'Localizable.strings').write_bytes(plistlib.dumps(strings))
    BUILD['capture'](['codesign', '--force', '--sign', '-', str(bundle)], env=env)
    if device['state'] != 'Booted':
        BUILD['capture'](['xcrun', 'simctl', 'boot', args.simulator], env=env)
    BUILD['capture'](['xcrun', 'simctl', 'bootstatus', args.simulator, '-b'], env=env, timeout=180)
    BUILD['capture'](['xcrun', 'simctl', 'install', args.simulator, str(bundle)], env=env, timeout=120)
    with ThreadingHTTPServer(('127.0.0.1', 0), Fixture) as server:
        server.receipts = []
        server.fixture_cookie = secrets.token_hex(16)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f'http://127.0.0.1:{server.server_port}/'
        try:
            result = subprocess.run(['xcrun', 'simctl', 'launch', '--console', '--terminate-running-process', args.simulator, identifier,
                                     '--fixture-origin', origin], env=env, text=True, capture_output=True, timeout=90)
        finally:
            server.shutdown()
    (output / 'console.log').write_text(result.stdout + '\n' + result.stderr)
    markers = [line.split('ATHENA_NATIVE_CHECK_RESULT ', 1)[1] for line in result.stdout.splitlines() if 'ATHENA_NATIVE_CHECK_RESULT ' in line]
    if not markers:
        raise RuntimeError(f'No native acceptance receipt; inspect {output}/console.log')
    receipt = json.loads(markers[-1])
    if receipt.get('screenshots'):
        data_directory = Path(BUILD['capture'](['xcrun', 'simctl', 'get_app_container', args.simulator, identifier, 'data'], env=env))
        for name in receipt['screenshots']:
            if Path(name).name != name or not name.endswith('.png'):
                raise RuntimeError('Unexpected native screenshot path')
            shutil.copyfile(data_directory / 'tmp' / name, output / name)
    receipt['requests'] = server.receipts
    file_reads = [request for request in server.receipts if request['path'] == '/api/files/inline-note/content']
    if len(file_reads) != 1 or not file_reads[0].get('fixture_session_present'):
        receipt.update(ok=False, error='Inline attachment lost session cookies or downloaded more than once')
    if any(request['method'] != 'GET' for request in server.receipts):
        receipt.update(ok=False, error='Native recovery replayed a mutation')
    receipt.update(simulator_udid=args.simulator, simulator_runtime=device['runtime'], bundle=str(bundle))
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))
    print(f'Acceptance receipt: {output}/receipt.json')
    if not receipt['ok']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
