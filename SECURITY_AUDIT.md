# Game Mover security audit register

Last review: 2026-09-16 (public repository controls verified)

Reviewed version: 0.36.0 (source, package, deployment, and live workflows)

Reviewed source: canonical `main` commit
`fe73b4c0779283648169a25b8b7ad145e9d41a93`.
Historical commit identifiers in this register may predate metadata rewriting.

Publication verdict: version 0.36.0 passed source review, hosted CI, package
inspection, deployment verification, and the required live per-player
move/launch checks. The canonical repository was published on 2026-09-16 and
all publication-time controls in the general gate below were verified.

## Repository publication: 2026-09-16

The canonical `michalbernacky-dev/game-mover` repository was changed from
private to public at exact main commit `fe73b4c`. Immediately before the
visibility change, full-history Gitleaks scanned all 170 reachable commits
without a finding; the identity review contained only the intended GitHub and
owner no-reply addresses; the example-data review was completed; and GitHub
reported only `main`, no tags, and a clean exact upstream tip.

GitHub post-merge RPM Build run `35121270238` passed for exact publication
commit `fe73b4c`. Its inspected Fedora 43 binary RPM and SRPM have SHA-256
digests `348778d58a415f5676fa5b654da4a9598a79ebbfd0605ddd19c89eb476f653de`
and `58196948a6db65c58f9eea494b8786484f3a771c05a420f29b71875184c9f1b9`.
Both RPM header and payload digests passed. Package identity and license were
correct, and the SRPM source archive was byte-for-byte identical to
`git archive` for the publication commit.

After the visibility change, GitHub API verification confirmed secret scanning,
push protection, dependency vulnerability alerts, automated security fixes,
and private vulnerability reporting are enabled. `main` protection requires a
pull request, a strict current `build-rpm` check, dismissal of stale reviews,
and conversation resolution; it applies to administrators and blocks force
pushes and branch deletion.

An unauthenticated API request returned the public repository and exact
publication commit. Each of the three known excluded web-merge SHAs returned
`422 No commit found`, and the historical `game-mover-rpm` repository returned
`404` through the same public boundary while remaining private to its owner.

## Publication readiness and live verification: 2026-09-16

The owner explicitly confirmed successful managed launches of GTA V Enhanced
through the Rockstar/Epic adapter and Star Wars Battlefront II through the
EA/Epic adapter on deployed version 0.36.0. The owner also confirmed that the
EA App game-data move and its conversion to the persistent bind mount work in
the live per-player installation. This completes the previously pending live
Rockstar/Epic launch, EA/Epic launch, EA mover, and bind-mount compatibility
checks. Direct launch from Heroic remains outside the Rockstar adapter, as
documented below. Runtime verification of the separate Heroic PackageKit
updater was not part of these confirmations and remains a functional follow-up,
not a security or publication blocker.

Read-only host verification found the installed package
`game-mover-0.36.0-1.fc44.noarch`; `rpm -V game-mover` reported no differences.
The API and privileged broker were active under `gameplatform:gameplatform`
and `root:gameplatform` respectively, and the loopback health endpoint reported
version 0.36.0.

For canonical main commit `90b76e6`, Ruff, full-history Gitleaks over 168
commits, all 327 unit tests, Bash syntax checks, and `git diff --check` passed.
GitHub post-merge RPM Build run `34981292767` succeeded for that exact commit.
Its inspected Fedora 43 binary RPM and SRPM have SHA-256 digests
`0f4e0070fed210e3bf98f005a2de590aa91c89922ec16a95b30434a183043920`
and `47e34c4a232b306be4d2571d4fef7b23b414100959ea041f3eae6758316ee3f6`.
Both RPM header and payload digests passed; the package identity, license,
dependencies, file list, scriptlets, and source contents were inspected. The
SRPM source archive was byte-for-byte identical to `git archive` for the exact
commit.

