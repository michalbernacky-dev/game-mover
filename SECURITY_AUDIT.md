# Game Mover security audit register

Last review: 2026-09-11 (publication readiness re-review)

Reviewed version: 0.31.4 (source, RPM, and local deployment)

Reviewed source: `d7bf92aa150cab77ba15784c1221aea696ab0679` plus the
CI-fixture and audit updates described in the 2026-09-11 review below.
Historical commit identifiers in this register may predate metadata rewriting.

Publication verdict: **not ready for a public repository**. Old rewritten
commits containing the owner's personal e-mail remain retrievable from GitHub;
see the GM-SA-2026-006 regression below.

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
| GM-SA-2026-006 | Low | Open | Personal and infrastructure metadata in Git history |
| GM-SA-2026-007 | Low | Fixed | Installer source list can drift from the RPM payload |
| GM-SA-2026-008 | Informational | Mitigated | Public security and licensing policy is incomplete |
| GM-SA-2026-009 | High | Fixed | Steam cache status GET invokes an unauthorized mutation |
| GM-SA-2026-010 | Medium | Fixed | Legacy server ACL migration widens existing access and affects hardlinked files |

## Publication readiness re-review: 2026-09-11

The current tree, reachable local and remote refs, Git metadata, binary RPM
payload, installation scriptlets, CI configuration, security and licensing
documents, service identities, and the deployed 0.31.4 backend were reviewed.
The remote has only `main` and no tags. All reachable commit authors and
committers use the intended no-reply or neutral identities. Gitleaks scanned the
complete reachable history without finding a secret. Ruff, all 280 unit
tests, Bash syntax checks, `git diff --check`, systemd unit verification, and a
fresh Fedora 44 RPM/SRPM build passed locally. The binary RPM contains the
expected 30 Python modules and policy/license files.

The deployed `game-mover-0.31.4-1.fc44.noarch` package passes `rpm -V`. Its ACL
helper is byte-for-byte identical to the reviewed source. Both API and broker
are active under `gameplatform:gameplatform` and `root:gameplatform`
respectively, and the loopback health endpoint reports 0.31.4. A root-authorized
read-only dry run of the production ACL migration traversed the managed server
tree with ACL writes disabled and proposed zero changes. It also encountered no
unsafe hardlink, inode replacement, or filesystem-boundary condition. No player
file contents, credentials, PAM state, or service data were changed during
verification.

GitHub dependency alerts and automated security fixes are enabled. While the
repository remains private on the current account, GitHub reports that branch
protection/rulesets require a public repository or a paid plan; private
vulnerability reporting is likewise unavailable. Those publication-time
controls therefore remain part of GM-SA-2026-008.

The re-review initially found two publication blockers. The CI blocker is now
resolved; the hosted-history blocker remains:

1. The earlier GitHub Actions runs were red because two tests implicitly
   depended on the deployment account existing in the Fedora CI container. The
   production paths were not failing. The tests now inject a neutral fixture
   account. Successive hosted runs after the repair passed the history scan,
   Ruff, all 280 tests, RPM/SRPM build, and artifact upload, including a run
   using the updated Node 24 Actions.
2. Five commits rewritten during the 2026-09-09 identity repair remain directly
   retrievable from GitHub by their old SHA, including the personal author and
   committer e-mail. Historical Actions runs make those SHAs discoverable. This
   is a reproduced GM-SA-2026-006 regression in the hosted repository, even
   though none of the objects is reachable from a branch or tag. GitHub must
   purge the cached/dangling objects and associated references, or publication
   must use a newly created repository containing only the reviewed history.
   Verify that every old SHA returns unavailable before changing visibility.

## Regression review: 2026-09-09

The review compared the current source against the previous resolutions,
including the privilege split preceding the latest Podman fixes. The API remains
unprivileged, the broker retains peer and systemd allowlist validation, and
Podman socket and broker peer UIDs are resolved from the local account database.
The runtime-directory fix does not disable the service sandbox. The fixed API
permission target, traversal checks, PAM throttling, CI configuration and shared
installer module glob remain present. This does not establish that every
filesystem migration is confined; see GM-SA-2026-010.

