Name:           game-mover
Version:        0.33.0
Release:        1%{?dist}
Summary:        Shared game library manager with local Flask API and Qt GUI

License:        PolyForm-Noncommercial-1.0.0
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

Requires:       python3
Requires:       python3-flask
Requires:       python3-qt5
Requires:       python3-requests
Requires:       python3-psutil
Requires:       python3-pam
Requires:       python3-pyyaml
Requires:       openssh-clients
Requires:       curl
Requires:       jq
Requires:       rsync
Requires:       podman
Requires:       acl
Requires:       PackageKit
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

for module in game_mover*.py; do
    mode=0644
    case "$module" in
        game_mover.py|game_mover_flask.py|game_mover_dns.py|game_mover_ea_epic.py) mode=0755 ;;
    esac
    install -Dpm"$mode" "$module" %{buildroot}/opt/game_mover/"$module"
done
install -Dpm0755 game-mover %{buildroot}/opt/game_mover/game-mover
install -Dpm0644 requirements.txt %{buildroot}/opt/game_mover/requirements.txt
install -Dpm0644 game_mover_logo.jpg %{buildroot}/opt/game_mover/game_mover_logo.jpg

install -Dpm0755 game-mover %{buildroot}%{_bindir}/game-mover
ln -s ../../opt/game_mover/game_mover_ea_epic.py \
    %{buildroot}%{_bindir}/game-mover-ea-epic
install -Dpm0644 game_mover.service %{buildroot}%{_unitdir}/game_mover.service
install -Dpm0644 game-mover-privileged.service \
    %{buildroot}%{_unitdir}/game-mover-privileged.service
install -Dpm0644 game-mover-dns.service %{buildroot}%{_unitdir}/game-mover-dns.service
install -Dpm0644 game-mover.sysusers \
    %{buildroot}%{_sysusersdir}/game-mover.conf
install -Dpm0644 game-mover.desktop %{buildroot}%{_datadir}/applications/game-mover.desktop
install -d -m 0750 %{buildroot}%{_sharedstatedir}/game-mover

%pre
getent group gemers >/dev/null || groupadd -r gemers
getent passwd gameplatform >/dev/null || \
    useradd -r -m -d /var/lib/game-platform -s /usr/sbin/nologin gameplatform

%post
%systemd_post game-mover-privileged.service game_mover.service

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

if [ -f /etc/game_mover/curseforge.key ]; then
    chown root:gameplatform /etc/game_mover/curseforge.key || :
    chmod 0640 /etc/game_mover/curseforge.key || :
fi

if [ ! -f /etc/game_mover/allowed-services.json ]; then
    printf '%s\n' '["forge-srv.service", "satisfactory.service"]' \
        > /etc/game_mover/allowed-services.json
fi
chown root:root /etc/game_mover/allowed-services.json || :
chmod 0644 /etc/game_mover/allowed-services.json || :

install -d -m 0750 -o gameplatform -g gameplatform /var/lib/game-mover
for config in servers.json gate.json security.json dns.json dns-runtime.json dns-pihole-state.json; do
    if [ -f "/etc/game_mover/${config}" ] && [ ! -e "/var/lib/game-mover/${config}" ]; then
        mv "/etc/game_mover/${config}" "/var/lib/game-mover/${config}"
    fi
    if [ -f "/var/lib/game-mover/${config}" ]; then
        chown gameplatform:gameplatform "/var/lib/game-mover/${config}" || :
        chmod 0640 "/var/lib/game-mover/${config}" || :
    fi
done

mkdir -p /var/Games /var/Games/EA /var/Games_links /var/Games/steam-cache
chgrp gemers /var/Games /var/Games/EA /var/Games_links /var/Games/steam-cache || :
chmod 0775 /var/Games /var/Games/EA /var/Games_links /var/Games/steam-cache || :
setfacl -m 'g:gemers:rwx,m::rwx,d:g:gemers:rwx,d:m::rwx' \
    /var/Games /var/Games/EA /var/Games_links /var/Games/steam-cache || :

