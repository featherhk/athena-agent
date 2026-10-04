# Reusing file semantics and delivering originals — 2026-10-05

## Problem and change

The previous quotation task used separate model-written parsing and repeatedly
registered presentation components. Although the UI already distinguished a
missing formula cache from a saved empty string, the model did not have a direct
runtime path to those same semantics. It described a missing cache as an empty
value. File handling therefore needed a concrete consumer of the canonical
preview, not a longer always-present prompt.

The existing `runtime` tool now routes `op=files` to `file_tools.py`:

- `list` discovers the current conversation's files, or an explicitly supplied
  conversation belonging to the same account. It returns file entries and a
  bounded directory page without the full application-controls catalog.
- `read` accepts an existing file ID and optional observed `content_revision`.
  It returns `{file, preview, range, delivery}`. `file.content_url` is the existing file
  reference; there is no implicit copy, component registration, or new version.
  `preview` is produced by the same safe parser used by the HTTP preview.
- Spreadsheet reads preserve `raw`, `display`, `formula_status`,
  `format_status`, and the top-level unrecalculated/formula-warning flags.
  Each runtime cell additionally carries its absolute worksheet `address`,
  derived from the canonical row number and selected column. The UI schema and
  source file remain unchanged; the model does not need to count array positions.
  `sheet_index`, row `offset`/`limit`, and `column_offset`/`column_limit` select
  bounded pages. `range` states the actual returned rectangle, available sheets,
  total preview rows/columns, and next row/column offsets. Source-preview
  truncation is retained separately from pagination.
- Text is paged by characters. Word is paged by root blocks; a single oversized
  block explicitly returns `requires_cli=true`, not a claim that the document is
  empty. Image previews return the existing file reference, without claiming OCR.

The new `skills/files/` package has short metadata, an operating body, and the
progressively readable `references/reading.md`. It explains missing versus saved
formula results, hidden zeros, original-file delivery, and incomplete-preview
boundaries. Existing execution guidance points to it. The model-facing tool list
and runtime envelope do not grow. No system-prompt construction or persisted
prefix is rewritten; an existing session keeps its prefix until its normal
boundary. Skill bodies remain available on demand.

## Integrity and bounds

The runtime resolves ownership through the file entry and its owning session.
Cross-account IDs and conversation discovery fail closed. Reads reuse no-follow
file access, refuse symlinks/hardlinks, and verify the observed revision before
and after canonical preview creation. A same-size overwrite with restored mtime
still changes the metadata revision and rejects a caller's old token. This is a
read-time consistency guard, not immutable historical storage or a filesystem
snapshot guarantee.

Files are never rewritten by `read`. `list` reuses normal discovery/upsert and
preserves already registered IDs; it does not copy files. Generated deliverables
still belong in the external workspace. The skill directs edits to a new output
name and uses the existing file link for an unmodified delivery.

Spreadsheet page selection targets 16,000 serialized characters. It narrows the
rectangle and stops between rows while keeping each returned cell's complete
canonical metadata. A single unusually escaped cell may exceed this target, but
the parser's existing 2,000-character field bounds still impose an absolute bound;
no second silent clipping layer discards its formula state. Source previews remain
bounded to six sheets, 200 rows, 40 columns and the existing serialized budget.
Full-file verification beyond those limits still requires an explicit bounded
CLI workflow; this change does not add a formula recalculation engine.

## Evidence and limits

The 16 new `test_file_tools.py` cases exercise the actual runtime dispatch, owned
HTTP upload/download routes, canonical-preview equality, rectangular recovery
across sheets, formula-state preservation, source truncation, stable file IDs,
same-size changes, changes during parsing, no-follow enforcement, cross-account
isolation, text/Word pagination, and unchanged original bytes.

A synthetic provider drives the real Engine and streaming protocol through
`skill → files read → original file link`. The resulting requests and durable
tool messages contain the canonical missing-versus-saved states. It finishes with
one registered file and no component or application-action side effects. This
proves mechanical integration, not that a real model will always choose or
interpret the workflow correctly; the separate opt-in real-model evaluator must
provide that quality evidence.

The complete related run passed **163 tests**:

```sh
uv run pytest tests/test_file_tools.py tests/test_spreadsheet_preview.py \
  tests/test_files.py tests/test_file_revisions.py tests/test_document_preview.py \
  tests/test_skillpacks.py tests/test_app_controls.py -q
```