Ruff passed, Gitleaks scanned 138 commits without finding a secret, all 263 unit
tests passed, and Bash syntax checks passed. Additional temporary-directory
reproductions exposed the gaps below despite that green suite. No live deployment,
new RPM build, or GitHub publication-control verification was performed in this
review. Source-text tests and mocked privileged calls do not substitute for
production-path behavioral coverage.

GM-SA-2026-006 also regressed: five recent commits used the owner's personal
email again. The repository-local identity on this workstation was already the
no-reply identity. With the owner's authorization, all five affected commits in
the project branches were rewritten to the no-reply author and committer
identity. Their content trees were verified unchanged, and all project branches,
tags and remote-tracking branches were checked for the old email. A private
recovery bundle and commit mapping were kept outside the repository; internal
application snapshots and recovery data are not publication refs. `AGENTS.md`
now requires identity verification on each machine before every commit. Gitleaks
alone does not detect this privacy regression. Remote synchronization is a
separate lease-protected push and must be reported with its actual result.

## Compatibility regression: mixed-case login names (2026-09-09)

The privileged broker rejected existing interactive accounts with uppercase
letters before invoking Timekpr, although account discovery offered those users
in the GUI. Version 0.31.4 permits ASCII uppercase letters in the shared username
validator and retains the exact spelling for account lookup and command arguments.
Account existence and interactive UID/home checks, action allowlists and argument
bounds remain enforced; usernames are not lowercased or accepted as shell input.

A new regression test failed against the previous validator, then passed for
status, bonus time and schedule arguments after the fix. A second test verifies
rejection of path/option/shell-like input, missing accounts and system accounts.
All 280 tests, Ruff and full-history Gitleaks passed. No live Timekpr settings or
user sessions were changed; installed-host verification is pending.

## GM-SA-2026-009: Steam cache status GET invokes an unauthorized mutation

- Severity: **High**
- Status: **Fixed** (source, RPM, and deployed artifact verified)
- Affected component: `GET /steam_cache_status`, with the privileged helper enabled

The production branch of `steam_cache_status()` calls the broker action
`set-steam-cache` instead of inspecting status. `_set_steam_cache()` can move a
player's downloads into shared storage, replace the local path with a symlink,
and change shared permissions. The GET does not enforce the `steam.cache`
operation policy, local admin token, or PAM authorization. Direct non-loopback
requests are blocked by the network filter, but a local caller can reach it
without credentials. This predates the latest four commits and was introduced
during the broker integration. It violates the read-only principle documented
under GM-SA-2026-002 and the policy boundary for mutations; it does not establish
that the earlier path-traversal flaw itself returned.

A temporary-directory reproduction routed the Flask test client's unauthenticated
loopback GET to the actual cache mutation handler, substituting fixture account
paths and suppressing ACL changes. It returned 200, never called the operation
authorization guard, moved a fixture file into shared cache and created a symlink.
This was not a live root-broker exploit test.

Required remediation: introduce an inspection-only production path and retain
mutations solely behind the authorized POST operation. Add production-mode tests
proving GET leaves filesystem state unchanged and cannot bypass disabled/PAM
policy through a mutating action.

### Resolution

Resolved in source on 2026-09-09. The GET now calls a distinct
`steam-cache-status` broker action. The broker runs that inspection through its
existing filesystem worker after dropping to the selected player's UID/GID;
the root process does not inspect player-controlled paths. The worker and the
local API fallback share the same inspection-only helper. Missing libraries
return 404; a missing downloads directory remains a valid `missing` status.
Local, shared, custom and unexpected-file states retain the GUI response fields.

The `set-steam-cache` mutation is now routed from the POST endpoint, after its
existing `steam.cache` authorization check. Disabled policy, absent credentials,
and an admin token without the required PAM session cannot dispatch the mutation.
Authorized sharing still uses the unprivileged player worker.

