# Athena open-source launch

License decision: 2026-10-10. Organization source publication: 2026-10-11.
See the [publication record](releases/2026-10-11-open-source.md) for verified scope.

## Current release status

The source is public at [featherhk/athena-agent](https://github.com/featherhk/athena-agent) under Apache-2.0.
The original private repository is retained. Public history comes from an
isolated copy with operational account details, host addresses and machine
paths redacted across old commits; the private repository was not made public.
Anonymous cloning, the organization CI, Issues, Discussions and private
vulnerability reporting have been verified.

Local source mode uses the user's own Chat Completions model connection without
a Feather account. The [Mac download](https://app.athena.featherhk.com/) remains
[Build 26](releases/2026-10-10-build26.md), the hosted-account distribution. It
requires an enabled account with credit and is ad-hoc signed, not notarized.
There is no prebuilt BYOK installer on that page.

Discord login remains deferred. Announcement drafts have not been sent;
payment collection and external-agent integration are not part of this release.

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

- [x] Apache-2.0, contribution guidance, historical MIT and third-party notices.
- [x] All reachable publication history reviewed; known operational records
  redacted; exact comparisons with the two configured provider keys found no
  matches. See the bounded [publication record](releases/2026-10-11-open-source.md).
- [x] Repository/history checks, strict Git integrity checks, Python 3.14T
  doctor, 1,118 Python tests and 453 Node tests, TypeScript and production build.
- [x] Clean checkout and empty external data directory: local BYOK setup,
  real-provider synthetic file task, Stop and restart without replay. This was
  verified on a development Mac, not an independently provisioned clean machine.
- [x] Public source cloned without credentials; source/download labels match
  their actual distributions. Issues, Discussions and private reports work.
- [x] Read-only workflow token, no PR review approval by Actions, and approval
  required for all external contributors' fork workflows. No deployment keys
  were added to the public repository.

The [original audit](open-source-audit.md) remains a dated snapshot. The public
repository has fresh Actions history; original private logs are not copied.

Build 25's installed acceptance covers moving/resizing/closing an independent
file surface, restoring it from the current conversation, updating the same
file/component, and restart without replaying the disabled legacy fireworks.
It does **not** establish a shipped fullscreen Stage or a complete new effect
lifecycle. See [the acceptance record](acceptance.md). A reviewed recording is
still missing; it is a promotion deliverable, not a reason to claim source is
unsafe. Organization transfer, Discord, payment activation, and the research
roadmap are not prerequisites for releasing local BYOK source.

### Completed history treatment

An isolated publication copy preserves all 103 existing commits, their
parent relationships, authorship, dates and license notices. The three known
operations documents were redacted throughout their history. A second pass
removed operator-specific references in two scripts and five acceptance
records. Changed commits and their descendants have new hashes.

The execution smoke now requires `ATHENA_TEST_SELF_HOST_URL` instead of assuming
one operator's host; the network-denial check is still mandatory. Other changed
current files contain documentation only. Legitimate third-party names and
synthetic test fixtures are preserved. Original private history has not been rewritten.

The public branch is maintained in a separate checkout. Never merge or force-push
unclean private history into it. Transfer reviewed changes as patches or individual
commits, then scan the reachable history again. Build identifiers in older
acceptance documents refer to their original pre-redaction source revisions;
released binaries and checksums have not been changed by this source publication.

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

Source publication is complete. The [real Build 26 walkthrough](https://featherhk.com/athena/#demonstration)
is a reviewed sequence of synthetic application-window screenshots, not a
continuous video. The [Chinese and English announcement drafts](launch-announcement-drafts.md)
now have verified source and feedback links but remain unsent.

Invite 5–10 testers around concrete file-comparison tasks. Record whether they
can finish a first task, update the same result and resume later without losing
work. Use [Issues](https://github.com/featherhk/athena-agent/issues) for reproducible failures and
[Discussions](https://github.com/featherhk/athena-agent/discussions) for design feedback. Fix observed failures
before expanding the pitch. Stage, new memory architecture and payment activation
have separate acceptance requirements; source publication does not deliver them.

## Organization and community

The repository belongs to the [Feather AI organization](https://github.com/featherhk).
Its public name is `featherhk/athena-agent`; the prior private repository remains
separate. Issues and Discussions are enabled, and sensitive reports use
[private vulnerability reporting](https://github.com/featherhk/athena-agent/security/advisories/new). Keep the installed update
feed independent of source ownership and preserve historical download URLs.

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

The [published Build 26 walkthrough](https://featherhk.com/athena/#demonstration)
uses real application-window screenshots captured as separate steps, not a
continuous video. Its lighting-cost follow-up updates the original file and
panel to quote A 13,200 and quote B 13,500, difference 300, retaining the saved
position and size. All quote data is synthetic. The installation-cost scenario
below is the earlier Build 25 acceptance path; do not conflate its 13,000/13,500
totals with the published Build 26 sequence.

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
