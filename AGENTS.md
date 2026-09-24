# Repository instructions

These instructions travel with this repository and apply on every workstation,
notebook, remote checkout, and deployment host. Read them before changing code,
creating commits, reviewing security, or deploying. Do not rely on a previous
chat, a machine's global Git settings, or remembered numeric account IDs.

## Sources of truth

- Read `SECURITY.md` for the deployment boundary and disclosure policy.
- Read `SECURITY_AUDIT.md` for resolved findings, accepted exceptions, open
  regressions, and publication conditions. A historical `Fixed` label is not
  proof that today's code or deployed host remains safe.
- Read the relevant parts of `ARCHITECTURE.md` and `README.md` for supported
  workflows. Check both systemd units, `game-mover.spec`, `install.sh`,
  `build_rpm.sh`, `deploy.sh`, `ruff.toml`, and `.github/workflows/rpm-build.yml`
  when changing packaging, security, or deployment.
- Explicit user instructions take precedence. Point out material conflicts and
  their consequences; do not silently weaken a security control to fix a
  functional problem. Routine work within the authorized scope needs no new
  approval. Never mark a finding fixed solely to make documentation consistent.

## Branch and merge workflow

`main` is the protected integration and release branch. Start ordinary work
from the current `origin/main` on a short-lived, descriptively named branch.
Commit and push that branch, open a pull request targeting `main`, and require
the complete `RPM Build` check to pass before review and merge. Do not push
directly to `main`, bypass a failed or pending check, or force-push/delete
`main`, except for an explicitly authorized emergency recovery with its reason
and verification recorded.

Review the complete pull-request diff, commit identities, audit impact, and any
generated or vendored content before merging. A branch becoming public exposes
all commits reachable from it, so secrets, personal data, production examples,
and uncoordinated vulnerability details must never be committed even
temporarily. After merging, require a new green `RPM Build` run for the exact
resulting `main` commit; inspect and publish artifacts from that run, not from
the pre-merge branch run. Delete the merged short-lived branch only after the
result and remote state have been verified.

GitHub creates web merge commits with the authenticated account's selected
commit e-mail, independently of the branch commits and repository-local Git
configuration. Before any web merge, enable GitHub's **Keep my email addresses
private** and **Block command line pushes that expose my email** account
settings. After every merge, inspect the exact resulting commit's author and
committer names and e-mail addresses. Branch commit metadata and a green check
do not prove that the web-generated merge commit is private. If a personal
address appears, stop, keep the repository private, record the regression, and
repair the hosted history before any further merge or publication.

When GitHub repository controls are available, protect `main` by requiring a
pull request and the `build-rpm` status check, dismissing stale approvals when
appropriate, and blocking force pushes and deletion. Until GitHub can enforce
those settings, treat this section as a mandatory process gate rather than
permission to merge directly.

## Git identity and private data

The canonical repository is `michalbernacky-dev/game-mover`. The former
`game-mover-rpm` repository is historical, must remain private, and must never
be used as a release source or made a remote of the canonical checkout. On every
new workstation and before a release, inspect `git remote -v`, the repository
owner/name, all local and remote branches and tags, and the exact upstream tip.
Stop if a remote points at the old repository or if unexpected refs or history
are present. Do not copy an old `.git` directory, objects, refs, tags, build
directory, or recovery bundle into a clean clone.

Before every commit, inspect `git status --short`, `git var GIT_AUTHOR_IDENT`,
and `git var GIT_COMMITTER_IDENT`. For this owner's human/agent-authored commits,
use the following repository-local identity:

```bash
git config --local user.name "michalbernacky-dev"
git config --local user.email "michalbernacky-dev@users.noreply.github.com"
```

Environment variables can override local configuration. Correct unexpected
author/committer overrides before committing and verify the resulting commit
metadata. Preserve legitimate bot and other contributors' identities; do not
attribute their work to the owner. Never change global Git identity implicitly.
Local configuration is not cloned, so repeat this check in each new checkout.

Do not commit personal email addresses, real infrastructure examples, tokens,
passwords, API keys, private keys, recovery bundles, or deployment logs containing
credentials. Use neutral fixture users, `.example` domains and documentation IP
ranges; retain special networking ranges only where behavior requires them.
Gitleaks does not replace a separate author/committer and example-data review.

History rewriting must be explicitly authorized. Before rewriting, inventory
all branches/tags and the remote tips, preserve a private recovery bundle outside
the repository, and preserve unrelated changes. Verify content trees as well as
metadata afterward. Never publish recovery refs or bundles. Update remote history
with an explicit expected-tip `--force-with-lease`, not unrestricted `--force`.
If the remote changed, inspect the new commits instead of overwriting them.
After a rewrite, other machines should use a fresh clone or carefully realign
their checkout after preserving local work; never merge the old history back.

## Security invariants