The first audit update was merged as canonical main commit `6106685`. GitHub
post-merge RPM Build run `35117436382` passed for that exact commit. Its
inspected binary RPM and SRPM have SHA-256 digests
`6f7c0d4374fa7f8ed280ee007c5c183855ecdf371acd925130f280279359fb77`
and `352ac7fecbb4a97656bd38c4ee3bb0e7ec0507e7f8b458f1df27162a387adeee`.
Both RPM header and payload digests passed, and the SRPM source archive was
byte-for-byte identical to `git archive` for `6106685`.

The accepted-risk update was merged as publication commit `fe73b4c`; its final
hosted build and artifact inspection are recorded in the repository publication
section above.

## Accepted public fixture-alias risk: 2026-09-16

- Status: **Accepted**
- Severity: **Informational**

The final example-data review found the aliases `Bernye` and `Luky` in tests
and historical audit examples. A public-web review found that `Bernye` and
`BernyeCZ` are already deliberately used as public gaming and technical
pseudonyms. `Luky` is a common, highly ambiguous diminutive and gaming alias.
The reviewed public results did not associate the two aliases with each other
or with Game Mover.

The repository contains no surname, age, address, credential, contact detail,
or family relationship for `Luky`. Neither alias is an authentication secret,
and Game Mover does not rely on login-name secrecy. Publication can add a small
amount of linkability between the public `Bernye` identity, this project, and
the fixture data, but it does not expose an authentication factor or identify
the person behind `Luky`.

On 2026-09-16, the owner explicitly accepted this low residual linkability
risk after reviewing the public search results. Rewriting or replacing the
clean publication repository solely for these aliases would be disproportionate
to the information exposed. This acceptance does not permit adding further
real names, account relationships, hostnames, addresses, or other personal
fixture data.

## Rockstar/Epic launch-state regression: 2026-09-15

Local runtime evidence after Heroic, GTA V Enhanced and Rockstar Launcher
updates showed that the existing game-directory `fix.bat` wrapper remained
present. Rockstar Launcher instead regenerated its private-prefix
`ProgramData/Rockstar Games/Launcher/titles.dat`; launching through the old
path then lost the Epic ownership context. Logs independently showed the
expected Epic portal and relaunch parameters reaching Rockstar, so this is a
launcher-state regression rather than evidence that an update deleted the
wrapper.

Version 0.36.0 adds a current-user-only `rockstar_epic` adapter for the single
verified GTA V Enhanced Epic app ID. Each managed launch resolves bounded
Heroic metadata, requires the recorded Proton runner, UMU, authenticated
Legendary state, both game shims and the Rockstar executable, and refuses to
continue while GTA or Rockstar processes are running. It atomically renames
only a small regular `titles.dat` to the fixed
`titles.dat.game-mover-disabled` path, then invokes Legendary with a
package-owned Wine wrapper. That wrapper validates its complete context and
executes `EpicGamesLauncher.exe PlayGTAV.exe` through UMU while preserving the
transient Epic arguments. Standard streams are detached so authentication
arguments are not captured by Game Mover logs.

The adapter performs no privileged operation and never reads another player's
home. Metadata symlinks, an escaping Proton `pfx`, unsupported app IDs, unsafe
cache objects and incomplete installations fail closed. Focused tests cover
inventory/UI classification, prerequisite redaction, process exclusion, the
actual cache rename, wrapper argument order and prefix escape rejection.
Source verification is complete only after the full suite and package checks
below pass; RPM installation and live post-update launch testing remain
pending. Direct launch from Heroic bypasses the adapter and remains an explicit
functional limitation.

## Current-player managed-launcher inventory: 2026-09-15

Version 0.35.0 adds read-only launcher cards for explicit installed entries in
the current player's Heroic sideload library and Lutris database. Detection is
limited to a fixed launcher-name catalog and authoritative `installed` records;
it does not infer a launcher from executables bundled in a game's Wine prefix.
The local Qt process performs the scan because private player homes remain
outside the service account's access. JSON, YAML, SQLite and Wine registry reads
are bounded or read-only, symlinked metadata is rejected, no Wine executable is
run, and no paths or registry contents are returned to the backend. Optional
versions come only from launcher metadata or a matching Wine product section.

