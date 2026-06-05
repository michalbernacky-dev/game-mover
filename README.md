## English

# game-mover
Application for managing game data of installed games through Steam, GOG, Epic etc.

## Deployment on Fedora (systemd + GNOME autostart)

1) Install dependencies (as root or via sudo):

```bash
pip install -r requirements.txt
```

> Timekpr Next integration: the Timekpr tab requires the `timekpr-next` package to be installed so the `timekpra` CLI tool is available. It is not a Python package, so it cannot be installed via pip. Install it with your distribution package manager.

> PAM authentication: wheel user authentication on the server requires the `python3-pam` module to be installed (on Fedora: `sudo dnf install python3-pam`). The `python-pam` pip dependency is listed in `requirements`, but it will not work without the system PAM library.

2) Run the installer only for first-time/manual bootstrap (creates `/opt/game_mover`, the systemd service, and the desktop launcher). The script is idempotent, so you can run it again, but the normal deploy path is now the RPM:

```bash
sudo sh ./install.sh
```

If you want to keep the service from being restarted/enabled immediately, use:

```bash
sudo sh ./install.sh --no-restart
```

If you want to enable GNOME autostart for the GUI, add:

```bash
sudo sh ./install.sh --enable-autostart
```

For the normal development deploy on the same PC, use:

```bash
cd ~/Projects/game-mover-rpm
./deploy.sh
```

What installer does:
- copies scripts + logo into `/opt/game_mover`
- ensures the `gemers` group and shared directories `/var/Games` and `/var/Games_links` exist with sgid and group ownership
- installs and enables the `game_mover.service` systemd unit (Flask API on `127.0.0.1:5000`)
- installs the desktop launcher `/usr/share/applications/game-mover.desktop` (visible in the app menu)
- optionally installs `/etc/xdg/autostart/game-mover.desktop` when `--enable-autostart` is used
- exposes the CLI helper via `/usr/local/bin/game-mover` and also in `/opt/game_mover/game-mover`
- protects mutating local API calls with a random token stored in `/etc/game_mover/api.token`, readable only by members of the `gemers` group

`deploy.sh` builds a local RPM from the current repository and installs it. This is the recommended path for development on the same PC where the deployed instance is also running.

To uninstall:

```bash
sudo systemctl disable --now game_mover.service
sudo rm /etc/systemd/system/game_mover.service
sudo rm /etc/xdg/autostart/game-mover.desktop
sudo rm /usr/share/applications/game-mover.desktop
sudo rm -rf /opt/game_mover
sudo rm -rf /etc/game_mover
sudo systemctl daemon-reload
```

## RPM build and install

This repository contains a native RPM spec file: `game-mover.spec`.

For local builds, use the helper:

```bash
./build_rpm.sh
```

It prints the path to the generated RPM.

`./deploy.sh` builds the RPM, installs it locally with `rpm -Uvh --replacepkgs --replacefiles`, and restarts the service.

If you need to install a prebuilt RPM manually:

```bash
sudo rpm -Uvh --replacepkgs --replacefiles /path/to/game-mover-*.rpm
```

Note: a manual RPM install may not start the service automatically. If the service is `dead`, run:

```bash
sudo systemctl restart game_mover.service
```

## CI on push

Git itself does not build RPMs on push. The RPM build is handled by the CI workflow.

Repository now includes GitHub Actions workflow:

- `.github/workflows/rpm-build.yml`

Behavior:

- on every `push` and `pull_request`, the workflow builds an RPM and SRPM
- the resulting artifacts are available in the Actions run as downloadable files

---

## Čeština

# game-mover
Nástroj pro správu herních dat nainstalovaných her přes Steam, GOG, Epic atd.

## Nasazení na Fedoře (systemd + GNOME autostart)

1) Nainstaluj závislosti (jako root nebo přes sudo):

```bash
pip install -r requirements.txt
```

> Integrace Timekpr Next: záložka Timekpr vyžaduje nainstalovaný balík `timekpr-next`, aby byl dostupný CLI nástroj `timekpra`. Není to Python balíček, takže jej nelze instalovat přes pip. Nainstaluj jej přes správce balíčků své distribuce.

> PAM autentizace: ověření wheel uživatelů na serveru vyžaduje nainstalovaný modul `python3-pam` (na Fedoře: `sudo dnf install python3-pam`). Pip závislost `python-pam` je uvedená v `requirements`, ale bez systémové PAM knihovny nebude fungovat.

2) Spusť instalátor pouze pro první ruční bootstrap (vytvoří `/opt/game_mover`, systemd službu a desktop launcher). Skript je idempotentní, takže jej můžeš spustit znovu, ale běžná deploy cesta je už přes RPM:

```bash
sudo sh ./install.sh
```

Pokud chceš službu nespouštět ani neenableovat hned, použij:

```bash
sudo sh ./install.sh --no-restart
```

Pokud chceš zapnout GNOME autostart pro GUI, přidej:

```bash
sudo sh ./install.sh --enable-autostart
```

Pro běžný vývojový deploy na stejném PC použij:

```bash
cd ~/Projects/game-mover-rpm
./deploy.sh
```

Co instalátor dělá:
- kopíruje skripty a logo do `/opt/game_mover`
- zajistí skupinu `gemers` a sdílené adresáře `/var/Games` a `/var/Games_links` se sgid a group ownership
- nainstaluje a zapne systemd jednotku `game_mover.service` (Flask API na `127.0.0.1:5000`)
- nainstaluje desktop launcher `/usr/share/applications/game-mover.desktop` (viditelný v menu aplikací)
- volitelně nainstaluje `/etc/xdg/autostart/game-mover.desktop` při použití `--enable-autostart`
- zpřístupní CLI helper přes `/usr/local/bin/game-mover` a také v `/opt/game_mover/game-mover`
- chrání mutující lokální API volání náhodným tokenem v `/etc/game_mover/api.token`, který je čitelný jen pro členy skupiny `gemers`

`deploy.sh` sestaví lokální RPM z aktuálního repa a nainstaluje ho. To je doporučená cesta pro vývoj na stejném PC, kde zároveň běží nasazená instance.

Odinstalace:

```bash
sudo systemctl disable --now game_mover.service
sudo rm /etc/systemd/system/game_mover.service
sudo rm /etc/xdg/autostart/game-mover.desktop
sudo rm /usr/share/applications/game-mover.desktop
sudo rm -rf /opt/game_mover
sudo rm -rf /etc/game_mover
sudo systemctl daemon-reload
```

## Build a instalace RPM

V repozitáři je nativní RPM spec soubor: `game-mover.spec`.

Pro lokální build použij helper:

```bash
./build_rpm.sh
```

Vypíše cestu k vygenerovanému RPM.

`./deploy.sh` RPM sestaví, lokálně ho nainstaluje přes `rpm -Uvh --replacepkgs --replacefiles` a restartuje službu.

Pokud chceš nainstalovat předem připravené RPM ručně:

```bash
sudo rpm -Uvh --replacepkgs --replacefiles /path/to/game-mover-*.rpm
```

Poznámka: ruční instalace RPM nemusí službu automaticky spustit. Pokud je služba ve stavu `dead`, spusť:

```bash
sudo systemctl restart game_mover.service
```

## CI při pushi

Git sám o sobě při pushi RPM nebuilduje. Build RPM zajišťuje CI workflow.

Repozitář obsahuje GitHub Actions workflow:

- `.github/workflows/rpm-build.yml`

Chování:

- při každém `push` a `pull_request` workflow vytvoří RPM a SRPM
- výsledné artefakty jsou dostupné v Actions běhu ke stažení
