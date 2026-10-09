# Reporting a vulnerability

Do not put secrets, exploit details involving real users, or private logs in a
public issue. Use GitHub's **Report a vulnerability** on this repository when it
is available. If private reporting is not yet enabled, contact the repository
owner through an existing private channel and first ask for a secure reporting
route; do not send sensitive evidence in a public comment.

Include the affected revision, a minimal synthetic reproduction, expected
boundary, and observed effect. Redact tokens and personal data. We do not yet
promise a response SLA or long-term support for preview builds.

Runtime extensions and local command execution can change the host. The
prototype currently has no per-action approval workflow. Stop and Reset must
remain outside generated surfaces; operator keys must never reach agent tools,
logs, request snapshots or child-process environments. An unknown side effect
must be reconciled rather than automatically retried.
