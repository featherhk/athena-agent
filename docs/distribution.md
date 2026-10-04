# Distribution

Athena’s desktop application is a native macOS window over the same web client and local Python service. The operator’s model key must never be shipped to a customer. The desktop sidecar and hosted web execution are different trust boundaries; a successful DMG build does not by itself make the cloud service safe to publish.

## Current macOS preview

See the [build 13 release evidence](releases/2026-10-05-build13.md) for the
current source, hosted verification, package acceptance and explicit download
publication status.

Build on macOS with Xcode command line tools, Node, and uv installed:

```sh
uv sync --python 3.14t
npm ci
: "${ATHENA_BUILD_NUMBER:?Set the next unpublished build number before packaging}"
uv run python scripts/build-macos.py --cloud-url https://chat.athena.featherhk.com/v1
```

The optional `--cloud-url` selects the public HTTPS account/model gateway and writes it into `Contents/Resources/bundle-config.json` before signing. It accepts no credentials, query string or fragment. The launcher reads only this non-secret bundle configuration; it never falls back to local authentication if the selected cloud service is unavailable. Omitting the option creates an isolated local development preview without provider credentials. The DNS and TLS endpoint must be verified before distributing the cloud build.

The script uses the locked production Python dependencies, the installed uv-managed Python 3.14T distribution, and a fresh React build. It constructs an allowlisted bundle: application source, skill packages, web assets, Python runtime, dependency license metadata, the native launcher, the pinned native browser executable and its license, and localized shell strings. It does not copy the checkout wholesale, `.env`, database, user files, browser profiles or operator credentials. The bundled Python imports the real dependency stack and must report that the GIL remains disabled. No Python, Node, uv or developer tools are required on the recipient’s Mac. The optional managed browser currently uses installed Google Chrome/Chromium; automatic browser installation is not yet included.

Outputs are ignored under `build/macos/`: `Athena.app`, an architecture-specific `.dmg`, a SHA-256 checksum, and a release manifest. An Apple Silicon build is not an Intel build. Build and test on an Intel Mac before advertising Intel support. macOS 13 is the shell deployment target; only versions actually tested should be advertised.

Set `ATHENA_BUILD_NUMBER` to the next positive build number for each published preview; the marketing version alone can remain unchanged. The same build number is included in `CFBundleVersion` and the JSON release manifest. The bundle also records its source revision and dirty state. When publishing, propagate the full manifest, including `build`, into the `macos` object at the distribution site's `/release.json`; never point that file to an unverified artifact.

The disk image is assembled in temporary staging before the `.app` enters the output directory. A synced workspace can attach Finder metadata immediately on arrival and invalidate strict signature checks if that copy is used as the image source. The build strips copy metadata, verifies the staged app, then mounts the finished DMG read-only and verifies the contained app and Applications link before emitting its manifest. Test the downloaded image itself, rather than relying on the loose `.app` in a synced output directory.

To exercise the shipped browser executable against disposable form, download and login fixtures, mount that image and run `ATHENA_TEST_BROWSER=1 ATHENA_TEST_BROWSER_ROOT='/path/to/mounted/Athena.app/Contents/Resources/app' uv run pytest tests/test_browser_native.py -q`. This check uses an isolated browser profile and no model requests or personal cookies.

Open the DMG and drag Athena to Applications. To remove the application, quit it and move `Athena.app` to the Trash. User conversations and files remain in `~/Library/Application Support/Athena Desktop`, independently of the app bundle. Removing that folder is a separate, destructive action, never an automatic uninstall side effect. This preview deliberately keeps desktop data separate from the developer web app’s `Athena` directory.

The native window provides standard editing shortcuts, file selection and a Save dialog for downloads. The model-facing frontend remains the same React application. Microphone access is requested only for the trusted application frame; generated components cannot request camera or microphone access. WebKit website data is managed by macOS for the `com.featherhk.athena` application. Developer credentials are not inherited by the child process.

