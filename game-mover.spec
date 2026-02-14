Name:           game-mover
Version:        0.1.0
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
mkdir -p %{buildroot}/opt/steam_mover

install -Dpm0755 steam_flask.py %{buildroot}/opt/steam_mover/steam_flask.py
install -Dpm0755 steam_mover.py %{buildroot}/opt/steam_mover/steam_mover.py
install -Dpm0755 game-mover %{buildroot}/opt/steam_mover/game-mover
install -Dpm0644 requirements.txt %{buildroot}/opt/steam_mover/requirements.txt
install -Dpm0644 steam_mover_logo.jpg %{buildroot}/opt/steam_mover/steam_mover_logo.jpg

install -Dpm0755 game-mover %{buildroot}%{_bindir}/game-mover
install -Dpm0644 steam_mover.service %{buildroot}%{_unitdir}/steam_mover.service
install -Dpm0644 game-mover.desktop %{buildroot}%{_datadir}/applications/game-mover.desktop

%pre
getent group gemers >/dev/null || groupadd -r gemers

%post
%systemd_post steam_mover.service

mkdir -p /var/Games /var/Games_links /var/Games/steam-cache
chgrp gemers /var/Games /var/Games_links /var/Games/steam-cache || :
chmod 2775 /var/Games /var/Games_links /var/Games/steam-cache || :

%preun
%systemd_preun steam_mover.service

%postun
%systemd_postun_with_restart steam_mover.service

%files
%doc README.md
%dir /opt/steam_mover
/opt/steam_mover/steam_flask.py
/opt/steam_mover/steam_mover.py
/opt/steam_mover/game-mover
/opt/steam_mover/requirements.txt
/opt/steam_mover/steam_mover_logo.jpg
%{_bindir}/game-mover
%{_unitdir}/steam_mover.service
%{_datadir}/applications/game-mover.desktop

%changelog
* Sat Feb 14 2026 Game Mover Packager <packager@example.invalid> - 0.1.0-1
- Initial RPM packaging
