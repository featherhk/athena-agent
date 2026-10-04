"""Exercise the real native recovery menu in a separate fixture app, without a backend."""
import argparse
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import plistlib
import runpy
import subprocess
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[3]
BUILD = runpy.run_path(str(ROOT / 'scripts/build-ios.py'))
Fixture = runpy.run_path(str(ROOT / 'mobile/ios/Tests/run-native-checks.py'))['Fixture']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xcode', required=True)
    args = parser.parse_args()
    env, _, _ = BUILD['toolchain'](args.xcode)
    output = Path(tempfile.mkdtemp(prefix='athena-macos-native-recovery-'))
    bundle = output / 'AthenaNativeRecoveryChecks.app'
    resources = bundle / 'Contents/Resources'
    resources.mkdir(parents=True)
    executable = bundle / 'Contents/MacOS/AthenaNativeRecoveryChecks'
    executable.parent.mkdir()
    source = ROOT / 'desktop/macos'
    subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-target', 'arm64-apple-macos13.0', '-g',
                    '-D', 'ATHENA_NATIVE_CHECKS', '-framework', 'AppKit', '-framework', 'WebKit',
                    str(source / 'Athena.swift'), str(source / 'UpdateCheck.swift'), str(source / 'WindowRecovery.swift'),
                    str(source / 'Tests/NativeRecoveryChecks.swift'), '-o', str(executable)], env=env, check=True, timeout=180)
    info = {'CFBundleExecutable': executable.name, 'CFBundleIdentifier': 'com.featherhk.athena.macos-nativechecks',
            'CFBundleName': 'Athena Native Recovery Checks', 'CFBundlePackageType': 'APPL',
            'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0', 'LSMinimumSystemVersion': '13.0'}
    (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    for language, strings in json.loads((source / 'locales.json').read_text()).items():
        directory = resources / f'{language}.lproj'
        directory.mkdir()
        (directory / 'Localizable.strings').write_bytes(plistlib.dumps(strings))
    BUILD['capture'](['codesign', '--force', '--sign', '-', str(bundle)], env=env)
    with ThreadingHTTPServer(('127.0.0.1', 0), Fixture) as server:
        server.receipts = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            result = subprocess.run([str(executable), '--fixture-origin', f'http://127.0.0.1:{server.server_port}/'],
                                    env=env, text=True, capture_output=True, timeout=90)
        finally:
            server.shutdown()
    (output / 'console.log').write_text(result.stdout + '\n' + result.stderr)
    markers = [line.split('ATHENA_NATIVE_CHECK_RESULT ', 1)[1] for line in result.stdout.splitlines() if 'ATHENA_NATIVE_CHECK_RESULT ' in line]
    if not markers:
        raise RuntimeError(f'No native recovery receipt; inspect {output}/console.log')
    receipt = json.loads(markers[-1])
    receipt.update(bundle=str(bundle), requests=server.receipts)
    if any(request['method'] != 'GET' for request in server.receipts):
        receipt.update(ok=False, error='Native recovery replayed a mutation')
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))
    print(f'Acceptance receipt: {output}/receipt.json')
    if not receipt['ok']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
