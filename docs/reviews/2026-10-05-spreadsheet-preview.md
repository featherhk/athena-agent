# XLSX preview semantics — 2026-10-05

## User outcome and previous failure

A quotation workbook previously displayed a currency amount `1234.5`, an 8% rate
as `0.08`, and a date as the Excel serial `46299`. Styles and the workbook's date
system were ignored. Missing shared-formula results looked like ordinary empty
cells, and a legitimate cached empty string could be displayed as a formula.
These were meaning errors in an otherwise successful file preview.

`backend/athena/spreadsheet_preview.py` now separates stored values, supported
display formatting, and formula-cache state. `files.py` delegates XLSX parsing to
that module without changing ownership checks, revision validation, file access,
download, or storage. Previewing never rewrites the source bytes.

## Additive response contract

Each sheet retains `rows: string[][]`, `row_numbers`, `name`, and `truncated`.
`rows` contains display values, and the aligned `cells` matrix adds:

- `raw`, `display`, and `kind` (`text`, `number`, `boolean`, `date`, `error`, or
  `formula`), including explicit empty cells for sparse columns.
- Optional `number_format` and `format_status` (`formatted`, `raw`, or
  `unsupported`). Unknown formats preserve their stored value with an explicit
  unsupported status; they never guess a currency or locale.
- Formula cells have `formula_status: saved | missing`, plus `formula` when the
  workbook stores its text. A missing cache displays `=formula` if known, otherwise
  an empty string; consumers use the metadata to indicate “not calculated.” A
  saved empty string (`t="str"` and an existing empty `v` element) stays saved.

If a formula is found in an inspected sheet, the top-level response contains
`formula_results: not_recalculated`. The workbook's `calcPr` adds
`calculation_warning: true` when it specifies manual calculation,
`fullCalcOnLoad`, or `forceFullCalc`. A saved cache is not a claim of freshness or
correctness. No formula engine, external link, macro, or embedded executable runs.
For example, a cached `SUM(1, 2)` result of `9999` remains `9999`, with its formula
and the unrecalculated warning visible to consumers.

## Deliberate formatting subset

Supported numeric formats include common decimal/grouped numbers, mandatory and
optional trailing decimals (up to 10 places), leading zeros, percentages, explicit
currency symbols or currency text, quoted/escaped units, common color modifiers,
and positive/negative/zero sections. Color and column-width fill/spacing are not
reproduced. Conditional formats in **any** section are unsupported: conditions
replace ordinary positive/negative selection, so selecting a seemingly simple
section first would produce false values or lose a sign.

Supported calendar/time formats are normalized to unambiguous ISO-like dates and
24-hour times. This is a semantic preview, not an exact Excel visual replica;
partial dates such as `d-mmm` become a full date. Both 1900 and 1904 date systems
are honored. The fictional 1900-02-29 (serial 60), negative calendar serials, and
out-of-range dates remain explicitly unsupported. Nonseparator date literals such
as `UTC` or `days` are unsupported instead of being silently dropped. Hidden
whole seconds do not round the minute: `23:59:31` in `h:mm` remains `23:59`.
If fractional-second rounding would cross midnight, the value is conservatively
unsupported rather than changing its saved calendar day.

Scientific/fraction formats, conditional or elapsed-time modifiers, scaling
commas, unknown locale-dependent built-ins, and ambiguous style inheritance are
conservatively unsupported. An empty `xf` defaults to General. Explicit
`applyNumberFormat=false` and invalid inherited style references do not guess
whether a parent or direct format applies. No cross-locale currency assumption or
full OOXML style/rendering implementation is claimed.

The design follows Microsoft's documented distinction between formulas and their
[saved results](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/working-with-formulas)
and the two [Excel date systems](https://support.microsoft.com/en-us/excel/date-systems-in-excel).

## Bounded parsing and recovery

The existing six-sheet, 200-row-per-sheet, 40-column, and 2,000-character field
limits remain. The 200,000-character preview budget now includes serialized cell
metadata, formula text, format codes, JSON escaping, and duplicate display values
in `rows`. Format codes are bounded to 255 characters and sheet names to 256;
clipping is marked. Full original bytes remain downloadable.

The parser reuses the bounded OOXML XML reader: at most 20 MiB compressed input,
32 MiB aggregate uncompressed entries, 2,048 ZIP entries, a bounded central
directory, and no duplicate entries. Each parsed XML member is limited to 4 MiB,
60,000 nodes, and depth 64. The prior XLSX member-size limit was 8 MiB; oversized
workbooks now have an unsupported preview while their originals remain intact.
Parser-level DTD rejection covers UTF-16 declarations as well as UTF-8. Unsupported
ZIP compression is a recoverable unsupported preview, not a server error.

## Evidence

`tests/test_spreadsheet_preview.py` contains 62 passing cases using synthetic OOXML
archives and the real upload/preview/download/auth routes. They cover quotation
currency/percentage/unit semantics, date epochs, midnight/hidden-second behavior,
conditional sections, cached/missing/shared/empty/error formulas, sparse alignment,
all preview limits including metadata, unchanged downloads, ownership, hostile
XML, and unsupported archive compression. No real account, document, or provider
request is used.

The complete file-related run passed **107 tests**:

```sh
uv run pytest tests/test_spreadsheet_preview.py tests/test_files.py \
  tests/test_document_preview.py tests/test_file_revisions.py -q
```

`uv run --directory backend python -m athena.doctor` confirmed Python 3.14.8,
`free_threaded_build=true`, and `gil_enabled=false`. Repository data checking and
`git diff --check` passed. The independent reviewer reproduced the conditional
format and hidden-second failures, verified the fixes, and reran the 60 new cases.

The default preview and custom-surface SDK consume the same metadata. A formula
or unsupported-format cell has an optional inline disclosure for the stored
value, formula and number format. Missing results, genuinely empty caches, and
nonempty values hidden by number formatting have distinct labels in all nine
locales. Calculation/unsupported-format notices remain visible, with the
unchanged download alongside the preview. No separate workspace is opened.

Unchanged worksheets are memoized: a real React DOM test renders 200 rows, then
rerenders the surrounding component 20 times without walking those cells again
or refetching the file. Additional DOM tests cover saved/missing/empty/hidden
values, inert strings, sheet changes, the legacy response shape and SDK metadata.

`node scripts/check-surface-webkit.mjs --preview` passed 17 checks using the actual
FilePreview, locale provider and CSS in a disposable WKWebView. Desktop, 390 × 844
and Arabic RTL screenshots were inspected; formula details, download, worksheet
switching and local scrolling of a wide table remain usable without whole-page
overflow. This is a synthetic component acceptance fixture, not a real account,
physical iPhone or model-generated workbook. The final receipt/screenshots are
outside the checkout under `athena-spreadsheet-webkit-qasz1u` in the system temp
directory.

The full `npm run check` passed 947 Python tests (7 opt-in tests skipped), 332
frontend tests, TypeScript and the production build. After the final fractional
midnight regression fix, all 107 file-related tests passed again. These counts
overlap the focused runs above. This evidence does not assert a full Excel renderer, formula
recalculation, or validation of a user's actual workbook. Publication is recorded
separately; a source change alone does not update an installed desktop package.
