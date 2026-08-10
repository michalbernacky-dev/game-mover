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
  and remain supported for status, lifecycle, backups, management, and Velocity
  routing; self-service creation does not create new systemd units.

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
- a route through the shared Velocity proxy; and
- policy-controlled management operations without shell or direct Podman
  access for ordinary users.

Velocity is the final Minecraft ingress on TCP 25565. Minecraft backends reside
on a private Podman network and are addressed by stable container/network names,
not by LAN-published backend ports. During migration, Velocity is staged on TCP
25580 and may temporarily route to the existing host Forge service on 25565.

Velocity resolves backend targets through the workload adapter rather than
assuming one runtime:

- a managed Podman Minecraft backend joins `game-platform` and is routed by its
  private network alias and container port, without publishing that port to LAN;
- an adopted systemd Minecraft backend is routed through the host gateway and
  its validated `server.properties` port;
- runtime-specific discovery stays behind the adapter, while RCON, properties,
  players, logs, backup and UI management use common workload capabilities.

## User interface

The **Servers** tab is the daily overview. Every manageable workload has the
appropriate lifecycle actions. Minecraft server cards additionally expose a
**Management** action that opens a dynamic, closable in-application tab bound to
that server (for example, `Management: Forge`). It is not a separate desktop
window.

The Minecraft management tab will progressively contain:

- overview, lifecycle and resource use;
- live logs and an RCON console;
- a validated `server.properties` editor;
- online players, allowlist, operators and bans;
- worlds, backups and restore;
- loader, version, mods/modpacks and Velocity routing.

Form changes use **Save and close** and **Discard changes**. Closing with dirty
state requires confirmation. Immediate commands such as RCON, ban, or restart
are clearly distinguished from pending form edits.

The **Connections** tab is limited to host profiles and registered/adopted
workloads. The existing action-authentication controls will move into a separate
**Security** tab.

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

Remote notebook/client access remains read-only unless a later explicit design
decision grants narrowly scoped mutations. The Podman socket, RCON passwords,
API tokens, and Velocity forwarding secret never leave the host API and must not
appear in logs or responses.

## Implementation sequence

1. Finish persistent Velocity lifecycle and its private Podman network.
2. Add validated image discovery and general Minecraft workload installation.
3. Back up, restore into Podman, verify, and cut over the current Forge server.
4. Add the per-server Management tab: logs, RCON, properties, players and lists.
5. Extract and extend operation policies into the Security tab.
6. Continue with modpacks, worlds, quotas and broader Linux gaming features.
