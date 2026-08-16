# Game Platform: target architecture

This document records the agreed product direction. The existing **Game Mover**
name and package remain in place while the application grows into a broader
Linux gaming platform.

## Product scope

Game Platform is a host-side service and desktop client for managing games,
dedicated game servers, their persistent data, and Minecraft server instances.
It is intended for a trusted home LAN and Tailscale network; it is not designed
to expose its management API or game backends directly to the public Internet.

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

The Minecraft installer opens **Minecraft: Modpacks** as a single closable,
contextual tab instead of making Minecraft-specific discovery part of the
platform's fixed top-level navigation. It is a provider-backed, read-only
surface. Its first provider is CurseForge and supports text, Minecraft-version
and loader filters, sorting, pagination, project summaries and compatible file
or server-pack metadata. Provider credentials remain host-only. Catalog
responses are not persisted or cached, download URLs are not relayed, and the
initial UI contains no download or installation action. The provider boundary
must allow a future source to be added without coupling the GUI to CurseForge
response schemas.

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
administrative API, and the host `api.token` is never copied to the client.
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
