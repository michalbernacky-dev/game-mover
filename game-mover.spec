Name:           game-mover
Version:        0.25.1
Release:        1%{?dist}
Summary:        Shared game library manager with local Flask API and Qt GUI

License:        Proprietary
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

Requires:       python3
Requires:       python3-flask
Requires:       python3-qt5
Requires:       python3-requests
Requires:       python3-psutil
Requires:       python3-pam
Requires:       openssh-clients
Requires:       curl
Requires:       jq
Requires:       rsync
Requires:       podman
Requires(pre):  shadow-utils
Requires(post): shadow-utils
Requires(post): util-linux
Requires(preun): systemd
Requires(postun): systemd

BuildRequires:  systemd-rpm-macros

%description
Game Mover provides:
- a Flask service for moving/linking game directories into a shared library
- a Qt GUI frontend
- a CLI helper script

%prep
%autosetup -n %{name}-%{version}

%build
# Nothing to build.

%install
mkdir -p %{buildroot}/opt/game_mover

install -Dpm0755 game_mover_flask.py %{buildroot}/opt/game_mover/game_mover_flask.py
install -Dpm0755 game_mover.py %{buildroot}/opt/game_mover/game_mover.py
install -Dpm0644 game_mover_mods.py %{buildroot}/opt/game_mover/game_mover_mods.py
install -Dpm0644 game_mover_catalog.py %{buildroot}/opt/game_mover/game_mover_catalog.py
install -Dpm0755 game_mover_dns.py %{buildroot}/opt/game_mover/game_mover_dns.py
install -Dpm0644 game_mover_pihole.py %{buildroot}/opt/game_mover/game_mover_pihole.py
install -Dpm0644 game_mover_minecraft.py %{buildroot}/opt/game_mover/game_mover_minecraft.py
install -Dpm0644 game_mover_gate.py %{buildroot}/opt/game_mover/game_mover_gate.py
install -Dpm0644 game_mover_backups.py %{buildroot}/opt/game_mover/game_mover_backups.py
install -Dpm0644 game_mover_installs.py %{buildroot}/opt/game_mover/game_mover_installs.py
install -Dpm0644 game_mover_jobs.py %{buildroot}/opt/game_mover/game_mover_jobs.py
install -Dpm0644 game_mover_endpoints.py %{buildroot}/opt/game_mover/game_mover_endpoints.py
install -Dpm0644 game_mover_satisfactory.py %{buildroot}/opt/game_mover/game_mover_satisfactory.py
install -Dpm0644 game_mover_connections.py %{buildroot}/opt/game_mover/game_mover_connections.py
install -Dpm0644 game_mover_security.py %{buildroot}/opt/game_mover/game_mover_security.py
install -Dpm0644 game_mover_properties.py %{buildroot}/opt/game_mover/game_mover_properties.py
install -Dpm0644 game_mover_logs.py %{buildroot}/opt/game_mover/game_mover_logs.py
install -Dpm0644 game_mover_launchers.py %{buildroot}/opt/game_mover/game_mover_launchers.py
install -Dpm0644 game_mover_notes.py %{buildroot}/opt/game_mover/game_mover_notes.py
install -Dpm0644 game_mover_tip_checks.py %{buildroot}/opt/game_mover/game_mover_tip_checks.py
install -Dpm0644 game_mover_game_inventory.py %{buildroot}/opt/game_mover/game_mover_game_inventory.py
install -Dpm0644 game_mover_operators.py %{buildroot}/opt/game_mover/game_mover_operators.py
install -Dpm0644 game_mover_whitelist.py %{buildroot}/opt/game_mover/game_mover_whitelist.py
install -Dpm0644 game_mover_version.py %{buildroot}/opt/game_mover/game_mover_version.py
install -Dpm0755 game-mover %{buildroot}/opt/game_mover/game-mover
install -Dpm0644 game_mover_workloads.py %{buildroot}/opt/game_mover/game_mover_workloads.py
install -Dpm0644 requirements.txt %{buildroot}/opt/game_mover/requirements.txt
install -Dpm0644 game_mover_logo.jpg %{buildroot}/opt/game_mover/game_mover_logo.jpg

install -Dpm0755 game-mover %{buildroot}%{_bindir}/game-mover
install -Dpm0644 game_mover.service %{buildroot}%{_unitdir}/game_mover.service
install -Dpm0644 game-mover-dns.service %{buildroot}%{_unitdir}/game-mover-dns.service
install -Dpm0644 game-mover.desktop %{buildroot}%{_datadir}/applications/game-mover.desktop

