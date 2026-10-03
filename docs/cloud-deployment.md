# Cloud deployment and operator runbook

This document describes the implementation in this checkout. The [2026-10-03 release receipt](releases/2026-10-03-preview.md) records verified deployment results and remaining limitations. Repeat that acceptance for each release.

The initial access policy is an operator-funded preview: fund explicitly selected test accounts; all other registrations have no model access. There is no customer recharge endpoint or recharge UI. The existing New API installation is a separate service and must remain untouched.

## Process and data boundaries

| Service | Responsibility | Host binding / persistent storage |
| --- | --- | --- |
| `edge` | HTTPS, streaming reverse proxy, static downloads | TCP/UDP 443; `/opt/athena/data/tls`, `/opt/athena/data/caddy`; read-only `/opt/athena/downloads` |
| `gateway` | Central accounts, authentication, account credits, model/audio proxy | `127.0.0.1:4322` → container 4321; `/opt/athena/data/accounts` |
| `web` | Web client, sessions, files and assistant runtime | `127.0.0.1:4317`; `/opt/athena/data/web` |
| `execution` (optional) | Trusted container supervisor; account-scoped command/browser workers | Unix socket only; `/opt/athena/data/execution`, `/opt/athena/data/execution-ipc` |

`deploy/Caddyfile` routes `https://chat.athena.featherhk.com/v1/*` to the gateway and other paths on that host to the Linux web runtime. `https://app.athena.featherhk.com` serves the separate app distribution page from `/opt/athena/site`, with `/downloads/*` served from `/opt/athena/downloads`. The main `featherhk.com` marketing website uses the same static site mount on Tencent Cloud; `www.featherhk.com` redirects to it. The website source remains versioned on GitHub. Both Athena subdomains use the same server and automatic HTTPS; port 80 remains with the existing New API service.

The gateway does not import the agent engine or execute user code. Only it mounts the operator's provider key. The web worker and desktop sidecar send an account bearer token to the gateway; the provider key is never distributed to clients. Browser authentication uses an HttpOnly, SameSite=Strict cookie, with Secure enabled for the configured HTTPS public origin. The gateway's bearer endpoints reject browser Origin headers; the application backend mediates browser login and inference.

The web worker is one shared process. Account ownership checks protect its application records. With the optional [Linux execution adapter](linux-execution.md), model-authored commands run in disposable account-scoped containers and browsers in separate account-scoped containers. The web process has a private broker socket, never the Docker socket. Without a configured broker, Linux commands still fail closed. Do not run model-authored code directly in the shared web process to bypass that condition.

All databases, files, tokens, TLS state and operator secrets live outside the checkout. The two Python services run as UID/GID `10001:10001`, with read-only container roots, bounded temporary storage, dropped capabilities and no-new-privileges. Their database connections retain the dedicated worker-thread ownership used by the local application. Python 3.14T and the GIL-disabled doctor check are part of the image build.

## Prepare a deployment

1. Inspect the server's existing listeners, containers, disk space and firewall rules. The supplied edge needs port 443; it deliberately does not bind port 80. If 443 already belongs to another service, plan the ingress integration before starting edge. Do not stop, reconfigure, migrate, or prune New API to free a port.
2. Point the application subdomain at the intended server and make TCP 443 reachable. The supplied Caddy configuration uses the TLS ACME challenge; an intervening proxy must support that arrangement. UDP 443 enables HTTP/3 but is not required for ordinary HTTPS. Database, gateway and worker ports must not become public listeners.
3. Build the frontend and validate the release from a clean checkout. The Dockerfile copies the already-built `dist/` directory; it does not run the frontend build itself.

```sh
npm ci
npm run check
npm run build
```

Create private runtime directories on the server. Keep source, release files, secrets and application data separate:

```sh
sudo install -d -o 10001 -g 10001 -m 0700 /opt/athena/data/accounts /opt/athena/data/web
sudo install -d -m 0700 /opt/athena/secrets /opt/athena/data/tls /opt/athena/data/caddy
sudo install -d -m 0755 /opt/athena/downloads /opt/athena/site
```

Provision `/opt/athena/secrets/model-key` from an existing private operator file using a secure transfer or hidden input. Its owner must be UID 10001 and its mode should be `0400`. Never paste the key into shell arguments, checked-in Compose files, screenshots, logs, or release archives. The supplied Compose file mounts this file read-only in the gateway only.

