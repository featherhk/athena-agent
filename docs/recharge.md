# Account credit top-ups

Athena's payment integration buys **USD-denominated service credit**. It does not
create a wallet, hold signing keys, send cryptocurrency, withdraw funds, or make
account credit transferable. Customer payments are quoted and received by an
existing, operator-controlled payment service.

**Activation status:** no live payment service or receiving wallet has been
configured for this implementation. New-account credit remains zero. A disabled
service does not expose a checkout link or claim that top-ups are open. Automated
acceptance uses synthetic invoices and isolated account databases; it is not a
real-money settlement test.

## Customer flow

The account panel contains a small top-up section when the central gateway enables
it. The customer chooses a server-defined amount and opens the payment service's
HTTPS checkout. The checkout determines the cryptocurrency, network, exact amount,
quote expiry and any network fee. Athena never assumes that one stablecoin unit
equals one USD or asks the customer to paste a transaction hash as proof.

An order survives closing the panel or restarting the client. A failed creation
response is retried using the same persisted, account-scoped idempotency key.
Neither returning from checkout nor pressing Refresh grants credit. The central
service verifies payment evidence and commits the order, ledger entry and balance
together. Newly credited users can call the model without signing in again.

The account panel refreshes active orders only while expanded and visible, with a
bounded polling period and a manual refresh action afterward. Existing orders can
still be inspected when new payments are disabled. There is no recharge entry on
the default conversation surface.

## First adapter: BTCPay Greenfield

The implementation follows the official [Greenfield API](https://docs.btcpayserver.org/API/Greenfield/v1/)
and [e-commerce integration guide](https://docs.btcpayserver.org/Development/ecommerce-integration-guide/).
It creates invoices and reads their state. Webhooks use `BTCPay-Sig` HMAC-SHA256
over the original body and trigger an independent authenticated invoice read.
`Processing` is not final settlement. Exceptional, manually marked, underpaid,
overpaid or late payment states do not automatically receive credit.

An invoice is bound to its central user ID, service-credit amount, currency,
provider origin and store. A changed provider/store configuration cannot silently
reinterpret an older order. Repeated notifications and out-of-order notifications
must never add the same credit twice. A lost create response becomes an uncertain
order; recovery queries the original invoice reference and does not blindly issue
another create request.

`review` and unresolved `unknown` states need operator reconciliation. This first
integration does not implement refunds, chargebacks, manual invoice overrides or
automatic blockchain reorganization adjustments. Do not compensate with an
unrelated grant, edit balances, or mark a payment settled at the provider to force
credit: that can produce duplicate or unsupported credit. Reconciliation tooling
and a real payment acceptance run remain prerequisites for unattended commercial
operation.

## Operator configuration

The operator must already control the payment store and receiving wallet. Athena
does not provision them. No node or payment server is installed on the shared
Athena/New API host by this change.

1. Prepare the external JSON configuration following
   [`deploy/recharge.example.json`](../deploy/recharge.example.json). Its example
   hostname and store ID are placeholders. `base_url` must be a credential-free
   HTTPS origin, with no path, query or fragment. Amounts are positive integer
   microcredits: `10_000_000` means USD 10 of Athena service credit.
2. Supply the store-scoped API key and webhook secret through separate private
   files. Limit the API key to invoice creation and invoice viewing; the adapter
   never needs wallet signing, payout, refund or invoice-marking permissions.
3. Configure the existing store's webhook to
   `https://chat.athena.featherhk.com/v1/payments/btcpay/webhook`, using the same
   secret. Verify the recipient wallet and confirmation policy at the payment
   service. Its policy determines when an invoice becomes settled.
4. On the Athena server, the optional
   [`deploy/compose.recharge.yaml`](../deploy/compose.recharge.yaml) mounts the
   external files **only in gateway** and sets `ATHENA_RECHARGE_CONFIG`. Preserve
   all existing deployment overlays. The gateway's UID must be able to read the
   files; keep them private and read-only. Neither the web executor nor the Mac
   bundle receives these credentials.
5. Before enabling real customers, verify one deliberate, owner-operated payment,
   a repeated notification and account credit on two clients. Review and publish
   the applicable service-credit tariffs and payment terms. The existing voice
   tariff is documented as a preview account tariff, not a provider invoice or a
   current currency conversion. This repository's tests cannot certify the
   operator's wallet, provider availability, fees, legal terms or real settlement.

Without `ATHENA_RECHARGE_CONFIG`, top-ups are disabled. Invalid configuration fails
closed. Removing the configuration disables checkout without deleting account
history. Runtime configuration, secrets and databases stay outside the checkout;
the private filenames are also covered by `.gitignore` and the staged-file guard.

## API ownership

- Client: `/api/account/recharge` (GET options/history, POST create) and
  `/api/account/recharge/{id}` (GET reconcile/read), through its signed-in cookie.
- Central service: corresponding `/v1/account/recharge` routes, through that
  account's central token; zero credit does not block top-up access.
- Notifications: `/v1/payments/btcpay/webhook`, authenticated by its separate
  signature, never by a browser's assertion of success.

The client cannot choose an owner, credit amount outside the published packages,
receiving address, provider URL, or ledger operation. Agent-authored UI and harness
plugins do not configure the operator's payment destination or settlement rules.

## Implementation acceptance — 2026-10-04

The payment-specific suite passed 62 cases. A combined 99-case run also exercised
existing credit reservation, gateway, voice and cloud-client behavior. Twenty
concurrent creates with one key produced one external POST; twenty settlement
notifications produced one receipt and one credit entry. Fault injection proved
that receipt, balance, ledger and order updates roll back together. Cancellation
after database admission retains an unknown, recoverable order without another
payment POST. Exact decimal tests reject differences smaller than one microcredit
instead of rounding them into an apparent match.

The browser-facing HTTP integration traversed the local cookie/proxy, central
identity, order creation, signed notification, independent invoice read and model
stream. A zero-credit user received 402 before settlement and successfully called
the model afterward with the same login. Only the central database contained the
payment order and ledger. The payment and model HTTP transports were synthetic.

Eleven real React DOM tests cover disabled configuration, status-only balance
refresh, untrusted checkout protocols, retained creation intents, account
isolation, interrupted responses, visibility/unmount behavior and the explicit
pre-admission rejection case. All nine language catalogs include the recharge
strings. A newer desktop proxy handles an older gateway's missing catalog route
as disabled only after its authenticated credit response explicitly advertises
`recharge_enabled: false`; it never converts a missing create or receipt into
success.

This evidence proves the local protocol and state machine under the exercised
conditions. Live merchant provisioning, a real-chain payment, manual financial
reconciliation, refunds and commercial release remain unverified or unfinished.
