# Athena open-source launch

Decision date: 2026-10-10. This is the launch record, not a claim that the
repository, Discord, or external-agent adapter is already public.

## Current release status

As of 2026-10-10, `NoW0NDER/Athena` remains a private repository. Apache-2.0,
contribution guidance, local BYOK and the dependency-notice work are committed;
this does not mean the open-source launch is complete. Public visibility and
historical operational-record disclosure still require a final release decision.

Organization setup/transfer and Discord server creation have not been completed.
Discord login is deferred at the user's request; do not keep retrying it or block
the source release on it. There is no verified organization repository URL or
community invite to publish.
Build 25 (`c4cc0fa`) passed installed-app acceptance and is now in the public
macOS preview download/feed. The public DMG was downloaded again, hashed and
mounted for verification; the prior Build 15 remains available. This preview
still lacks Developer ID signing and notarization. It is the hosted-account
distribution, not a public source release.

The [context evolution research](context-evolution-research.md) is a proposal:
single-entry task routing, source-wide retrieval, context manifests and expanded
memory lifecycle are not delivered by that document. The separate Stage
prototype and external-agent integration also remain outside this release's
verified capabilities. Keep their implementation work separate from launch copy.

## Position and scope

Athena is an assistant that can bring useful, persistent work surfaces into the
user's desktop. The differentiator to prove is continuity: add a file to the
same task, update the result in place, put it away, and resume later. A mascot
or an animation alone is not the product.

Keep one repository for the assistant and desktop host. Do not rewrite it into
a generic framework before validating one external-agent integration. The Stage
prototype is currently separate; do not advertise MCP compatibility as shipped.

## License decision

Use Apache-2.0 for this and subsequent self-owned source releases. Its explicit
contributor patent grant and trademark boundaries fit a reusable desktop host.
MIT would also support adoption; this is not an attempt to restrict commercial
use or prevent forks. Existing MIT grants remain valid in their historical
revisions and their notice is preserved. Dependency licenses remain independent.

The current author listing and source checks are useful inventory, not proof of
all rights. Keep provenance for adapted contributions and use DCO sign-offs.
See LICENSE, NOTICE, TRADEMARKS.md and CONTRIBUTING.md.

## Two distributions

| | Local open-source mode | Hosted distribution |
| --- | --- | --- |
| Identity | Internal local owner; no Feather cloud sign-in | Athena account |
| Provider | User-configured key/base/model | Operator-configured gateway |
| Charges | Paid directly to the selected provider | Account balance |
| State | Local external data directory | Mac local or web server, as disclosed |
| Keys | Write-only fixed setup; never model tools | Never customer onboarding |

The local setup is implemented. A fresh-data-directory browser run verified
bootstrap, provider setup, streamed replies and reload persistence using a local
test provider, without a cloud account or paid API request. Existing multi-account
stores are not silently assigned to a new owner. Non-loopback/self-hosted server
deployment keeps authentication. This acceptance does not certify every external
provider or the future public download.

## Source release gates

- [x] Decide license; retain historical and third-party notices.
- [x] Scan tracked paths and all reachable Git blobs for prohibited data and
  known credential patterns (`node scripts/check-repository.mjs --history`).
  This is a pattern scan, not a complete secret/provenance audit.
- [x] Review the accessible Actions logs/artifacts, releases and reachable LFS
  references; see [the audit and its remaining scope](open-source-audit.md).
  Recheck newly created runs before changing visibility.
- [x] Fresh local BYOK start without cloud sign-in; write-only credentials,
  streamed response and reload persistence verified with synthetic fixtures.
- [ ] Resolve historical operational records in the three known document paths
  below; then scan the exact publication candidate and its reachable refs.
- [ ] Reproduce the README from a clean checkout, empty external data directory
  and environment without operator configuration. Record the commit,
  OS/architecture, dependency versions and first-task result; do not describe
  an existing development Mac as an independently tested clean machine.
- [ ] Check final source/download links and distribution labels; provide a
  working private vulnerability-reporting route before inviting contributions.
  Verify the repository's fork-workflow approval setting as part of visibility
  preparation; do not add deployment credentials to pull-request jobs.

