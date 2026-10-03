"""Build an iPhone app and optionally install/launch it in a named Simulator.

An iOS .app is not a macOS application. Never launch it with Finder or `open`.
This command does not change xcode-select or install/upgrade developer tools.
"""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


class BuildError(RuntimeError):
    pass


def capture(command, *, env=None, timeout=30):
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise BuildError(f"Timed out after {timeout}s: {shlex.join(map(str, command))}. No developer tools were installed or changed.") from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise BuildError(f"Command failed ({result.returncode}): {shlex.join(map(str, command))}\n{detail}")
    return result.stdout.strip()


def developer_directory(value):
    path = Path(value).expanduser().resolve()
    if path.suffix == '.app':
        path /= 'Contents/Developer'
    return path


def toolchain(value=None):
    directory = developer_directory(value or os.environ.get('DEVELOPER_DIR') or capture(['xcode-select', '-p']))
    executable = directory / 'usr/bin/xcodebuild'
    if not executable.is_file():
        raise BuildError(f"Full Xcode is required; {directory} does not contain xcodebuild. Select an installed Xcode with --xcode /Applications/Xcode.app.")
    env = dict(os.environ, DEVELOPER_DIR=str(directory))
    version = capture([str(executable), '-version'], env=env)
    initialized = subprocess.run([str(executable), '-checkFirstLaunchStatus'], env=env, capture_output=True, timeout=30)
    if initialized.returncode:
        raise BuildError(f"{version.replace(chr(10), ', ')} at {directory} has not completed first-launch setup. Open that Xcode and complete its setup, or select another initialized installation with --xcode. This script will not run privileged setup or change xcode-select.")
    return env, executable, version


