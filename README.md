# game-mover
Application for managing game data of installed games through Steam, GOG, Epic etc.

## Deployment on Fedora (systemd + GNOME autostart)

1) Install dependencies (as root or via sudo):

```
pip install -r " requirements.txt"
```

> Timekpr Next integrace: pro záložku Timekpr musí být v systému nainstalovaný balík `timekpr-next`, aby byl dostupný CLI nástroj `timekpra` (není to Python balíček, nelze instalovat přes pip). Nainstaluj jej správcem balíčků své distribuce.

> Ověření přes PAM: pro autentizaci wheel uživatelů na serveru je potřeba mít nainstalovaný modul `python3-pam` (na Fedora: `sudo dnf install python3-pam`). Pip závislost `python-pam` je uvedená v requirements, ale bez systémové knihovny PAM nebude fungovat.

2) Run installer (creates /opt/steam_mover, systemd service, desktop launcher). Skript je idempotentní, můžeš ho pouštět znovu pro update z repa:

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

What installer does:
- copies scripts + logo into `/opt/steam_mover`
- ensures group `gemers` and shared dirs `/var/Games` + `/var/Games_links` (sgid, group-owned)
- installs/enables systemd service `steam_mover.service` (Flask API on 127.0.0.1:5000)
- installs desktop launcher `/usr/share/applications/game-mover.desktop` (app vyhledatelná v menu)
- optional autostart `/etc/xdg/autostart/game-mover.desktop` when `--enable-autostart` is used
- exposes CLI helper via `/usr/local/bin/game-mover` (a také v `/opt/steam_mover/game-mover`)

To uninstall:

```
sudo systemctl disable --now steam_mover.service
sudo rm /etc/systemd/system/steam_mover.service
sudo rm /etc/xdg/autostart/game-mover.desktop
sudo rm /usr/share/applications/game-mover.desktop
sudo rm -rf /opt/steam_mover
sudo systemctl daemon-reload
```
