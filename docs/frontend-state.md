# The frontend as an interaction instrument

Athena should be able to observe the user's current communication surface, change it through a registered or fixed control, and verify the resulting state. A successfully stored component or delivered command is insufficient evidence that the user can use it.

## Current contract

The existing `runtime` envelope exposes `app list` for a compact client inventory and `app snapshot, client_id` for an on-demand semantic observation. This does not add an always-present model tool or mutate historical prompt prefixes. Application and interface skills explain the operating details progressively.

An authenticated browser reports `POST /api/frontend/observation`. Its random, page-scoped client identity is bound to the account and the exact authentication session. The SQLite worker stores only its latest bounded observation: current conversation, viewport, foreground state, open fixed views, preview loading status, workspace, registered component geometry/state, waiting reasons, fixed error codes, attachment identifiers and a boolean indicating unsent changes. Drafts, password/form values, DOM, clipboard, file contents and arbitrary error text are rejected by the schema. Component observations are client-reported semantics, not screenshots or proof that their displayed data is correct.

A revision increases on semantic changes. An identical revision and state can renew the 75-second freshness lease; lower revisions or conflicting content cannot overwrite newer state. The cache admits at most 24 current identities per account and replaces expired entries. A state can be inspected after expiry, but is explicitly marked stale and cannot authorize a UI action.

## Addressing and receipts

A view request is bound to its selected client before delivery. Omitting `client_id` selects a target only if exactly one fresh foreground client displays the originating conversation. No available client or multiple candidates produces a specific unavailable/ambiguous error without creating an action. An explicit client can display another conversation in the same account. `expected_revision` provides an optimistic precondition; the chosen revision and selected conversation are checked again when claiming the action.

The client-scoped event endpoint continues delivering pending actions after navigation, independently of the selected conversation's normal event stream. Requests retain their originating conversation for auditing and acknowledgement URLs. Still-pending requests are offered again every five seconds to recover a failed transport before claim; claiming transitions durably to dispatched and never replays the UI operation. Unknown outcomes after dispatch require inspection, not automatic execution again.

If an HTTP failure permanently closes the browser's event source, the client replaces it only after a successful fresh observation, with a 5–30 second retry backoff. Native EventSource reconnects remain untouched. Backgrounding, page departure and logout cancel replacement retries; the observation reports a connection problem until the stream opens again. This prevents an interrupted deployment from leaving a responsive chat with a permanently dead interface-action channel.

Unclaimed actions belonging to a failed or interrupted run are dismissed in the same database transaction as run finalization. Claim also rejects old persisted intents from unsuccessful terminal runs. Successful completed runs may still finish an already requested UI action. Cancellation never claims that a dispatched effect was undone: explicit stop/reset records its outcome as unknown, while pending intents record that they were never dispatched.

An applied acknowledgement includes the observation revision. If a newer report arrives before the acknowledgement, the server accepts a revision from the claim boundary through the latest observation and verifies the latest state; future or pre-claim revisions fail. The receipt records both the reported revision and the actual revision used. The server verifies navigation, opened/closed views, preview identity and attachment effects against fresh state from the bound client. Preview panel opening and file readiness are separate facts. A download receipt means only that the browser requested a download. Password forms and file pickers likewise do not prove that the user completed them.

Reset dismisses pending/dispatched actions and removes observations without deleting chats/files. Old identities are revoked for 24 hours (a bounded 128-entry account cache), so delayed pre-reset observations cannot revive them. The frontend receives `frontend_identity_revoked`, registers a fresh identity, and reports its restored surface. A shared cookie renewed for the same account returns `frontend_identity_changed` after retiring the old identity; the client rechecks authentication before registering anew. A different account cannot adopt the old identity or its observations. Logout invalidates the bound authentication session and clears its observations.

## Limits

This release makes the existing surface observable and its fixed view actions addressable. It does not make the entire React renderer a replaceable provider, observe arbitrary pixel content, control native OS windows, or allow extensions to bypass independent stop/reset controls. Arbitrary task interactions still use versioned component registration and the bounded bridge. Replacing larger frontend services remains part of the service-provider architecture, with recovery and lifecycle contracts required before claiming completion.

Requirement tests cover account/token isolation, stale observations, ambiguous foreground clients, changed revisions, background claim rejection, duplicate acknowledgement, reset late-report rejection, bounded storage, legacy client honesty, and navigating from one conversation before receiving and verifying subsequent actions from its still-running task.
