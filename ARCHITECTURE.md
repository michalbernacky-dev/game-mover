# Game Platform: target architecture

This document records the agreed product direction. The existing **Game Mover**
name and package remain in place while the application grows into a broader
Linux gaming platform.

## Product scope

Game Platform is a host-side service and desktop client for managing games,
dedicated game servers, their persistent data, and Minecraft server instances.
It is intended for a trusted home LAN and Tailscale network; it is not designed
to expose its management API or game backends directly to the public Internet.

## Privilege boundary

The network-facing Flask API runs as the unprivileged `gameplatform` account
with an empty capability set, a read-only host filesystem and read-only home
directories. Its explicit writable paths are limited to the application state,
rootless workload data, backups, proxies and shared game libraries. Rootless
Podman uses the same account; platform-created Minecraft containers use
`--userns=keep-id` and the account's real UID/GID so their persistent files do
not require host root ownership repair.

Host operations that cannot run unprivileged cross a local Unix socket to
`game-mover-privileged.service`. The broker accepts one bounded JSON request,
checks the peer UID with `SO_PEERCRED`, and dispatches only semantic actions for
Steam moves/links, shared permissions, PAM, Timekpr, Pi-hole and allowlisted
systemd services. It has no TCP/IP address family and cannot execute arbitrary
client-supplied commands or paths. The API cannot control its own unit or the
broker. Additional adopted systemd services must be entered manually in the
root-owned `/etc/game_mover/allowed-services.json`; the API cannot modify that
allowlist.

Mutable registry, DNS, Gate and security-policy state lives under
`/var/lib/game-mover`, owned by `gameplatform`. Authentication tokens and the
CurseForge BYOK credential remain root-provisioned under `/etc/game_mover`.
The split service never installs upstream Heroic RPM files. The local Qt client
downloads the exact official asset, validates its release digest and RPM
identity, and submits the private temporary file to PackageKit. PackageKit and
the desktop polkit agent own the privileged transaction and interactive
authorization; neither the network API nor its root broker can install a
package.

The platform has two complementary workload models:

- Generic systemd services remain first-class workloads. This supports
  Satisfactory and other present or future non-Minecraft game servers.
- All platform-created Minecraft servers ultimately run as isolated rootless
  Podman containers owned by the dedicated `gameplatform` account. The current
  systemd Forge server is a migration source, not a restriction on generic
  systemd support. Existing Minecraft servers may also be adopted from systemd
  and remain supported for status, lifecycle, backups, management, and Gate Lite
  routing; self-service creation does not create new systemd units.

Every workload can register an ordered set of host-facing network endpoints.
An endpoint has a human-readable name, `tcp` or `udp` protocol, and a validated
port. This common registry is the source for server cards, connection hints,
host-port conflict checks and future firewall/Tailscale policy assistance.
Game-specific protocols such as Minecraft status, RCON, Steam query ports or
Satisfactory reliable messaging remain capabilities layered on this neutral
network model.

## Desktop game-data ownership

The mutable **Mover** workflow supports Steam and one narrowly bounded EA App
layout. Steam payloads move into `/var/Games/steam`; complete EA App game
payloads move into `/var/Games/EA`. Both use per-user proxy symlinks. Steam also
manages its shared download cache and library permissions.

The EA adapter accepts only an EA App sideload entry found in Heroic's own
configuration and a Wine prefix confined below that player's home. It moves one
child directory below `drive_c/Program Files/EA Games`, never the prefix itself.
The EA prefix must be distinct from Heroic's global shared default prefix; a
dedicated child such as `Prefixes/default/EA App` may still be shared by the EA
launcher and its EA game shortcuts. A global default is read-only inventory and
must be migrated or reinstalled before Mover enables mutation.
The launcher, Wine registry, credentials and account state remain private in the
player profile. Staged EA download journals and running Heroic/EA processes make
move or link operations fail closed. The filesystem worker performs discovery
and traversal after dropping to the selected player's UID; the broker receives
only a platform, player and single-component game name.

GOG and Epic installation data belong to Heroic, which already separates a
game's payload from its per-user Wine prefix and presents that relationship to
the user. Game Mover does not copy Heroic prefixes, rewrite Wine registries or
maintain competing installation metadata. Other launchers remain read-only
inventory/knowledge providers until their storage and lifecycle have been
implemented and tested explicitly. EA support does not grant a general Heroic
prefix mover or modify Heroic's GOG/Epic data. Existing legacy directories below
`/var/Games` are preserved; narrowing the supported workflow never deletes or
migrates them automatically. These providers remain visible in Mover's selector
with an explicit read-only or externally managed state rather than disappearing
from the product model.

