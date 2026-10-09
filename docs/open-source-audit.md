# Open-source release audit

Reviewed 2026-10-10, against the current working tree, the installed macOS Build 22, and the private `NoW0NDER/Athena` repository. This is a bounded release audit, not a claim that all secrets, copyright issues, vulnerabilities, or user data have been ruled out. No repository visibility, repository ownership, historical commits, remote logs, or releases were changed.

## Release decision

Keep the repository private until the remaining public-source gates below are closed. The known native notice collection gaps described during the initial inspection have now been addressed in `LICENSES/third-party`; verify their inclusion in the final DMG before publication. There is no finding that warrants deleting all Git history.

| Scope | Finding | Required action |
| --- | --- | --- |
| Public source | Deployment documents used an actual preview account and account-credit record. This is operational data, not a discovered credential. | Replace current examples with generic placeholders; review the corresponding historical passages before changing visibility. Keep any real operational evidence outside public Git. |
| Web / DMG distribution | Build 22 has no frontend dependency notice file in `app/dist`, and its minified JS has no retained license comments. React and the Markdown dependency chain contain third-party copyright and permission notices. Athena's own Apache/MIT notices do not substitute for them. | Generate and ship production dependency license/notice texts with each frontend build; verify they remain in the packaged app and served web artifact. |
| DMG distribution | The installed Build 22 initially lacked the native-browser dependency and standalone Python library notice inventory. Those known gaps are now addressed in `LICENSES/third-party`, with exact sources/checksums and three explicitly identified SPDX-text supplements. | Verify that the final packaged versions match the inventory and the complete directory is present in the mounted DMG. Do not describe the already installed Build 22 as retroactively repaired. |
| Public source / assets | Only one tracked binary visual asset was found, the iOS app icon. Re-running `desktop/macos/Icon.swift` with `--ios` reproduced it byte for byte. No downloaded font files, stock photos, or vendored source trees were found in the current tracked inventory. | Retain the generator and this provenance result; no additional asset clearance blocker was identified. This audit does not establish copyright ownership merely from the absence of a third-party label. |

## Repairs made in this working tree

`scripts/generate-third-party-notices.mjs` now runs after the normal frontend build. It reads only the package lock and installed production dependency metadata/texts, preserves original LICENSE/NOTICE/COPYRIGHT/AUTHORS files, and writes `dist/THIRD_PARTY_NOTICES.txt`. A missing package, wrong installed version, absent license metadata/text, or escaped notice symlink fails the build; failure removes a stale generated notice file. Output is deterministic and explicitly excludes any claim of a complete Python/Rust/native inventory. macOS packaging now regenerates these notices even with `--skip-web-build`; it requires the previous web artifact's notice lock hash to match the current lock before doing so. Assets from another lock must be rebuilt. Packaging copies `dist`, so subsequent builds include this file; the already installed Build 22 was not modified or retroactively verified.

The full `npm run check` passed: 1,091 Python tests, 7 explicit skips, 442 Node tests, TypeScript and production build. After adding the packaging guard, its 9 targeted bundle tests, the 5 notice tests, and the production build passed again; the full suite was not repeated. The current deployment/requirements documents now use a generic preview account and illustrative credit operation. Historical commits were left intact. The icon's reproduced SHA-256 is `21732f7fb04677779d5167292f05a21ca0343c320fe98ac86456f7bea9e224cc`.

Native collection subsequently completed for the known packaged versions:

- Python's official 20261001 free-threaded 3.14.8 Apple Silicon full archive was checksum-verified against GitHub release metadata. All 16 referenced license paths are present in the new inventory. Two texts absent from the archive, zlib-ng 2.2.4 and zstd 1.5.7, were recovered from the exact official source archives and checksums named in that release's downloads metadata.
- All 337 registry archives in agent-browser v0.38.2's Cargo.lock were checksum-verified. Original notices were retained; 11 missing crate-level texts were recovered from the precise repository commits in their `.cargo_vcs_info.json`. `r-efi`'s complete MIT text was already in `AUTHORS`. Three upstream MIT declarations without standalone texts have clearly labelled canonical SPDX supplements, with no invented copyright holders.
- Both macOS browser binaries match the official release SHA-256 values. Their Rust compiler source fingerprint matches Rust 1.99.0; its project licenses and complete upstream `COPYRIGHT-library.html` were retained from the verified official compiler archive. Manifest/file hashes were independently recomputed after writing.