Version 0.35.1 corrected the first locally reproduced Heroic update failure:
`pkcon --noninteractive` could not request PackageKit authorization. Live testing
then exposed a second failure, `user declined simulation`, because interactive
`pkcon` could not accept its confirmation without a terminal. Version 0.35.2
uses desktop polkit through a fixed `pkexec /usr/bin/pkcon` argument vector, then
runs `pkcon` noninteractively after that explicit administrator authorization.
It still performs no shell or sudo invocation and submits only the previously
downloaded and validated private temporary RPM. Regression coverage asserts the
exact executables and flags. Runtime deployment verification remains pending.

## EA per-player deployment regression: 2026-09-13

Live 0.33.0 testing reproduced a denied EA move while creating
`/var/Games_links/Luky/ea`. The root broker correctly dropped to the selected
player before traversing player-controlled data, but a legacy root-created
per-player proxy directory lacked owner/group write permission. Source
remediation prepares only the validated username/platform proxy components
before dropping privileges, opens every component with `O_NOFOLLOW`, rejects
a directory owned by an unrelated account, and assigns the exact player private
`0700` directories. It does not broaden access to another player's prefix.

The same live review found that the service inventory could not inspect a
properly private `/home/Luky`, while shared Steam payload directories were
treated as confirmed solely because they existed. The desktop client now scans
only the current player's private metadata under that player's identity.
Unverified shared payloads and Steam directories without launcher confirmation
are classified as possible remnants and hidden by default; a real Steam
manifest or EA installation metadata clears that state.

Focused tests cover legacy proxy repair, symlink rejection, dropped-privilege
dispatch, current-player EA/Epic discovery, and a large shared Steam payload
with and without a confirming manifest. Source tests pass; a new RPM,
installation, and live retry on the affected profile remain required.

## EA bind-mount compatibility remediation: 2026-09-14

Version 0.34.2 addresses the second live regression found after deploying
0.34.1. Although the hexadecimal path values were parsed, Fedora's PID 1
rejected the native `.mount` unit because its decoded `Where=` did not match
the path-derived unit name. EA mounts now use one packaged service template
whose instance is only a SHA-256 state identifier. A short-lived root helper
loads a root-owned mode-0600 marker, revalidates the player, game, fixed
`/var/Games/EA` source and home-confined real mountpoint, and invokes `mount`
with an argument array. The long-running broker still has no `CAP_SYS_ADMIN`;
the template bounds the short-lived process to `CAP_SYS_ADMIN` for mounting and
`CAP_DAC_OVERRIDE` for traversing private player homes. Repair
accepts only the exact legacy marker and a root-owned Game Mover unit, disables
and removes that old unit, and replaces an already mounted wrong source.

Version 0.34.1 corrects a live regression in the initial 0.34.0 mount-unit
renderer. Quoted `What=` and `Where=` values were passed to the mount helper
with literal quote characters on the deployed Fedora system, so the mounted
directory was not the shared EA payload. Paths now use systemd hexadecimal
escapes without quotes, and reapplying the operation restarts an existing
managed unit so both affected profiles are repaired in place. A mountpoint with
unexpected ownership is accepted only when its root-only marker exactly matches
the requested player, source, destination and unit; foreign units remain
rejected.

Live testing on two player profiles showed that EA App requested a complete
download after restart when the game directory was a Unix symlink, even with
the expected Wine registry data present. The same shared payload was recognized
when mounted at the identical path with a bind mount. This isolates the remaining
compatibility failure from the Epic entitlement handoff and registry repair.

Version 0.34.0 replaces EA source symlinks with persistent systemd bind mounts;
Steam remains unchanged. The API still supplies only platform, player and a
single-component game name. Heroic discovery, legacy-link validation and
mountpoint preparation run in the dropped-UID worker. The broker accepts the
mountpoint only from that worker, requires an owned real directory below the
selected player's home, derives the source exclusively from `/var/Games/EA`,
rejects foreign unit collisions, and records root-only ownership metadata.
Systemd performs and restores the mount, so the broker's capability bounding set
does not acquire `CAP_SYS_ADMIN`. Focused fixture tests cover legacy migration,
foreign-link rejection, home confinement, unit contents and broker routing.
Source verification is recorded with the implementing commit; RPM installation
and live validation on both affected profiles remain required.

