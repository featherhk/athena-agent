# Hosted interaction and metered voice release — 2026-10-04

The hosted web client and account gateway were promoted at **13:31:24 UTC** from frozen commit `f81704569a956578dcbedc32c299dca90fc36040`, which records runtime source `38fb0df6b5479272513336b49ee5ddf2eb90cd54`. The release enables the independent operator audio profiles, WAV validation and the interaction changes described in [source acceptance](2026-10-04-interaction-voice.md). No later working-tree changes were included.

Only the web and gateway containers changed. New API, its database/cache/proxy, the Athena edge proxy, execution broker, QA broker and worker image retained their recorded identities. The published macOS DMG and download manifest were not changed by this release.

## Frozen artifact and data preservation

- Release: `/opt/athena/releases/f817045`; `/opt/athena/current` points there.
- Image: `athena-preview:f817045`, ID `sha256:963130b6f79b9b273e79c7b186bd0846b433c213f200ccfa27e0576062aa9213`.
- All 76 runtime source files matched the frozen source hashes. Candidate verification ran as UID 10001 with networking disabled.
- Both running services report Python 3.14.3, a free-threaded build and `gil_enabled: false` after dependency imports.
- Public `index.html`, `assets/index-BAe02Yqu.js` and `assets/index-CVaLKvuK.css` matched the built artifacts byte for byte. Health and unauthenticated endpoint checks passed.

Fresh idle checks ran twice immediately before stopping the services, then again during shutdown. They found no running, queued or waiting runs, active task checks, running audio requests or reserved credit requests. The services exited normally. Consistent SQLite backups were made after shutdown; earlier preparation snapshots were not substituted for these final backups.

The final private backup directory is `/var/backups/athena/20261004-f817045/cutover-133106` (0700), with database copies at 0600. Isolated, network-disabled migration checks preserved every original column and row hash, passed integrity checks and remained unchanged on a second initialization:

| Database | Migration | Original tables | SHA-256 of final backup |
| --- | --- | ---: | --- |
| Gateway accounts | 12 → 14 | 37 | `3e79820c2e6952e909ad721125721edf448e0b8954b7ce61b97b66de8774df45` |
| Web | 14 → 14 | 45 | `ab1463bec069db768279a6ea8fd0de5621824ca7c9c000bd660ca5322d6557d3` |

The gateway migration added four empty tables (`activity_reads`, `frontend_clients`, `frontend_revocations`, `run_frontend_sources`) and the initialization cutoff setting `activity_started_at`. It did not rewrite original data. Both live databases now use schema 14.

## Audio configuration and test-credit accounting

Recognition uses `qwen3-asr-flash` through the Beijing DashScope compatible HTTP endpoint; synthesis uses `qwen3-tts-flash` with the Cherry voice through the Beijing DashScope HTTP endpoint. The key is an operator-owned 0400 file readable by UID 10001, mounted read-only into the gateway only. It is absent from the image, web container, repository and client responses.

The explicit tariff version is **`voice-preview-2026-10-04`**:

| Operation | Gifted USD test-credit tariff |
| --- | ---: |
| Recognition | $0.00004 per provider-reported second |
| Synthesis | $0.15 per 10,000 provider-billed characters |

These values debit the existing gifted test balance. They are an operator preview tariff, **not a provider invoice or an exchange-rate conversion**. No grants or existing passwords were changed. Recharge remains disabled; new accounts start unfunded and cannot call the provider.

## Production synthetic roundtrip

Acceptance completed at **13:32:12 UTC** using an existing authorized account and a short synthetic Mandarin utterance. One synthesis and one recognition request were made. The returned WAV had finite valid lengths, contained 211,244 bytes and measured 4.4 seconds; the recognition included the expected test phrase.

| Operation | Actual provider-reported units | Preview charge | Settlement |
| --- | ---: | ---: | --- |
| Synthesis | 43 billable characters | 645 microcredits ($0.000645) | settled |
| Recognition | 4 billable seconds | 160 microcredits ($0.000160) | settled |
| Total | 2 requests | **805 microcredits ($0.000805)** | 2 exact debit entries |

Each request had exactly one ledger entry for its owner and request ID with the expected negative amount. Consistent read snapshots verified that the account balance fell by exactly 805 microcredits, matching the two entries. Unused reservations were released. Provider-reported billable seconds are recorded separately from the measured WAV duration; this test did not verify the provider's final invoice.

An isolated default-zero-balance QA account received HTTP 402 for voice availability, synthesis and recognition. It generated no provider-audit or credit-request rows and was logged out. Unauthenticated access received HTTP 401. The disabled QA account remains as an audit trace; it received no grant.

## Evidence and recovery boundary

Private evidence is retained beneath the final backup directory:

- `deployment-audit.json`: source/image, current health, protected container identities, public asset hashes and backup references.
- `gateway-proof/evaluation/migration-audit.json` and `web-proof/evaluation/migration-audit.json`: final copy migration proofs.
- `audio-acceptance/audit.json`: observed units, exact preview charges, access controls and reconciliation.
- `audio-acceptance/synthetic-speech.wav`: only the generated test utterance, not user audio.
- `rollback-policy.txt`: paired recovery constraints.

The old gateway image `5bcda5a` must not run against the migrated schema-14 account database. Restoring the old database after new requests would discard usage, logins or other work. Preserve fresh incident snapshots and reconcile all post-cutover changes before any paired rollback; a forward repair is preferred. Previous immutable images and final pre-cutover backups remain available.

This proves authenticated, metered **buffered HTTP audio** in the deployed gateway. It does not certify a real microphone, acoustic echo cancellation, physical iOS/macOS device behavior, streaming audio, full-duplex conversation or production latency. Those remain separate acceptance work.