Epic-owned titles that delegate execution to EA App are a narrow launch adapter
on top of the same ownership model, not a new prefix manager. Executable game
metadata lives in the installed-game catalog (`launcher_type=ea_epic`, Epic app
name, shared EA directory and logical EA prefix name); the SQLite knowledge base
continues to store human procedures and declarative checks. The first verified
entry is Battlefront II (`MtMassive`). The adapter resolves the dedicated EA App
prefix and its Proton runner from Heroic sideload/GamesConfig metadata, supports
both direct Wine and Proton `pfx` layouts, prefers Heroic's UMU runtime and uses
Lutris UMU only as a fallback. The Qt client and installed generic helper run as
the current desktop user. They never call the root broker, copy launcher state,
or select another player's home.

The launch chain is `Legendary --origin` → `link2ea://` → generic Wine wrapper →
UMU → configured Proton → that player's EA App `start.exe`. Legendary owns the
short-lived Epic exchange-code lifecycle. Its launch output is detached from
the GUI and application logs; defensive error rendering redacts
`AUTH_PASSWORD`. Only the transient process arguments needed by EA App carry
the generated URL. Launch fails closed when Heroic/Legendary authentication,
EA App, `start.exe`, UMU, the configured Proton runner, shared payload, or the
player's symlink is missing.

Adapters may discover endpoints from an authoritative game/runtime source and
merge them with optional registry entries. Minecraft reads `server.properties`
or Podman publication metadata. The Satisfactory systemd adapter reads the
effective `ExecStart`, recognizes explicit game and reliable-messaging ports,
and otherwise applies the documented Satisfactory defaults. The API and GUI
retain the source of each effective endpoint so discovered state is not confused
with a potentially stale manual value.

## Minecraft self-service

The intended experience is a small private alternative to a hosted Minecraft
control panel. Two or more family members can create independent Vanilla,
Forge, Fabric, NeoForge, or other supported instances for their friends and may
run multiple instances side by side when host resource limits permit.

Each managed instance has:

- an immutable/recreatable container and separately persisted server data;
- an explicit image, Minecraft version, loader/modpack and resource limits;
- lifecycle, health and player status;
- verified backup, restore and migration metadata;
- a hostname route through the shared Gate Lite ingress, with an optional
  directly published port as a compatibility fallback; and
- policy-controlled management operations without shell or direct Podman
  access for ordinary users.

Gate Lite is the final shared Minecraft ingress on TCP 25565. It reads the
virtual hostname from the initial Minecraft handshake and then transparently
passes the original connection, authentication, and mod-loader negotiation to
the selected backend. Backends therefore keep `online-mode=true` and require no
proxy-forwarding mod or shared secret. During migration Gate Lite is staged on
TCP 25581 and routes to the existing host Forge service on 25565. The final
Podman topology routes hostnames to stable private container aliases; an
explicit unique LAN port remains available for servers that need a fallback.

Gate Lite resolves backend targets through the workload adapter rather than
assuming one runtime:

- a managed Podman Minecraft backend joins `game-platform` and is routed by its
  private network alias and container port, without publishing that port to LAN;
- an adopted systemd Minecraft backend is routed through the host gateway and
  its validated `server.properties` port;
- runtime-specific discovery stays behind the adapter, while RCON, properties,
  players, logs, backup and UI management use common workload capabilities.
- the standard Minecraft status response is the authority for the client version
  name and protocol displayed by the UI; image tags are not a substitute, while
  RCON may independently remain the player-count authority.
- a persisted Gate route becomes the recommended player endpoint on overview
  cards. Per-server management retains both that ingress endpoint and the direct
  backend endpoint; an unresolved or absent route leaves direct access unchanged.

## User interface

A compact capacity strip remains visible below the top-level tabs. It reports
RAM pressure, genuinely available memory, and swap use for the machine relevant
to the current management context: the local workstation normally, or the game
server host while the GUI-owned SSH tunnel is active. This is a read-only,
periodically refreshed planning aid for deciding whether another workload can
be started; it requires no PAM session. Disk bars remain in Mover because they
describe the local game libraries rather than generic host capacity.

