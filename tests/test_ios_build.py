"""Product-level platform/build safeguards; no Xcode or simulator needed."""
from pathlib import Path
import plistlib
import runpy
import subprocess
from unittest.mock import patch

import pytest

BUILD = runpy.run_path(str(Path(__file__).parents[1] / 'scripts/build-ios.py'))
BuildError = BUILD['BuildError']


def test_simulator_bundle_diagnosis_does_not_mistake_arm64_for_macos(tmp_path):
    bundle = tmp_path / 'Athena.app'
    bundle.mkdir()
    (bundle / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleSupportedPlatforms': ['iPhoneSimulator'], 'MinimumOSVersion': '17.0',
        'CFBundleIdentifier': 'com.featherhk.athena.ios', 'CFBundleShortVersionString': '0.1.0',
    }))
    result = BUILD['inspect_bundle'](bundle)
    assert result['platform'] == 'iphonesimulator'
    assert result['minimum_os'] == '17.0'
    assert 'Do not double-click' in result['launch_instructions']
    assert 'macOS' in result['launch_instructions']


def test_iphone_bundle_cannot_be_distributed_as_mac_or_simulator(tmp_path):
    bundle = tmp_path / 'Athena.app'
    bundle.mkdir()
    (bundle / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleSupportedPlatforms': ['iPhoneOS'], 'LSRequiresIPhoneOS': True}))
    result = BUILD['inspect_bundle'](bundle)
    assert result['platform'] == 'iphoneos'
    assert 'cannot launch directly on macOS or in an iOS Simulator' in result['launch_instructions']


def test_macos_bundle_is_diagnosed_separately(tmp_path):
    bundle = tmp_path / 'Athena.app'
    (bundle / 'Contents/MacOS').mkdir(parents=True)
    (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({'LSMinimumSystemVersion': '13.0'}))
    assert BUILD['inspect_bundle'](bundle)['platform'] == 'macos'


def test_unknown_bundle_is_not_opened_as_mac(tmp_path):
    with pytest.raises(BuildError, match='Cannot read'):
        BUILD['inspect_bundle'](tmp_path)
    (tmp_path / 'Info.plist').write_bytes(plistlib.dumps({}))
    with pytest.raises(BuildError, match='Unrecognized'):
        BUILD['inspect_bundle'](tmp_path)


def test_simulator_selection_uses_exact_available_ios_udid():
    inventory = {'devices': {
        'com.apple.CoreSimulator.SimRuntime.iOS-18-1': [
            {'name': 'iPhone 16 Pro Max', 'udid': 'first', 'isAvailable': True},
            {'name': 'iPhone 16 Pro Max', 'udid': 'broken', 'isAvailable': False}],
        'com.apple.CoreSimulator.SimRuntime.iOS-18-0': [{'name': 'iPhone 16 Pro Max', 'udid': 'older', 'isAvailable': True}],
        'com.apple.CoreSimulator.SimRuntime.tvOS-18-1': [{'name': 'Apple TV', 'udid': 'tv', 'isAvailable': True}],
    }}
    assert BUILD['simulator_device'](inventory, 'first')['runtime'].endswith('iOS-18-1')
    for ambiguous in ['iPhone 16 Pro Max', 'booted', 'broken', 'tv']:
        with pytest.raises(BuildError):
            BUILD['simulator_device'](inventory, ambiguous)


@pytest.mark.parametrize('origin', [
    'https://127.0.0.1:4317', 'http://localhost', 'http://evil.example:4317',
    'http://user:password@127.0.0.1:4317', 'http://127.0.0.1:4317/anything',
    'http://127.0.0.1:4317/?secret=value', 'http://127.0.0.1:4317/#fragment',
    'http://127.0.0.1:0', 'http://localhost:99999',
])
def test_fixture_override_rejects_external_or_secret_bearing_origin(origin):
    with pytest.raises(BuildError):
        BUILD['validated_test_origin'](origin)


def test_fixture_override_is_explicit_loopback_only():
    assert BUILD['validated_test_origin'](None) is None
    assert BUILD['validated_test_origin']('http://127.0.0.1:4331/') == 'http://127.0.0.1:4331/'


def test_uninitialized_xcode_stops_before_xcrun_or_privileged_setup(tmp_path):
    developer = tmp_path / 'Xcode.app/Contents/Developer'
    (developer / 'usr/bin').mkdir(parents=True)
    executable = developer / 'usr/bin/xcodebuild'
    executable.touch()
    outputs = [subprocess.CompletedProcess([], 0, 'Xcode 16.4\nBuild version 16F6', ''), subprocess.CompletedProcess([], 69, '', '')]
    with patch.object(subprocess, 'run', side_effect=outputs) as run:
        with pytest.raises(BuildError, match='has not completed first-launch setup'):
            BUILD['toolchain'](str(developer))
    assert len(run.call_args_list) == 2
    assert run.call_args_list[1].args[0][-1] == '-checkFirstLaunchStatus'
    assert all('xcode-select' not in str(call) and 'runFirstLaunch' not in str(call) for call in run.call_args_list)


def test_specific_xcode_does_not_mutate_global_environment(tmp_path):
    import os
    developer = tmp_path / 'Xcode.app/Contents/Developer'
    (developer / 'usr/bin').mkdir(parents=True)
    (developer / 'usr/bin/xcodebuild').touch()
    outputs = [subprocess.CompletedProcess([], 0, 'Xcode 16.1', ''), subprocess.CompletedProcess([], 0, '', '')]
    before = dict(os.environ)
    with patch.object(subprocess, 'run', side_effect=outputs):
        env, executable, _ = BUILD['toolchain'](str(tmp_path / 'Xcode.app'))
    assert env['DEVELOPER_DIR'] == str(developer)
    assert str(executable).startswith(str(developer))
    assert dict(os.environ) == before
