# Managed browser execution

The `runtime browser` operation connects ordinary assistant tasks to a real managed browser on the execution node. It uses the pinned native [agent-browser 0.38.2](https://github.com/vercel-labs/agent-browser/tree/v0.38.2) engine. Python's event loop owns each account's operation lock; SQLite owns account/conversation tab bindings, action receipts and restart transitions. This is a concrete browser adapter, not yet a dynamically replaceable service provider.

## Identity and execution

- macOS with installed Google Chrome or Chromium, and Linux with the optional [container execution adapter](linux-execution.md), are supported. npm installs the native engine; the macOS build copies the native executable and its license into the application. The optional browser feature still requires Chrome/Chromium on a clean machine. No Node runtime is used by the distributed executor.
- Athena starts a separate account-scoped profile beneath its external data directory. It does not copy personal profiles, attach to personal debugging endpoints or expose cookies/credentials as tool results. The model cannot set executable paths, profile paths, launch flags, environment or raw browser protocol packets.
- The native engine is a trusted host service, started directly with fixed arguments and a filtered environment. Provider keys and arbitrary inherited browser settings are removed. Agent-authored commands/extensions retain the existing Seatbelt boundary. Chromium retains its own renderer/network sandboxes: nesting the command Seatbelt sandbox prevents those sandboxes from initializing on macOS. This is not OS-user isolation against a compromised native driver or browser parent process.
- HTTP(S) navigation is explicit. A new empty tab is bound to its conversation before navigating. Stable native target IDs are reconciled with daemon identity and live inventory. A missing or closed tab is not silently recreated. Forks do not inherit live tabs or browser actions.
- Each observation issues a new host token. Actions require that token and a ref from that observation. The native ref map is global to its current observed tab, so another conversation must observe again after switching. A harmless tab reselection must not accidentally clear refs before a click.
- Full semantic observations are retained as recoverable artifacts; only the returned text is clipped. Compact native trees can omit result text, so they are not the evidence source. Downloads and screenshots pass through no-follow, regular-file and size checks into the ordinary file area. They remain owned by the current conversation.

## Transport and recovery

The upstream CLI retries after socket EOF/read errors. Such a retry may repeat a side effect. Athena therefore uses the CLI only for local inventory/configured startup, and sends each webpage operation exactly once over the owned Unix socket. The small protocol mapping is pinned to upstream [`commands.rs`](https://github.com/vercel-labs/agent-browser/blob/v0.38.2/cli/src/commands.rs) and [`connection.rs`](https://github.com/vercel-labs/agent-browser/blob/v0.38.2/cli/src/connection.rs). Upgrades require native integration tests and protocol review, not merely changing the npm version.

An action is committed as running before dispatch. Success is stored before returning it to the model; a lost result becomes unknown and invalidates its observation. Restart marks interrupted actions unknown without replay. Cancellation attempts to close the affected tab and retains an interrupted receipt. It cannot undo completed web effects or guarantee immediate cancellation of an already accepted native operation. `handoff` uses the existing durable user-help transition; it releases the agent run until the person responds, then requires fresh observation. Password inputs are not filled by this executor.

## Verification and limits

`tests/test_browser.py` checks universal-tool routing, scope, stale observations, concurrent conversations, original evidence, file publication, handoff, restart/cancellation, launch environment, password handling and lost-response single-send behavior. Run the real browser fixture with:

```sh
ATHENA_TEST_BROWSER=1 uv run pytest tests/test_browser_native.py -q
```

It uses only a disposable profile and synthetic loopback pages: fill/submit, one server-side receipt, readable result text, real CSV download, PNG screenshot and persistent login cookie after browser shutdown/restart. No user browser, external website or provider credit is involved.

Observed file inputs accept owned file IDs through the upload operation. The host creates bounded no-follow snapshots, sends file selection once, and distinguishes selection from website acceptance. Local native fixtures verify exact bytes and onchange receipts; cloud upload acceptance must be repeated for the deployed release. Snapshots are reclaimed after a later browser observation confirms their tab/generation closed; failed or orphaned cleanup remains bounded by staging limits and needs operator attention.

Vision input, visual clicking, arbitrary desktop control, custom/hidden file choosers, drag-only uploads, automatic popup ownership, browser installation/updating and Windows/Android execution adapters remain unfinished. Screenshots are files for the user, not model-visible image input. Browser dialogs are not auto-accepted. Headed login handoff and real external task quality need separate acceptance; the headless fixture alone does not establish those. The release/cloud/DMG status in requirements-status.md must be refreshed for each built artifact.