The app owns its sidecar: Python binds a vacant loopback port atomically, then reports readiness only after migrations and application startup. The window never attaches to an unrelated process occupying a well-known port. Closing the window keeps the application and local jobs running; quitting ends the sidecar gracefully. Local scheduled work cannot run while the application or computer is off. Durable hosted jobs are needed for an always-on promise. The shell can reload independently of generated frontend surfaces and presents a native recovery dialog if its sidecar exits.

**View → Restore Default Interface…** (Command-Shift-R) is a separate native recovery entry. After a warning about unsent text, it loads a GET page on the sidecar's exact loopback origin with `?recovery=1`. The matching web client skips custom surface/component mounts while preserving chats, files and existing tasks. Ordinary Command-R stays in safe mode after recovery; a restarted sidecar uses its newly reported port and keeps the safe-mode choice. A first WebKit content-process failure automatically attempts this safe page once. A further failure stops automatic reloading and shows native Retry/Close controls, avoiding a crash loop. None of these operations replay a model request or restart an already-running task.

After a successful Reset, the web client can leave safe mode in the current page once the server confirms that custom extensions were disabled. The native shell deliberately retains its recovery choice until the app is relaunched; a later native reload enters safe mode again. A script or subframe cannot silently revoke that native recovery choice.

## Signing and public release

The default build is ad-hoc signed and named `-preview.dmg`. This supports internal testing; it is **not notarized** and is not presented as a frictionless public download. This machine currently has no Developer ID signing identity, and the operator has not enrolled in the Apple Developer Program. Do not tell testers to disable Gatekeeper globally.

For a future signed build:

```sh
uv run python scripts/build-macos.py --identity 'Developer ID Application: Your Organization (TEAMID)'
sh scripts/notarize-macos.sh /absolute/path/to/Athena.dmg athena-notary
```

Create the `athena-notary` profile through `xcrun notarytool store-credentials` using the login Keychain. Do not put Apple account credentials, app-specific passwords, private keys or certificates in source files. Every nested Mach-O binary is signed before signing the outer app. The notarization script submits, staples, validates and evaluates the image, then recalculates its checksum because stapling changes the file. The build manifest remains `notarized: false` until release automation is extended to attest the final notarized artifact; do not publish the pre-stapling checksum.

