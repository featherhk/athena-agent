# macOS interaction: shared-client rendering

The Mac app embeds the shared React client. Source review identified three avoidable costs in that client, without assuming they explain every reported interaction problem:

- Every composer update and streamed message rendered all historical Markdown again. `MessageBody` and `MarkdownContent` now memoize by their actual props. A changing answer, reasoning block or text effect still updates immediately; there is no delayed text buffer or token sampling.
- An expanded completed activity refreshed on every later session cursor. It now stops observing unrelated revisions after the run settles, while a final status transition still fetches the final trace. Pagination, manual retry and online recovery remain active.
- The host measured floating-panel geometry after DOM changes even when no floating component existed. That observer now starts only while visible floating components require bounds, and disconnects when the last one disappears. Widget-specific text/layout observation is unchanged.

## Evidence

Six regression tests use real React DOM and the real Markdown parser inside an isolated Happy DOM document. They cover unchanged history, equivalent snapshot hydration, focused controlled input, every streamed update and its final tail, text effects and reset, localized reasoning labels, owned-image rules, completed-trace pagination/reconnect, final status fetching, and surface-observer teardown/reactivation. TypeScript and the production bundle also pass.

The test-only dependency is pinned to `happy-dom` 20.14.5. The npm registry lists Node >=20 compatibility; the local run used Node 25.5.0. `npm audit` reported no vulnerabilities after installation. No real account, application endpoint, browser profile or model API was used.

A five-trial synthetic benchmark rendered 60 historical Markdown messages and then committed 20 parent updates. Its baseline uses the same component with the new memo boundaries disabled, reproducing the prior parsing path. Initial mount time is excluded from the elapsed figures below.

| Workload | Before: parser calls during updates | After | Before: median total update time | After |
|---|---:|---:|---:|---:|
| Controlled draft updates | 1,200 | 0 | 679.49 ms | 5.84 ms |
| One streamed reply | 1,220 | 20 | 596.05 ms | 8.78 ms |

Local reproduction and all trial results are retained at `/private/tmp/athena-markdown-perf.mjs` and `/private/tmp/athena-markdown-perf.json`. These measure avoidable React/Markdown CPU work, not WebKit frame times, input latency or perceived Mac smoothness. They are not a native visual acceptance result. Native typing with IME, scrolling while streaming, window resizing and voice interaction still require direct macOS acceptance.

## Follow-up: scrolling and hidden animation

An independent nonpersistent WKWebView loaded only synthetic HTML, the actual component/CSS, and extracted scroll handlers through `loadHTMLString`. Its CSP prohibited network access; no application port, account, visible user window or browser profile was accessed. It reproduced a concrete interaction problem: after the reader moved 60 px upward (`scrollTop` 2430 → 2370), the next output update immediately returned them to 2430. The old 160 px proximity rule overrode explicit user intent. Idle also retained six running work/voice animations beneath transparent groups.

The follow-up patch replaces proximity-only following with an explicit scroll policy. Upward gestures pause before a stream commit. Downward movement resumes at the real bottom, with a 2 px rounding tolerance; explicit Send and ordinary conversation navigation also restore following. Activity navigation clears pending gesture intent and stays paused. Wheel, touch momentum, scrollbar dragging and keyboard scrolling share the policy, while text editing, composition events and nested scrollable content retain their own handling. Geometry shortening that clamps an already-following view to a new bottom does not disable following. Workspace surfaces are excluded from transcript following. Work/voice animations now exist only in their corresponding active state; their visible design is unchanged.

Twelve additional policy and real React DOM tests cover these boundaries. The existing submission-handler fixture now receives the real scroll policy, and its Stop/draft-preservation regressions still pass. The complete frontend suite passed **228 tests**, including concurrent recharge work, followed by TypeScript and the production bundle.

The offline WKWebView follow-up bundles the actual production React hook and Orb. Ten assertions passed: an upward reader remains at 2445 across new output; reaching the bottom restores following; subsequent output follows; activity targeting stays paused; editing/composition events are ignored; keyboard and scrollbar intent preserve position; resizing clamps 2695 → 2575 without disabling following; and idle has zero running animations. Evidence and reproduction are `/private/tmp/athena-scroll-webkit-after.json`, `/private/tmp/athena-scroll-webkit-probe.mjs`, and `/private/tmp/AthenaOfflineProbe.swift`. The original failure result remains `/private/tmp/athena-native-offline-probe.json`.

This is real WebKit layout/event acceptance with synthetic content, not a human Mac session, real trackpad/IME hardware test, frame-time profile or voice-call acceptance. Keyboard submission, draft persistence, network stream ordering and runtime execution are unchanged.