- Flask runs as `gameplatform`, without root or Linux capabilities. Preserve the
  service sandbox and its explicit writable paths. Do not fix access errors by
  running the API as root, disabling hardening, or broadly opening permissions.
- Keep privileged operations in the local Unix-socket broker. Preserve
  `SO_PEERCRED` authorization, bounded requests, semantic action validation,
  root-owned credentials and the service allowlist. No client-supplied shell
  commands or arbitrary privileged filesystem paths.
- Systemd lifecycle and privileged logs require the broker's allowlist. Reading
  registered unit status locally is intentional. Neither the API nor broker
  unit may be controlled through management operations.
- Rootless Podman belongs to `gameplatform`. Resolve UID/GID through the local
  account database (`pwd`, `grp`, `id`), never hardcode values such as 955.
  Preserve `keep-id` for managed containers and the separate client runtime at
  `/var/lib/game-platform/runtime`. Distinguish socket access, runtime access,
  storage ownership, and subordinate-ID mappings when diagnosing failures.
- Preserve working legacy data access across upgrades. Ownership/ACL migrations
  must stay within approved roots, handle symlinks and hardlinks, preserve other
  principals' effective permissions, and address concurrent path replacement.
  A fixed path string or `setfacl -P` alone does not prove confinement.
- Permission repair accepts a fixed target identifier, never a client path.
  Validate managed roots and traversal, use descriptor-relative operations where
  needed, and reject unsafe filesystem objects. Player filesystem workers must
  drop privileges before traversing player-controlled paths.
- GET/status operations must not create directories, move data, change ACLs,
  create symlinks, or call a mutating broker operation. Every mutation must enforce
  its applicable `silent`/PAM/disabled policy; loopback alone is not authorization.
  Preserve authenticated remote read-only restrictions and PAM throttling.
- Preserve the narrow Steam and Heroic-managed EA App Mover scope. For EA App,
  move only a complete child game directory below `Program Files/EA Games` into
  `/var/Games/EA`; keep the Wine prefix, launcher, registry, credentials and
  account state in the player's profile. Discover the prefix from Heroic's
  sideload configuration, require it to differ from Heroic's global shared
  default prefix, reject staged downloads and active Heroic/EA processes, and
  never guess a prefix from an arbitrary client path. Do not
  migrate or delete legacy Heroic/GOG/Epic data. Heroic package installation
  belongs to local PackageKit/polkit after asset verification, not the API or
  root broker.
- Keep CurseForge credentials host-side. Validate destinations before sending
  credentials, including redirects; preserve download integrity, archive path,
  extraction-size, backup/restore, and managed deletion checks.

## Verification and audit records

For a security review, map affected code and production execution paths to the
audit IDs. Include adjacent migration commits when the issue predates the last
few commits. Distinguish a reproduced regression, a plausible risk, a documented
exception, and something not verified on the deployed host.

Before committing code for deployment, run the repository's CI checks:

```bash
ruff check game_mover*.py
gitleaks detect --source . --no-banner --redact
python3 -m unittest discover -v
bash -n install.sh build_rpm.sh deploy.sh
git diff --check
```

Do not disable checks, broaden Ruff ignores, or weaken tests to make a change
pass. Report missing tools or environment limitations rather than claiming a
pass. Add focused behavioral regression coverage for security fixes, including
the `PRIVILEGED_HELPER_ENABLED=True` path where relevant. Test malicious paths,
authorization failures and actual filesystem effects in temporary fixtures;
source-text checks or a mocked broker alone do not establish runtime safety.
Do not touch live player data, real credentials, PAM state or running services
while testing. Documentation-only edits need proportionate verification.

Update `SECURITY_AUDIT.md` when discovering or fixing a regression, recording
evidence, affected paths, remaining limitations and validation. `Fixed` requires
reviewed remediation and regression coverage; distinguish source verification
from installation/runtime verification. Keep open regressions visible to the
next machine and session. Never publish an uncoordinated vulnerability report.

## Packaging and deployment

RPM is the normal packaging and deployment unit. `build_rpm.sh` creates a local
development package, `deploy.sh` builds and installs that working tree on the
same development machine, and `install.sh` is the manual bootstrap alternative.
These are Bash scripts: use `./install.sh` or `bash install.sh`, not
`sh install.sh`. Both installation paths must include all `game_mover*.py`
modules, required policy/license files, units, dependencies, and equivalent
state/ownership migrations. Keep the version in `game_mover_version.py`
synchronized with `game-mover.spec`. Every release must also add a newest-first
entry with meaningful user-facing notes and the release date to
`game-mover.metainfo.xml`; keep its latest version synchronized with the
application and RPM versions. Preserve the canonical repository homepage in
both the AppStream metadata and the RPM `URL` field. Validate changed desktop
metadata with `appstreamcli validate --no-net game-mover.metainfo.xml` and
`desktop-file-validate game-mover.desktop`.