## Heroic EA App Mover extension review: 2026-09-13

Version 0.32.0 extends mutation to complete EA App game payloads stored inside a
Heroic-managed Wine prefix. Read-only inspection of the model installation
confirmed Heroic's sideload library and matching `GamesConfig` entry as the
authoritative prefix mapping, the game payload below `Program Files/EA Games`,
and EA staged journal/state files for a paused download. No live data, service,
credential or installation state was changed.

The implementation confines configured prefixes to the player's home, rejects
Heroic's global shared default prefix, requires the EA launcher marker, rejects
symlinked game roots and single-component path escapes, and moves only one child
game directory into `/var/Games/EA`. Prefix, launcher, registry, credentials and
account state remain in place. Discovery,
staging checks and filesystem traversal execute in the broker worker after it
drops to the selected player's UID and groups. Ambiguous prefixes, staged
download journals, active Heroic/EA processes, pre-existing targets and unsafe
paths fail before mutation; the existing move/link rollback remains in force.
GOG and Epic mutation remains excluded.

Focused temporary-fixture tests cover Heroic mapping, home confinement,
shared-default rejection, symlinked roots, staged-download recognition, runtime
recognition, inventory, broker routing, and the actual move/proxy/source symlink
effects while proving that the prefix and launcher remain. Ruff, full-history
Gitleaks, all 292 unit tests, Bash syntax checks and `git diff --check` pass. A
local Fedora 44 RPM/SRPM build passed; payload and scriptlet inspection confirmed
the EA module, version 0.32.0 and `/var/Games/EA` ACL setup. GitHub PR #3 run
`34754643674` also passed its full RPM Build job. Installation, live functional
verification, merge and post-merge artifact inspection remain pending; this
review does not claim them.

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

The re-review initially found two publication blockers. Both are now resolved
for the clean publication repository:

1. The earlier GitHub Actions runs were red because two tests implicitly
   depended on the deployment account existing in the Fedora CI container. The
   production paths were not failing. The tests now inject a neutral fixture
   account. Successive hosted runs after the repair passed the history scan,
   Ruff, all 280 tests, RPM/SRPM build, and artifact upload, including a run
   using the updated Node 24 Actions.
2. Five commits rewritten during the 2026-09-09 identity repair remain directly
   retrievable from the old private `game-mover-rpm` repository by their old
   SHA. That repository is not the publication target and must remain private.
   A new private `game-mover` repository was created with a distinct repository
   identity and populated only from the reviewed reachable `main` history. All
   five superseded SHAs, plus the tip of the unrelated legacy repository that
   previously occupied the name, return GitHub's `No commit found` response in
   the new repository. The reviewed tip is available and its initial hosted RPM
   Build run passed.

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
network service's privileged workflow. Its local Qt client instead verifies the
official release digest and RPM identity, requests explicit desktop polkit
authorization through `pkexec`, and hands the exact private temporary file to
PackageKit. This remains outside both the network API and its broker.

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

### Generated CI source artifact regression: 2026-09-11

Inspection of the post-merge artifacts for commit `2b4afde` found that the SRPM
source archive contained `.ruff_cache`. The workflow ran Ruff before copying the
entire runner working tree with `rsync`, so generated runner state entered the
source artifact even though it was not tracked by Git. The binary RPM payload
was unaffected and no credential or private data was found, but that SRPM is not
approved for publication.