See [the native notice scope and provenance](../LICENSES/third-party/README.md). The existing packager copies the full `LICENSES` directory. This records actual texts and their provenance, not a complete binary SBOM or a claim that every conservatively included package is linked. The final candidate still needs mounted-DMG verification.

## GitHub data exposure

Authenticated read-only connector access confirmed `NoW0NDER/Athena` is private. The repository run collection returned **49 runs**, all `push` runs of `.github/workflows/check.yml`, with one attempt and one job each. All 49 job logs were accessible. All 49 run artifact lists were empty. The GitHub Release list was empty. The newest inspected run was `37956839041`, commit `96fed0f02f9cdbaa0a66247025f946d4afc71f9b`; recheck after further pushes.

The 49 logs totalled 2,711,102 decoded characters. A non-printing scan found no matches for the repository checker's known GitHub/API token formats, private-key headers, the operator home path, the two known preview account names, the known test password, or unmasked Basic/Bearer Authorization headers. Raw log contents were not added to the repository. This is pattern coverage, not a semantic proof that every line is harmless. Unknown credential formats, redacted values, and unrelated personal data remain outside that claim.

Two subsequent pushes were independently checked after completion:

| Commit / run | CI result | Incremental log review |
| --- | --- | --- |
| `afbe7d5` / [run 50](https://github.com/NoW0NDER/Athena/actions/runs/37968854144) | 1,105 Python passed, 7 skipped; 449 Node passed; build passed | 94,588 decoded characters, zero matches for the same patterns, zero artifacts |
| `c4cc0fa` / [run 51](https://github.com/NoW0NDER/Athena/actions/runs/37970122629) | 1,114 Python passed, 7 skipped; 449 Node passed; build passed | 94,955 decoded characters, zero matches for the same patterns, zero artifacts |

Both runs reported Python 3.14.8 with the GIL disabled, passed the 480-file and reachable-history scan, and generated frontend dependency notices. Run 51 completed at 2026-10-09 18:03:36 UTC. The authenticated repository metadata still reported private visibility; the GitHub Release list was still empty. These CI results do not constitute installed-app acceptance or change the public download. Fork-workflow administration settings, the historical operational-record disclosure decision, organization transfer and public community setup remain outside the completed checks.

The repository-wide artifact endpoint was unsupported by the connector, so artifact checks used the artifact endpoint of **every returned run**. The job/artifact wrappers are first-page wrappers, but each inspected run returned only one job and zero artifacts; no page boundary was reached. No artifact binary existed to inspect. Deleted or expired logs/artifacts cannot be recovered by this review. No GitHub Releases means no GitHub release attachments to inspect; independently hosted downloads are not included in that conclusion.

GitHub explicitly states that [Actions history and logs become visible when a repository becomes public](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility). Preserve these review results, then recheck the final candidate rather than deleting clean logs as a blanket precaution.

## Git and document scope

Root's `node scripts/check-repository.mjs --history` passed the tracked files and all locally reachable Git objects for its known credential patterns and prohibited runtime paths. The checker detects common `sk-`/GitHub tokens and private-key headers; it is not a universal credential detector. The local result is retained outside the checkout in `/tmp/athena-oss-history-scan.log`.

This supplement found **no Git LFS pointer blobs** in locally reachable history; `.gitattributes` contains only text/line-ending rules. The remote branch list contained only `main`, matching local `origin/main`, and the remote matching-tags collection was empty. That does not inspect unreferenced remote LFS objects or server-retained unreachable objects. Remote LFS object administration is unavailable through the connector. Hidden/unadvertised refs remain outside this coverage.

The current tracked Markdown review found no known test password, private IP literal, operator home path, email address, phone number, or UUID-shaped conversation identifiers. The actual preview-account references were in `docs/cloud-deployment.md` (account setup/credit and acceptance), `docs/distribution.md` (account boundary), and `docs/requirements-status.md` (historical grant receipt). They should be replaced with neutral examples. A current-file edit does **not** redact an old commit; inspect those exact historical passages and make a targeted disclosure/redaction decision before opening the repository. No API key or copied private conversation transcript was identified in this review.

The current untracked `docs/collab/` board contains local workspace paths and internal working context. It is a collaboration channel, not automatically approved public documentation. Review/sanitize any such files before adding them to a public release. Do not commit raw audit logs, account records, browser profiles, screenshots of customer conversations, or runtime databases as evidence.

## Third-party inventory

- The frontend lockfile's installed production package entries carry MIT, ISC, or Apache-2.0 metadata. Preserve the original copyright and license texts, including the ISC structured-clone dependency; a table of SPDX names alone is insufficient. Include a conservative superset of production packages rather than silently omit uncertain bundled dependencies.
- Build 22 contains 26 Python distribution metadata directories. Their license files are retained, including `certifi`'s MPL-2.0, `typing_extensions`' PSF-2.0, `opentelemetry-api`'s Apache-2.0, and MIT/BSD packages. Retaining these files is positive evidence, not a complete audit of code statically linked into wheels. Python sources and `certifi` data are present in the installation; release notices should identify their locations and sources accurately.
- The bundled interpreter is from a portable standalone Python distribution, build marker `20261001`. The install-only tree lacks `PYTHON.json`. Upstream [distribution documentation](https://github.com/astral-sh/python-build-standalone/blob/main/docs/distributions.rst) distinguishes the full archive's licensing/build metadata from install-only archives; its [licensing guidance](https://github.com/astral-sh/python-build-standalone/blob/main/docs/running.rst#licensing) calls out dependency-specific terms. The new notice inventory uses the full metadata and exact source fallbacks rather than treating CPython's `LICENSE.txt` as sufficient for all native libraries.
- `agent-browser` is locked to `0.38.2`; its dedicated runtime directory contains its native binary, `LICENSE`, and version. Its [versioned Cargo manifest](https://github.com/vercel-labs/agent-browser/blob/v0.38.2/cli/Cargo.toml) includes many Rust dependencies. Their collected attribution is now distributed through `LICENSES/third-party`; the top-level Apache text alone was insufficient evidence.
- Current CSS uses installed-system font fallback names. It does not bundle the named commercial/system font files. Do not add downloadable font files without separately recording their redistribution terms.
- Athena's new Apache-2.0 license and legacy MIT notice govern Athena-owned work. [Apache's redistribution requirements](https://www.apache.org/licenses/LICENSE-2.0.html#redistribution) retain applicable third-party notices; changing Athena's license cannot relicense third-party material.

## Workflow review

The current workflow uses `push` and `pull_request`, an ephemeral GitHub-hosted macOS runner, and `contents: read`. It has no `pull_request_target`, deployment step, self-hosted runner, explicit repository secret reference, or artifact upload. `uv sync --locked`, `npm ci`, history scanning, and the normal checks are appropriate starting controls.

The initially mutable major-tag action references were subsequently pinned to their reviewed full commit SHAs in commit `32d5ec5`; checkout now sets `persist-credentials: false`. Confirm fork-workflow approval settings in GitHub's administration UI/API; those repository settings were not available in this connector audit and remain unverified. Do not add privileged deployment, signing, or payment secrets to the untrusted PR job.

## Final acceptance after repairs

1. Run the repository/history scan again at the exact release commit; document any deliberate historical operational references.
2. Generate frontend notices from the locked, installed production packages and fail the build if a required package/version/license text is missing. Inspect the output without reading runtime data.
3. Verify the served website and final mounted DMG both contain the applicable notices; match the Python/native-browser versions and hashes to the collected inventory. Do not equate notice collection with complete executable or supply-chain verification.
4. Re-query all new Actions runs, their logs/artifacts and Releases created since this snapshot. Do not infer cleanliness from a green build badge.
5. Keep any remaining unknown scope explicit. No blanket history rewrite, deletion, repository transfer, or public release is authorized by this audit report itself.