Six added regression tests exercise real temporary filesystem fixtures through
the production API/handler routing (substituting the transport and user switch),
verify unchanged inode metadata and symlinks for status reads under all policy
modes, cover missing libraries and invalid users, verify POST authorization and
successful cache movement, reject remote requests, and check the worker's
requested UID/GID and supplementary group. All 269 tests pass, along with Ruff,
the full-history Gitleaks scan and Bash syntax checks. This is source verification,
not a live root-broker, installed RPM, or deployed-host verification. The separate
legacy server ACL finding GM-SA-2026-010 is addressed below.

The 0.31.4 deployment was verified on 2026-09-11: the installed package and
helper match the reviewed source, both services are active under their intended
identities, and the backend health endpoint reports 0.31.4. The status endpoint
was not invoked against live player data; its no-mutation property remains
verified by the production-path filesystem fixtures above.

## GM-SA-2026-010: Legacy server ACL migration has unintended effects

- Severity: **Medium**
- Status: **Fixed** (source, RPM, and deployment verified)
- Affected components: recursive server ACL migration in `install.sh` and RPM `%post`

The 0.31.3 migration intentionally restores `gameplatform` access to subordinate-ID
owned data. However, setting `m::rwX` can also activate previously masked rights
of existing groups or named ACL entries. In a temporary fixture, an existing
group entry changed from effectively `---` to `rw-`. This requires pre-existing
masked rights; it is not an unconditional public-data exposure.

`setfacl -R -P` skips symbolic links but does not isolate hardlinks: processing an
existing file hardlink inside the server tree also changes the ACL of the same
inode reachable outside that tree. This was reproduced on temporary files. It
requires an existing hardlink on the same filesystem and does not imply that an
unprivileged caller can create links to arbitrary root-owned files.

Required remediation: preserve intended legacy account access while retaining
other principals' effective permissions, reject or safely handle multiply linked
files, and validate confinement under concurrent path changes. Add behavioral
filesystem tests; the existing test only checks script text. Do not revert to a
root API or remove the needed Podman access repair as a workaround.

### Resolution

Resolved in source on 2026-09-09 for version 0.31.4. Both installation methods
invoke the same deployment-only `game_mover_acl.py` helper with isolated Python
imports. It resolves the real `gameplatform` account locally and preserves
existing file ownership. Before expanding the target account's access, it
intersects other group-class ACL entries with their old mask, retaining their
effective permissions. This applies to existing default ACLs as well. When no
default exists, new defaults grant the future owner and `gameplatform` access,
without copying directory group/other permissions that could otherwise bypass
a restrictive creation umask. File creation modes and subsequent explicit chmod
still limit inherited access; the migration does not override application policy.

Every root path component is opened without following symlinks. Children are
selected with directory-relative `O_PATH|O_NOFOLLOW` descriptors and inspected
before reopening that same inode. ACL reads/writes use file descriptors and
POSIX ACL extended attributes; no recursive pathname-based setfacl is used.
Symlinks and special files are skipped. Multiply linked regular files and
different-filesystem descendants cause an error before their ACL is changed.
Inode/link-count checks also reject detected changes during processing. The
installer aborts on errors, and the RPM scriptlet explicitly returns failure;
there is no permissive fallback. Existing server-root ACLs are no longer
preemptively changed by `install -d` before migration.

The migration is idempotent but not transactional and does not lock out writers.
Run it with server-data writers stopped for a stable whole-tree result. A held
descriptor protects against pathname redirection, not against another process
moving that selected inode or creating new hardlinks after the final check.
Already repaired entries remain repaired after an error; resolve the cause and
rerun. Neither installation method automatically deletes links or rewrites
container-visible owners to force success.

Nine new tests cover mask preservation for named users/groups, owner versus
default ACL behavior, real ACLs and inheritance, repeatability, external symlinks,
special files, hardlinks, symlinked root components, and path/hardlink replacement
after inode selection. The ACL suite also passed outside the single-UID sandbox
with a distinct target UID. All 278 tests, Ruff, full-history Gitleaks, and Bash
syntax checks passed. A local Fedora 44 RPM/SRPM build succeeded; the RPM contains
all 30 Python modules and calls the shared helper from its postinstall scriptlet.
No live host installation, service restart, or active-container migration was
performed. Fedora 43 CI and deployed-host verification are separate checks.

