# Streaming durability and lossless event storage

The client still receives complete `message` payloads with the existing global sequence and conversation cursor. Storage encoding is private to the database; reconnect does not require a client-side delta cache or a new frontend protocol.

## Writes and recovery

- The first reasoning chunk and the first answer chunk are persisted immediately. Further chunks are coalesced over a 50 ms interval. Slow storage can increase that interval; it is not a real-time guarantee.
- One SQLite transaction updates the message and appends its event. Broadcast follows commit. A failed event insert cannot leave a half-updated message.
- Stop, provider failure and normal completion flush pending answer and reasoning before finalizing the run. Repeated cancellation cannot cancel that final write. An abrupt process or machine failure can lose the not-yet-flushed tail; this is not a zero-loss crash guarantee.
- Message events use JSON, bounded zlib checkpoints, or exact prefix deltas. A chain contains at most 31 deltas before a new checkpoint. Replacements and non-prefix edits start independent checkpoints. Every delta also retains the complete current metadata.
- Conversation and message IDs are checked when recovering a base. Invalid offsets, unsupported encodings, missing bases and oversized decompression fail explicitly instead of inventing output. Decoded message payloads are limited to 32 MiB. Event pages are bounded by decoded size as well as row count.
- Application/control events retain ordinary JSON for their transactional readers. Source messages, model request snapshots, tool results, usage and prompt prefixes are unchanged.

This reduces repeated snapshot storage and avoids a database transaction and UI update for every provider token. It does not change context clipping, provider token usage, or the meaning of a completed external action.

## Existing databases

Schema 10 adds event encoding/base columns and per-message append heads. Existing JSON events remain readable immediately. Old event archival is a separate, resumable maintenance operation; startup does not unexpectedly rewrite a large history.

After upgrading, stop Athena and run:

```sh
uv run --directory backend python -m athena.admin archive-events --vacuum
```

Use the same external `ATHENA_DATA_DIR` as the application when overriding its default. The command takes the dispatcher's exclusive instance lock, refuses unfinished queued/running work, and creates a mode-0600 SQLite backup under the external data directory's `backups` folder before changing events. An idle running server is also refused: it could otherwise wake a task during maintenance.

Archival works in transactions of up to 500 eligible message events. Each batch starts new bounded chains, verifies every recovered payload against the original UTF-8 bytes, and leaves event sequences/timestamps intact. Noncanonical legacy JSON gets an independent compressed checkpoint so original whitespace and escaping survive. A stopped archive can resume from remaining ordinary JSON rows. Existing append heads are not moved backward.

`--vacuum` reclaims free database pages after successful archival. Backups remain separate and are not automatically deleted or encrypted. Leave enough free disk space for the original database, its backup and SQLite's temporary rewrite. This command is a database backup, not a backup of user files, browser profiles or extension directories.

For recovery, keep Athena stopped and restore a verified consistent backup with schema-compatible code. Do not copy a database over a running process or discard its WAL transactions. Releases older than schema 10 cannot read the new encoded events; rolling back code alone is insufficient. Retain a pre-upgrade backup when planning a rollback across that boundary. Restoring an old snapshot also restores old account/task state, so inspect changes since the backup before resuming external work.

## Validation

Regression coverage includes atomic rollback, live updates before completion, pending reasoning/text on stop and provider error, repeated cancellation during disk writes, arbitrary reconnect cursors, cross-conversation base rejection, bounded decoding, interleaved messages, replacements, interrupted archival, append-head preservation, exclusive maintenance locking and backup restoration through the normal reader.

On 2026-10-03, a private copy of a local schema-8 backup was migrated to schema 10 and archived. Its 103,865 event payloads were recovered byte-for-byte; all 33 other existing tables had unchanged row digests. The archive's backup passed the same comparison and SQLite integrity check. The database decreased from 1,012,076,544 to 93,134,848 bytes (90.8%); retained backups are not included in that reduction. Customer data and the temporary evaluation copy were kept outside the checkout.