Treat workstation builds as potentially contaminated by machine-local state.
`build_rpm.sh` packages the current source directory and its explicit exclusions
do not automatically honor `.gitignore`; an ignored or untracked credential,
editor file, patch, log, backup, or generated artifact can therefore enter the
source archive. For anything intended for publication:

1. Prefer the RPM and SRPM produced by the green GitHub `RPM Build` workflow for
   the exact reviewed commit. A laptop build is a development artifact unless
   the same clean-build and inspection requirements are completed.
2. Start from a fresh canonical `game-mover` clone with only the intended
   branch checked out. Verify `HEAD`, `origin/main`, `git remote -v`, every
   branch/tag, and `git status --short --untracked-files=all`; do not build a
   release from a dirty tree.
3. Inspect ignored files as well as untracked files before building. Do not
   assume `.gitignore`, Gitleaks, or the `rsync` exclusions keep machine-local
   files out of the source archive. Never place credentials, recovery data,
   production configuration, deployment logs, or personal fixtures anywhere
   under the release checkout.
4. Treat per-user RPM configuration such as `~/.rpmmacros`, environment
   overrides, signing configuration, and cached build roots as inputs. Use a
   clean Fedora build environment for official artifacts, and never reuse an
   old repository's `.rpmbuild` output as publication evidence.
5. Inspect the exact generated SRPM source archive, binary RPM file list,
   scriptlets, metadata, license, and digests. Confirm that they contain only
   reviewed files and that their version and source revision match the intended
   publication commit. A successful `rpmbuild` alone is not approval to upload.
6. Immediately before publishing, repeat full-history Gitleaks plus a separate
   author/committer, remote/ref, example-data, and package-payload review. After
   pushing, require a green hosted run for that exact commit and publish only
   the inspected artifacts from that run.

### Deployment flow

CI builds packages but does not authorize or perform deployment. Do not add
production credentials, SSH deployment, package installation, service restart,
or other host mutation to a pull-request or push workflow. Every deployment is
a separate, explicitly authorized operation with a named target host and exact
source commit.

For a normal deployment:

1. Merge the reviewed change through a pull request, update the deployment
   checkout to the resulting `origin/main`, and verify that the hosted `RPM
   Build` succeeded for that exact merge commit. A green branch or pre-merge PR
   run is not sufficient.
2. Download that run's binary RPM and SRPM into a fresh staging directory.
   Record their hashes and inspect their identity, version/release, digests,
   license, file lists, source archive, dependencies, and scriptlets. Do not
   substitute a similarly named laptop artifact or an older successful run.
3. Before changing the host, record the currently installed package and service
   state, confirm configuration/data backup and rollback arrangements, verify
   free space and the intended maintenance impact, and state which services may
   be restarted. Never include secrets or player data in the deployment record.
4. Install the inspected RPM through the documented RPM path. Do not use
   `deploy.sh` for a publication or production deployment: it rebuilds the local
   working tree and is only for an explicitly authorized same-machine
   development deployment. Do not replace RPM scriptlets with ad hoc copies,
   ownership changes, or permission broadening.
5. Treat package installation, migrations, service enablement, and restarts as
   mutations requiring deployment authorization. Do not touch live player data,
   PAM state, credentials, containers, or services merely to validate a build.
6. After installation, verify `rpm -V`, the installed version, unit syntax,
   actual API/broker process identities, socket and credential ownership,
   service health, the loopback health/version endpoint, and the functionality
   affected by the change. For Podman changes, test under the service sandbox
   and `gameplatform` identity rather than only from a root shell.
7. If a migration, package verification, service, or functional check fails,
   stop and preserve sanitized diagnostics. Follow only the rollback plan agreed
   before deployment; do not improvise destructive cleanup, restore data, or
   weaken a security control to make the service start.
8. Report source verification, hosted build, artifact inspection, installation,
   migrations, restarts, and live verification as distinct results. Record the
   exact commit, package NEVRA and hashes, target class, failures, rollback, and
   remaining limitations without recording private host details or credentials.

Before an authorized deployment, establish the intended host and exact source
revision, inspect uncommitted changes, and finish applicable checks. Build and
inspect the RPM payload when packaging changes. Never assume a successful build
means security tests ran: `deploy.sh` currently does not run them. Do not deploy
known security regressions as if they were fixed; report their impact and honor
the user's explicit decision about proceeding. A review request alone does not
authorize installing packages or restarting services.

After service changes, verify unit syntax, actual process identities, socket and
credential ownership, service health/version, and relevant functionality on the
target host. For Podman changes, verify status and intended data access under
the service sandbox, not only from an interactive root shell. Compare hardening
with the recorded baseline, explaining accepted differences rather than relying
only on a numeric score. Distinguish tests, package build, installation and live
verification in the completion report. Never claim remote/host checks that were
not performed.
