# Bundled native dependency notices

Collected on 2026-10-10 for the macOS preview. The application builder copies this entire directory into `Athena.app/Contents/Resources/LICENSES/third-party`. Python wheels also retain their own installed `.dist-info` license files; JavaScript package notices are generated separately in `app/dist/THIRD_PARTY_NOTICES.txt`.

These are original third-party legal texts and attribution, not Athena runtime data. Their authors' names and public upstream contact details are intentional attribution. Athena's Apache-2.0 license does not replace their terms.

## Python 3.14.8, standalone build 20261001, Apple Silicon

`python-3.14.8-20261001-aarch64-apple-darwin/manifest.json` records the official full archive URL, its verified SHA-256, build options and per-file hashes. All 16 license paths referenced in that archive's `PYTHON.json` are covered. This is a conservative set: the metadata lists alternative linked-library variants as well as actual target libraries.

Fourteen texts come directly from the checksum-verified full archive. That archive omitted the referenced `LICENSE.zlib-ng.txt` and `LICENSE.zstd.txt`. Their exact versions, source URLs and checksums were taken from the same release's `pythonbuild/downloads.json`; the original legal texts were extracted from those checksum-verified official source archives. No guessed current-version text was substituted.

The local managed interpreter reports Python 3.14.8, the free-threaded ABI, and build marker 20261001. Full-archive and locally managed executable hashes differ, so this is version/build-source correspondence, not a bit-for-bit binary reproducibility assertion. Stripping, signing and relocation can change those hashes. The manifest does not describe a different Python release or an Intel interpreter; collect the corresponding notices before distributing either.

## agent-browser 0.38.2 and Rust

`agent-browser-0.38.2/manifest.json` identifies the exact upstream Cargo.lock and its SHA-256. All **337 registry package archives** were verified against the checksums in that lock. The notice set intentionally includes development and other-platform packages; it is a conservative superset of an individual binary, not a claim that every package is linked.

Original license/notice/attribution files from the crate archives are in `THIRD_PARTY_NOTICES.txt`. Eleven crates omitted their license files; their package metadata and `.cargo_vcs_info.json` identify the repository and exact commit used for the supplemental original texts. `r-efi`'s `AUTHORS` already contains its full MIT grant and copyright notices; MIT is selected from its upstream alternative licenses.

Three crates (`fax`, `fax_derive`, `simd_helpers`) contain explicit MIT SPDX declarations but no standalone license text or copyright notice in their published archives or exact upstream commits. Their declared author attribution is retained, with unmodified standard MIT text from a pinned SPDX license-data commit. The manifest clearly distinguishes this supplement from an upstream-authored license file. No copyright owner or year has been invented or filled into the SPDX template.

Both packaged macOS agent-browser binaries match the SHA-256 values in the official v0.38.2 release metadata. Their embedded Rust source path identifies commit `b940084d7eb6a299eb4bfeb8e34901bc051e7ac4`, matching Rust 1.99.0 in the official release channel manifest. The Rust project license texts and `COPYRIGHT-library.html` are retained. The latter was extracted from the checksum-verified official Rust compiler archive and covers the standard library's own attribution inventory. The browser's top-level Apache license is also copied separately by the existing packager.

The original source archives linked in these manifests and notices provide the corresponding source code under their respective licenses. No dependency source was executed during collection. Archive URLs and checksums enable rechecking without committing entire dependency source trees.

## Scope and maintenance

This directory is a versioned notice inventory, not a security audit, legal warranty, or complete executable SBOM. When a packaged interpreter, browser binary or dependency lock changes, review and update the corresponding inventory; do not relabel old notices as covering a new version. Preserve the original texts, update hashes, and verify the final mounted distribution contains this directory before publication. The final DMG, external download server and fresh-machine behavior still require release acceptance.