if [ -L /var/lib/game-platform/servers ]; then
    echo "Managed server directory must not be a symbolic link" >&2
    exit 1
fi
install -d -m 0750 -o gameplatform -g gameplatform \
    /var/lib/game-platform \
    /var/lib/game-platform/backups \
    /var/lib/game-platform/proxies \
    /var/lib/game-platform/runtime || :
# Do not continue an upgrade with a failed or unsafe ACL migration.
python3 -I -B /opt/game_mover/game_mover_acl.py || exit 1
PYTHONPATH=/opt/game_mover python3 -c \
    'from game_mover_notes import initialize_notes_database; initialize_notes_database("/var/lib/game-platform/game-mover-notes.sqlite3")'
chown gameplatform:gameplatform /var/lib/game-platform/game-mover-notes.sqlite3 || :
chmod 0600 /var/lib/game-platform/game-mover-notes.sqlite3 || :
loginctl enable-linger gameplatform >/dev/null 2>&1 || :
gameplatform_uid="$(id -u gameplatform)"
runuser -u gameplatform -- env XDG_RUNTIME_DIR="/run/user/${gameplatform_uid}" \
    systemctl --user enable --now podman.socket podman-restart.service >/dev/null 2>&1 || :

%preun
%systemd_preun game_mover.service game-mover-privileged.service game-mover-dns.service

%postun
%systemd_postun_with_restart game_mover.service game-mover-privileged.service game-mover-dns.service
if [ "$1" -eq 0 ]; then
    rm -f /etc/game_mover/api.token || :
    rm -f /etc/game_mover/read.token || :
    rm -f /etc/game_mover/allowed-services.json || :
    rmdir /etc/game_mover 2>/dev/null || :
fi

%files
%license LICENSE NOTICE
%doc README.md ARCHITECTURE.md
%doc SECURITY.md TRADEMARKS.md
%dir /opt/game_mover
/opt/game_mover/game_mover*.py
/opt/game_mover/game-mover
/opt/game_mover/requirements.txt
/opt/game_mover/game_mover_logo.jpg
%{_bindir}/game-mover
%{_bindir}/game-mover-ea-epic
%{_unitdir}/game_mover.service
%{_unitdir}/game-mover-privileged.service
%{_unitdir}/game-mover-dns.service
%{_sysusersdir}/game-mover.conf
%{_datadir}/applications/game-mover.desktop
%attr(0750,gameplatform,gameplatform) %dir %{_sharedstatedir}/game-mover

%changelog
* Sun Sep 13 2026 Game Mover Packager <packager@example.invalid> - 0.33.0-1
- Launch Epic-owned EA titles through Legendary, UMU and per-user Heroic prefixes
- Add prerequisite reporting and the verified Battlefront II catalog entry
- Install a generic token-safe EA/Epic helper for every local player

* Sun Sep 13 2026 Game Mover Packager <packager@example.invalid> - 0.32.0-1
- Move complete EA App game payloads from a dedicated Heroic-managed Wine prefix
- Keep launcher, registry and account state in the player profile
- Reject staged downloads and active Heroic or EA App processes

* Wed Sep 09 2026 Game Mover Packager <packager@example.invalid> - 0.31.4-1
- Preserve effective ACL rights and reject hardlinked legacy server data
- Keep Steam cache inspection separate from authorized mutations
- Accept existing mixed-case account names in the privileged Timekpr workflow

* Mon Sep 07 2026 Game Mover Packager <packager@example.invalid> - 0.31.3-1
- Repair API access to legacy rootless Podman server data with scoped ACLs

* Mon Sep 07 2026 Game Mover Packager <packager@example.invalid> - 0.31.2-1
- Read registered systemd server status without requiring lifecycle allowlisting

* Mon Sep 07 2026 Game Mover Packager <packager@example.invalid> - 0.31.1-1
- Restore rootless Podman access through the hardened API service sandbox

