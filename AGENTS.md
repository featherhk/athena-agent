# Athena development conventions

- Build a general personal assistant for nontechnical users. Keep the default UI minimal; status, stop, settings and history belong to the fixed shell.
- Use Python 3.14T for the backend and TypeScript/React for the initial web client. `uv run --directory backend python -m athena.doctor` must confirm the GIL remains disabled after dependency imports.
- Event-loop state has one owner. SQLite connections have a dedicated worker thread. Do not rely on the GIL for atomicity or share connections across threads.
- Store runtime state in SQLite outside the checkout. Never commit real user data, credentials, cookies, database files, request recordings or browser profiles. Check `.gitignore` when adding new persistence paths.
- Keep the stable system prompt and OpenAI tool descriptions short. Put operating conventions in `backend/athena/skills/`. Preserve previous prompt prefixes until a persisted compaction boundary.
- Model switches are explicit cache boundaries: preserve Athena's identity, identify the configured provider/base model, increment the session context version, and snapshot each request's profile. Never replay completed actions while switching.
- Provider keys and model profiles are operator configuration, never customer onboarding. Keep secrets out of tools, browser APIs, request snapshots and agent subprocess environments.
- Agent-authored changes belong in the external file area and take effect through versioned registration. Protect the application core and fixed reset shell; reset retains chats and files.
- Prefer fewer concepts and accurate information. Verify send/stop/reconnect/login paths before expanding the product surface. Keep all UI strings in locale catalogs; no flags for languages. Default README is English with Chinese and Japanese links.
- Retain recoverable originals when clipping tool results. Preserve DeepSeek reasoning content and assistant/tool-call pairing.
- Persist transitions before broadcasting. Do not equate a disconnected browser with cancellation or automatically replay a possibly side-effecting interrupted action.
- Forking must not activate duplicated background jobs. Keep inherited history boundaries immutable.
- The prototype deliberately has no per-action approval workflow; do not add one without discussing the product change.
- Run meaningful tests for concurrency, persistence and protocol changes. Run `npm run check` before publishing code; check staged paths with `node scripts/check-repository.mjs --staged`.
- Document unfinished features honestly; do not claim integrated computer use, system notifications or model switching until actually implemented and verified.
