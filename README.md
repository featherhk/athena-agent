# Athena

English · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

Athena is a personal assistant that starts with a conversation. Describe what you want done; progress, results, and useful controls appear when needed. Everyday use should not require understanding models, API keys, or agent frameworks.

The current release is a **local macOS / web prototype**, built with Python 3.14T, SQLite, TypeScript, and React. It is a general assistant with programmable capabilities, not a coding-only interface. The [product direction](docs/product.md) separates implemented behavior from ideas still to validate.

The current preview also includes a separate account/model gateway with zero-credit registration, audited USD test credits, and a self-contained Apple Silicon macOS test package. The gateway has passed a real provider-stream and settlement check; the authenticated [web preview](https://chat.athena.featherhk.com/) and [Mac download page](https://app.athena.featherhk.com/) are live over HTTPS on Tencent Cloud. The DMG is ad-hoc signed, not notarized. Voice turn-taking, interruption, and provider adapters are implemented, but a real audio channel and microphone call have not yet been verified. See [release readiness](docs/requirements-status.md), [cloud operations](docs/cloud-deployment.md), [desktop distribution](docs/distribution.md), and [voice](docs/voice.md).

## What works today

- Accounts with display names and avatars, plus a fixed password-change form that keeps passwords out of model messages and tools. Provider credentials remain server-managed.
- Nine interface languages: Arabic, English, Spanish, French, Japanese, Korean, Portuguese, Simplified Chinese, and Traditional Chinese. Browser preferences are matched automatically with English fallback; native language names appear without flags, ordered by BCP 47 tags. Arabic uses right-to-left layout. Interface language, font, palette, and reduced motion are saved separately from reply preferences.
- Streaming chat with safe Markdown rendering, separately collapsible reasoning actually returned by the provider, and distinct thinking, replying, and execution states. Durable events restore updates after reconnecting; closing the browser does not stop a task.
- File attachments and generated outputs associated with their conversation. Owned files can be downloaded or previewed as Markdown, isolated HTML, images, or bounded XLSX cells and cached values.
- Managed browsing through the same runtime tool on macOS with Chrome/Chromium: isolated persistent account profiles, conversation-owned tabs, semantic observations, forms, downloads and screenshots. Actions have durable receipts and are not automatically resent after a lost response. [Browser execution](docs/browser-execution.md) describes verification and remaining limits.
- Message queues, interruption, stop, and conversation forks. Forks inherit a fixed history boundary and copy background tasks in a paused state.
- Bounded parallel delegation through `runtime`: separate subtask context and files, durable wait/resume, result references and cancellation. Questions and output files return to the main conversation; conversation costs include its children. Forks retain historical receipts without starting copied work. See [delegation](docs/delegation.md).
- Through `runtime`, Athena can find/read other conversations in the same account, create or rename them, and fork idle conversations. Completed runs can be excluded from the current model context and restored; original chats remain intact. Browser navigation and other shell actions use durable claims and acknowledgements; returning a session ID alone does not navigate.
- Three core OpenAI-format tools: `exec`, `update_frontend`, and `runtime`. The runtime uses a small `{op, args}` envelope. Skills are directory packages with `SKILL.md` metadata and optional scripts, references, templates, or other resources; bodies and resources load on demand.
- Versioned tools, components, skills, and policy providers outside the source checkout. Restoring a previous registration creates a new version. Frontend contributions support inline, workspace, sidebar, fixed, overlay, fullscreen and presence surfaces with live updates and render receipts; the fixed shell retains input, stop, navigation, and reset.
- Three connected policy hooks: context compression, memory recall, and command preparation. Plugins run in bounded subprocesses; errors, timeouts, or invalid results fall back to the default. Service IDs are open, but a registration without a consumer is reported as waiting and does not change behavior.
- Timers, recurring tasks, and script-based conditions. Checks run without an LLM; a match queues work in the original conversation.
- Explicit user, project, and conversation memories; recoverable tool-output clipping; and runtime inspection of usage, cache hits, cost estimates, and context. Requested compaction can target an estimated token budget while preserving original messages, reasoning, and tool-call pairing. Missing usage and unknown model limits remain unknown.
- Optional developer telemetry and operator-registered Chat Completions model profiles. Athena can switch profiles at the next request, creating an audited context version while keeping its product identity. Reset stops work, restores defaults, disables extensions, and pauses tasks while retaining chats, files, memories, and historical versions.

See the [acceptance criteria](docs/acceptance.md), [command-derived minimum suite](docs/command-acceptance.md), and [plugin architecture](docs/plugin-architecture.md) for observable checks, implemented boundaries, and the service-host roadmap.

## Run locally

These instructions are for developers and operators. End users register an Athena account and send a message; they do not configure a provider.

Requirements: macOS, `uv >= 0.9`, Node.js `>= 24`, and npm. Managed browsing additionally requires installed Google Chrome or Chromium.

```sh
uv python install 3.14t
uv sync --locked
npm ci
uv run --directory backend python -m athena.doctor
npm run build
npm start
```

Open [Athena](http://127.0.0.1:4317). The first start initializes the local database. In a second terminal, configure the service credential with hidden input:

```sh
uv run --directory backend python -m athena.admin provider-key
uv run --directory backend python -m athena.admin status
```

Alternatively, read a credential from a local file without printing it:

```sh
uv run --directory backend python -m athena.admin provider-key --file /path/to/private/key.txt
```

The default profile uses the official DeepSeek API and `deepseek-flash` (DeepSeek V4.1 Flash). A server-side `DEEPSEEK_API_KEY` environment variable takes precedence over the saved default credential. Saved credentials stay in the local database and are never returned to the client. They are not yet stored in the system keychain. Credential changes need no restart.

For frontend development, use `npm run dev`: Vite serves port 5173 and proxies API requests to the backend on port 4317.

Startup requires a free-threaded Python 3.14 build and verifies that dependencies have not enabled the GIL. The project currently uses the USTC PyPI mirror through its local uv configuration; it does not change global package-manager settings.

## Configure another model

The backend is not tied to DeepSeek. Operators register immutable profiles; the agent can select only those profiles and cannot supply arbitrary endpoints or credentials.

```sh
uv run --directory backend python -m athena.admin register-model \
  --id second-model \
  --provider 'Provider name' \
  --model 'api-model-id' \
  --base-model 'Underlying model name' \
  --api-base 'https://provider.example/v1'

uv run --directory backend python -m athena.admin provider-key --profile second-model
```

Replace the example values with the provider's documented configuration. `register-model --help` lists options for the instruction role, output-token parameter, reasoning preservation, generation settings, and context guard. Use a new profile ID when changing its definition so active requests and audit records retain their original meaning.

The current adapter targets streaming **Chat Completions** APIs. Native Responses, Anthropic Messages, and other protocols need separate adapters. Compatibility is not a claim that every provider or model has been verified. A model switch preserves conversation records, takes effect at the next request, and is treated as a prompt-cache boundary; actual cache behavior belongs to the provider.

## Runtime and data

Each conversation has serial execution; independent conversations can run concurrently. Event-loop state has one owner. SQLite connections live on a dedicated worker thread, with WAL and transactional state transitions. The implementation does not use the GIL as a synchronization mechanism.

State is stored outside the checkout:

| Platform | Default data directory |
| --- | --- |
| macOS | `~/Library/Application Support/Athena` |
| Windows | `%LOCALAPPDATA%/Athena` |
| Linux | `$XDG_DATA_HOME/athena`, or `~/.local/share/athena` |

`ATHENA_DATA_DIR` can override this location, but it must remain outside the source repository. The directory contains `athena.sqlite3`, full output artifacts, user files, extension versions, and private browser-profile storage. Stop the service before backing up the complete data directory. [Lossless event archival](docs/event-storage.md) creates a database backup before maintenance; scheduled backups and encryption are not implemented yet.

Messages, requests, tool results, task checks, wake-ups, model switches, and clipping records are auditable. Events are persisted before broadcast. After a backend restart, unfinished work is marked interrupted rather than automatically replaying a potentially side-effecting operation. A disconnected client and a crashed backend have different recovery guarantees.

Scheduled work requires the local service and computer to be running. Missed recurring intervals are coalesced after resumption. Conditions run for at most 30 seconds per check, at intervals of at least 60 seconds. External effects such as sending mail still require executor-specific idempotency or result verification.

`.gitignore`, repository checks, Git hooks, and CI protect against committing runtime data and common credential formats. Never add real chats, cookies, screenshots, databases, private files, or recorded model requests as test fixtures. JSON remains a valid source/configuration format.

## Current boundaries

The macOS command runner uses Seatbelt to prevent writes to application source and restrict access to Athena's runtime data. Registered code lives in the user file area. This is not a complete sandbox for hostile tenants: other host resources remain within the local user's reach. Command execution is disabled on platforms without the protection adapter, including the hosted Linux preview. The local runtime stays on loopback. Hosted deployment uses a separate gateway and explicit HTTPS origin; it is not a general-purpose public execution sandbox.

The current Engine and Store still implement the agent loop and persistence directly. They are not replaceable providers behind a service host yet. Existing policy hooks do not make the model transport, scheduler, authentication, or entire frontend interchangeable; registering an unused service ID cannot activate a missing integration. Core application updates still require a controlled restart.

Still to build or validate: browser vision input, uploads/popup workflows, desktop control, OS notifications, production execution isolation, signed/notarized distribution, native mobile/Windows clients, real audio calls, and broader task-quality evaluation. Central accounts and a reservation/settlement ledger exist; payment collection, account recovery and production reconciliation remain out of scope. XLSX previews are bounded readers, not a spreadsheet calculation engine. There is deliberately no per-action approval workflow in this prototype.

## Verify

```sh
npm run check
node scripts/check-repository.mjs --staged
```

Automated tests use mocked model responses and do not consume API credit. They check observable state changes: account and file ownership, settings versus reply preferences, streaming and interruption, recovery and forks, registration versions and restoration, skill resources, policy fallback, usage and compaction, locale matching, and repository hygiene. TypeScript checks and the production build run in the same command. Safari and the in-app browser have also been used to verify chat rendering, user-help resumption, live component removal, theme changes and a narrow layout. See the [design language](docs/design-language.md) and [acceptance record](docs/acceptance.md); Chrome remains unverified because it returned a client-side block.

An optional real-provider evaluation runs against a temporary account and database, without using existing customer conversations:

```sh
uv run --directory backend python ../scripts/evaluate_requirements.py --key-file /path/to/operator-key
```

This check consumes API credit, is excluded from CI, and prints verdicts and usage, plus brief synthetic diagnostics on failure, without credentials or provider recordings. Temporary data is removed after the run. Use repeatable `--case NAME` flags to rerun selected cases. Live DeepSeek checks have passed for interface language, appearance, persistent reply preferences, display name, registering and executing a new tool, usage/context inspection, reading another conversation, recoverable compaction, and excluding a completed run from context. Compaction success means a shorter recoverable projection, not a guaranteed exact token target. Those results do not establish broad task success, browser-control readiness, or cross-provider compatibility.