From the release checkout, inspect the effective configuration without including secret values, then start the isolated Athena project:

```sh
docker compose -f deploy/compose.yaml config
docker compose -f deploy/compose.yaml build gateway
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml ps
```

`athena-preview:local` is a local build tag, not an immutable release identifier. Record the source commit, resulting image ID, frontend build, operator overrides and tariff configuration before upgrading. Do not use global `docker compose down`, `docker system prune`, or unrelated service restarts as an Athena deployment step.

An environment variable in the host shell or `.env` file is not automatically passed into a container. The supplied Compose file has explicit environment entries. Put operator overrides outside the checkout and pass the additional Compose file consistently to `config`, `up`, `exec` and later maintenance commands.

## Central identity and initial credit

Start the gateway once before running the administration console so it can initialize its schema. Create the deployed preview account with a new password of at least 12 characters, entered through the hidden prompt. A pre-existing local test user does not become a cloud account merely because its name matches.

```sh
docker compose -f deploy/compose.yaml exec gateway /opt/venv/bin/python -m athena.gateway_admin --data-dir /data create-user preview-user
```

If preview-user already registered centrally, skip account creation. Grant the operator-approved initial credit (the $10 below is illustrative) in Athena account credits with a unique, recorded operation key:

```sh
docker compose -f deploy/compose.yaml exec gateway /opt/venv/bin/python -m athena.gateway_admin --data-dir /data grant preview-user --micros 10000000 --operation-key preview-credit-example-001 --reason 'Approved initial preview credit'
docker compose -f deploy/compose.yaml exec gateway /opt/venv/bin/python -m athena.gateway_admin --data-dir /data status preview-user
```

The same grant operation key with the same user and amount is idempotent. A different operation key creates another grant; do not change the key just because a command's response was lost. Creating or registering an account supplies zero credit and does not make the first registrant an administrator. Only the local operator console grants credit; no grant API is exposed.

In cloud mode, the local service stores its central bearer token in its private database and exposes only a local session cookie to the browser. Central login expiry, logout or unavailability cannot be bypassed with a cached entitlement. Local and cloud users are associated by immutable cloud ID, not by username. Use a separate data directory for a cloud-connected client instead of merging an old local-authentication database by hand.

Central identity and credit do not currently synchronize desktop and hosted conversation/file stores. Those remain in their respective runtime directories.

## Credit accounting and uncertain outcomes

All amounts are integer **USD microcredits**: `1 USD = 1,000,000 micros`. This is Athena's account tariff ledger, **not a reconstruction of the provider's invoice**. Provider discounts, currencies, invoice rounding, promotions and taxes are not inferred.

| Field / state | Meaning |
| --- | --- |
| `balance_micros` | Granted credit minus settled charges. |
| `held_micros` | Sum of reservations in `reserved` or `uncertain` state. |
| `available_micros` | `max(0, balance_micros - held_micros)`. |
| `reserved` | Funds earmarked before contacting the provider; not yet debited. |
| `settled` | A final charge recorded once. The unused part of the reservation becomes available. |
| `uncertain` | Completion or billable usage could not be established. The reservation still reduces available credit. |

A request reserves a conservative ceiling atomically before inference. Concurrent requests cannot spend the same available credit. Text settlement uses the provider's reported prompt, cached-prompt and completion token counts, multiplied by Athena's configured tariff and rounded upward to a microcredit. A balance greater than zero may still be too small for a request's reservation.

The current `GatewayConfig` defaults are:

| Text category | Integer microcredits per million tokens | Equivalent Athena tariff |
| --- | ---: | ---: |
| Uncached input | 300000 | $0.30 / million tokens |
| Cached input | 6000 | $0.006 / million tokens |
| Output | 1200000 | $1.20 / million tokens |

These values are implementation defaults to review before release; they are not a claim about the provider's current retail prices. Text rates and the 16,384 maximum output-token limit are currently constructor fields, **not environment-variable settings**. Changing them requires a reviewed configuration/code release. Preserve the applicable configuration alongside deployment records: the credit request row does not currently snapshot the tariff itself. The ledger's historical `provider usage` reason means usage-based Athena credit settlement, not an invoiced provider amount.

