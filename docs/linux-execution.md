# Linux execution adapter

Hosted web and the iOS host execute on the Linux server. They do not control the
phone, its apps, or the user's local computer. `runtime capabilities` reports the
actual execution node; a small current-turn note keeps resumed conversations
from relying on an old macOS or unavailable-executor assumption.

## Boundaries

The shared web service connects to a trusted supervisor through a private Unix
socket. Only the supervisor holds the Docker socket. Its API accepts account
identity, bounded commands and fixed browser lifecycle operations; callers
cannot choose images, mounts, host paths, Docker flags or browser launch flags.
The Docker socket makes the supervisor a privileged part of the trusted core.

- Commands: disposable non-root containers mount only that account's files and
  read-only extensions. No application source, database, provider key, broker
  socket or another account's directory is mounted. Python 3.14T, curl, git,
  openpyxl and pypdf are available in the worker image.
- Browsers: account-scoped Chromium profiles persist outside the checkout.
  The existing single-send browser protocol, observation tokens, durable action
  receipts and owned downloads remain in use. At most two browser containers
  are admitted; a slot is reclaimed only after inactivity is confirmed.
- Policies: read-only extensions, no network, isolated Python input/output.
- Workers: read-only roots, dropped capabilities, no-new-privileges, bounded
  memory, CPU, process count, temporary storage and individual file size.

The dedicated `athena-tools` bridge has an IP egress chain denying private,
metadata, host-public-IP and special destinations. A separate nftables bridge
table blocks traffic between its containers and IPv6 access to the host. It
does not depend on a host-wide `br_netfilter` sysctl. Rules match only the Athena
execution bridge; existing web, gateway and New API networks are untouched.

## Installation

Build a reviewed application image with `deploy/Dockerfile`. Build the worker
and broker targets in `deploy/Dockerfile.execution`, passing that application's
immutable image as `ATHENA_BASE_IMAGE` for the broker. Record image IDs and
browser/Chromium versions. The worker contains tools, not Athena source.

Install `execution-network.sh` at `/opt/athena/execution-network.sh`, its systemd
unit, and `docker-execution-readiness.conf` as
`/etc/systemd/system/docker.service.d/athena-execution-readiness.conf`. Put
`ATHENA_SERVER_PUBLIC_IPV4=<host IPv4>` in `/opt/athena/execution-network.env`.
Reload systemd, enable and start only `athena-execution-network.service`; no
Docker daemon restart is required to install these files.

The dedicated service writes a readiness proof after checking bridge, IPAM and
rules. The broker rejects startup/dispatch without a proof for the current OS
boot. Docker's pre-start hook invalidates it before restoring containers even
when the daemon restarts within the same OS boot. The service recreates it after
Docker starts. Operator rule changes during an OS boot require rerunning the
service; the proof is not continuous firewall attestation.

Add `deploy/compose.execution.yaml` after the base Compose file, then an
operator-owned immutable image override for `web`, `execution`, and
`ATHENA_EXECUTION_IMAGE`. Keep the gateway image/configuration unchanged unless
that release intentionally changes the gateway. Use the same complete Compose
file list for maintenance. Inspect and finish active work, take a SQLite backup,
then replace only the intended Athena services. Do not restart Docker or New API
as an application deployment step.

## Cancellation, recovery and storage

Execution IDs are unique and durable. A duplicate ID is refused. Disconnects,
timeouts and cancellation remove the whole command container, including detached
children. A failed removal is `cleanup_unknown`, blocks new commands and requires
operator recovery. On broker restart, remaining command containers are removed;
unfinished records become `unknown_after_restart` and are never replayed.

Command files have a **soft** 512 MiB account limit, 10,000 entries and 32 directory
levels. Checks run off the event loop before dispatch, once per second and after
exit; less than 2 GiB free data-volume space stops execution. Symlinks are not
followed. This is not a filesystem hard quota: bursts can exceed limits between
checks. Browser profile/download storage is not yet covered by that periodic
account limit. Operators must monitor disk use and preserve user files when
recovering space; a blocked executor cannot be used to delete its way out.

## Acceptance

`tests/test_execution_broker.py` covers request validation, mount boundaries,
concurrency, admission, cancellation during create, uncertain cleanup, recovery,
readiness and resource limits. `scripts/smoke-execution.py` refuses to use a data
directory without `.athena-execution-fixture`. Run it in a disposable web-like
container beside a QA broker, never against real user records. Set
`ATHENA_TEST_SELF_HOST_URL` to the execution host URL whose access should be
blocked; the smoke fails when this value is absent and never silently skips
that isolation assertion. It verifies real
XLSX generation/readback, account/secret isolation, public HTTP, metadata/host
rejection, optional same-bridge peer rejection, recoverable clipped output,
timeout, detached-child cancellation and managed browsing.

For browser forms, use `--prepare-browser-fixture`, start
`scripts/execution-browser-fixture.py` inside that browser container, and run
`--browser-fixture`. The synthetic page verifies exactly one submitted receipt,
exact CSV bytes, PNG screenshots and a retained cookie after browser restart.
This is not evidence of CAPTCHA support, remote interactive login, model vision,
arbitrary desktop control or successful access to every external website.