The workflow now creates its source tarball with `git archive HEAD`, so only
files tracked by the exact checked-out commit can enter the SRPM. The local
development build also explicitly excludes common analysis/test caches,
coverage output, environment files, keys, and logs. Regression coverage rejects
a return to CI working-tree `rsync` packaging and requires the local exclusions.
Post-merge hosted run 34749051335 passed on replacement-repository commit
`12afe18`. Its exact SRPM contained the same 85 entries and file contents as
`git archive` for that commit, with no cache, environment, key, log, editor, or
workflow paths. Its exact RPM payload, metadata, license, scriptlets, and
digests were inspected. Publication still requires the same checks for the
final audit-only publication commit.

The first hosted pull-request run of this remediation failed before creating an
archive because the Fedora container user did not own the Actions checkout and
Git rejected it as a dubious repository. The archive command now supplies the
exact current checkout as `safe.directory` in Git's command scope. It does not
persist global configuration and does not use the unsafe wildcard value. Tests
require the scoped exception and reject global or wildcard alternatives; the
hosted runs above verify the scoped command in the Fedora builder.

Tracked CI configuration and the local VS Code launch profile had previously
been excluded by the removed `rsync` rules. Repository export attributes retain
those exclusions without returning to working-tree packaging; the attributes
file itself is also excluded. Tests require all three `export-ignore` entries.

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

### Clean-repository isolation: 2026-09-11

The repository selected for publication is now a newly created private
`michalbernacky-dev/game-mover` repository with a distinct GitHub repository
identity. It was populated by pushing only the reviewed reachable `main` ref;
no mirror, tag, recovery ref, or unreachable local object was pushed. The old
`game-mover-rpm` repository remains private and is not a publication candidate.

GitHub returned `No commit found` for each of the five known superseded SHAs in
the new repository. The former tip of the unrelated legacy repository that
previously used the `game-mover` name returned the same result. The current
reviewed tip returned successfully, and the first hosted RPM Build on the new
repository passed. A complete recovery bundle of the deleted legacy repository
is stored privately outside the project. This isolates the publication history
and closes the hosted-history regression for the new repository; the old
`game-mover-rpm` repository must not be made public without a separate purge and
review.

### Web-merge identity regression: 2026-09-13

Three later pull requests were merged through GitHub after their branch commits
had passed the local identity checks. GitHub generated merge commits
`61a2960a40dc4d94d1608e3bc87ed39b0d15c17c`,
`2b4afde4603a4003c61b9d7b902c71cdcd57070b`, and
`112bb834e8f0153178d67d4a8d3f8e45acf70642` with the owner's personal author
e-mail. Their committer is GitHub's no-reply identity, and the five actual
content commits use the intended repository no-reply identity. This proves that
repository-local Git configuration and branch-commit review do not control the
author metadata of a merge commit created by GitHub's web interface.

The repository owner enabled GitHub's account-level private e-mail and exposed
command-line e-mail blocking settings. `AGENTS.md` now requires those controls
before web merges and requires inspection of the exact resulting merge commit.
A regression test preserves that process gate.

With explicit authorization, a private recovery bundle of the affected
repository was created outside the project and verified as complete. A clean
linear history was reconstructed from the last unaffected publication commit
by applying only the five reviewed content commits and omitting the three web
merge commits. Before the documentation changes in this review, its final tree
object exactly matched affected tip `112bb834`. The affected GitHub repository
must be retained under a private archival name, and only the clean reachable
`main` ref may be pushed to a newly created private `game-mover` repository.

The replacement private repository was created with a distinct GitHub repository
identity and populated only with clean `main`. It has no tags; the three excluded
merge SHAs return `No commit found`; and a fresh clone contains no unreachable
objects or unexpected identities. Dependency alerts and automated security fixes
are enabled. Pull-request run 34749000207 and exact post-merge run 34749051335
passed. Merge commit `12afe18` uses the account-specific GitHub no-reply author
and GitHub's no-reply committer. Its inspected RPM and SRPM SHA-256 digests are
`e8457e1bc4d2e18340dc61d187fb6910cb9f01ef7d70c0974b3f3bbd08a6be0b`
and `d8e7131c11830406da12e6f841130b17ac98db97198e164875f2745dbd90c1cd`
respectively. This closes the web-merge regression. The private archive and
recovery bundle must never become publication sources.

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
- Status: **Fixed**

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

