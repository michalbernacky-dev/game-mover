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
- Preserve the Steam-only mutable Mover scope; do not migrate or delete legacy
  Heroic/GOG/Epic data. Heroic package installation belongs to local
  PackageKit/polkit after asset verification, not the API or root broker.
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

Use the RPM workflow (`build_rpm.sh` / `deploy.sh`) for normal deployment;
`install.sh` is the manual bootstrap alternative. These are Bash scripts: use
`./install.sh` or `bash install.sh`, not `sh install.sh`. Both installation paths
must include all `game_mover*.py` modules, required policy/license files, units,
dependencies, and equivalent state/ownership migrations. Keep the version in
`game_mover_version.py` synchronized with `game-mover.spec`.

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
