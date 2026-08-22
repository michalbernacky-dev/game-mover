# Game Mover security audit register

Last review: 2026-08-23

Reviewed version: 0.30.1

Reviewed commit: `e81c2f0`

Publication verdict: **not ready for a public repository**

This document is the working register for security findings discovered during
periodic source-code reviews. Finding identifiers use the project-local format
`GM-SA-YYYY-NNN`. They are not CVE identifiers and must not be presented as
official CVE assignments.

Status values used below are `Open`, `In progress`, `Mitigated`, `Fixed`, and
`Accepted`. A finding may be marked `Fixed` only after its remediation and
regression tests have been reviewed.

## Finding summary

| ID | Severity | Status | Finding |
|---|---|---|---|
| GM-SA-2026-001 | Critical | Open | Arbitrary recursive permission changes by the root API |
| GM-SA-2026-002 | High | Open | Path traversal in privileged Game Mover operations |
| GM-SA-2026-003 | High | Open | Excessive privileges and insufficient isolation of the API service |
| GM-SA-2026-004 | Medium | Open | PAM authentication has no application-level rate limiting |
| GM-SA-2026-005 | Medium | Open | Mutable CI dependencies and incomplete security verification |
| GM-SA-2026-006 | Low | Open | Personal and infrastructure metadata in Git history |
| GM-SA-2026-007 | Low | Open | Installer source list can drift from the RPM payload |
| GM-SA-2026-008 | Informational | Open | Public security and licensing policy is incomplete |

## GM-SA-2026-001: Arbitrary recursive permission changes by the root API

- Severity: **Critical**
- Status: **Open**
- Relevant weakness classes: CWE-73, CWE-732
- Affected component: `POST /fix_perms`

### Description

The endpoint accepts an arbitrary existing filesystem path supplied by the
client. The Flask service runs as root and recursively changes the group to
`gemers`, directory modes to `2775`, and file modes to `0664`.

The `library.permissions` operation defaults to `silent`. A local member of the
`gemers` group can read the local API token and request permission changes on a
root-owned security-sensitive directory outside the game library.

### Evidence

- `game_mover_flask.py`: `set_group_perms()` and the `/fix_perms` endpoint.
- `game_mover_security.py`: `library.permissions` defaults to `silent`.
- `game_mover.service`: the API process runs as `User=root` and `Group=gemers`.
- `install.sh`: the local API token is installed as `root:gemers` with mode
  `0640`.

### Impact

This can make root-owned configuration files writable by the `gemers` group
and may lead to complete local root compromise.

### Required remediation

1. Do not accept a filesystem path from the API client.
2. Accept a fixed target identifier and map it server-side to an approved root.
3. Resolve and validate the canonical target before every mutation.
4. Reject symlink targets and any target outside the explicit allowlist.
5. Add regression tests covering `/etc`, path traversal, and symlink escapes.

## GM-SA-2026-002: Path traversal in privileged Game Mover operations

- Severity: **High**
- Status: **Open**
- Relevant weakness classes: CWE-22, CWE-23, CWE-59
- Affected components: `/move_game`, `/create_symlink`, `/set_steam_cache`,
  `/list_user_games`

### Description

Client-controlled `user` and `game_name` values are joined into paths without
proving that the resulting canonical path remains under its intended library
root. Values containing parent-directory components can therefore escape the
Steam library, shared library, or proxy root.

`/list_user_games` is a GET endpoint but calls `user_common_dir()` with
`fallback_ok=True`; this can create directories as root. A nominally read-only
request consequently has a filesystem side effect.

### Impact

Because the service runs as root, path traversal can move data or create
directories and symlinks outside the intended game-library hierarchy.

### Required remediation

1. Resolve users through `pwd.getpwnam()` and use the account's canonical home.
2. Require game names to be one safe path component; preferably use an opaque
   inventory identifier selected from a server-generated list.
3. Introduce one shared `resolve_beneath(root, candidate)` validation helper.
4. Reject absolute paths, `.` and `..`, separators, NUL bytes, and symlink
   escapes.
5. Remove filesystem creation from GET requests.
6. Add regression tests for every affected endpoint.

## GM-SA-2026-003: Excessive privileges and insufficient service isolation

- Severity: **High**
- Status: **Open**
- Relevant weakness class: CWE-250
- Affected component: `game_mover.service`

### Description

The complete Flask application runs as root and combines HTTP parsing, file
operations, systemd control, Podman orchestration, package installation, DNS,
Timekpr, backup processing, and external downloads. The service unit currently
has no meaningful systemd sandboxing directives.

### Impact

A vulnerability in any reachable parser or endpoint can inherit all service
privileges and affect the entire host.

### Required remediation

1. In the long term, move privileged operations into a small, allowlisted
   helper or narrowly scoped services.