%pre
getent group gemers >/dev/null || groupadd -r gemers
getent passwd gameplatform >/dev/null || \
    useradd -r -m -d /var/lib/game-platform -s /usr/sbin/nologin gameplatform

%post
%systemd_post game_mover.service

# Runtime bytecode is not packaged and can survive an RPM replacement. Remove
# it after installing new sources; normal launchers use -B and do not recreate it.
find /opt/game_mover -type d -name __pycache__ -prune -exec rm -rf -- {} + 2>/dev/null || :

mkdir -p /etc/game_mover
if [ ! -f /etc/game_mover/api.token ]; then
    token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    printf '%s\n' "$token" > /etc/game_mover/api.token
fi
chgrp gemers /etc/game_mover/api.token || :
chmod 0640 /etc/game_mover/api.token || :

if [ ! -f /etc/game_mover/read.token ]; then
    token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    printf '%s\n' "$token" > /etc/game_mover/read.token
fi
chgrp gemers /etc/game_mover/read.token || :
chmod 0640 /etc/game_mover/read.token || :

mkdir -p /var/Games /var/Games_links /var/Games/steam-cache
chgrp gemers /var/Games /var/Games_links /var/Games/steam-cache || :
chmod 2775 /var/Games /var/Games_links /var/Games/steam-cache || :

install -d -m 0750 -o gameplatform -g gameplatform \
    /var/lib/game-platform \
    /var/lib/game-platform/servers \
    /var/lib/game-platform/backups \
    /var/lib/game-platform/proxies || :
PYTHONPATH=/opt/game_mover python3 -c \
    'from game_mover_notes import initialize_notes_database; initialize_notes_database("/var/lib/game-platform/game-mover-notes.sqlite3")'
chown root:gemers /var/lib/game-platform/game-mover-notes.sqlite3 || :
chmod 0640 /var/lib/game-platform/game-mover-notes.sqlite3 || :
loginctl enable-linger gameplatform >/dev/null 2>&1 || :
gameplatform_uid="$(id -u gameplatform)"
runuser -u gameplatform -- env XDG_RUNTIME_DIR="/run/user/${gameplatform_uid}" \
    systemctl --user enable --now podman.socket podman-restart.service >/dev/null 2>&1 || :

%preun
%systemd_preun game_mover.service game-mover-dns.service

%postun
%systemd_postun_with_restart game_mover.service game-mover-dns.service
if [ "$1" -eq 0 ]; then
    rm -f /etc/game_mover/api.token || :
    rm -f /etc/game_mover/read.token || :
    rmdir /etc/game_mover 2>/dev/null || :
fi

%files
%doc README.md ARCHITECTURE.md
%dir /opt/game_mover
/opt/game_mover/game_mover_flask.py
/opt/game_mover/game_mover.py
/opt/game_mover/game_mover_mods.py
/opt/game_mover/game_mover_catalog.py
/opt/game_mover/game_mover_dns.py
/opt/game_mover/game_mover_pihole.py
/opt/game_mover/game_mover_minecraft.py
/opt/game_mover/game_mover_gate.py
/opt/game_mover/game_mover_backups.py
/opt/game_mover/game_mover_installs.py
/opt/game_mover/game_mover_jobs.py
/opt/game_mover/game_mover_endpoints.py
/opt/game_mover/game_mover_satisfactory.py
/opt/game_mover/game_mover_connections.py
/opt/game_mover/game_mover_security.py
/opt/game_mover/game_mover_properties.py
/opt/game_mover/game_mover_logs.py
/opt/game_mover/game_mover_launchers.py
/opt/game_mover/game_mover_notes.py
/opt/game_mover/game_mover_tip_checks.py
/opt/game_mover/game_mover_game_inventory.py
/opt/game_mover/game_mover_operators.py
/opt/game_mover/game_mover_whitelist.py
/opt/game_mover/game_mover_version.py
/opt/game_mover/game-mover
/opt/game_mover/game_mover_workloads.py
/opt/game_mover/requirements.txt
/opt/game_mover/game_mover_logo.jpg
%{_bindir}/game-mover
%{_unitdir}/game_mover.service
%{_unitdir}/game-mover-dns.service
%{_datadir}/applications/game-mover.desktop

%changelog
* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.25.1-1
- Discover manually installed GOG, Ubisoft, Rockstar, Epic and EA games in common Wine paths

* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.25.0-1
- Build the tips catalog from locally installed games across known platforms
- Merge users, platforms, paths and sizes and flag tiny possible remnants
- Attach existing notes through stable game aliases without listing launchers

* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.24.5-1
- Support bounded checks of larger CurseForge instance metadata

* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.24.4-1
- Keep local checks responsive by requiring exact paths instead of recursive globs

* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.24.3-1
- Add local installation and configuration checks to knowledge-base tips
- Detect Steam games by AppID across all configured Steam libraries

* Fri Aug 21 2026 Game Mover Packager <packager@example.invalid> - 0.24.2-1
- Rename the global knowledge tab to Tipy a poznámky
- Load all game, server, and launcher note targets when the tab is first opened

* Fri Aug 21 2026 Game Mover Packager <packager@example.invalid> - 0.24.1-1
- Initialize and migrate the SQLite knowledge base during a fresh host install
- Keep service startup as a fallback schema initializer

* Fri Aug 21 2026 Game Mover Packager <packager@example.invalid> - 0.24.0-1
- Add a shared SQLite knowledge base for games, servers, and launchers
- Store platform-specific verified procedures and expose them read-only to clients
- Add editable server notes and a general knowledge-base tab

* Wed Aug 19 2026 Game Mover Packager <packager@example.invalid> - 0.23.1-1
- Infer missing legacy file loaders from an exact catalog filter
- Use a project's unique loader for the selected Minecraft version as a safe fallback

* Wed Aug 19 2026 Game Mover Packager <packager@example.invalid> - 0.23.0-1
- Support declarative CurseForge server-pack recipes without executing pack scripts
- Resolve, authorize, size-check, and hash-check every recipe mod through the official API
- Select the matching Java runtime and delegate exact Forge setup to the managed container

* Wed Aug 19 2026 Game Mover Packager <packager@example.invalid> - 0.22.0-1
- Install user-selected CurseForge server packs into new managed Minecraft servers
- Keep API keys and download URLs host-only and enforce author distribution consent
- Verify archive size and hash and safely extract ZIP contents with atomic rollback

* Mon Aug 17 2026 Game Mover Packager <packager@example.invalid> - 0.21.0-1
- Add launcher discovery and native version status for Heroic, Lutris, and Steam
- Add a PAM-protected verified Heroic RPM update action

* Mon Aug 17 2026 Game Mover Packager <packager@example.invalid> - 0.20.1-1
- Show only the player-facing address without a Gate Lite source suffix
- Keep Gate routing and direct backend details available in server management

* Sun Aug 16 2026 Game Mover Packager <packager@example.invalid> - 0.20.0-1
- Add explicitly enabled DNS integrations with no default external dependency
- Add a local Pi-hole v6 provider for Gate-derived Local DNS Records
- Preserve manual Pi-hole records and reject conflicting ownership

* Sat Aug 15 2026 Game Mover Packager <packager@example.invalid> - 0.19.2-1
- Show discovered and configured endpoints together in one structured table
- Render discovered rows in green and keep them read-only
- Restrict endpoint editing and deletion to explicit registry rows

* Sat Aug 15 2026 Game Mover Packager <packager@example.invalid> - 0.19.1-1
- Keep daily server cards focused on the player-facing connection address
- Retain technical endpoint purposes and sources for backend policy and diagnostics
- Move optional endpoint inspection behind a compact advanced-management action

* Sat Aug 15 2026 Game Mover Packager <packager@example.invalid> - 0.19.0-1
- Add automatic Satisfactory endpoint discovery from effective systemd ExecStart
- Merge adapter-discovered and manually registered endpoints with visible sources
- Reserve automatically discovered ports for cross-game conflict checks

* Sat Aug 15 2026 Game Mover Packager <packager@example.invalid> - 0.18.1-1
- Move generic endpoint editing from the crowded Connections registry into Management
- Add a structured endpoint editor shared by systemd and Podman workloads
- Show missing endpoint registration explicitly on server cards

* Sat Aug 15 2026 Game Mover Packager <packager@example.invalid> - 0.18.0-1
- Add generic TCP/UDP endpoint registration for every game server type
- Display game endpoints in server cards and management views
- Validate duplicate host ports independently of Minecraft

* Sat Aug 15 2026 Game Mover Packager <packager@example.invalid> - 0.17.0-1
- Add provider-neutral private DNS derived from exact Gate routes
- Ship an optional authoritative DNS service without a Pi-hole dependency
- Add PAM-protected DNS configuration and LAN/Tailscale diagnostics

