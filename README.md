# Athena

English · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

Athena is a personal assistant that starts with a conversation. Describe what you want done; progress, results, and useful controls appear when needed. Everyday use should not require understanding models, API keys, or agent frameworks.

The current release is a **local macOS / web prototype**, built with Python 3.14T, SQLite, TypeScript, and React. It is a general assistant with programmable capabilities, not a coding-only interface. The [product direction](docs/product.md) separates implemented behavior from ideas still to validate.

## What works today

- Accounts and server-managed model credentials. Provider configuration stays out of the normal user experience.
- Nine interface languages: Arabic, English, Spanish, French, Japanese, Korean, Portuguese, Simplified Chinese, and Traditional Chinese. Browser language preferences are matched automatically, with English as the fallback; the language picker uses native names without flags, ordered by BCP 47 tags. Arabic uses right-to-left layout, and motion can follow system preferences or be reduced manually.
- Streaming conversations with durable events. Changing conversations or closing a browser does not stop a running task; reconnecting replays persisted updates.
- Queued messages, interruption, stop, and conversation forks. Forks inherit a fixed history boundary and copy background tasks in a paused state.
- Three core OpenAI-format tools: `exec`, `update_frontend`, and `runtime`. Short instructions reference skills loaded only when needed.
- Registered frontend components and Python backend tools, versioned outside the source checkout. Reset stops current work, disables custom extensions, and pauses background tasks while preserving chats, files, and memories.
- Timers, recurring tasks, and script-based conditions. Checks run without an LLM; a match queues work in the original conversation.
- Recoverable tool-output clipping and explicit user, project, and conversation memories. Original messages and output artifacts remain available.
- Optional developer telemetry: reported token usage, cache hits, request status, and clearly labeled cost estimates. Missing usage is not treated as zero cost.
- Operator-registered Chat Completions model profiles. Athena can switch to a configured profile at the next model request; its stable instructions identify Athena and the configured underlying model. Switches create an audited context version.

## Run locally

These instructions are for developers and operators. End users register an Athena account and send a message; they do not configure a provider.

Requirements: macOS, `uv >= 0.9`, Node.js `>= 22.12`, and npm.

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

`ATHENA_DATA_DIR` can override this location, but it must remain outside the source repository. The directory contains `athena.sqlite3`, full output artifacts, user files, extension versions, and reserved browser-profile storage. Stop the service before backing up the complete data directory. Automatic backup and encryption are not implemented yet.

Messages, requests, tool results, task checks, wake-ups, model switches, and clipping records are auditable. Events are persisted before broadcast. After a backend restart, unfinished work is marked interrupted rather than automatically replaying a potentially side-effecting operation. A disconnected client and a crashed backend have different recovery guarantees.

Scheduled work requires the local service and computer to be running. Missed recurring intervals are coalesced after resumption. Conditions run for at most 30 seconds per check, at intervals of at least 60 seconds. External effects such as sending mail still require executor-specific idempotency or result verification.

`.gitignore`, repository checks, Git hooks, and CI protect against committing runtime data and common credential formats. Never add real chats, cookies, screenshots, databases, private files, or recorded model requests as test fixtures. JSON remains a valid source/configuration format.

## Current boundaries

The macOS command runner uses Seatbelt to prevent writes to application source and restrict access to Athena's runtime data. Registered code lives in the user file area. This is not a complete sandbox for hostile tenants: other host resources remain within the local user's reach. Command execution is disabled on platforms without the protection adapter. Keep this prototype on loopback; it is not a ready-to-host public service.

Still to build: integrated browser and desktop control, OS notifications, packaged resident execution nodes, hosted accounts, a balance/payment ledger, native mobile/Windows clients, voice, subagents, and broader task-quality evaluation. Current login and cost estimates do not constitute production identity or billing infrastructure. There is deliberately no per-action approval workflow in this prototype.

## Verify

```sh
npm run check
node scripts/check-repository.mjs --staged
```

Automated tests use mocked model responses and do not consume API credit. They cover database transactions, concurrent claims, streaming tool calls, reasoning continuity, interruption, forks, recoverable clipping, restart handling, conditional wake-ups, account boundaries, extension reset, model switching, locale matching and fallback, and repository hygiene. TypeScript checks and the production build run in the same check command.

A live DeepSeek request and an isolated account-registration-to-reply smoke test have also been verified. This does not imply broad task-success, browser-control, or cross-provider validation.
