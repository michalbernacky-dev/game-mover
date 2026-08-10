## English

# game-mover
Local-first game data manager for trusted local users on a single Linux machine.

The agreed long-term product and host architecture is recorded in
[`ARCHITECTURE.md`](ARCHITECTURE.md).

Note: the original `game-mover` repository is no longer used for active development. This repository, `game-mover-rpm`, is the canonical source for code, packaging, and deployment.

## Security model

This project is intended for a trusted local environment:

- mutating actions are restricted to members of the `gemers` group
- the backend listens on `127.0.0.1` only
- admin operations use a local token stored outside the repository
- it is not intended to be exposed as a public network service

The **Servers** tab monitors `forge-srv.service` and `satisfactory.service` and
refreshes their systemd state every 10 seconds. Different unit names can be set
with `GAME_MOVER_MINECRAFT_SERVICE` and `GAME_MOVER_SATISFACTORY_SERVICE` in a
systemd override for `game_mover.service`.

### Minecraft mod inventory and remote server

The Minecraft card can load the server's JAR inventory and compare it with a
client `mods` directory. Comparison uses mod IDs, versions and SHA-256 hashes;
client files stay on the client computer. The protected inventory endpoint uses
the separate `/etc/game_mover/read.token` token. Never copy `api.token` to a
client computer.

The API remains bound to localhost by default. For a trusted Tailscale network,
bind it to the server's Tailscale address with a systemd override:

```ini
[Service]
Environment=GAME_MOVER_BIND_HOST=100.x.y.z
Environment=GAME_MOVER_MINECRAFT_MODS_DIR=/opt/forge_srv/mods
```

After `systemctl daemon-reload` and a service restart, copy only `read.token` to
the client through a secure channel, for example to
`~/.config/game-mover/server-read.token` with mode `0600`. Configure the client
in `~/.config/game-mover/config.json`:

```json
{
  "server_url": "http://100.x.y.z:5000",
  "server_read_token_path": "/home/USER/.config/game-mover/server-read.token"
}
```

The other Game Mover tabs continue to use the local backend. Do not expose this
Flask service directly to the public internet; use Tailscale or an equivalent
trusted encrypted network and restrict the firewall accordingly.

### Connection profiles in the GUI

The **Connection** tab includes local client profiles with an address, port, and
read token. They do not require PAM because they only select a remote read-only
endpoint and are stored in the current user's configuration. Tailscale and LAN use
the same HTTP port; Game Mover defaults it to `5000`. The selected profile is
used only for the two remote read-only endpoints. Tokens entered in the registry
are stored in `~/.config/game-mover/config.json` with file mode `0600`; do not
enter the local `api.token` there.

### Client and server modes

The **Connection** tab selects either Client mode for remote read-only monitoring
or Server mode for local administration. In Server mode, authenticate as a wheel
user in the **Timekpr** tab, then edit the local service registry. Each row has an
ID, display name, systemd unit, type (`generic` or `minecraft`), absolute
data and `mods` directories for Minecraft services, and an independent policy
for every available action. The
game port is discovered from the workload at runtime. Multiple Minecraft
instances such as Forge and Pixelmon each keep their own mod path and comparison;
for example, Pixelmon can use the `pixelmon-srv` systemd unit. A `silent` action
uses the same local `api.token` as Mover actions, `pam` requires Timekpr login,
and `disabled` rejects the action. Server registry editing itself remains behind
wheel/PAM authentication. All control is always rejected over LAN/Tailscale.
A successful stop also clears systemd's failed state caused by Minecraft exiting
with status 130.

Registered systemd and Podman workloads with a data directory support verified
full-data backups. A running workload is cleanly stopped before archiving and
returned to its previous running state afterward. The manifest records the
source backend and systemd unit or non-secret Podman runtime metadata, making a
systemd Forge backup suitable as the input for a later container migration.

### Rootless Podman workloads

The Podman backend adopts existing containers and exposes status/start/stop/restart.
Podman runs as the dedicated `gameplatform` account;
the root Flask service reaches its private Unix socket. The socket is never
exposed to clients, and remote API access remains read-only.

Host defaults:

```text
user:      gameplatform
home:      /var/lib/game-platform
socket:    /run/user/<uid>/podman/podman.sock
data root: /var/lib/game-platform/servers
backups:   /var/lib/game-platform/backups/<workload-id>
```

Example entry in `/etc/game_mover/servers.json`:

```json
{
  "id": "mc-test",
  "name": "Minecraft Test",
  "backend": "podman",
  "kind": "minecraft",
  "permissions": {
    "start": "silent",
    "stop": "silent",
    "restart": "silent",
    "backup": "pam"
  },
  "management_mode": "adopted",
  "runtime": {
    "container_name": "mc-test"
  },
  "data": {
    "directory": "/var/lib/game-platform/servers/mc-test/data",
    "mods_relative_path": "mods"
  },
  "mods_dir": "/var/lib/game-platform/servers/mc-test/data/mods"
}
```