The host also owns a small SQLite knowledge base for operational notes that do
not belong in workload configuration. Notes target a stable game, server, or
launcher ID and carry a title, platform/environment qualifier, verified plain
text procedure, ordering, and timestamps. Remote authenticated clients may read
the catalog; mutation remains a host-management operation. Server notes appear
in the contextual server page, while the fixed knowledge-base tab also covers
desktop games and launchers. This keeps locally verified compatibility fixes
shareable between family clients without turning `servers.json` into a document
store.
Notes can carry an allowlisted declarative check profile. Checks are evaluated
locally by each GUI client and return only `ok`, `missing`, or `unknown` plus a
sanitized explanation. They cannot execute shell commands, and results are not
uploaded. Installation checks include ordinary path candidates and Steam AppID
manifest discovery across all configured Steam libraries; configuration checks
cover bounded file markers and JSON values (including nested JSON strings used
by CurseForge).
The knowledge tab's target picker is populated from a local installed-game
inventory rather than from launchers. The desktop process scans the current
player's private home with that player's identity and combines `/var/Games`,
Steam manifests/symlinks, Heroic metadata, Lutris SQLite and CurseForge
instances. This avoids granting the system service access to private homes.
Providers deduplicate paths and aggregate the users that reference each game.
Logical directory size is measured once per real path. Unverified shared
payloads and unconfirmed Steam directories are possible remnants regardless of
size; launcher or installation metadata clears that state. Confirmed small
games remain in the catalog, while the GUI hides possible remnants behind an
explicit filter. The local-only backend inventory endpoint remains available
for service-visible data; remote knowledge notes remain a separate data source
and are joined in the GUI through stable aliases.

The fixed **Launchers** tab is a provider-backed inventory of native desktop game
launchers. Missing launchers remain visible but muted, installed versions are
compared with their authoritative source, and available updates are highlighted.
Providers keep discovery separate from mutation. Heroic version discovery uses
the exact x86_64 RPM asset metadata from the official GitHub release. Because
the upstream RPM is not signed for the host RPM trust store, installation is a
manual administrator operation and is never delegated to the privileged broker.
Repository-managed launchers such as Lutris and Steam remain read-only inventory
entries.

The **Servers** tab is the daily overview. Every manageable workload has the
appropriate lifecycle actions. Minecraft server cards additionally expose a
**Management** action that opens a dynamic, closable in-application tab bound to
that server (for example, `Management: Forge`). It is not a separate desktop
window.

The first delivered management-tab slice contains the common workload overview,
lifecycle, bounded log viewing, verified backup catalog and backup creation.
Minecraft prefers its persistent `data/logs/latest.log`, preserving Forge and
mod output, and falls back to the systemd journal or Podman runtime output.
The first player-management slice reads `ops.json` as a bounded, symlink-safe
catalog and maps form actions only to validated `op`/`deop` commands. Podman
executes its image-local `rcon-cli`; systemd uses host-local configured RCON.
RCON credentials are never returned to the GUI or published for this feature.
Whitelist management follows the same boundary: a symlink-safe catalog plus a
fixed command map for `on`, `off`, `add`, `remove`, and `reload`, protected by
its own policy. Enabling it affects subsequent joins and does not imply kicking
already connected players.
Minecraft tabs also contain the existing mod inventory and client comparison. The
Servers card keeps only the status, a context-sensitive quick start/stop action
and **Management**.

The management overview exposes destructive deletion only for platform-created
managed Podman Minecraft workloads. The backend enforces an independent policy,
exact workload-ID confirmation, canonical managed paths, and optional deletion
of persistent data and backups. Named Gate routes are removed automatically; a
workload that still owns the wildcard fallback must be rerouted before deletion.
Systemd and adopted Podman workloads remain outside this destructive operation.

The Minecraft management tab will progressively add:

- overview, lifecycle and resource use;
- live logs and an RCON console;
- a validated `server.properties` editor;
- online players, allowlist, operators and bans;
- worlds, backups and restore;
- loader, version, mods/modpacks and Gate Lite routing.

Form changes use **Save and close** and **Discard changes**. Closing with dirty
state requires confirmation. Immediate commands such as RCON, ban, or restart
are clearly distinguished from pending form edits.

The **Connections** tab is limited to host profiles and registered/adopted
workloads. A separate **Security** tab contains the central policy registry for
global platform operations and per-server lifecycle/backup actions. Fixed
PAM-only rows keep security-policy changes and Timekpr administration visible in
the same model without allowing those protections to be weakened. Remote SSH
administration is absent from Connections and remains hidden in Security until
local PAM authentication.

Security has two explicit machine scopes. **This computer** is always available
through the loopback backend and, in Client mode, renders only local Mover and
launcher policies. **Game-server host** becomes selectable only in local Server
mode or through the GUI-owned SSH tunnel. Each scope has its own PAM token;
launcher updates always use the workstation scope. Distinct local and host
button colors supplement, but do not replace, explicit text labels.
Timekpr is deliberately context-sensitive rather than workstation-fixed: it
uses the local loopback backend and local PAM session without a tunnel, switches
to the managed host backend and host PAM session while the tunnel is active,
and restores the cached local context when the tunnel closes. Local and host
user catalogs are kept separate.

