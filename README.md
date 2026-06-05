# game-mover
Application for managing game data of installed games through Steam, GOG, Epic etc.

## Deployment on Fedora (systemd + GNOME autostart)

1) Install dependencies (as root or via sudo):

```
pip install -r requirements.txt
```

> Timekpr Next integrace: pro záložku Timekpr musí být v systému nainstalovaný balík `timekpr-next`, aby byl dostupný CLI nástroj `timekpra` (není to Python balíček, nelze instalovat přes pip). Nainstaluj jej správcem balíčků své distribuce.

> Ověření přes PAM: pro autentizaci wheel uživatelů na serveru je potřeba mít nainstalovaný modul `python3-pam` (na Fedora: `sudo dnf install python3-pam`). Pip závislost `python-pam` je uvedená v requirements, ale bez systémové knihovny PAM nebude fungovat.

2) Run installer only for first-time/manual bootstrap (creates /opt/game_mover, systemd service, desktop launcher). Skript je idempotentní, můžeš ho pouštět znovu, ale běžný deploy už řeší RPM:

```
sudo sh ./install.sh
```

Chceš-li ponechat službu bez restartu/enable (třeba při batch deployi), použij:

```
sudo sh ./install.sh --no-restart
```

Chceš-li zapnout autostart GUI po přihlášení do GNOME, přidej:

```
sudo sh ./install.sh --enable-autostart
```

Pro běžný vývojový deploy na stejném PC použij tento postup:

```
cd ~/Projects/game-mover-rpm
./deploy.sh
```

What installer does:
- copies scripts + logo into `/opt/game_mover`
- ensures group `gemers` and shared dirs `/var/Games` + `/var/Games_links` (sgid, group-owned)
- installs/enables systemd service `game_mover.service` (Flask API on 127.0.0.1:5000)
- installs desktop launcher `/usr/share/applications/game-mover.desktop` (app vyhledatelná v menu)
- optional autostart `/etc/xdg/autostart/game-mover.desktop` when `--enable-autostart` is used
- exposes CLI helper via `/usr/local/bin/game-mover` (a také v `/opt/game_mover/game-mover`)

`deploy.sh` sestaví lokální RPM z aktuálního repa a nainstaluje ho. To je doporučená cesta pro vývoj na stejném PC, kde zároveň běží nasazená instance.

To uninstall:

```
sudo systemctl disable --now game_mover.service
sudo rm /etc/systemd/system/game_mover.service
sudo rm /etc/xdg/autostart/game-mover.desktop
sudo rm /usr/share/applications/game-mover.desktop
sudo rm -rf /opt/game_mover
sudo systemctl daemon-reload
```

## RPM build and install

This repository now contains a native RPM spec file: `game-mover.spec`.

For local builds there is a helper:

```
./build_rpm.sh
```

It prints the path to the generated RPM.

`./deploy.sh` builds the RPM, installs it locally with `rpm -Uvh --replacepkgs --replacefiles`, and restarts the service.

If you need to install a prebuilt RPM manually:

```
sudo rpm -Uvh --replacepkgs --replacefiles /path/to/game-mover-*.rpm
```

Poznámka: ruční instalace RPM nemusí službu automaticky spustit. Když je stav `dead`, dej:

```
sudo systemctl restart game_mover.service
```

## CI on push

Git itself does not build RPMs on push. RPM build is done by CI workflow.

Repository now includes GitHub Actions workflow:

- `.github/workflows/rpm-build.yml`

Behavior:

- on every `push` and `pull_request`, workflow builds RPM + SRPM
- resulting artifacts are available in Actions run as downloadable files
