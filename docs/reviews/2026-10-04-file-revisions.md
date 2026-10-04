# Observable file revisions

## Defect and implemented behavior

Generated files can be overwritten at their existing path. Before this change,
the registry exposed size but no content change marker. Inline images used size
as their cache key; other previews only watched the file ID and preview URL.
Consequently, a same-size PNG or Markdown rewrite could leave old content on
screen after an otherwise successful snapshot refresh.

`file_entries.content_revision` now persists an observed filesystem version.
An additive, idempotent migration runs on the Store-owned SQLite worker. Existing
IDs, ownership, upload/output origin, paths and creation timestamps remain intact;
older rows acquire their first version on normal discovery. Uploads receive their
version only after the completed file is renamed into place. Discovery still
cannot register a pending upload as a generated file.

The `s1-` token hashes device, inode, byte size, nanosecond modification time and
nanosecond change time. Computing it reads metadata, not every file's contents.
This observes same-size writes, preserved modification timestamps, and atomic
file replacements on the supported local macOS/Linux filesystems. Access-time
changes caused by reading a file do not invalidate it.

The public file record carries the token. `ImageResult`, `FilePreview`, and their
`ArtifactResult` integration reload on a changed token; unchanged stream renders
retain their existing loaded image/document. Each document version owns its load
state, abort signal, media callbacks and display receipts. Images stay lazy and
decoding remains asynchronous. A revision change does not automatically open a
dialog or separate workspace.

`GET /api/files/{id}/preview?revision=<token>` and the corresponding `content`
route validate the token against the securely opened descriptor before reading.
A mismatch returns HTTP 409 with `{"code":"file_changed"}`. Existing callers
without a token, and the old numeric image-size cache hint, remain compatible.
Authentication, ownership, no-follow traversal, single-link checks, bounded
previews, original download and `Cache-Control: no-store` remain in force.

## Deliberate limits

The token is a change detector, **not a content-addressed immutable revision**.
It can change after metadata-only updates and does not give an atomic snapshot
while another process writes through an already-open descriptor. A conditional
read checks the start of the read. It does not promise that an arbitrary historic
URL can reproduce previous bytes.

Generated files overwritten at the same path do not have a historical byte store
today. Separate uploads continue to have unique paths and retain their originals;
bounded previews still retain the complete current file for download. Historical
recovery for overwritten generated outputs requires a separate immutable blob
and retention design, rather than presenting a cache token as an undo mechanism.

## Validation

Synthetic owned-file API tests cover real equal-size PNG and Markdown rewrites
with restored mtime, atomic replacement, unchanged repeated discovery, persisted
versions across restart, legacy schema migration, upload registration races,
conditional-read conflicts, ownership and symlink rejection. No provider or real
user data is involved.

Actual React DOM tests exercise `ArtifactResult`, `FilePreview`, and
`ImageResult`, including unchanged stream rerenders, same-size version changes,
late aborted fetches, late iframe/image load events, preserved download links,
and a visible conditional-read failure without an automatic retry loop.
