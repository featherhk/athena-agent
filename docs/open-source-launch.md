# Athena open-source launch

Decision date: 2026-10-10. This is the launch record, not a claim that the
repository, Discord, or external-agent adapter is already public.

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

## Release gates

- [x] Decide license; retain historical and third-party notices.
- [x] Scan tracked paths and all reachable Git blobs for prohibited data and
  known credential patterns (`node scripts/check-repository.mjs --history`).
  This is a pattern scan, not a complete secret/provenance audit.
- [x] Review the accessible Actions logs/artifacts, releases and reachable LFS
  references; see [the audit and its remaining scope](open-source-audit.md).
  Recheck newly created runs before changing visibility.
- [x] Fresh local BYOK start without cloud sign-in; write-only credentials,
  streamed response and reload persistence verified with synthetic fixtures.
- [ ] Installed Mac: owl movement/collapse/current turn; independent file surface.
- [ ] A short effect ends, Stop ends it, and app restart never replays it.
- [ ] Record the exact downloadable build with synthetic files.
- [ ] Enable private security reporting and check public Issues/Discussions links.
- [ ] Verify organization ownership, CI, release URLs and redirects after transfer.

Do not discard history by default. Preserve it if audit allows; if a clean export
is necessary, retain the private development history and original notices.
Never publish runtime data or keys to make a reproduction easier.

## Organization and community

Use a Feather organization for the product family and Athena as the repository
name; check name availability before deciding the final URL. An organization
helps ownership and contributor roles, not automatic credibility. Start on the
free plan unless a required capability demonstrably needs a paid plan. Transfer
only after the release audit and link checks; do not break the installed update
feed during an ownership change.

Discord starts with five channels: `start-here`, `announcements`, `help`,
`showcase`, `contributors`. Keep bug records in Issues and durable design
conclusions in Discussions. Publish an invite only after the server exists and
moderation settings have been checked. No invented members, testimonials or SLA.

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

1. 60–90 seconds: compare two synthetic quotes; put the result beside the user's
   work; add a third file; update the same panel; put away and return.
2. 20 seconds: bounded desktop effect, Stop, restart with no replay.
3. 45 seconds: local BYOK setup, key never visible in output, first task completed.

Only record flows that actually work. Label sped-up waits. Do not splice failures
out to imply one successful uninterrupted run. A video supplements receipts and
restart tests; it does not replace them. Use a clean test profile, no personal
notifications, and verify that its build matches the published artifact.

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