* Sat Aug 15 2026 Game Mover Packager <packager@example.invalid> - 0.16.0-1
- Add a provider-backed read-only CurseForge modpack browser
- Filter projects and files by Minecraft version and loader without downloads
- Keep the API key host-only and prohibit persistence or caching of catalog data

* Sat Aug 15 2026 Game Mover Packager <packager@example.invalid> - 0.15.2-1
- Suggest the first available direct Minecraft port in the server installer
- Reserve configured ports of stopped workloads and the Gate Lite listener
- Recheck the selected host port immediately before installation

* Fri Aug 14 2026 Game Mover Packager <packager@example.invalid> - 0.15.1-1
- Show managed-server deletion phases and progress inline in cards and management
- Stop displaying a delayed modal success dialog after the user changes tabs
- Keep deletion failures visible in the originating management overview

* Fri Aug 14 2026 Game Mover Packager <packager@example.invalid> - 0.15.0-1
- Add PAM-policy-controlled deletion for managed Podman Minecraft servers
- Remove selected persistent data, backups, registry state, and named Gate routes safely
- Require exact server-ID confirmation and protect wildcard Gate routing from deletion

* Thu Aug 13 2026 Game Mover Packager <packager@example.invalid> - 0.14.0-1
- Display the Minecraft version advertised by each server in cards and management
- Keep RCON player counts while independently discovering version and protocol
- Prefer a configured Gate address on server cards and retain both paths in management

* Wed Aug 12 2026 Game Mover Packager <packager@example.invalid> - 0.13.0-1
- Add policy-protected Minecraft whitelist state and player management through RCON
- Read whitelist.json safely and support explicit server reload without exposing RCON

* Wed Aug 12 2026 Game Mover Packager <packager@example.invalid> - 0.12.0-1
- Add policy-protected Minecraft operator management through validated RCON commands
- Read the operator catalog safely from each server's persistent ops.json

* Wed Aug 12 2026 Game Mover Packager <packager@example.invalid> - 0.11.1-1
- Prefer each Minecraft server's persistent logs/latest.log over runtime stdout

* Wed Aug 12 2026 Game Mover Packager <packager@example.invalid> - 0.11.0-1
- Add policy-protected, bounded systemd and Podman log viewing to server management
- Add manual tail selection and optional five-second log refresh

* Wed Aug 12 2026 Game Mover Packager <packager@example.invalid> - 0.10.1-1
- Clearly report host PAM-session expiry from Minecraft settings and lock management

* Wed Aug 12 2026 Game Mover Packager <packager@example.invalid> - 0.10.0-1
- Add one closable in-application management tab per registered server
- Keep only a quick start/stop action and server management on overview cards
- Move lifecycle, verified backup catalog, backup creation, and mods into management

* Wed Aug 12 2026 Game Mover Packager <packager@example.invalid> - 0.9.0-1
- Move SSH host administration from Connection into PAM-protected Security UI
- Create and own a key-only loopback SSH tunnel without storing SSH passwords
- Require separate local and host PAM sessions and close access with the tunnel

* Wed Aug 12 2026 Game Mover Packager <packager@example.invalid> - 0.8.0-2
- Prevent Security policy tables from overlapping at constrained window heights
- Scroll the complete Security page and size tables to their actual rows

* Tue Aug 11 2026 Game Mover Packager <packager@example.invalid> - 0.8.0-1
- Add a central backend-enforced Security policy registry and GUI tab
- Move per-server authorization policies out of the Connection tab
- Show Timekpr and security management as fixed PAM-protected operations