The gateway persists text settlement before emitting the final stream completion marker. Missing or invalid usage, interrupted streams and ambiguous provider outcomes retain a hold. On gateway startup, outstanding `reserved` requests become `uncertain`; restart does not refund or replay them. A repeated request ID is rejected instead of executing the request again. The current text adapter settles an upstream HTTP rejection at zero, while network errors and incomplete streams remain uncertain; review this classification against the configured provider's billing contract when changing providers.

Voice follows the same ledger. Only a completed response with valid provider-reported duration/characters is settled. Cancellation, failed generation, missing usage and failure to download already-generated speech all remain uncertain. The current audio implementation also retains a hold for explicit upstream rejection; this conservative behavior may require operator reconciliation. Stopping playback does not establish that no speech was generated or billed.

### Reconcile without creating credit

There is no public reconciliation endpoint or finished reconciliation CLI. Review an uncertain request using its ID, timestamps, numeric audit record and the provider's own usage evidence. Do not replay it, delete its reservation, grant compensating credit to hide it, or edit a balance directly.

After obtaining evidence, a reviewed maintenance procedure can use `Credits.settle(request_id, verified_micros, usage_json)`: zero is appropriate only when no billable usage is established; otherwise apply the original Athena tariff to verified units. Preserve the evidence reference in the maintenance audit. Settlement is idempotent for the same amount. A charge above the original reservation remains uncertain and needs a separate reviewed resolution; the implementation does not silently overdraw an account. If no reliable outcome is available, retain the hold and communicate that limitation.

## Model configuration

The hosted gateway currently exposes **one allowed model** through `/v1/models`, defaulting to `deepseek-flash` at `https://api.deepseek.com`. Requests for another model are rejected even if the upstream provider would accept them. An agent cannot use model switching to escape the account gateway.

Operators can change `ATHENA_GATEWAY_MODEL`, `ATHENA_GATEWAY_PROVIDER_URL` and the mounted `ATHENA_GATEWAY_KEY_FILE`. The upstream must use a credential-free HTTPS URL and the supported streaming Chat Completions request/usage protocol. This is not automatic support for every provider API. Update the client's registered model profile to the same allowed model, including verified context limits and base-model identity. Recheck tariff/usage compatibility and model-switch cache boundaries, then deploy the reviewed gateway configuration. Do not put provider credentials in the client profile.

For example, an external Compose override can set the gateway model and URL. The key remains in its existing read-only secret mount:

```yaml
services:
  gateway:
    environment:
      ATHENA_GATEWAY_MODEL: deepseek-flash
      ATHENA_GATEWAY_PROVIDER_URL: https://api.deepseek.com
```

For a cloud-connected local service, set `ATHENA_CLOUD_URL=https://chat.athena.featherhk.com/v1`. Hosted web additionally uses `ATHENA_PUBLIC_ORIGIN=https://chat.athena.featherhk.com` and `ATHENA_DEPLOYMENT=hosted`. The public origin is the exact HTTPS origin, without a path. The default supplied Compose configuration already sets these values for web. Do not place `DEEPSEEK_API_KEY` or audio keys in that service or in a distributed app.

## Audio configuration and present limits

The supplied deployment does **not** provision an audio key or publish an audio tariff. Voice therefore reports unavailable; text remains usable. Do not claim a successful microphone-to-provider-to-playback test until the selected channel has actually been configured and tested.

The hosted metering adapter currently supports DashScope's Qwen ASR/TTS duration and character usage. OpenAI-compatible local audio adapters exist, but hosted token-priced audio requires its own reviewed meter before enablement. To enable the implemented hosted adapter, provision a separate private audio key file, mount it read-only **only in gateway**, and explicitly pass these settings there:

```text
ATHENA_AUDIO_PROVIDER=dashscope
ATHENA_AUDIO_API_KEY_FILE=/run/secrets/audio-key
ATHENA_AUDIO_BASE_URL=https://dashscope.aliyuncs.com/api/v1
ATHENA_AUDIO_STT_BASE_URL=https://YOUR_WORKSPACE.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
ATHENA_AUDIO_STT_MODEL=qwen3-asr-flash
ATHENA_AUDIO_TTS_MODEL=qwen3-tts-flash
ATHENA_AUDIO_VOICE=Cherry
ATHENA_AUDIO_STT_USD_PER_SECOND=<reviewed positive USD tariff>
ATHENA_AUDIO_TTS_USD_PER_10K_CHARACTERS=<reviewed positive USD tariff>
```