Build 25's installed acceptance covers moving/resizing/closing an independent
file surface, restoring it from the current conversation, updating the same
file/component, and restart without replaying the disabled legacy fireworks.
It does **not** establish a shipped fullscreen Stage or a complete new effect
lifecycle. See [the acceptance record](acceptance.md). A reviewed recording is
still missing; it is a promotion deliverable, not a reason to claim source is
unsafe. Organization transfer, Discord, payment activation, and the research
roadmap are not prerequisites for releasing local BYOK source.

### Minimum history treatment

The bounded audit found historical preview-account and credit-operation records
in `docs/cloud-deployment.md`, `docs/distribution.md`, and
`docs/requirements-status.md`. Their current examples are generic. No API key or
private conversation transcript was identified by that review; this is not proof
that none exists. Do not expose real operational records just because a token
scanner passes.

Recommended publication preparation: retain the private original, inspect those
exact historical passages, and replace only the real account/credit details in
an isolated publication copy. Keep code, authorship and license notices intact.
Even a document-only history rewrite changes descendant commit IDs: verify the
resulting tree, branches/tags, provenance and fresh CI before deciding how to
publish it. Do not force-push the shared working repository as a cleanup step.
The final review must also cover visible Actions logs and any newly added refs;
rewriting Git does not itself remove old hosting records.

If a bounded review cannot establish that this history is suitable for public
view, a clean release snapshot in a separate public repository is the fallback.
Retain the original private history and attribution, and state where public
history begins. Do not delete all history by default or advertise a private
repository as already open source. These are recommendations, not authorization
or evidence that a rewrite/publication has happened.

### Clean first-use acceptance