The final clean-repository review found stale `game-mover-rpm` references in
the README, development checkout examples, security-advisory URL, and CI
artifact label. They were changed to the canonical `game-mover` repository and
the neutral `game-mover-packages` artifact name. Regression coverage now checks
the exact advisory destination and rejects the obsolete repository name in the
public-facing README and security policy. Six README commands that incorrectly
invoked the Bash installer through `sh` were also corrected to execute it
directly, and the regression check rejects that unsupported invocation. These
documentation corrections still require a green RPM Build for their exact final
commit before publication.

The repository instructions now also treat builds from laptops and other
workstations as exposed to machine-local contamination. They require the
canonical remote and clean history, a fresh clean checkout for publication,
review of ignored and untracked files, isolation from per-user RPM macros and
old build roots, and inspection of the exact SRPM/RPM payload and scriptlets.
Official artifacts should come from the green hosted workflow for the exact
reviewed commit. Regression coverage requires these safeguards to remain in
`AGENTS.md`.

The development workflow now uses short-lived branches and pull requests into
`main`. CI runs for pull requests targeting `main`, then runs again after merge
for the exact resulting commit; only artifacts from the reviewed green `main`
or version-tag run are publication candidates. Repository instructions prohibit
ordinary direct pushes, bypassing checks, and force-pushing or deleting `main`.
After visibility changes, GitHub protection must technically require pull
requests and the `build-rpm` check and must block force pushes and deletion.

The deployment policy now separates build provenance from authorization and
host mutation. Normal publication/production deployment requires the exact
post-merge `main` CI RPM and SRPM, recorded hashes and payload/scriptlet review,
an explicit host and rollback plan, RPM installation, and distinct post-install
identity, ownership, service, version, and functional checks. `deploy.sh` is
documented as a same-machine development tool because it rebuilds the current
working tree; CI must not receive production credentials or deploy automatically.
Regression coverage requires these deployment gates to remain in `AGENTS.md`.

### Publication resolution: 2026-09-16

Resolved when the canonical repository was made public in the coordinated
publication recorded above. The required security and licensing files were
already present in the exact publication tree. Secret scanning, push
protection, dependency alerts, automated security fixes, private vulnerability
reporting, and protected-branch enforcement were then enabled and verified
through the GitHub API. Anonymous checks confirmed the intended repository and
commit are public while the excluded history and old repository remain
unavailable through the public boundary.

## Controls observed during the review

### PAM-gated systemd registration: 2026-09-16

The server registry previously allowed an authenticated administrator to save
a custom systemd workload without granting the root broker permission to
control it. The resulting UI exposed lifecycle buttons that always failed at
the separate root allowlist boundary. Custom systemd registration is now
available only with an active local PAM session. The PAM-authenticated root
broker issues a short-lived, in-memory authorization that the API never returns
to the GUI; on registry save the broker verifies each exact `.service` is
loaded and atomically extends the root-owned allowlist. The API still cannot
write that file, and the API and broker units remain explicitly denied.

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

The repository must remain private while any Critical/High finding is open.
Before publication, other findings must be fixed or explicitly accepted with a
written rationale. The current concrete gates are:

1. repeat Gitleaks and identity/example-data review over every remote branch
   and tag immediately before the visibility change;
2. require a green RPM Build run for the exact publication commit and inspect
   its exact binary and source RPM; and
3. immediately after changing visibility, enable and verify private
   vulnerability reporting, secret scanning, push protection, and `main`
   branch/ruleset protection requiring pull requests and the `build-rpm` check
   while blocking force pushes and deletion, and repeat the known-old-SHA checks
   through the unauthenticated public boundary.

The initial publication completed all three items on 2026-09-16 for exact
commit `fe73b4c`; evidence is recorded in the repository publication section.
These remain release and repository-control invariants for future changes.

This review is a source, configuration, and Git-history assessment. It is not
a penetration test of a deployed host and does not certify that installed
dependencies are free of known vulnerabilities.