Use the actual compatible-mode endpoint and models available to the operator's region/workspace. Both USD tariff values must be explicitly configured; the service does not invent an exchange rate from a CNY price. TTS settlement uses provider billing-character counts, which can differ from the number of Unicode characters in the input. Arabic is not supported by the selected Qwen TTS adapter and is reported as unavailable for a call.

The worker/desktop forwards authenticated audio requests to the gateway without holding the audio provider key. Raw recordings and generated audio are processed in memory; durable conversation content is the transcript, with separate numeric voice request audit records. Reverse-proxy access logs must not be extended to record request bodies, bearer headers or audio.

## Release acceptance

Run these checks against the deployed release, retaining results without credentials or user content:

- `http://127.0.0.1:4322/v1/health` responds locally; `configured: true` confirms a key is loaded, not that an upstream call succeeded.
- `https://chat.athena.featherhk.com/` has a valid certificate, loads its assets, and preserves streamed responses through edge.
- An unauthenticated account/model request returns 401. A newly registered, unfunded account cannot incur a model charge. No recharge path is offered.
- preview-user can log in, complete one authorized model request, and inspect a balance that matches one settled ledger entry. A concurrent reservation and a repeated request ID cannot spend twice.
- Browser reconnect and conversation switching recover the active stream; logout/expired central credentials stop further authenticated access.
- The worker and packaged client contain no provider key; gateway/worker ports remain loopback-only. Existing New API health is unchanged.
- Voice is honestly unavailable while unconfigured. When enabled, verify listening, automatic send, playback, interruption, permission denial, disconnect and reservation behavior with real audio.
- Downloaded artifacts match their published checksum and the expected build. A downloadable preview DMG is not evidence of Developer ID signing or Apple notarization; see [distribution](distribution.md).

The public web worker's actual adapter configuration must be part of release expectations. Linux commands and a managed browser do not provide control of the user's Mac or phone. Do not describe this deployment as a completed multi-tenant desktop-control service.

## Backup, restart and recovery

Back up runtime state outside the checkout and outside the downloads directory. Account databases contain password hashes and session tokens; web data contains user files and conversation records; Caddy state contains private TLS material. Restrict and encrypt backups, and manage retention as user data. Keep the operator provider-key backup separately controlled.

For a simple consistent preview backup, schedule a maintenance window, allow active work to finish, and stop **only** Athena web and gateway before copying their data. Keep edge running so it can continue serving release files. A live copy of only `accounts.sqlite3` can omit WAL transactions; use SQLite's backup API for an online backup, or copy the complete data directories after both services stop.

```sh
docker compose -f deploy/compose.yaml stop web gateway
backup_dir="/var/backups/athena/$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -m 0700 "$backup_dir"
sudo tar --numeric-owner -C /opt/athena -czf "$backup_dir/athena-state.tar.gz" data
sudo chmod 0600 "$backup_dir/athena-state.tar.gz"
docker compose -f deploy/compose.yaml up -d gateway web
```

These commands assume the supplied Compose file; include the same external override file used for deployment. Archive the release/configuration identity with the backup, without writing secret values to public logs. Downloads can be backed up separately as immutable artifacts.

Before restoring, stop the two Python services and preserve a forensic copy of the current state. Restore a mutually consistent account/web snapshot with original ownership and permissions. Do not merge accounts by username, overwrite active SQLite files, or run two gateway instances on the same data directory. Restore matching configuration and review uncertain requests before reopening access.

An old account snapshot can predate real provider spending: restoring it must not silently grant that spending back. Reconcile ledger changes and upstream evidence from the missing interval before resuming calls. A code rollback should normally keep the current data and use only schema-compatible code; it is not a reason to roll the credit database backward.

If login or inference fails, inspect sanitized service logs and account status first. Central outages fail closed. Missing model configuration requires an operator fix; repeated user retries do not supply it. Interrupting a deployment can leave side effects or provider charges with unknown outcomes, so inspect existing request records rather than automatically resubmitting tasks. The current single-host SQLite layout has no automatic failover or distributed account ledger.
