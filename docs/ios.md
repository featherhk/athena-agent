# iPhone preview

Athena for iPhone is a native UIKit/WKWebView host for `https://chat.athena.featherhk.com/`. Login and model entitlement belong to the hosted service. Conversations and tool execution remain on the server; this app does not embed Python, a model key, or a local shell. Installing an iOS client does not add a missing server capability.

The project targets **iOS 17 or newer**. That is an iPhone OS requirement, not a minimum macOS version. A successful build is not a real-device or end-to-end acceptance result. No iPhone installation, App Store distribution or TestFlight distribution has been verified yet.

## Which Athena app should open where?

| Output | Runs on | Correct entry point |
| --- | --- | --- |
| `build/ios/simulator/.../Athena.app` | The selected iOS Simulator | Install/launch with `simctl`, or use the build command below |
| `build/ios/device/.../Athena.app` | A provisioned iPhone | Xcode's device installation workflow |
| `build/macos/Athena.app` or the published `.dmg` | A supported Mac | Open the DMG, drag to Applications, open Athena |

The iOS Simulator output also contains an ARM64 executable on Apple Silicon. **It is still not a macOS application.** Double-clicking it in Finder can produce a message that Athena cannot run on this version of macOS. Updating macOS does not turn that simulator bundle into the desktop product.

To identify an existing bundle without launching it:

```sh
uv run python scripts/build-ios.py --inspect-bundle build/ios/simulator/Build/Products/Debug-iphonesimulator/Athena.app
```

The diagnostic reports the actual bundle platform, version, minimum OS and correct launch instructions. If a macOS bundle is the one that failed, inspect the macOS release's architecture, minimum OS and signing separately; do not assume that every compatibility dialog is an iOS mix-up.

## Select and check Xcode

Use an installed Xcode that supports the Mac and the intended iOS runtime. This script checks first-launch initialization **before** invoking the SDK or Simulator tools. It never runs privileged setup, installs runtimes, upgrades macOS or changes the global `xcode-select` setting.

```sh
uv run python scripts/build-ios.py --xcode /Applications/Xcode.app --preflight
```

If that installation has not completed setup, open that Xcode and finish its setup, or explicitly select another initialized installation. `--xcode` applies only to this command. Without it, the script respects `DEVELOPER_DIR`, then the current `xcode-select` selection.

On the development Mac checked on 2026-10-03, macOS was 15.4.1, `/Applications/Xcode.app` was 16.4 with unfinished first-launch setup, and `/Applications/Xcode-beta.app` was an initialized 16.1 installation with the iOS 18.1 SDK. The directory name does not describe the actual version; preflight prints the actual version. These are local observations, not required versions for all contributors.

## Build and launch in a Simulator

List the installed, available iOS Simulators:

```sh
uv run python scripts/build-ios.py --xcode /Applications/Xcode-beta.app --list-simulators
```

Use the exact UDID, since several Simulators can have the same phone name on different iOS versions:

```sh
uv run python scripts/build-ios.py --xcode /Applications/Xcode-beta.app --simulator <UDID> --launch
```

This builds for the selected device, boots it if necessary, installs the iOS app and launches the app inside that Simulator. Open the **Simulator application** supplied by the same Xcode to see its window; do not open the produced Athena bundle in Finder. Relaunching replaces the currently running Athena simulator process, while its installed application data remains.

A build without `--launch` creates an artifact without installing or launching it:

```sh
uv run python scripts/build-ios.py --xcode /Applications/Xcode-beta.app
```

For a disposable local fixture, a Debug Simulator build can explicitly select a loopback endpoint:

```sh
uv run python scripts/build-ios.py --xcode /Applications/Xcode-beta.app --simulator <UDID> --launch --test-origin http://127.0.0.1:4331
```

The command rejects credentials, non-loopback addresses, paths, query strings and fragments. Release builds and physical-iPhone builds do not accept this override. The fixture endpoint must already be running. This is not a route for shipping configurable provider keys or production credentials.

Each successful build writes an ignored `build/ios/<platform>/build-receipt.json` with actual platform, app version/build, minimum OS, source revision, dirty state, selected Xcode/SDK and whether simulator launch completed. A new build removes the old receipt first, so a failed rebuild cannot leave an old success receipt appearing current. A launch receipt is **not** an end-to-end test receipt.

## Physical iPhone

An unsigned device compilation can detect device-only compiler problems:

```sh
uv run python scripts/build-ios.py --device --xcode /Applications/Xcode.app
```

Installing on a real iPhone requires development signing/provisioning and device trust. Open `mobile/ios/Athena.xcodeproj` in a compatible Xcode, choose the connected iPhone and a development team, then run. With an already configured development team, the build script accepts `--device --team <TEAMID>` and verifies the resulting signature and embedded provisioning profile. It does not enroll a device, accept account agreements, store Apple credentials or claim installation succeeded.

Keep device provisioning files and Xcode user state out of Git. App Store/TestFlight distribution and a general iOS download are separate future work; the macOS DMG cannot install an iPhone app.

## Current native behavior and acceptance limits