Every fixed tab that exposes host-protected actions has a contextual PAM unlock
control. All of them use the same short-lived backend session token; credentials
exist only for the authentication request and are never retained by the GUI.
The loopback authentication endpoint serializes PAM attempts and applies
bounded exponential backoff independently to the normalized username and the
connection source. It returns only generic credential or service failures and
logs rejected and throttled attempts without passwords. The limiter is
process-local defense in depth; persistent account lockout remains the host PAM
stack's responsibility.
The authentication response also supplies the host's filtered interactive-user
catalog so Timekpr never mistakes orphaned home directories for active accounts.
An explicit Security-tab lock calls the loopback-only logout endpoint, revokes the
presented token server-side, and clears all corresponding GUI state.
Server-scoped knowledge uses the same compact master-detail interaction as the
global knowledge view so long procedures never determine table-row height.
Legacy system `dnsmasq` conflict handling belongs to Network rather than Mover;
its stop action has an independent `dnsmasq.stop` policy. Mover mutations
(`game.move`, `game.link`, `library.permissions`, and `steam.cache`) and edits to
the shared knowledge base (`knowledge.manage`) are likewise independently
configurable as silent, PAM-protected, or disabled. The GUI reads Mover policy
state from the local backend even while a different host is managed over SSH,
so credentials and authorization decisions cannot cross those boundaries.

The Minecraft installer opens **Minecraft: Modpacks** as a single closable,
contextual tab instead of making Minecraft-specific discovery part of the
platform's fixed top-level navigation. Its first provider is CurseForge and
supports text, Minecraft-version and loader filters, sorting, pagination,
project summaries and compatible server-pack metadata. Provider credentials
and download URLs remain host-only, and catalog responses are neither persisted
nor cached. Only an authenticated `minecraft.install` operation can resolve a
selected release to a distribution-enabled server pack. The host streams and
verifies that ZIP, safely extracts it into staging, and atomically publishes it
as the initial data of a new workload. A narrowly parsed `mods.csv` server
recipe may add verified CurseForge JARs under `mods/`; every file and project is
resolved through the official API and any distribution opt-out aborts the
whole staging transaction. Pack-provided scripts are data, never executable
installation instructions. An exact Forge installer coordinate is converted
to the loader version installed by the existing managed itzg container.
Client modpacks and client mods remain outside Game Mover. The provider boundary
keeps the GUI independent of CurseForge response schemas.

Private game DNS is a platform-level provider boundary shown in the fixed
**Network** tab. Its registry is derived from exact, in-zone Gate routes so DNS
and proxy routing cannot become independent sources of truth. The optional
built-in provider is an authoritative-only UDP/TCP service with explicit bind
and answer addresses, no recursion, and PAM-protected configuration. It remains
disabled by default. Integrations are selected explicitly and external products
remain behind adapters. The first external adapter targets a local Pi-hole v6
installation through the supported FTL `dns.hosts` configuration. It keeps an
ownership ledger outside Pi-hole, removes only records it created, preserves
equal manual records and fails on conflicting manual values. Pi-hole is never a
package dependency. Further DNS providers can implement the same registry sync
boundary without changing Gate routing or the server installer.

## Security model

Authorization is a backend-enforced policy per operation, not merely GUI button
visibility. Supported policy modes currently are `silent`, `pam`, and
`disabled`; the model is expected to grow as management operations are added.

Typical defaults:

- status and safe read operations: silent authentication;
- start/stop/restart and player administration: configurable by policy;
- properties, mods, restore, deletion, and security policy changes: PAM where
  the operation is privileged or destructive;
- security policy changes themselves: always PAM protected.

Direct remote notebook/client access over LAN or Tailscale remains read-only.
Administrative access from a notebook is permitted only through an SSH
local-forwarding tunnel owned by the GUI and bound explicitly to loopback. The
tunnel controls require a local wheel/PAM session; the SSH process uses only a
key or agent, strict host-key checking, no password input, and no agent
forwarding. The GUI verifies that this SSH PID owns the listener before using
it. A separate host wheel/PAM session remains mandatory for the remote
administrative API, and can be opened from any relevant tab. The host `api.token`
is never copied to the client.
This is transport-level access through an existing SSH account, not a remotely
exposed administrative HTTP API. The Podman socket, RCON passwords, and API
tokens never leave the host API and must not appear in logs or responses.

## Implementation sequence

1. Extend the existing persistent Gate Lite lifecycle with editable route management.
2. Extend the validated Minecraft workload installer with image discovery; the
   provider-backed read-only CurseForge modpack catalog is now delivered as the
   discovery foundation.
3. Use the UI to back up and restore the current Forge server into Podman, verify it,
   and expose cut-over as a separate explicit user action.
4. Extend the delivered per-server Management tab with RCON, properties, players
   and lists.
5. Extend the delivered Security policy registry as new management operations
   are implemented.
6. Continue with modpacks, worlds, quotas and broader Linux gaming features.