The workload ID fixes the allowed data path to `<data root>/<id>/data`. Each
local action has an independent `silent`, `pam`, or `disabled` policy. `silent`
uses the local group-readable capability token, `pam` requires an administrator
session, and `disabled` rejects the action. These policies never enable remote
control. If a workload is running during backup, it is cleanly
stopped, the complete data directory is archived and verified, and its previous
running state is restored. Each gzip archive has a SHA-256 file and a JSON
manifest with selected non-secret image/runtime metadata. Restore, container
creation and deletion of containers/data are deliberately not enabled yet.

For Minecraft entries, the systemd port is read from `server.properties` and the
Podman host port is read from the container's published `25565/tcp` mapping. The
resolved port is used for a standard read-only Minecraft status ping and is
combined with the remote client's configured host to display the game address.
For local systemd servers with RCON enabled, player counts prefer the
authenticated read-only `list` command; the RCON password never leaves the
backend. The standard status protocol remains the fallback, so RCON is optional.
The total number of known players is counted from UUIDs in persistent world data
and `usercache.json`.

### Velocity proxy staging

Game Mover can stage Velocity as a rootless Podman workload. It initially listens
on TCP `25580` and routes to the existing Forge server on `25565`, so production
traffic is not replaced. Only validated backend IDs, hosts and ports enter the
generated `velocity.toml`. The forwarding secret is created once under
`/var/lib/game-platform/proxies/velocity` and is never returned by the API.
Deployment is local-only and PAM protected. The Ambassador plugin provides Forge
1.20.1 compatibility. Modern forwarding remains disabled until the Forge backend
support and network isolation are enabled during migration.
Velocity is attached to the managed rootless bridge network `game-platform`;
future Minecraft backends will join the same network without publishing their
game ports to the LAN.

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
Lokální nástroj pro správu herních dat pro důvěryhodné uživatele na jednom Linux stroji.

Poznámka: původní repozitář `game-mover` se už nepoužívá pro aktivní vývoj. Tento repozitář, `game-mover-rpm`, je kanonický zdroj pro kód, balíčkování i nasazení.

## Bezpečnostní model

Projekt je určený pro důvěryhodné lokální prostředí:

- mutující akce jsou dostupné jen členům skupiny `gemers`
- backend poslouchá pouze na `127.0.0.1`
- admin operace používají lokální token uložený mimo repozitář
- není určený jako veřejně vystavená síťová služba

Záložka **Servery** sleduje jednotky `forge-srv.service` a
`satisfactory.service` a jejich systemd stav obnovuje každých 10 sekund. Jiné
názvy jednotek lze nastavit proměnnými `GAME_MOVER_MINECRAFT_SERVICE` a
`GAME_MOVER_SATISFACTORY_SERVICE` v systemd override pro `game_mover.service`.

### Inventář Minecraft modů a vzdálený server

Karta Minecraft umí načíst inventář JARů na serveru a porovnat ho s klientským
adresářem `mods`. Porovnává ID modů, verze a SHA-256; klientské soubory přitom
zůstávají na klientském počítači. Chráněný endpoint používá samostatný token
`/etc/game_mover/read.token`. Na klienta nikdy nekopíruj `api.token`.

API ve výchozím stavu nadále poslouchá jen lokálně. V důvěryhodné Tailscale síti
ho lze navázat na Tailscale adresu serveru pomocí systemd override:

```ini
[Service]
Environment=GAME_MOVER_BIND_HOST=100.x.y.z
Environment=GAME_MOVER_MINECRAFT_MODS_DIR=/opt/forge_srv/mods
```

Po `systemctl daemon-reload` a restartu služby bezpečně zkopíruj pouze
`read.token` na klienta, například jako
`~/.config/game-mover/server-read.token` s právy `0600`. Na klientovi vytvoř
`~/.config/game-mover/config.json`:

```json
{
  "server_url": "http://100.x.y.z:5000",
  "server_read_token_path": "/home/UZIVATEL/.config/game-mover/server-read.token"
}
```

Ostatní záložky Game Moveru dál používají místní backend. Flask službu
nevystavuj přímo do internetu; použij Tailscale nebo obdobnou důvěryhodnou
šifrovanou síť a podle toho omez firewall.

### Profily připojení v GUI

Záložka **Připojení** obsahuje místní klientské profily s adresou, portem a read
tokenem. PAM nevyžadují, protože pouze vybírají vzdálený read-only endpoint a
ukládají se do konfigurace aktuálního uživatele. Tailscale i LAN používají
stejný HTTP port; Game Mover předvyplní `5000`. Vybraný profil používají jen dvě
vzdálené read-only operace. Tokeny zapsané v registru se ukládají do
`~/.config/game-mover/config.json` s právy `0600`; nikdy sem nezadávej lokální
`api.token`.

### Režimy Klient a Server

