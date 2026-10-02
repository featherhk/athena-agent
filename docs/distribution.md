# Distribution

Athena’s desktop application is a native macOS window over the same web client and local Python service. The operator’s model key must never be shipped to a customer. The desktop sidecar and hosted web execution are different trust boundaries; a successful DMG build does not by itself make the cloud service safe to publish.

## Current macOS preview

Build on macOS with Xcode command line tools, Node, and uv installed:

```sh
uv sync --python 3.14t
npm ci
uv run python scripts/build-macos.py --cloud-url https://app.featherhk.com/v1
```

The optional `--cloud-url` selects the public HTTPS account/model gateway and writes it into `Contents/Resources/bundle-config.json` before signing. It accepts no credentials, query string or fragment. The launcher reads only this non-secret bundle configuration; it never falls back to local authentication if the selected cloud service is unavailable. Omitting the option creates an isolated local development preview without provider credentials. The DNS and TLS endpoint must be verified before distributing the cloud build.

The script uses the locked production Python dependencies, the installed uv-managed Python 3.14T distribution, and a fresh React build. It constructs an allowlisted bundle: application source, skill packages, web assets, Python runtime, dependency license metadata, the native launcher and localized shell strings. It does not copy the checkout wholesale, `.env`, database, user files, browser profiles or operator credentials. The bundled Python imports the real dependency stack and must report that the GIL remains disabled. No Python, Node, uv or developer tools are required on the recipient’s Mac.

Outputs are ignored under `build/macos/`: `Athena.app`, an architecture-specific `.dmg`, a SHA-256 checksum, and a release manifest. An Apple Silicon build is not an Intel build. Build and test on an Intel Mac before advertising Intel support. macOS 13 is the shell deployment target; only versions actually tested should be advertised.

Open the DMG and drag Athena to Applications. To remove the application, quit it and move `Athena.app` to the Trash. User conversations and files remain in `~/Library/Application Support/Athena Desktop`, independently of the app bundle. Removing that folder is a separate, destructive action, never an automatic uninstall side effect. This preview deliberately keeps desktop data separate from the developer web app’s `Athena` directory.

The native window provides standard editing shortcuts, file selection and a Save dialog for downloads. The model-facing frontend remains the same React application. Microphone access is requested only for the trusted application frame; generated components cannot request camera or microphone access. WebKit website data is managed by macOS for the `com.featherhk.athena` application. Developer credentials are not inherited by the child process.

The app owns its sidecar: Python binds a vacant loopback port atomically, then reports readiness only after migrations and application startup. The window never attaches to an unrelated process occupying a well-known port. Closing the window keeps the application and local jobs running; quitting ends the sidecar gracefully. Local scheduled work cannot run while the application or computer is off. Durable hosted jobs are needed for an always-on promise. The shell can reload independently of generated frontend surfaces and presents a native recovery dialog if its sidecar exits.

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

Planned public application origin: `https://app.featherhk.com`; gateway base: `https://app.featherhk.com/v1`. The endpoint is not permission to package a provider key. Hosted authentication and a server-side model gateway must enforce account entitlement and reserve/settle balance for each request. Only explicitly enabled preview accounts have access; other accounts start with zero credit, and there is no recharge UI. An API key configured in a customer’s local SQLite database would defeat this design.

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

The web client and runtime API can be reused. The native host is intentionally thin; it does not make Swift the product’s cross-platform business layer. Windows needs its own packaged Python sidecar and WebView2 host. iOS and Android need native capture/background policies and a remote execution service: a desktop Python/CLI process cannot simply be transplanted into their app sandbox.

Automatic self-update is not yet implemented. Treat the signed application core and agent-authored extension registry separately. A future updater must verify signed releases, install atomically, retain rollback metadata and wait for a safe runtime boundary. Registration updates can apply live; replacing the desktop executable or Python runtime requires a supervised process restart. No in-app updater should accept an arbitrary URL or overwrite the app while an agent task is running.

## Verified in this workspace

The Apple Silicon preview was built and its nested code signatures verified. Its bundled Python reported 3.14.8 with free threading enabled and GIL disabled. `scripts/smoke-macos.py` passed relocation into an app path containing spaces, startup, local account persistence across restart, origin rejection, unauthenticated API rejection, graceful shutdown and absence of an inherited provider key using disposable data. Three focused desktop tests cover whole-bundle protection, real Seatbelt rejection of native/interpreter writes while external files remain writable, and rejection of credential-bearing or non-HTTPS cloud URLs. The final cloud-configured bundle also passed the relocation/startup/auth-boundary/origin/restart/shutdown smoke without contacting the gateway or creating remote accounts. Native-window visual/microphone acceptance is still outstanding: the first computer-use launch was interrupted after the tool stopped responding. A cloud-configured build needs a live central-login test in addition to these local bundle checks.
