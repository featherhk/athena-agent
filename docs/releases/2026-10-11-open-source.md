# Organization source release — 2026-10-11

Athena is public at [featherhk/athena-agent](https://github.com/featherhk/athena-agent), under Apache-2.0.
The original private repository remains private and keeps its original history.
The [README](../../README.md#run-locally) is the supported local setup guide.

## History and privacy

Publication began from a separate copy, not a visibility change to the original
repository. All 103 existing commits and their parent topology, authorship,
dates and applicable license notices were preserved. Targeted redactions changed
historical preview-account and credit records in three operations documents,
then operator key-file examples, a fixed host address and machine-specific
artifact paths in two scripts and five acceptance documents. Descendant commit
hashes therefore changed. The sanitized base is `781fb38185b8ecb4593565320a4de80a0ef5d543`.

The final base review covered 1,408 unique file versions and 489 historical paths.
Exact in-memory comparisons with both configured provider keys found zero
matches. Known credential, private-key, unmasked Authorization, JWT, operator
path and deployment-address checks also found no remaining sensitive matches.
Legitimate third-party attribution and explicitly synthetic test data remain.
No user database, cookies, browser profile, raw request recordings or private
collaboration notes were added. No raw key was printed or stored in an audit report.
These checks do not prove the absence of every unknown or encoded secret.

The original private refs and hosting records are not exposed by the new repo.
New changes are maintained through a separate public checkout; do not merge the
original private history back into this branch. Earlier build identifiers refer
to pre-redaction source revisions. Existing DMGs, checksums and update feeds
are unchanged by this source release.

## Verification

- Local clean publication checkout: Python 3.14.8 free-threaded, GIL disabled
  after imports; 1,118 Python tests passed, seven skipped; all 453 Node tests
  passed; TypeScript and production build passed; dependency audit reported zero
  known vulnerabilities.
- [Organization CI run 1](https://github.com/featherhk/athena-agent/actions/runs/38068947593) passed on
  `0b03012e56544fb1133c2bdb97a3d24dae7af2ad`. Job logs were reviewed without
  publishing raw recordings; known sensitive-pattern matches were zero and no
  run artifacts were present. Repository/history checks and strict Git integrity
  checks passed.
- A full clone with no configured credentials succeeded after publication,
  matching the published main branch. The source API was readable anonymously.
- [Build 26 acceptance](2026-10-10-build26.md) includes a clean data directory,
  local BYOK, a real-provider synthetic file task, Stop, and restart without
  replay. That is a development-Mac check, not a claim about all providers or a
  separately provisioned clean machine.
- The sanitized network smoke requires an explicit `ATHENA_TEST_SELF_HOST_URL`;
  missing configuration fails before actions, shell quoting is checked, and
  the host-denial assertion remains mandatory. This cleanup did not rerun the
  production network-isolation smoke.

## Feedback and distribution

[Issues](https://github.com/featherhk/athena-agent/issues) and [Discussions](https://github.com/featherhk/athena-agent/discussions) are enabled.
[Security reports](https://github.com/featherhk/athena-agent/security/advisories/new) are private and require a GitHub sign-in.
Actions defaults to a read-only token and cannot approve pull requests; workflows
from all external fork contributors require maintainer approval. No deployment
or provider keys were added to the public repository.

Source mode uses the user's own Chat Completions connection without a Feather
account. Native Anthropic Messages and Responses protocols are not supported by
that setup form. The [downloadable Mac DMG](https://app.athena.featherhk.com/)
remains the hosted-account Build 26 and requires enabled access with credit.
It is ad-hoc signed, not notarized; there is no prebuilt BYOK installer there.

The [real walkthrough](https://featherhk.com/athena/#demonstration) uses synthetic
data and separate application-window screenshots. Continuous video, Discord,
payment activation, full desktop control and the Stage prototype are not
announced as complete. Announcement drafts remain unsent.