Deployment verification completed on 2026-09-11. The installed helper is
byte-for-byte identical to the reviewed source, `rpm -V` is clean, systemd unit
verification passes, and both services are active under the expected accounts.
A root-authorized read-only dry run over the production managed server tree,
with the ACL writer replaced by a counter, completed without a confinement
error and proposed zero ACL changes. This verifies that the deployed migration
reached an idempotent state without modifying live player data during review.

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

### CI regression review: 2026-09-11

The latest ten hosted workflow runs were failing, including the run for 0.31.4.
The workflow correctly reached and executed the complete test suite; two
Minecraft installation tests then tried to resolve the production
`gameplatform` account, which does not exist in the clean Fedora CI container.
The tests now inject a neutral UID/GID fixture while continuing to exercise the
real environment construction. Both formerly failing cases and the complete
local suite pass. Hosted run 34628562732 then passed Gitleaks, Ruff, all 280
tests, the Fedora 43 RPM/SRPM build, and artifact upload for commit `19797f3`.
Its only annotation reported that the pinned v4 Actions declared deprecated
Node 20 runtimes. Both Actions were subsequently updated to their reviewed
v7.0.1 full commit SHAs, which declare Node 24; the exact publication commit
must retain a green hosted run.

## GM-SA-2026-006: Personal and infrastructure metadata in Git history

- Severity: **Low**
- Status: **Open** (hosted dangling objects remain accessible)
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

### Hosted-history regression: 2026-09-11

The rewritten `main` branch is present on GitHub and is the remote's only
branch; the remote has no tags. Nevertheless, five superseded commits remain
retrievable from the GitHub API by their old SHA and expose the owner's personal
author and committer e-mail. Those SHAs are discoverable in retained historical
Actions runs. This is hosted evidence, not merely a local unreachable-object
artifact, and it reopens the finding for publication.

Before visibility changes, have GitHub purge the cached/dangling commits and
their discoverable references, or publish a newly created repository populated
only from the reviewed reachable history. Recheck each known old SHA through
the unauthenticated/public boundary; it must be unavailable. Do not publish the
old local recovery bundle or reconnect the superseded history.

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

The 2026-09-11 API recheck confirms that dependency vulnerability alerts and
automated security fixes remain enabled. GitHub still rejects rulesets and
branch protection for this private repository on the current plan, and private
vulnerability reporting is not available before the visibility change. When
the history privacy blocker is resolved, change visibility in a coordinated
maintenance window and immediately enable private vulnerability reporting,
secret scanning with push protection, and `main` protection requiring the RPM
Build workflow. Verify each control through the API before announcing the
repository as public.

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

The repository must remain private while any Critical/High finding or the
GM-SA-2026-006 hosted-history regression is open. Before publication, other
findings must be fixed or explicitly accepted with a written rationale. The
current concrete gates are:

1. remove or isolate every hosted superseded commit recorded under
   GM-SA-2026-006, then verify the old SHAs are unavailable;
2. commit and push the CI fixture repair and require a green RPM Build run for
   the exact publication commit;
3. repeat Gitleaks and identity/example-data review over every remote branch
   and tag immediately before the visibility change;
4. inspect the exact binary and source RPM produced by the green run; and
5. immediately after changing visibility, enable and verify private
   vulnerability reporting, secret scanning, push protection, and `main`
   branch/ruleset protection.

The 2026-09-11 review completed the current-tree, remote-ref, package,
deployment-documentation, local-host, and CI portions of this gate. Item 2 is
complete, and the exact Fedora 43 CI artifacts satisfy item 4. Publication is
blocked by item 1; item 3 must be repeated immediately before visibility changes,
and item 5 is necessarily a coordinated post-visibility step on the current
GitHub plan.

This review is a source, configuration, and Git-history assessment. It is not
a penetration test of a deployed host and does not certify that installed
dependencies are free of known vulnerabilities.
