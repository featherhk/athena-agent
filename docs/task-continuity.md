# Task continuity

User outcome: leave a conversation without losing track of its work, request for help, or unread result. Task continuity is a projection of existing durable runs, not a second scheduler or an inference polling loop.

The default shell has no additional idle control. When another conversation needs attention, a compact control leads to its work. It remains reachable from file preview. Updates never navigate automatically, replace a draft, or answer an unrelated request. Stop applies to the entire selected conversation, including queued and delegated work; its label must say so.

Account ownership is enforced in the database and transport. The account activity response carries owner_id, which the frontend checks before displaying it. Hidden pages pause bounded polling. Requests do not overlap; stale responses and acknowledgements cannot overwrite a newer account or outcome. Failure preserves last known state with an explicit retry path.

A durable feature-start boundary excludes pre-upgrade completed history. Existing active work is included, and its eventual result remains visible even when it began before the boundary. Unread terminal outcomes have no age-based expiry. An acknowledgement compares the exact attention key and records a read receipt; it does not delete the conversation or mark unfinished work complete. Current-chat acknowledgement requires actual visible conversation content with the matching run outcome; fetching the feed or a hidden preview is not reading it.

Root conversations aggregate child help. The feed distinguishes complete, failed, interrupted/cancelled and active states. Completion is not a universal proof that the real-world goal succeeded. No question text, tool output, credentials, or drafts are copied into the feed.

The existing runtime envelope exposes the same discovery, acknowledgement and conversation stop functions. Opening the activity view uses the existing app action receipt. Details are loaded from the application skill; the stable system prefix does not change.

This is in-app continuity. It does not establish OS push notifications, remote desktop login, cross-device file synchronization, or a fully replaceable service graph. Source-window affinity remains a separate architecture gap.

## Acceptance

An isolated real-engine browser fixture verified background completion while another conversation held an unsent draft, an unread result surviving reload, navigation to that result, a durable read receipt, and the original draft on return. A second task requested help while the user previewed Markdown; its entry remained reachable inside the preview, navigation opened the actual question, and answering resumed the same run to completion. A failing task displayed “Needs attention”; stopping another conversation displayed “Stopped” with a durable interrupted run, without navigating away. Desktop, 390 px width, Arabic RTL and keyboard dismissal were inspected. The provider in this fixture was deterministic, not evidence of broad model success.

Regression tests cover ownership, feature-start boundaries, non-expiring unread results, stale acknowledgement, delegated help, storage reopen, malformed/foreign account responses, interrupted fetches, visibility pause/resume, late GET/ACK races and real engine stop races. The full check passed 557 Python tests and 108 frontend tests, plus TypeScript and production build; seven opt-in native-browser cases were skipped. The free-threaded doctor reported GIL disabled on Python 3.14.8T.

Schema 13 adds read receipts and the feature-start setting. Back up the database consistently before deployment. A schema-12 binary cannot use the upgraded database: rollback requires the matching pre-upgrade database backup, with an explicit plan for any writes made after the upgrade.
