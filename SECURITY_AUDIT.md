# Game Mover security audit register

Last review: 2026-09-06

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
| GM-SA-2026-001 | Critical | Fixed | Arbitrary recursive permission changes by the root API |
| GM-SA-2026-002 | High | Fixed | Path traversal in privileged Game Mover operations |
| GM-SA-2026-003 | High | Fixed | Excessive privileges and insufficient isolation of the API service |
| GM-SA-2026-004 | Medium | Fixed | PAM authentication has no application-level rate limiting |
| GM-SA-2026-005 | Medium | Fixed | Mutable CI dependencies and incomplete security verification |
| GM-SA-2026-006 | Low | Fixed | Personal and infrastructure metadata in Git history |
| GM-SA-2026-007 | Low | Fixed | Installer source list can drift from the RPM payload |
| GM-SA-2026-008 | Informational | Mitigated | Public security and licensing policy is incomplete |

## GM-SA-2026-001: Arbitrary recursive permission changes by the root API

- Severity: **Critical**
- Status: **Fixed** (deployment verified)
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

### Resolution

Resolved on 2026-08-23. The API now accepts only the fixed
`steam-library` target identifier and maps it server-side to
`/var/Games/steam`. The backend verifies that the configured root and target
are real directories beneath the managed game root and rejects symbolic-link
targets. Recursive permission changes use directory-relative file descriptors
with `O_NOFOLLOW`; symbolic links and non-regular filesystem objects are not
modified. The GUI and CLI no longer transmit filesystem paths.

Regression coverage verifies rejection of an arbitrary `/etc` path, rejection
of unknown and symbolic-link targets, enforcement of silent/PAM policy, and
that a file symlink inside the library does not change its external target.
The targeted security tests and the complete suite of 221 tests passed. The
packaged deployment was subsequently verified without a functional regression.

## GM-SA-2026-002: Path traversal in privileged Game Mover operations

- Severity: **High**
- Status: **Fixed** (deployment verified)
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
   inventory identifier selecte GM-SA-2026-007d from a server-generated list.
3. Introduce one shared `resolve_beneath(root, candidate)` validation helper.
4. Reject absolute paths, `.` and `..`, separators, NUL bytes, and symlink
   escapes.
5. Remove filesystem creation from GET requests.
6. Add regression tests for every affected endpoint.

### Resolution

Resolved on 2026-08-23. Usernames are now resolved through the system password
database and must identify an existing interactive account with its canonical
`/home/<user>` directory. Game names must be exactly one safe path component.
All derived source and destination paths are resolved beneath their approved
home, shared-library, or proxy root, and unsafe root or leaf symlinks are
rejected.

Managed directory creation validates every component instead of following an
existing redirect. `/list_user_games` is now side-effect free and returns an
empty catalog when the user's Steam library does not exist. Move, link, and
Steam-cache operations reject path traversal, conflicting proxy paths, source
symlink escapes, and a symlink in place of the managed shared cache.

Regression coverage exercises invalid users, `../` game names, source and
cache symlink escapes, a side-effect-free GET, and successful normal move and
link workflows. The complete suite of 228 tests passed. The packaged deployment
was subsequently verified by moving and successfully running a Steam game.

## GM-SA-2026-003: Excessive privileges and insufficient service isolation

- Severity: **High**
- Status: **Fixed** (deployment verified)
- Relevant weakness class: CWE-250
- Affected component: `game_mover.service`

### Description

Before remediation, the complete Flask application ran as root and combined
HTTP parsing, file operations, systemd control, Podman orchestration, package
installation, DNS, Timekpr, backup processing, and external downloads. The
service unit had no meaningful systemd sandboxing directives.

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

### Resolution

Resolved and deployed on 2026-09-06. The network-facing Flask process now runs
as the dedicated `gameplatform` user with an empty capability set,
`NoNewPrivileges`, `ProtectSystem=strict`, explicit writable state paths, a
private temporary directory and devices, native system-call architecture, an
address-family allowlist, SUID/SGID and realtime restrictions, and protection
for the host clock, hostname, kernel logs, kernel modules, and control groups.
Mutable application state was migrated from `/etc/game_mover` to the
service-owned `/var/lib/game-mover`; credentials and the root-owned service
allowlist remain under `/etc/game_mover`.

Privileged host mutations were moved into `game-mover-privileged.service`, a
separate root broker with no network address families, private devices, a
bounded capability set without `CAP_SYS_ADMIN`, and a root-owned Unix socket.
The broker accepts peers only when `SO_PEERCRED` identifies the configured
`gameplatform` UID. Its protocol exposes semantic, validated operations rather
than arbitrary commands. Systemd targets must match built-in units or a small,
root-owned, non-writable allowlist; the network API and broker units themselves
cannot be targeted. PAM passwords are bounded and transmitted only over the
local socket. Rootless Podman remains in the unprivileged service and managed
containers use `keep-id` ownership. Steam filesystem operations run in a fixed
internal worker after dropping to the selected player's UID with only the
`gemers` supplementary group, so player-writable paths are never traversed as
root. Installation of Heroic's unsigned upstream RPM was removed from the
privileged workflow. Its local Qt client instead verifies the official release
digest and RPM identity before handing the exact private temporary file to
PackageKit, whose polkit transaction performs interactive authorization outside
both the network API and its broker.

