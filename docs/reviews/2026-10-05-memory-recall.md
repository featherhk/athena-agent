# Task-aware bounded memory recall

The prior automatic path selected only the latest 30 active entries, without the
current task. A policy could not recover an older entry because it never received
it. More subtly, the final snapshot filtered policy selections against another
latest-30 query: an entry selected before a slow policy finished could disappear
after unrelated writes.

## Implemented behavior

`Engine.prepare_memory` reads the admitted user input and asks `recall_candidates`
for a bounded union, inside the SQLite owner's worker:

- Up to 32 current active entries matching task terms, ranked by distinct matching
  terms, then session/project/global specificity and recency.
- Up to 6 recent entries from each visible scope, and 30 recent entries overall.
- Deduplicated, at most 80 entries and 64,000 serialized candidate characters.
  Whole entries are retained; originals are never clipped or rewritten.

Task text is limited to 4,000 characters (head and tail). Lexical extraction uses
at most 32 distinct words or Han bigrams, with a small stop-word list. SQLite scans
only scope-visible current revisions for matches; no model calls, vector service,
schema migration or new tools are required. Query results and policy inputs are
bounded; database scan cost still grows with the stored memory population.

Policy input retains `default` and `candidates`, adding `task` and `retrieval`
metadata. Candidates include `recall_sources`, `matched_terms`, and a score for
task matches. A policy still returns an ordered subset of candidate IDs. Duplicate,
foreign and unknown IDs fail existing validation and fall back to the safe union.
The provider sees the final memory note, not this retrieval payload.

The final write resolves selected IDs against visible current revisions without
the latest-30 restriction. Concurrent corrections use the latest version;
concurrently forgotten selections contribute empty withdrawal receipts, ahead of
unrelated recent withdrawals. Injection keeps the existing 8,000-character whole
entry budget and explicit retrieval hints. Preflight will not rewrite a run that
already has a persisted request, including if a request appeared while a policy
was running. The stable prefix and earlier admitted notes remain frozen.

The provisional note made when a message is queued remains compatible. Task-aware
recall replaces it before the first provider request; resumed runs retain their
already-admitted snapshot.

## Verified effects

`tests/test_memory_recall.py` uses synthetic account data and `httpx.MockTransport`
to capture the actual streamed provider request through `Engine.submit`. It does
not spend provider credit or read existing users' memories.

- An old Shanghai airport preference and the corrected 5,000-yuan budget survive
  110 newer unrelated entries; the obsolete 9,000-yuan budget is absent from the
  new turn's note, which is persisted in the actual request.
- Long input and 110 large entries respect both candidate and note budgets, while
  full originals remain recoverable. Scope anchors remain eligible without a
  lexical match.
- A slow policy receives task/provenance. Corrections and withdrawals during that
  wait take effect even when the entries fall beyond recent-30 windows.
- A fork recalls its independent session-memory snapshot and shared project/global
  entries, without another session's private entries or another account's data.
- Invalid policy selections safely fall back. An existing default-only selector
  runs in a real isolated process on macOS and still admits an old task match.
- Later recalls and repeated preflight cannot rewrite historical requests, notes,
  or the stable prefix. A mismatched session/run cannot rewrite another input.

Validation: 61 tests passed across memory, task recall, policy consumers, policy
isolation, actual request context budgets, and requirement regressions. The new
file contributes 11 tests (including parameterized cases). `athena.doctor`
confirmed Python 3.14.8 with the GIL disabled after imports; repository data and
whitespace checks passed.

## Limits

This is lexical retrieval, not universal semantic memory. It does not infer
synonyms, translate queries, resolve pronouns such as “the same as last time,” or
automatically merge contradictory separately-created entries. Han bigrams improve
Chinese literal matching; other scripts rely on literal word runs, and SQLite's
built-in lowercase matching is not a complete Unicode case-folding tokenizer.
Candidates and injections can still omit relevant entries when their budgets are
exhausted; explicit paginated memory search remains available. A changed memory
does not retroactively alter historical context or a request already sent.
