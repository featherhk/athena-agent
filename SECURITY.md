# Reporting a vulnerability

Do not put secrets, exploit details involving real users, or private logs in a
public issue. [Report a vulnerability privately](https://github.com/featherhk/athena-agent/security/advisories/new)
through GitHub. Private vulnerability reporting is enabled for this repository;
a GitHub account is required to submit a report.

Include the affected revision, a minimal synthetic reproduction, expected
boundary, and observed effect. Redact tokens and personal data. We do not yet
promise a response SLA or long-term support for preview builds.

Runtime extensions and local command execution can change the host. The
prototype currently has no per-action approval workflow. Stop and Reset must
remain outside generated surfaces; operator keys must never reach agent tools,
logs, request snapshots or child-process environments. An unknown side effect
must be reconciled rather than automatically retried.
