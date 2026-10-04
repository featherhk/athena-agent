# Interaction and voice acceptance — 2026-10-04

Source `38fb0df6b5479272513336b49ee5ddf2eb90cd54` adds bounded waits for real frontend action receipts, opt-in quoted-text geometry for registered components, and recoverable recognized speech after a failed submission. A response without an acceptance receipt remains uncertain and is never automatically resent. Independent recognition and synthesis profiles keep provider credentials on the operator side.

## Verification

The complete check passed **701 Python tests and 172 frontend tests**, TypeScript and the production build. Seven opt-in tests were skipped. Python 3.14.8T retained the GIL disabled after imports. iOS simulator compilation and the download-queue tests passed; signed device installation and real microphone acceptance are still outstanding.

Local browser acceptance covered quoted text spanning Markdown nodes, mobile wrapping, scrolling and clipping, fixed-menu precedence, conversation ownership, registration updates without reload, and Reset retaining chats and files. A real browser action returned an applied durable receipt after 0.254 seconds in one trial. This is an observed example, not a latency percentile. Voice failure acceptance used synthetic audio: recognized text survived an uncertain send while the typed draft stayed unchanged, switching conversations hid the other conversation's recovery text, and sign-out cleared it.

## Actual audio provider probe

One Qwen3-TTS-Flash synthesis and three Qwen3-ASR-Flash recognitions succeeded with synthetic speech. The provider returned 39 billable TTS characters and 4, 13 and 22 billable ASR seconds. At the published Beijing rates, the combined amount is **CNY 0.01170**. The billing console was still processing and had no detailed invoice rows. This amount is measured usage priced at the official tariff, not a verified account debit. See [the probe table and limits](../voice.md#live-low-cost-provider-probe-2026-10-04).

The probe also exposed a streaming WAV length placeholder in a fully downloaded TTS response. The adapter now validates and normalizes the observed PCM header without changing the audio samples. This is still a buffered HTTP pipeline; no claim of streaming or production-grade full-duplex calls follows from these tests.

## Mac login repair

The reported login failure came from a locally launched build 1 that still pointed at the retired `app.featherhk.com` authentication address. Its local sidecar was healthy. Installing the already published build 9 with the current `chat.athena.featherhk.com` endpoint restored real-account native login. The installed bundle was taken from the published DMG with SHA-256 `2a1bbfd70aa6fc31d52c6d370c122d95ae2e667ae30c457ff1124d6abd1d2428`.

The existing data directory and unsent draft were retained. Subsequent user-initiated local work executed successfully. This verifies login and a local tool task on this Mac; it does not certify other devices or microphone behavior. The old checkout bundle remains an obsolete development artifact.

## Publication boundary

The new source is pushed to the private repository. It has not yet replaced the hosted web/gateway runtime or the published build 9 DMG. The provider probe does not by itself enable account-metered cloud voice. Production audio still requires a reviewed preview-credit tariff, a gateway release and authenticated ledger acceptance. Avoid restarting services while user work is active.