* Sun Sep 06 2026 Game Mover Packager <packager@example.invalid> - 0.31.0-1
- Run the network API without root and delegate bounded host operations to a local broker
- Add a root-owned systemd service allowlist and keep managed Podman data host-owned
- Move mutable backend state to /var/lib/game-mover

* Sun Aug 23 2026 Game Mover Packager <packager@example.invalid> - 0.30.1-1
- Shorten RAM and swap labels so their values remain readable in the global strip
- Distinguish local and tunneled-host capacity with blue and purple context panels

* Sun Aug 23 2026 Game Mover Packager <packager@example.invalid> - 0.30.0-1
- Show persistent RAM and swap capacity below every application tab
- Read local capacity normally and host capacity through the active managed SSH tunnel
- Refresh the read-only capacity indicator every ten seconds without PAM authentication

* Sun Aug 23 2026 Game Mover Packager <packager@example.invalid> - 0.29.1-1
- Keep GOG, Epic, Ubisoft, and Rockstar visible as explicit read-only providers
- Show Heroic ownership for GOG/Epic and unsupported status for untested launchers
- Keep all non-Steam mutation controls disabled while the API remains Steam-only

* Sun Aug 23 2026 Game Mover Packager <packager@example.invalid> - 0.29.0-1
- Narrow mutable Mover operations to the tested Steam shared-library workflow
- Delegate GOG and Epic payload/prefix ownership to Heroic
- Remove untested prefix copying and registry rewriting from the local API
- Preserve other launcher data as read-only inventory without deleting legacy paths

* Sun Aug 23 2026 Game Mover Packager <packager@example.invalid> - 0.28.0-1
- Include small games when Lutris or another launcher confirms their installation
- Resolve DOSBox, ScummVM, native Linux, Wine, and Steam paths from Lutris YAML
- Mark only unverified small directories as possible remnants instead of hiding them
- Exclude launcher and non-game Lutris entries from the tips catalog

* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.27.2-1
- Hide unregistered Steam and incomplete GOG directories as possible remnants in Mover
- Keep possible remnants reachable through an explicit counted checkbox
- Exclude GE-Proton, Steam controller configs, and Steamworks redistributables
- Recognize small valid installs while rejecting stale manifests with missing payloads

* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.27.1-1
- Use local Timekpr, PAM, and users while no management tunnel is active
- Switch Timekpr to the managed host only for the lifetime of the SSH tunnel
- Restore the separate local Timekpr context after closing the tunnel
- Make the contextual Timekpr target explicit through labels and PAM colors

* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.27.0-1
- Separate local workstation and managed-host security policy scopes
- Show only Mover and launcher policies in read-only Client mode
- Authorize Heroic updates exclusively through the workstation policy and PAM session
- Distinguish local and host PAM controls by both labels and color

* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.26.2-1
- Replace the legacy inline Timekpr credentials with the shared PAM unlock dialog
- Keep the wheel-account requirement visible next to the contextual unlock control
- Add immediate host-side PAM session revocation from the Security tab
- Replace oversized server-note rows with a compact master-detail editor
- Move dnsmasq status and shutdown from Mover to Network
- Add independent silent, PAM, or disabled policies for Mover actions, notes, and dnsmasq

* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.26.1-1
- List only real interactive login accounts in Timekpr instead of every home directory
- Refresh the editable user selector from the authenticated host for SSH administration

* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.26.0-1
- Offer one shared host PAM unlock directly in every tab with protected operations
- Reuse the same short-lived backend token without storing the user's password

* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.25.5-1
- Make game metadata columns resizable and give each cell a contextual tooltip

* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.25.4-1
- Share non-game directory filters between Mover and the multi-provider tips inventory

* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.25.3-1
- Hide directories up to 1 GiB as remnants before building the game catalog
- Measure logical directory size instead of allocated filesystem blocks

* Sat Aug 22 2026 Game Mover Packager <packager@example.invalid> - 0.25.2-1
- Replace the crowded tips combo/table with a compact searchable master-detail view
- Add green, yellow, red and neutral local status indicators
- Treat installations below 128 MiB as probable remnants

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
