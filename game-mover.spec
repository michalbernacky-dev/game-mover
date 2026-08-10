Name:           game-mover
Version:        0.5.0
Release:        10%{?dist}
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
Requires:       curl
Requires:       jq
Requires:       rsync
Requires(post): shadow-utils
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
install -Dpm0644 game_mover_minecraft.py %{buildroot}/opt/game_mover/game_mover_minecraft.py
install -Dpm0644 game_mover_backups.py %{buildroot}/opt/game_mover/game_mover_backups.py
install -Dpm0644 game_mover_version.py %{buildroot}/opt/game_mover/game_mover_version.py
install -Dpm0755 game-mover %{buildroot}/opt/game_mover/game-mover
install -Dpm0644 game_mover_workloads.py %{buildroot}/opt/game_mover/game_mover_workloads.py
install -Dpm0644 requirements.txt %{buildroot}/opt/game_mover/requirements.txt
install -Dpm0644 game_mover_logo.jpg %{buildroot}/opt/game_mover/game_mover_logo.jpg

install -Dpm0755 game-mover %{buildroot}%{_bindir}/game-mover
install -Dpm0644 game_mover.service %{buildroot}%{_unitdir}/game_mover.service
install -Dpm0644 game-mover.desktop %{buildroot}%{_datadir}/applications/game-mover.desktop

%pre
getent group gemers >/dev/null || groupadd -r gemers

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

%preun
%systemd_preun game_mover.service

%postun
%systemd_postun_with_restart game_mover.service
if [ "$1" -eq 0 ]; then
    rm -f /etc/game_mover/api.token || :
    rm -f /etc/game_mover/read.token || :
    rmdir /etc/game_mover 2>/dev/null || :
fi

%files
%doc README.md
%dir /opt/game_mover
/opt/game_mover/game_mover_flask.py
/opt/game_mover/game_mover.py
/opt/game_mover/game_mover_mods.py
/opt/game_mover/game_mover_minecraft.py
/opt/game_mover/game_mover_backups.py
/opt/game_mover/game_mover_version.py
/opt/game_mover/game-mover
/opt/game_mover/game_mover_workloads.py
/opt/game_mover/requirements.txt
/opt/game_mover/game_mover_logo.jpg
%{_bindir}/game-mover
%{_unitdir}/game_mover.service
%{_datadir}/applications/game-mover.desktop

%changelog
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