V záložce **Připojení** lze zvolit režim Klient pro vzdálený read-only dohled,
nebo režim Server pro místní správu. V režimu Server se ověř jako wheel uživatel
v záložce **Timekpr** a poté uprav registr místních služeb. Každý řádek obsahuje
ID, zobrazovaný název, systemd jednotku, typ (`generic` nebo `minecraft`), u
Minecraftu absolutní cestu k datovému adresáři a adresáři `mods` a samostatnou
politiku každé dostupné akce. Herní port se zjišťuje automaticky z běžícího workloadu. Forge a Pixelmon
tak mají vlastní cestu i porovnání modů; například Pixelmon může používat jednotku
`pixelmon-srv`. Tichá akce používá stejný místní `api.token` jako funkce Moveru,
PAM akce vyžaduje přihlášení v Timekpr a zakázaná akce je vždy odmítnuta. Úprava
registru samotného zůstává za wheel/PAM ověřením. Veškeré ovládání je vždy
odmítnuto z LAN/Tailscale. Po úspěšném vypnutí se také vyčistí systemd stav
`failed`, který Minecraft může zanechat návratovým kódem 130.

Registrované systemd i Podman workloady s datovým adresářem podporují ověřované
úplné zálohy. Běžící workload se před archivací korektně zastaví a následně se
vrátí do původního stavu. Manifest zaznamená zdrojový backend a systemd jednotku
nebo nesekretní Podman metadata, takže záloha systemd Forge může přímo posloužit
jako vstup pro pozdější migraci do containeru.

### Rootless Podman workloady

Podman backend přebírá existující containery a zpřístupňuje status/start/stop/restart.
Podman běží pod dedikovaným účtem `gameplatform`;
root Flask služba používá jeho privátní Unix socket. Socket není dostupný
klientům a vzdálené API zůstává read-only.

Výchozí hostitelské uspořádání:

```text
uživatel:  gameplatform
home:      /var/lib/game-platform
socket:    /run/user/<uid>/podman/podman.sock
data root: /var/lib/game-platform/servers
zálohy:    /var/lib/game-platform/backups/<id-workloadu>
```

Příklad záznamu v `/etc/game_mover/servers.json`:

```json
{
  "id": "mc-test",
  "name": "Minecraft Test",
  "backend": "podman",
  "kind": "minecraft",
  "permissions": {
    "start": "silent",
    "stop": "silent",
    "restart": "silent",
    "backup": "pam"
  },
  "management_mode": "adopted",
  "runtime": {
    "container_name": "mc-test"
  },
  "data": {
    "directory": "/var/lib/game-platform/servers/mc-test/data",
    "mods_relative_path": "mods"
  },
  "mods_dir": "/var/lib/game-platform/servers/mc-test/data/mods"
}
```

ID workloadu určuje povolenou datovou cestu `<data root>/<id>/data`. Každá místní
akce má vlastní politiku `silent`, `pam` nebo `disabled`. `silent` používá místní
token dostupný skupině, `pam` vyžaduje administrátorskou relaci a `disabled` akci
zakáže. Žádná z těchto politik nepovoluje vzdálené ovládání. Běžící workload se
korektně zastaví, zazálohuje
se celý datový adresář, archiv se ověří a následně se obnoví původní stav běhu.
Ke gzip archivu vznikne SHA-256 soubor a JSON manifest s vybranými nesekretními
údaji o image/runtime. Restore, vytváření a mazání containerů či dat zatím
záměrně nejsou povolené.

U systemd Minecraftu se port čte ze `server.properties`, u Podmanu z publikovaného
mapování containerového portu `25565/tcp`. Zjištěný port slouží pro standardní
read-only Minecraft status ping a vzdálený klient jej spojí s hostitelem ze svého
profilu. U lokálních systemd serverů se zapnutým RCON se počet hráčů přednostně
načítá autentizovaným read-only příkazem `list`; RCON heslo backend nikdy
neposílá do API. Standardní status protokol zůstává fallbackem, takže RCON je
volitelný. Celkový počet známých hráčů se počítá ze UUID v persistentních datech
světa a v `usercache.json`.

### Příprava Velocity proxy

Game Mover umí připravit Velocity jako rootless Podman workload. Zpočátku
poslouchá na TCP `25580` a směruje na existující Forge na `25565`, takže
nepřebírá produkční provoz. Do generovaného `velocity.toml` se dostanou jen
ověřená ID backendů, adresy a porty. Forwarding secret vznikne pouze jednou v
`/var/lib/game-platform/proxies/velocity` a API jej nikdy nevrací. Nasazení je
dostupné jen místně a vyžaduje PAM. Kompatibilitu Forge 1.20.1 zajišťuje plugin
Ambassador. Modern forwarding zůstane vypnutý, dokud při migraci nezapneme jeho
podporu ve Forge backendu a síťovou izolaci.
Velocity je připojená do spravované rootless bridge sítě `game-platform`;
budoucí Minecraft backendy vstoupí do stejné sítě bez publikování herních portů
do LAN.

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
