# Contributing to Athena

Athena should help someone finish an everyday task without learning an agent
framework. A contribution is useful when it improves that experience and leaves
the user in control. Read [AGENTS.md](AGENTS.md) before changing the runtime.

## Where to begin

- Bugs: describe the goal, what happened, expected behavior, OS and build.
- Product ideas: start with a real task and a small, reproducible scenario.
- Extensions: keep provider-specific integrations and custom interaction surfaces
  outside the application core where possible. Include reset and failure behavior.
- Security issues: follow [SECURITY.md](SECURITY.md), never public issue logs.

Do not upload credentials, cookies, chat databases, personal documents or screen
recordings containing private content. Use synthetic fixtures.

## A reviewable change

Keep one purpose per pull request. Explain the user-visible effect and show its
acceptance evidence. For asynchronous behavior include reconnect, cancellation,
restart, stale-event and account-boundary cases as applicable. A tool returning
success is not proof that the user saw or received the result.

Run `npm run check`, the relevant native checks for macOS changes, and
`node scripts/check-repository.mjs --staged`. Python must remain free-threaded:
`uv run --directory backend python -m athena.doctor`.

Code, generated examples and documentation must preserve independent Stop and
Reset, runtime data outside Git, and localized official UI copy without emoji.
Do not expand scope with unrelated refactors or overwrite another contributor's
work. AI assistance is welcome; the submitting human remains responsible for
reviewing provenance, behavior and tests.

## Licensing and origin

New contributions are accepted under [Apache-2.0](LICENSE). Preserve third-party
notices and explain copied or adapted material. Sign your commits with
`git commit -s` to certify the [Developer Certificate of Origin 1.1](https://developercertificate.org/).
This is an origin certification, not a copyright assignment. Do not sign on
someone else's behalf. Previous MIT grants remain valid.

Community links will be published only after the corresponding channels exist.
No response-time guarantee is currently offered.