Apple’s [notarization documentation](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) describes the Developer ID, hardened runtime and timestamp requirements. Its [distribution packaging guide](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution) calls for testing a downloaded package on a clean Mac. The runtime comes from uv’s [portable managed Python distributions](https://docs.astral.sh/uv/concepts/python-versions/).

## Cloud and account boundary

Hosted conversation origin: `https://chat.athena.featherhk.com`; gateway base: `https://chat.athena.featherhk.com/v1`. The separate `https://app.athena.featherhk.com` site provides application downloads. The gateway endpoint is not permission to package a provider key. Hosted authentication and a server-side model gateway must enforce account entitlement and reserve/settle balance for each request. Only operator-enabled accounts with positive test credit can consume; registration grants no credit and there is no recharge UI. An API key configured in a customer’s local SQLite database would defeat this design.

A cloud-configured preview sidecar uses the central account and credit gateway implemented in `cloud_client.py`; provider credentials remain on the server. It does not inherit the developer’s key, create a default user account, or grant arbitrary accounts a balance. A build with no cloud URL retains isolated local authentication for development. This integration must pass before a customer build is published:

- Fresh install, central login, account switch, token expiry/revocation and offline behavior.
- A zero-balance account cannot issue model or speech requests through either the web app or a modified client.
- Provider credentials never reach browser responses, local request snapshots, extension files, subprocess environments or the app bundle.
- Request retries, interrupted streams and concurrent calls cannot overspend or settle twice.
- Local agent execution remains local; hosted web execution is isolated from the host, other users and the existing New API service.
- Desktop restart preserves history and acknowledges uncertain side effects without automatically replaying them.
- Real microphone recording, output, interruption, Bluetooth/headphones, sleep/wake and permission denial on the packaged app.
- Download/upload, a read-only mounted DMG, relocation into Applications, normal uninstall, and independent reset/recovery.

## Other platforms and updates

On Mac, **Athena → Check for Updates…** manually checks the fixed HTTPS distribution feed independently of the local sidecar and login service. The result includes the installed version/build and a link to the fixed download page. It distinguishes a newer preview, the current verified source/build, a local version ahead of the published preview, and incompatible Mac requirements. Missing build metadata, mismatched source identifiers and network errors never result in a claim that the app is up to date. The request has a bounded timeout/response size and uses no account cookies or credentials; redirects and download URLs supplied by a manifest are not followed.

Checking does not download an installer, replace the application, reload the chat or restart its sidecar. Finish active work and preserve any unsent input before manually quitting and replacing the app. Conversations and files remain in the separate data directory. The menu is in the native shell so a generated component or unavailable login endpoint cannot hide it. This manual discovery feature requires a new desktop build; it cannot appear retroactively in already installed previews.

The web client and runtime API can be reused. The native host is intentionally thin; it does not make Swift the product’s cross-platform business layer. Windows needs its own packaged Python sidecar and WebView2 host. iOS and Android need native capture/background policies and a remote execution service: a desktop Python/CLI process cannot simply be transplanted into their app sandbox.

Automatic self-update is not yet implemented. Treat the signed application core and agent-authored extension registry separately. A future updater must verify signed releases, install atomically, retain rollback metadata and wait for a safe runtime boundary. Registration updates can apply live; replacing the desktop executable or Python runtime requires a supervised process restart. No in-app updater should accept an arbitrary URL or overwrite the app while an agent task is running.

Run the native comparison/destination regressions without launching Athena:

```sh
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer xcrun swiftc desktop/macos/UpdateCheck.swift desktop/macos/Tests/UpdateCheckTests.swift -o /private/tmp/athena-macos-update-check
/private/tmp/athena-macos-update-check
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer xcrun swiftc desktop/macos/WindowRecovery.swift desktop/macos/Tests/WindowRecoveryTests.swift -o /private/tmp/athena-macos-recovery-check
/private/tmp/athena-macos-recovery-check
uv run python desktop/macos/Tests/run-native-recovery-checks.py --xcode /Applications/Xcode-beta.app
uv run pytest tests/test_desktop_bundle.py -q
```

Select an initialized Xcode appropriate to the development Mac. These tests cover same-version/higher-build previews, missing metadata, reused build/source mismatches, system compatibility, fixed destination URLs, and isolated transport responses for HTTP errors, offline failures, redirects and oversized data. They do not establish native-menu visual acceptance or installer signature verification by the discovery feature.

The separate recovery fixture application runs the actual AppKit menu and WKWebView without launching an Athena backend or using a real account. It serves only synthetic documents from a temporary loopback server. Its six checks exercise cancelling native recovery without losing a draft, confirming recovery, ordinary reload retaining safe mode, the first content-process failure callback entering safe mode, a second callback stopping the loop, and native failure Retry retaining safe mode. All observed requests must be GET. The native sheets are completed through AppKit's public sheet API; the process-death callbacks are simulated. The receipt is evidence of native menu/navigation behavior, not a physical crash, provider call, real account session or accessibility gesture acceptance.

## Verified in this workspace

The Apple Silicon preview was built and its nested code signatures verified. Its bundled Python reported 3.14.8 with free threading enabled and GIL disabled. `scripts/smoke-macos.py` passed relocation into an app path containing spaces, startup, local account persistence across restart, origin rejection, unauthenticated API rejection, graceful shutdown and absence of an inherited provider key using disposable data. Three focused desktop tests cover whole-bundle protection, real Seatbelt rejection of native/interpreter writes while external files remain writable, and rejection of credential-bearing or non-HTTPS cloud URLs. The final cloud-configured bundle also passed the relocation/startup/auth-boundary/origin/restart/shutdown smoke without contacting the gateway or creating remote accounts. Native-window visual/microphone acceptance is still outstanding: the first computer-use launch was interrupted after the tool stopped responding. A cloud-configured build needs a live central-login test in addition to these local bundle checks.

The [2026-10-03 release receipt](releases/2026-10-03-preview.md) supersedes the earlier native-window/cloud-login gap for the named build. A clean second Mac, Gatekeeper on a quarantined download and real microphone calls remain unverified.