Clone the exact release candidate into a new directory; do not copy `.env`,
`node_modules`, `.venv` or application support directories from the developer
checkout. Use a fresh browser profile and a shell without operator credentials
or hosted configuration. A dedicated local OS test account is preferable when
available, but does not block this bounded check. Follow the
[README's commands](../README.md#run-locally) unchanged. Use a new
`ATHENA_DATA_DIR` outside the checkout and leave `ATHENA_CLOUD_URL` and
`ATHENA_PUBLIC_ORIGIN` unset. Confirm `athena.doctor` reports the GIL disabled.

Accept the flow when local model setup appears without hosted sign-in, one
supported Chat Completions connection completes a small synthetic task, a result
file opens, Stop works during a separate bounded task, and restart restores the
conversation without replaying completed actions. Enter any real key off camera
in the fixed setup form; record provider/model, result and reported cost, never
the key or raw request. Distinguish a mock-stream check from a real-provider
check and a clean data directory from a clean machine. Also verify a hosted
bundle still presents hosted authentication; the current public DMG must not be
described as the no-login BYOK installer.

Never publish runtime data or keys to make a reproduction easier.

## Weekend developer preview: October 10–11

Keep the scope to an inspectable source release and one convincing task. The
download response headers have been fixed and a browser-saved Build 25 was
verified against its checksum. On October 10, finish the explicit-Quit fix, close
the source gates above, and capture the synthetic continuity flow below on the
exact build being offered. If a new build is needed, identify it and repeat the affected
installed checks; do not present Build 25 footage as proof of newer code.

On October 11, publish only after those gates pass. Verify the source can be
read/cloned without maintainer credentials, then use one factual announcement
and invite 5–10 testers with concrete file-comparison tasks. Initially direct
feedback to Issues; enable Discussions for longer design feedback when ready.
Organization and Discord setup can follow actual participation. The
[Chinese and English drafts](launch-announcement-drafts.md) are unsent and must
be filled with verified links before use.

If a source gate remains open, keep the repository private and describe the
available product as a hosted Mac preview. Do not spend the weekend making
unverified launch claims. The first week's useful evidence is a completed task,
an update to the same result, and successful return later; publish exact known
limitations and fix the first reproducible failures before expanding the pitch.

## Organization and community

Use a Feather organization for the product family and Athena as the repository
name; check name availability before deciding the final URL. An organization
helps ownership and contributor roles, not automatic credibility. Start on the
free plan unless a required capability demonstrably needs a paid plan. Transfer
only after the release audit and link checks; do not break the installed update
feed during an ownership change.

Discord is deferred. When it is usable and someone can maintain it, begin with
`start-here`, `announcements`, `help`, `showcase`, and `contributors`. Keep bug
records in Issues and durable design conclusions in Discussions. Publish an
invite only after the server exists and moderation settings have been checked.
No invented members, testimonials or SLA.

Draft welcome:

> Athena is a personal assistant with a workspace that can adapt to the task.
> Start with the installation guide and current limitations. Share a synthetic
> example when asking for help; never post API keys, cookies or personal files.
> For a bug, include the task, build, expected effect and actual effect. Use a
> private route for vulnerabilities. Be constructive and respect other users.

Invite 5–10 people to use it for one week. Record first completed task, successful
return to unfinished work, unwanted focus/occlusion, failures to stop, and repeat
use. Use these results to choose the next release, not star counts.

## Demo acceptance

Prefer an isolated OS account with a plain desktop and only synthetic files/apps
visible. If that is unavailable, record an exact window/region containing only
the synthetic scene, with a plain non-personal background behind transparent
panels. Preflight all visible pixels, including menus and window edges; avoid
capturing the personal desktop in the first place. Do not rely on cropping a
moving panel after capture to conceal unreviewed material. A new Athena
conversation alone is not isolation. Use a fresh browser profile without sync;
no personal account, real credit record, private task history or clipboard
should enter the recording. If hosted mode is shown, use a separate synthetic
demo account rather than a customer account. Review every frame and the audio
before publishing. Store raw footage outside the source tree.

The primary 60–90 second demo uses `quote-a.csv` and `quote-b.csv`, labelled as
fictional fixtures. Quote A: materials 8,000, labor including installation 3,000,
delivery 2,000, total 13,000. Quote B: materials 7,000, labor 4,000, delivery included, installation
missing, subtotal 11,000. Use a single stated currency and no real company names,
contacts, addresses or logos.

| Shot | User action | Observable result |
| --- | --- | --- |
| 0–15 s | Wake Athena; attach both files; ask to compare and list missing items. | Actual task starts; synthetic inputs are readable. |
| 15–40 s | Read and reposition the generated comparison. | A usable independent file surface; the missing installation item is explicit. |
| 40–65 s | Add a synthetic supplement: B's installation is 2,500. Ask to update. | The same panel updates to 13,500, compared with A's 13,000; user-set geometry remains. |
| 65–90 s | Put the panel away, then return to the work. | Original result can be reopened without creating a duplicate. |

Keep off-camera receipts for the unchanged file/component IDs and current file
values. Real model wait times can exceed this edit length; mark any time cuts.
Restart continuity may be a separately labelled follow-up clip. Show real
responses, not a scripted animation posing as execution. A source/BYOK walkthrough
is a separate short clip, recorded only after its clean first-use check; prepare
the connection off camera and identify the mode. Defer the fullscreen fireworks
demo until Stage and its Stop/expiry/restart lifecycle are integrated and tested.

Only record flows that actually work. Label sped-up waits. Do not splice failures
out to imply one successful uninterrupted run. A video supplements receipts and
restart tests; it does not replace them. Use a clean test profile, no personal
notifications, and verify that its build matches the published artifact. The
previous QuickTime attempt failed; no video has been delivered. If continuous
recording is unavailable, use clearly labelled screenshots or a frame-by-frame
walkthrough of the synthetic acceptance run. Do not present that as a continuous
real-time recording, and keep the video item unfinished.

## Primary sources

- [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0.html)
- [DCO 1.1](https://developercertificate.org/)
- [OpenClaw contribution guide](https://github.com/openclaw/openclaw/blob/main/CONTRIBUTING.md)
- [Hermes contribution guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md)
- [GitHub repository transfer](https://docs.github.com/en/repositories/creating-and-managing-repositories/transferring-a-repository)
- [GitHub visibility changes](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)

OpenClaw and Hermes use MIT. Their clear help/bug/plugin/security contribution
paths are observable practices worth adapting; they do not establish the cause
of either project's popularity.