def inspect_bundle(bundle):
    path = Path(bundle).expanduser().resolve()
    info_path = path / 'Info.plist'
    if not info_path.is_file():
        info_path = path / 'Contents/Info.plist'
    try:
        info = plistlib.loads(info_path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as error:
        raise BuildError(f"Cannot read the app's Info.plist: {path}") from error
    platforms = info.get('CFBundleSupportedPlatforms', [])
    if 'iPhoneSimulator' in platforms:
        platform = 'iphonesimulator'
        launch = 'Install inside an iOS Simulator with --launch --simulator <UDID>. Do not double-click this app in Finder; macOS cannot launch an iOS Simulator app.'
    elif 'iPhoneOS' in platforms or info.get('LSRequiresIPhoneOS'):
        platform = 'iphoneos'
        launch = 'Install on a provisioned iPhone through Xcode. This app cannot launch directly on macOS or in an iOS Simulator.'
    elif 'MacOSX' in platforms or (path / 'Contents/MacOS').is_dir():
        platform = 'macos'
        launch = 'This is the macOS application. Use its macOS distribution receipt for compatibility and signing checks.'
    else:
        raise BuildError(f"Unrecognized application platform in {info_path}; do not assume an arm64 executable is a macOS app.")
    return {'bundle': str(path), 'platform': platform, 'bundle_identifier': info.get('CFBundleIdentifier'),
            'version': info.get('CFBundleShortVersionString'), 'build': info.get('CFBundleVersion'),
            'minimum_os': info.get('MinimumOSVersion') or info.get('LSMinimumSystemVersion'), 'launch_instructions': launch}


def simulator_device(inventory, identifier):
    devices = []
    for runtime, entries in inventory.get('devices', {}).items():
        if '.iOS-' not in runtime:
            continue
        devices.extend(dict(device, runtime=runtime) for device in entries if device.get('isAvailable'))
    matches = [device for device in devices if device.get('udid') == identifier]
    if len(matches) != 1:
        raise BuildError('Select an available iOS Simulator by its exact UDID; run this script with --list-simulators. Names can refer to multiple OS versions.')
    return matches[0]


def validated_test_origin(value):
    if value is None:
        return None
    url = urlsplit(value)
    try:
        port = url.port
    except ValueError as error:
        raise BuildError('Test origin has an invalid port.') from error
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost') or not port
            or url.username is not None or url.password is not None or url.path not in ('', '/')
            or url.query or url.fragment):
        raise BuildError('--test-origin must be http://127.0.0.1:<port> or http://localhost:<port> without credentials or a path.')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', action='store_true', help='Build for an actual iPhone; no automatic device installation')
    parser.add_argument('--team', help='Apple development team; only for --device, otherwise build unsigned')
    parser.add_argument('--configuration', choices=['Debug', 'Release'], default='Debug')
    parser.add_argument('--xcode', help='Xcode.app or Contents/Developer path; applies to this command only')
    parser.add_argument('--preflight', action='store_true', help='Check the selected Xcode and iOS SDK, without building')
    parser.add_argument('--list-simulators', action='store_true', help='List available iOS Simulators and exact UDIDs, without building')
    parser.add_argument('--simulator', help='Exact simulator UDID, avoiding ambiguous device names/OS versions')
    parser.add_argument('--launch', action='store_true', help='After building, boot/install/launch on --simulator; never uses Finder')
    parser.add_argument('--test-origin', help='Debug Simulator only: explicit loopback fixture endpoint')
    parser.add_argument('--inspect-bundle', type=Path, help='Diagnose an existing .app platform without running Xcode')
    args = parser.parse_args()
    if args.inspect_bundle:
        print(json.dumps(inspect_bundle(args.inspect_bundle), indent=2))
        return
    if args.team and (not args.device or not args.team.isascii() or not args.team.isalnum() or len(args.team) != 10):
        parser.error('--team requires --device and a 10-character ASCII Apple development team ID')
    if args.device and (args.simulator or args.launch or args.list_simulators or args.test_origin):
        parser.error('Simulator options cannot be combined with --device')
    if args.launch and not args.simulator:
        parser.error('--launch requires --simulator <UDID>; select the runtime explicitly')
    if args.test_origin and (not args.launch or args.configuration != 'Debug'):
        parser.error('--test-origin requires --launch and --configuration Debug')
    test_origin = validated_test_origin(args.test_origin)
    env, executable, version = toolchain(args.xcode)
    sdk = 'iphoneos' if args.device else 'iphonesimulator'
    sdk_version = capture([str(executable), '-sdk', sdk, '-version', 'SDKVersion'], env=env)
    print(f"Using {version.replace(chr(10), ', ')}; {sdk} SDK {sdk_version}.", flush=True)
    if args.preflight:
        print(f"Developer directory: {env['DEVELOPER_DIR']} (global xcode-select unchanged).")
        return
    device = None
    if args.list_simulators or args.simulator:
        inventory = json.loads(capture(['xcrun', 'simctl', 'list', 'devices', 'available', '--json'], env=env))
        if args.list_simulators:
            for runtime, entries in inventory.get('devices', {}).items():
                if '.iOS-' in runtime:
                    for item in entries:
                        if item.get('isAvailable'):
                            print(f"{item['udid']}  {item['name']}  {runtime.rsplit('.', 1)[-1]}  {item['state']}")
            return
        device = simulator_device(inventory, args.simulator)
        print(f"Selected {device['name']}: {device['runtime']}, {device['udid']}.", flush=True)
    destination = f"platform=iOS Simulator,id={device['udid']}" if device else ('generic/platform=iOS' if args.device else 'generic/platform=iOS Simulator')
    derived = ROOT / 'build' / 'ios' / ('device' if args.device else 'simulator')
    derived.mkdir(parents=True, exist_ok=True)
    # A failed rebuild must not leave an old success receipt looking current.
    receipt_path = derived / 'build-receipt.json'
    receipt_path.unlink(missing_ok=True)
    command = [str(executable), '-project', str(ROOT / 'mobile/ios/Athena.xcodeproj'), '-scheme', 'Athena',
               '-configuration', args.configuration, '-destination', destination, '-derivedDataPath', str(derived), 'build']
    command += [f'DEVELOPMENT_TEAM={args.team}', 'CODE_SIGN_STYLE=Automatic'] if args.team else ['CODE_SIGNING_ALLOWED=NO']
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    bundle = derived / 'Build' / 'Products' / f'{args.configuration}-{sdk}' / 'Athena.app'
    inspection = inspect_bundle(bundle)
    if inspection['platform'] != sdk:
        raise BuildError(f"Expected {sdk}, built {inspection['platform']}; refusing installation.")
    signed = False
    if args.team:
        capture(['codesign', '--verify', '--strict', str(bundle)], env=env)
        if not (bundle / 'embedded.mobileprovision').is_file():
            raise BuildError('Signed iPhone build has no provisioning profile; installation is not ready.')
        signed = True
    receipt = dict(inspection, configuration=args.configuration,
                   cloud_origin='https://chat.athena.featherhk.com', signed_for_device=signed,
                   physical_device_verified=False, simulator_launched=False,
                   source_revision=capture(['git', 'rev-parse', 'HEAD']), source_dirty=bool(capture(['git', 'status', '--porcelain'])),
                   xcode=version, developer_directory=env['DEVELOPER_DIR'], sdk_version=sdk_version)
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    if args.launch:
        if device['state'] != 'Booted':
            capture(['xcrun', 'simctl', 'boot', device['udid']], env=env)
        capture(['xcrun', 'simctl', 'bootstatus', device['udid'], '-b'], env=env, timeout=180)
        capture(['xcrun', 'simctl', 'install', device['udid'], str(bundle)], env=env, timeout=120)
        command = ['xcrun', 'simctl', 'launch', '--terminate-running-process', device['udid'], inspection['bundle_identifier']]
        if test_origin:
            command += ['--athena-test-origin', test_origin]
        print(capture(command, env=env, timeout=60))
        receipt.update(simulator_launched=True, simulator_udid=device['udid'], simulator_runtime=device['runtime'],
                       launch_origin=test_origin or receipt['cloud_origin'])
        receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    print(f"iPhone preview built: {bundle}")
    print(inspection['launch_instructions'])
    print('Build/install success does not verify real iPhone behavior or end-to-end product flows.')


if __name__ == '__main__':
    try:
        main()
    except (BuildError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f'Athena iOS build: {error}', file=sys.stderr)
        sys.exit(1)
