# Security Policy

## Supported versions

Security fixes are provided for the current release and the current `main`
branch. Older releases and historical commits are not supported; reproduce a
problem against the newest version before reporting it when possible.

## Reporting a vulnerability

Report vulnerabilities privately through
[GitHub private vulnerability reporting](https://github.com/michalbernacky-dev/game-mover-rpm/security/advisories/new).
Do not open a public issue for a vulnerability that has not been coordinated.

Include the affected version or commit, required configuration, reproduction
steps, impact, and any suggested mitigation. Remove passwords, API keys,
session tokens, personal data, and unrelated host information from the report.

You should receive an acknowledgement within seven days and a status update
within fourteen days. These are response targets, not a bug-bounty promise or
a guarantee that every report will result in a release.

## Deployment boundary

Game Mover is designed for trusted users on a single Fedora host. Its backend
must remain bound to loopback unless access is deliberately constrained by the
documented authenticated network or SSH-tunnel configuration. Do not expose
the management API directly to the public internet.

The network-facing API runs without root privileges or Linux capabilities.
Root-required host changes are isolated in a local Unix-socket broker that
checks the API process UID and accepts only schema-validated semantic actions.
Additional adopted systemd units must be explicitly listed by root in
`/etc/game_mover/allowed-services.json`; never make that file writable by the
API account.

CurseForge support is optional and bring-your-own-key. Never include a
CurseForge API key in a report, repository, RPM, container image, log, backup,
or client configuration. If a key may have been exposed, revoke it through the
provider and configure a replacement on the host.