- One persistent trusted web origin owns login and native microphone permission. Generated subframes do not receive microphone/camera permission; camera capture is not enabled.
- The web surface respects the iPhone safe area and keyboard guide. Supported native strings use the same nine language catalogs as the product, with English fallback.
- Explicit external links open in an in-app Safari view; they do not gain access to the host's native permissions.
- Downloads from the trusted origin use native sharing/saving. Cross-origin download redirects and non-2xx responses are rejected; an expired login or missing attachment is never intentionally shared as a downloaded file. Concurrently completed files wait for the active share sheet or other native modal to close; background downloads wait until the app becomes active. Failed downloads also wait for a foreground presentation slot instead of silently disappearing behind a modal. Pending failures are combined into one notice; acknowledging it continues to the queued successful files without replaying downloads or removing another transfer's file.
- A refused navigation or connection failure before a new page commits keeps the existing usable conversation, draft and scroll position. A native notice explains the failure. First-load failures and page-process termination still show the independent native retry view.
- A two-finger long press offers page recovery even when page JavaScript fails. The native VoiceOver escape action (two-finger scrub) and hardware-keyboard Command-R offer the same confirmation, without adding a permanent toolbar. Confirmed recovery opens the trusted origin with `?recovery=1`; the matching web client uses its default interface without registered custom surfaces. It warns that an unsent draft can be lost, preserves chats/files, and never replays a model or tool request. A subsequent native Retry stays on the recovery URL, so an interrupted recovery does not reactivate the broken surface. Ordinary network retry before entering recovery keeps the ordinary entry point.
- Voice is foreground-only in this preview. Entering the background releases microphone/playback and notifies the web client without canceling a server task. Real-device call audio, interruption, headphones and Bluetooth remain to be accepted.

The web client's successful Reset can exit safe mode in its current page after the server confirms custom extensions were disabled. The native host retains its safe retry address until the app is relaunched; a subsequent native Retry uses that address again. This avoids giving generated page code a native recovery override.

Required real-device checks include login/expiry, send/stop/reconnect, long server tools across background/foreground, keyboard and rotation, file selection/download, microphone grant/deny, call interruption, process termination/recovery and VoiceOver. The simulator is useful for development but does not substitute for those checks.

## Regression checks

```sh
uv run pytest tests/test_ios_build.py -q
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer xcrun swiftc mobile/ios/Athena/HostPolicy.swift mobile/ios/Tests/HostPolicyTests.swift -o /private/tmp/athena-ios-policy-check
/private/tmp/athena-ios-policy-check --athena-test-origin http://127.0.0.1:4331
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer xcrun swiftc mobile/ios/Athena/DownloadQueue.swift mobile/ios/Tests/DownloadQueueTests.swift -o /private/tmp/athena-ios-download-check
/private/tmp/athena-ios-download-check
uv run python mobile/ios/Tests/run-native-checks.py --xcode /Applications/Xcode-beta.app --simulator <UDID>
```

The Python checks cover platform mix-ups, exact Simulator selection, safe fixture URLs and early refusal of uninitialized Xcode without global configuration changes. The Swift checks cover exact origin/native capability boundaries, safe recovery URL construction, rejection of error responses as files, malformed external links, refusal of a simulator override in a non-simulator executable, deferred/concurrent download batches, and failures arriving behind a share sheet or another failure notice. They verify that acknowledging failures preserves pending successful files and does not replay already acknowledged notices.

The separate native-check application runs the actual `AthenaController` and WebKit inside the selected Simulator. Its temporary loopback HTTP fixture serves only synthetic documents, attachments, 404 responses and deliberate disconnects; it never serves Athena or accesses a user account. It verifies safe-area/keyboard-guide constraints, draft retention across rejected navigations, native download failure/sharing, temporary-file cleanup, foreground voice shutdown notification without reload, accessible native recovery, the safe GET request, and independent recovery after a simulated content-process termination callback. The script emits a JSON receipt and request log outside the checkout. It deliberately invokes the accessibility/scene/share-completion callbacks directly: it does **not** prove physical VoiceOver gestures, a raised software keyboard, real OS suspension, real process death or a third-party share extension. Those interactions still need device acceptance. Run the repository's full `npm run check` before publishing source changes.

Verified on 2026-10-03: the 17 Python checks and both standalone Swift checks passed; the Debug Simulator target and unsigned Release iPhone target compiled successfully using Xcode 16.1 / SDK 18.1. The uninitialized Xcode 16.4 path stopped with guidance before launching SDK tools, and the global developer-directory selection stayed unchanged. No simulator UI, physical-device, signing or upload acceptance is implied by these checks.

Rechecked on 2026-10-04 for download failure recovery: the 17 Python checks and the expanded Swift download queue checks passed, and the Debug Simulator target compiled with Xcode 16.1 / SDK 18.1. A paired iPhone 16 Pro Max was available, but there were no valid code-signing identities, so no physical-device installation was attempted. Selecting the default Xcode 16.4 Simulator through the UI reached its additional-components setup prompt; no components were installed. The native share-sheet/failure-alert interaction has not been visually accepted, and this build receipt deliberately records `simulator_launched: false`.

Later on 2026-10-04, the independent native-check app passed **10** UIKit/WebKit checks on an iPhone 16 Pro Max / iOS 18.1 Simulator, including an actual successful file download followed by native share-sheet presentation and cleanup, and an actual 404 download that produced an error instead of a shareable file. The Debug Simulator and unsigned Release iPhone products compiled without compiler warnings using Xcode 16.1 / SDK 18.1. The expanded host-policy tests, download-queue tests and 17 Python build checks passed. This acceptance used a separate test bundle and synthetic HTTP fixture; the production iPhone app was not installed on the paired physical device. No valid signing identities were present. Real-device gesture discovery, software-keyboard interaction, audio interruptions and account-backed end-to-end tasks remain outstanding.