2. Run the main API as an unprivileged account where feasible.
3. Add compatible systemd hardening and explicit writable paths.
4. Measure the resulting unit with `systemd-analyze security` on the target
   host and record accepted exceptions.

## GM-SA-2026-004: Missing PAM authentication rate limiting

- Severity: **Medium**
- Status: **Open**
- Relevant weakness class: CWE-307
- Affected component: `/timekpr/auth`

### Description

PAM authentication is restricted to loopback access, including managed SSH
tunnels, but the application does not throttle repeated authentication
failures. Host PAM policy may add delays or lockouts, but the application does
not enforce or document this dependency.

### Required remediation

Add bounded per-user and per-source backoff, avoid revealing unnecessary PAM
failure detail, log throttled attempts, and document interaction with the host
PAM lockout policy.

## GM-SA-2026-005: Mutable CI dependencies and incomplete verification

- Severity: **Medium**
- Status: **Open**
- Relevant weakness class: CWE-829
- Affected component: `.github/workflows/rpm-build.yml`

### Description

The workflow uses `fedora:latest`, `actions/checkout@v4`, and
`actions/upload-artifact@v4`. These references can change without a change to
this repository. The workflow builds packages but does not run the project test
suite or a security-oriented static check.

### Required remediation

1. Pin GitHub Actions to reviewed full-length commit SHAs.
2. Use a fixed Fedora release or reviewed container digest.
3. Run the complete test suite before building artifacts.
4. Add secret scanning, dependency monitoring, and a suitable Python static
   security check.
5. Apply least-privilege `permissions:` to the workflow token.

## GM-SA-2026-006: Personal and infrastructure metadata in Git history

- Severity: **Low**
- Status: **Open**
- Relevant weakness class: CWE-200

### Description

The current tree and Git history contain personal author e-mail addresses,
real-looking local usernames, a Tailscale address, LAN addresses, and internal
DNS examples. No password, API token, private key, or cloud credential was
found by the review's heuristic scans.

These values are not authentication secrets, but publication would create an
unnecessary permanent association between people and private infrastructure.

### Required remediation

1. Replace personal test fixtures and examples with reserved documentation
   identities, domains, and address ranges.
2. Configure a GitHub no-reply author address for future commits if desired.
3. Decide explicitly whether the privacy benefit justifies rewriting history.
4. Repeat secret scanning across all branches and tags immediately before
   publication.

## GM-SA-2026-007: Installer payload can drift from the RPM payload

- Severity: **Low**
- Status: **Open**
- Affected component: `install.sh`

### Description

`install.sh` maintains a manual include list of Python modules while the RPM
specification maintains a separate payload list. New modules can be omitted
from one installation method even though tests and RPM deployment work.

### Impact

An incomplete installation can retain stale code or fail in security-relevant
paths, making the deployed behavior differ from the reviewed source.

### Required remediation

Generate both payloads from one source of truth, or make RPM deployment the
only supported installation method and remove the alternate installer.

## GM-SA-2026-008: Public security and licensing policy is incomplete

- Severity: **Informational**
- Status: **Open**

### Description

The repository has no `SECURITY.md` defining a private vulnerability-reporting
channel and supported versions. It also has no license file, while the RPM spec
declares the package `Proprietary`. A public repository would therefore be
source-visible but would not grant an open-source license.

### Required remediation

1. Add `SECURITY.md` before publication.
2. Choose and add the intended license, or clearly document that the source is
   publicly visible but remains proprietary.
3. Enable repository secret scanning, push protection, dependency alerts, and
   branch protection before changing visibility.

## Controls observed during the review

The following existing controls reduce exposure but do not close the open
findings:

- The Flask API binds to `127.0.0.1` by default.
- Non-loopback requests are limited to an authenticated read-only allowlist.
- Mutating host operations require a configured `silent`, PAM, or disabled
  policy even when reached through an SSH tunnel.
- Runtime API tokens and the CurseForge key are stored outside the repository.
- PAM session tokens are random, expire, and remain in process memory.
- Subprocess calls use argument arrays; no `shell=True` use was found.
- Backup restoration, modpack archive extraction, RCON commands, and managed
  server deletion contain targeted validation and size or scope limits.
- At the time of review, 218 automated tests passed and the working tree was
  clean.

## Publication gate

The repository must remain private while GM-SA-2026-001 or GM-SA-2026-002 is
open. Before publication, GM-SA-2026-003 through GM-SA-2026-008 must either be
fixed or explicitly accepted with a written rationale. A final review must
include the current tree, all branches and tags, packaged RPM contents, CI
configuration, and deployment documentation.

This review is a source, configuration, and Git-history assessment. It is not
a penetration test of a deployed host and does not certify that installed
dependencies are free of known vulnerabilities.