`systemd-analyze verify` accepts both units. On the deployed Fedora host,
`systemd-analyze security` reports **3.7 OK** for `game_mover.service` and
**4.9 OK** for the root broker. The broker's explicit ambient `CAP_SETUID` is
an accepted exception: it is bounded together with `CAP_SETGID` and used to
drop Steam filesystem workers to the selected player, never to raise the
network service's privileges. The running process table confirmed UID 955
(`gameplatform`) for Flask and UID 0 only for the broker. Runtime checks also
confirmed socket mode `0660 root:gameplatform`, successful allowlisted service
status, rejection of a UID 0 peer, rejection of `ssh.service`, migrated state
ownership, and a healthy version 0.31.0 API response.

A post-deployment regression in 0.31.0 prevented the unprivileged API from
inspecting rootless Podman containers because `ProtectHome=read-only` also made
`/run/user/<uid>/libpod` read-only. Version 0.31.1 gives the remote Podman client
a private runtime below the already allowlisted `/var/lib/game-platform`
hierarchy while retaining the rest of the service sandbox.

Version 0.31.2 separates read-only systemd status from privileged systemd
control. The unprivileged host API can report `is-active` for every registered
unit, while lifecycle operations and journal access still cross the root broker
and require its root-owned unit allowlist.

Version 0.31.3 repairs legacy rootless Podman data that predates `keep-id` and
is owned by subordinate host UIDs. Deployment grants the dedicated
`gameplatform` account an explicit ACL only below its fixed managed servers
root, installs a default ACL for newly created data, and uses a physical walk
that does not follow nested symbolic links. This restores mod inventory and
other intended management reads without making server data public or changing
container-visible ownership.

Deployment testing initially exposed an incompatibility between
`RestrictSUIDSGID` and the Mover's former `chmod 2775` inheritance mechanism.
The restriction remains enabled: shared-library inheritance was migrated to
POSIX default ACLs for the `gemers` group, while ordinary directory modes no
longer request SGID. The `acl` package is now an explicit runtime dependency,
ACL commands operate on a validated open directory descriptor, and recursive
processing does not follow nested symlinks. This preserves the measured
hardening score rather than accepting a weaker unit.

The ACL-based implementation and hardened service were subsequently deployed
and verified through both the local application path and the managed SSH tunnel
to the host. Mover operations and the surrounding host/client functionality
remained operational in both contexts.

Regression coverage exercises protocol size limits, peer authorization,
allowlists, traversal and symlink rejection, production routing of PAM and host
operations, service sandbox directives, rootless container identity, and the
disabled automatic Heroic update path. A clean RPM build and an upgrade from
0.30.1 completed successfully with both services active.

## GM-SA-2026-004: Missing PAM authentication rate limiting

- Severity: **Medium**
- Status: **Fixed**
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

### Resolution

Resolved on 2026-09-06. `/timekpr/auth` now serializes authentication attempts
and applies capped exponential backoff to both the normalized username and the
loopback source. The first failure waits one second before another PAM call is
allowed; subsequent failures double the interval up to 60 seconds. Throttled
requests return HTTP `429` with `Retry-After`, and both failed and throttled
attempts are written to the service journal without passwords. Tracking tables
have fixed entry limits and expire inactive state.

The endpoint no longer reveals whether a supplied account belongs to `wheel`;
invalid credentials and valid non-wheel accounts receive the same response.
PAM import and runtime failures likewise return a generic service-unavailable
message. The README documents that this process-local limiter resets on restart
and complements, but does not replace, persistent host policy such as
`pam_faillock`.

Regression coverage verifies the per-user and per-source dimensions, capped
backoff and tracking size, `Retry-After`, audit logging, response equivalence,
and reset after a successful authentication. The complete suite of 234 tests
passed.

## GM-SA-2026-005: Mutable CI dependencies and incomplete verification

- Severity: **Medium**
- Status: **Fixed**
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

### Resolution

Resolved on 2026-09-06. The RPM workflow now uses the fixed Fedora 43 release,
pins `actions/checkout` 4.2.2 and `actions/upload-artifact` 4.6.2 to reviewed
full commit SHAs, disables persisted checkout credentials, and grants the
workflow token only `contents: read`. Checkout fetches complete history so
Gitleaks examines every commit before any package is built.

The workflow installs its test and analysis tools from the signed Fedora
repositories, runs Ruff's Python security rules with documented project-specific
exceptions, executes the complete unit-test suite, and only then builds the RPM
and SRPM. Dependabot is configured to monitor both GitHub Actions and Python
dependencies weekly. Regression tests reject mutable Action references, a
floating Fedora image, missing least-privilege permissions, reordered checks,
or missing dependency monitoring.