* Tue Aug 11 2026 Game Mover Packager <packager@example.invalid> - 0.7.0-1
- Add PAM-authenticated host administration through a loopback-only SSH tunnel
- Keep notebook-local Mover services separate from the tunneled host API
- Preserve direct LAN and Tailscale HTTP access as read-only

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.6.0-2
- Route adopted pasta-network Podman workloads through their published host ports
- Keep private container-name routing for Game Mover managed bridge workloads

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.6.0-1
- Manage rootless Podman Minecraft servers from the GUI
- Restore verified systemd or Podman backups into new isolated workloads
- Route registered Minecraft servers through configurable Gate Lite ingress
- Remove the abandoned Velocity and forwarding-plugin implementation

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-27
- Remove the failed Velocity and forwarding-plugin implementation paths
- Keep Gate Lite as the only supported Minecraft router
- Clean obsolete proxy runtime artifacts during this development deployment

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-26
- Configure Gate routes in the GUI using registered Minecraft server targets
- Resolve systemd ports and private Podman container endpoints in the backend
- Apply route changes with Gate restart and configuration rollback on failure

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-25
- Add a PAM-protected Minecraft Podman installer with visible progress
- Restore verified systemd or Podman backups into isolated managed servers
- Register direct ports and optional Gate Lite hostname routes from the UI

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-24
- Replace the experimental full Velocity ingress with transparent Gate Lite routing
- Pin the tested Gate image and add validated hostname-to-backend route generation
- Keep Forge online-mode and mod handshakes end-to-end without forwarding plugins

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-23
- Pin Velocity 3.4.0 build 566 for Ambassador and Forge 1.20.1 compatibility

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-22
- Keep Forge mod-info ping passthrough enabled for Ambassador login handshakes

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-21
- Add atomic Forge backend configuration for Velocity modern forwarding
- Preserve rollback copies and restrict PCF forwarding to approved proxy hosts

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-20
- Pin Velocity 3.5.1 build 615 to the Java 21 proxy image
- Verify the proxy TCP listener independently from Forge status-ping behavior

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-19
- Remove a failed disposable Velocity container while preserving persistent data
- Allow a failed deployment to be retried cleanly from the Servers tab

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-18
- Skip remote default-config download when Game Platform supplies velocity.toml
- Require a successful Minecraft handshake before reporting Velocity ready

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-17
- Show live deployment phases and a progress bar on the Velocity server card
- Add a reusable operation progress model for Minecraft installs and migrations

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-16
- Create and attach Velocity to the managed private game-platform network
- Add PAM-protected start, stop, and restart controls for deployed Velocity

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-15
- Persist managed Velocity across host restarts with a validated Podman policy
- Bootstrap the dedicated rootless Podman account, socket, and restart service
- Record the agreed Game Platform architecture and management/security UX

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-14
- Add PAM-protected Velocity staging configuration and rootless Podman deployment
- Add validated proxy routes, persistent forwarding secret, and Ambassador setup
- Show Velocity status and deployment control in the Servers tab

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-13
- Add verified full-data backups for registered systemd servers such as Forge
- Record the source systemd unit in migration-ready backup manifests

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-12
- Prefer authenticated local RCON player counts when Forge status ping stalls
- Retain the standard status protocol as a fallback for servers without RCON

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-11
- Prefer local CGNAT overlay IPv4 when discovering a Minecraft status route
- Always release an asynchronous status probe after unexpected failures

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-10
- Make unchecked and checked connection-edit controls visible in the dark theme

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-9
- Guard local connection-profile edits with an explicit checkbox
- Replace the obsolete Timekpr profile-unlock description

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-8
- Discover and remember the working local address for Minecraft status queries

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-7
- Refresh server cards outside the Qt UI thread
- Poll Minecraft player counts asynchronously with caching and retry control

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-6
- Allow slow modded Minecraft servers eight seconds to return status data
- Do not request the optional Pong when only player counts are needed

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-5
- Preserve valid player counts when Forge omits or alters the optional Pong

* Mon Aug 10 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-4
- Allow local read-only connection profiles to be edited without server PAM
- Keep the local backend enabled across reboots for PAM and Timekpr operations
- Complete Minecraft status polling with Ping/Pong for Forge compatibility

* Sun Aug 09 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-3
- Probe each Minecraft workload only through its primary routable host

* Sun Aug 09 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-2
- Bound status refresh latency by probing workloads and local addresses concurrently

* Sun Aug 09 2026 Game Mover Packager <packager@example.invalid> - 0.5.0-1
- Add verified full-data backups for locally managed Podman workloads
- Add per-action silent, PAM, or disabled server authorization policies

* Sun Aug 09 2026 Game Mover Packager <packager@example.invalid> - 0.4.0-4
- Probe Minecraft status through the host's routable local address

* Sun Aug 09 2026 Game Mover Packager <packager@example.invalid> - 0.4.0-3
- Use the Minecraft 1.20.1 protocol for Forge-compatible status polling

* Sun Aug 09 2026 Game Mover Packager <packager@example.invalid> - 0.4.0-2
- Prevent stale Python bytecode after RPM replacement

* Fri Aug 07 2026 Game Mover Packager <packager@example.invalid> - 0.2.0-1
- Add remote Minecraft mod inventory and client comparison

* Sat Feb 14 2026 Game Mover Packager <packager@example.invalid> - 0.1.0-1
- Initial RPM packaging