Doctor confirmed Python 3.14.8 with the GIL disabled after imports. Repository
data checking and `git diff --check` passed. No real model API, real account, user
document, deployment, or commit was used for this slice.

## First real-model follow-up

The first isolated real-model evaluation finished in 15.29 seconds with eight
requests and eight tool calls. Canonical reads and the requested byte-identical
copy succeeded, but the model still registered a redundant component. It did not
explicitly discuss the fixture's missing-cache/hidden-zero/saved-empty cells, so
that semantic outcome remains unverified rather than passing by omission.

Because the model actually received `files list/read` but may not have read the
file skill, those results now include one short `delivery` explanation: return
the existing Markdown file link; the default conversation already supplies output
previews/download controls; create a new component only when new interaction is
needed. It explicitly describes default presentation behavior rather than a
browser render receipt. This adds no component restriction or prompt-prefix
change. A real-runtime regression also reads a file and then successfully
registers a user-requested checklist, preserving the original. The guidance was
then evaluated again rather than treated as proof of improved behavior.

The second evaluation completed in 14.686 seconds with eight requests and nine
tool calls. The model used canonical reads, delivered the byte-identical copy,
created no redundant component, and correctly explained D8's missing result,
D9's hidden zero, and D10's saved empty string. It still mislabeled the date at B7
as D7 and had two command errors, so the overall task is not counted as fully
passing. The command errors require separate diagnostic review.

Runtime cell addresses were added in response to this observed coordinate error.
The rectangular recovery tests now verify every added address across sheets and
pages, including sparse row 7 and a nonzero-column page through Z42, AA42, and
AN42. Removing only `address` recovers the exact canonical cells. This establishes
that coordinates are correct and available; a later real-model evaluation must
verify that the model actually cites them correctly.

## Subsequent real-model observations

The opt-in `scripts/evaluate_file_delivery.py` now creates a disposable local
account, uploads a synthetic workbook through the real API, submits to the real
Engine/provider, and checks the registered output and original bytes. It records
bounded operation metadata, usage and synthetic final prose outside the checkout.
It never turns prose assertions into an automatic semantic pass. The explicit
cell-state variant asks about D8/D9/D10 and, in its latest form, the date's address.

After addresses were added, one run completed in 18.644 seconds, seven model
requests and ten tools without a tool error or extra component. Its formula
explanation was materially better, but it used decorative emoji. Athena's short
default identity now says not to use emoji unless requested or quoting user
content. This affects newly created prompt boundaries only; persisted prefixes
are preserved.

The next run correctly cited B7 as the date and retained the three distinct cell
states, but recovered from a command error. A further instrumented run completed
in 14.354 seconds with seven requests/nine tools, no tool error, no extra
component, byte-identical delivery and no decorative emoji. Manual inspection
confirmed B7, the quotation arithmetic and the missing/hidden-zero/saved-empty
distinctions. This is one successful sample, not proof that prior intermittent
command failures are fixed. Their original output was not retained by the early
evaluator; later receipts classify exception/missing-module signals without
recording command bodies or credentials. The final run had no failure to diagnose.

These local isolated runs and the preceding hosted run use different inputs or
environments and are not a controlled latency benchmark. They demonstrate
recoverable product failures and a usable path; they do not establish a general
task-success percentage or full Excel calculation capability.

Private receipts: `/private/tmp/athena-file-delivery-20261005-{first,second,third,final,diagnostic}.json`.
The names identify the sequence of observations, not a blanket success verdict.

## Optional row-coordinate boundary

Independent review found that an omitted row `r` with an explicit cell `B7` was
being labeled as row 1. Row resolution now uses consistent explicit cell row
numbers when the row attribute is absent; with no references it advances from the
previous resolved row, including sparse rows. Conflicting row/cell coordinates,
duplicates, descending rows, and values outside 1–1,048,576 produce the existing
unsupported-preview fallback rather than fabricated addresses. Original bytes
remain downloadable. Seventeen parser boundary cases and one real runtime/HTTP
case cover the correction, including implicit B7, sparse continuation and mixed
conflicts. The file-related suite now passes 181 tests; doctor again confirms
Python 3.14.8 with the GIL disabled. This does not change the recorded real-model
observations above.