The complete CI sequence was reproduced in a clean Fedora 43 container. Ruff
passed, Gitleaks scanned all 125 commits without finding a secret, all 237 tests
passed, and the RPM build produced `game-mover-0.30.1-1.fc43.noarch.rpm`.

## GM-SA-2026-006: Personal and infrastructure metadata in Git history

- Severity: **Low**
- Status: **Fixed**
- Relevant weakness class: CWE-200

### Description

The original review identified personal author e-mail addresses in Git
metadata and real-looking local usernames, a Tailscale address, LAN addresses,
and internal DNS examples in tracked content and historical blobs. No password,
API token, private key, or cloud credential was found by the review's heuristic
scans.

These values are not authentication secrets, but publication would create an
unnecessary permanent association between people and private infrastructure.

### Required remediation

1. Replace personal test fixtures and examples with reserved documentation
   identities, domains, and address ranges.
2. Configure a GitHub no-reply author address for future commits if desired.
3. Decide explicitly whether the privacy benefit justifies rewriting history.
4. Repeat secret scanning across all branches and tags immediately before
   publication.

### Resolution

Resolved on 2026-09-06. Personal-looking usernames and
infrastructure examples were replaced with neutral fixture identities,
reserved `.example` names, the `192.0.2.0/24` documentation range, and generic
addresses from the shared `100.64.0.0/10` range where tests specifically need
to exercise Tailscale/CGNAT handling. The standardized `home.arpa` suffix is
retained where the application intentionally models a private home DNS zone.

All reachable local refs and historical blobs were rewritten. Human-authored
commits now use the repository owner's GitHub no-reply identity; GitHub and
Dependabot commits retain their original no-reply identities. The same
no-reply identity is configured locally for future commits. Reflogs and
unreachable pre-rewrite objects were pruned after a private recovery bundle
was created outside the repository.

Post-rewrite verification scanned all 129 reachable commits for the replaced
metadata and found no match. Gitleaks scanned the same history without finding
a secret, all 237 tests passed, and the CI-scoped Ruff check passed. Publishing
the rewritten history still requires a coordinated force-push because all
rewritten commit IDs differ from the existing remote history.

## GM-SA-2026-007: Installer payload can drift from the RPM payload

- Severity: **Low**
- Status: **Fixed**
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

### Resolution

Resolved on 2026-09-06. The source tree naming convention is now the single
source of truth for Python payloads: both `install.sh` and the RPM `%install`
section consume every root-level `game_mover*.py` module through the same glob,
and the RPM `%files` section owns the corresponding installed glob. This also
adds the four modules that the alternate installer previously omitted:
`game_mover_endpoints.py`, `game_mover_launchers.py`,
`game_mover_pihole.py`, and `game_mover_satisfactory.py`.

A regression test requires both installation paths to derive their payloads
from the source glob and rejects a return to per-module include/install lists.
The complete suite of 238 tests passed. An isolated installer staging run and
a clean RPM build each contained exactly the same 27 Python modules as the
source tree; the CI-scoped Ruff check and full-history Gitleaks scan also
passed.

## GM-SA-2026-008: Public security and licensing policy is incomplete

- Severity: **Informational**
- Status: **Mitigated**

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

### Mitigation

Mitigated locally on 2026-09-06. The repository now carries the unmodified
PolyForm Noncommercial License 1.0.0, its required copyright notice, a private
vulnerability-reporting policy with supported-version guidance, and separate
branding rules that distinguish official builds from modified forks. The RPM
metadata uses the matching SPDX identifier, and both RPM and `install.sh`
deployments include the license, notice, security policy, and branding policy.

CurseForge remains an optional bring-your-own-key integration: operators must
obtain their own approved key, which stays in a root-provisioned host file
readable only by the backend account and is not distributed with the source or
packages. API and newly required CDN requests use that key only after validating
the official CurseForge destination; an unapproved redirect is rejected before
the key can be sent to it.

Regression coverage verifies the license and reporting files, their inclusion
in both deployment paths, mandatory host-side CDN authentication, authenticated
allowlisted redirects, and rejection of downloads without a key. On 2026-09-06,
the repository owner enabled Dependabot vulnerability alerts and automated
security updates through the authenticated GitHub API. GitHub rejected secret
scanning and push protection as unavailable for this private repository, and
reported that branch protection and rulesets require GitHub Pro or a public
repository. Private vulnerability reporting must be enabled when the repository
is made public. The finding remains `Mitigated` until the publication-time
controls are enabled or their absence is explicitly accepted with a written
rationale.

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
- After the GM-SA-2026-003 remediation, 262 automated tests passed.

## Publication gate

The repository must remain private while GM-SA-2026-001 or GM-SA-2026-002 is
open. Before publication, GM-SA-2026-003 through GM-SA-2026-008 must either be
fixed or explicitly accepted with a written rationale. A final review must
include the current tree, all branches and tags, packaged RPM contents, CI
configuration, and deployment documentation.

This review is a source, configuration, and Git-history assessment. It is not
a penetration test of a deployed host and does not certify that installed
dependencies are free of known vulnerabilities.
