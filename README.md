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

### Client, server, and managed SSH administration

The **Connection** tab selects either Client mode for remote read-only monitoring
or Server mode for local administration. It no longer exposes an SSH management
mode. Remote host administration is opened from the **Security** tab after local
wheel/PAM authentication. Game Mover then owns a key-only SSH process and a local
forward while keeping the administrative HTTP API on loopback at both ends. Once
the tunnel is ready, authenticate separately as a wheel user of the host in the
**Timekpr** tab, then edit the host service registry. Each row has an
ID, display name, systemd unit, type (`generic` or `minecraft`), and absolute
data and `mods` directories for Minecraft services. The
game port is discovered from the workload at runtime. Multiple Minecraft
instances such as Forge and Pixelmon each keep their own mod path and comparison;
for example, Pixelmon can use the `pixelmon-srv` systemd unit. Authorization is
configured separately in the **Security** tab. Direct control over LAN/Tailscale
HTTP is always rejected.
A successful stop also clears systemd's failed state caused by Minecraft exiting
with status 130.

The selected connection profile supplies the SSH destination: use the host LAN
profile at home or its Tailscale profile when away. Tailscale only transports
SSH; Game Mover still connects to `http://127.0.0.1:5500` and the host backend
still sees a loopback request. The managed SSH client requires a key or
`ssh-agent`, rejects password and keyboard-interactive authentication, verifies
the existing `known_hosts` entry, disables agent/X11 forwarding, and binds the
forward only to `127.0.0.1`. Enrol a new host key interactively in a terminal and
verify its fingerprint before using the GUI. Game Mover verifies that its own SSH
PID owns the listener before sending host PAM credentials through it. Closing the
tunnel or GUI clears the host session and immediately returns to read-only Client
mode. Only the SSH username and ports are stored in client config; SSH passwords,
private keys, and the host `api.token` are never copied into it.

### Security policy registry

The **Security** tab is the single editor for backend-enforced authorization.
It contains policies for global platform operations and independent
start/stop/restart/backup policies for every registered server. `silent` uses
the host-local `api.token`, `pam` requires a valid wheel-user session, and
`disabled` rejects the operation in the backend. In SSH tunnel mode the PAM
session may also authorize a `silent` operation, so `api.token` never leaves the
host. Timekpr administration and changes to the security registry itself are
shown in the same table style as fixed, disabled controls with the only policy
**Requires PAM**; they cannot be weakened in the GUI or API.

Policies are stored atomically in `/etc/game_mover/security.json`. On the first
load, per-server values are migrated in memory from legacy `permissions` or
`control_auth` fields in `/etc/game_mover/servers.json`; saving the Security tab
creates the central file. The service registry no longer edits authorization.
The same tab contains the otherwise hidden managed-tunnel controls. Revealing
them requires PAM against the notebook's local Game Mover service; opening the
tunnel does not reuse that token for the host. A second host PAM login is always
required for administrative API operations.

### Per-server management tabs

The **Servers** tab remains a compact daily overview. A server card keeps one
context-sensitive start/stop button in host-management mode and opens a single
closable **Management: name** tab. Reopening the same server activates its
existing tab instead of creating duplicates. The management overview contains
status, connection, players, runtime, effective authorization, and all lifecycle
commands. Servers with persistent data also expose the verified backup catalog
and backup creation; Minecraft servers additionally expose mod inventory and
client comparison. The **Logs** section prefers the server's persistent
`data/logs/latest.log` (including Forge/mod output), with the registered systemd
unit or Podman container as a bounded fallback; it can optionally refresh every
five seconds.
The Minecraft **Players** section lists operators from the registered
`ops.json`; adding and removing OP privileges uses only validated `op`/`deop`
commands over host-internal RCON. Podman uses the image-local `rcon-cli`, so no
RCON port or password is exposed outside the container. The independent
`minecraft.operators` policy defaults to PAM.
The separate **Whitelist** section displays the effective state and safely reads
the registered `whitelist.json`. Its form maps only to `whitelist on`, `off`,
`add`, `remove`, and `reload`; the independent `minecraft.whitelist` policy
defaults to PAM. Enabling the whitelist does not disconnect players who are
already online.
Remote Client mode may inspect safe read-only content but cannot run host
operations.

Restore into an existing workload is intentionally not presented as an active
button yet. Current verified restore creates a new isolated Minecraft workload
through the installer. In-place restore will be added later as a separate
destructive, policy-controlled operation.

When a host PAM session expires, the first rejected protected request immediately
locks the host-management controls and tells the user to authenticate again in
the Timekpr tab; it is never silently retried with broader credentials.

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

The workload ID fixes the allowed data path to `<data root>/<id>/data`.
Authorization for its actions lives in the central Security registry and never
enables remote control. If a workload is running during backup, it is cleanly
stopped, the complete data directory is archived and verified, and its previous
running state is restored. Each gzip archive has a SHA-256 file and a JSON
manifest with selected non-secret image/runtime metadata. The local PAM-protected
Minecraft installer can create a fresh managed rootless Podman server or restore
one of these verified backups into a new isolated data directory. The source
server and its data are never modified by restore. The UI validates the workload
ID, Minecraft and loader versions, Java image, memory, unique direct port and an
optional Gate hostname, and requires explicit acceptance of the Minecraft EULA.

The management overview can permanently remove only Minecraft instances created
and owned by the platform (`podman` plus `management_mode=managed`). Deletion has
its own Security policy, requires the exact workload ID to be typed, always
removes the container and registry entry, and offers separate choices for the
persistent data and backup catalog. Named Gate routes are removed with the
server. If the server still owns the wildcard `*` route, deletion is rejected
until an administrator explicitly assigns that fallback to another server.
Systemd and adopted Podman workloads cannot be destroyed through this endpoint.
Deletion progress is reported by backend phases on the server card and in its
management overview. Completion closes the removed server's management tab
without displaying a delayed modal dialog over unrelated work; failures remain
visible inline for diagnosis and retry.

For Minecraft entries, the systemd port is read from `server.properties` and the
Podman host port is read from the container's published `25565/tcp` mapping. The
resolved port is used for a standard read-only Minecraft status ping and is
combined with the remote client's configured host to display the game address.
The same response supplies the exact Minecraft version advertised to clients
and its protocol number. The GUI displays that version on the server card and
in the management overview, so users can select a compatible launcher before
connecting. A failed version probe remains non-blocking and is never replaced
by a guess based on an image tag.
When a persisted Gate route resolves to a registered Minecraft workload, the
Servers card presents that route as the recommended player address. The
management overview then displays both the Gate route and the direct backend
address. Without a usable Gate route, the direct address remains the only one
shown everywhere.
For local systemd servers with RCON enabled, player counts prefer the
authenticated read-only `list` command; the RCON password never leaves the
backend. The standard status protocol remains the fallback, so RCON is optional.
The total number of known players is counted from UUIDs in persistent world data
and `usercache.json`.

### Gate Lite routing staging

Game Mover can stage Gate Lite as a rootless Podman workload. It initially
listens on TCP `25581` and transparently routes to the existing Forge server on
`25565`, so production traffic is not replaced. Only validated hostname,
backend host and port values enter the generated
`/var/lib/game-platform/proxies/gate/config.yml`; arbitrary YAML is never
accepted. Deployment is local-only and PAM protected. Gate Lite does not
terminate the player session or reconstruct the mod-loader handshake, so the
backend keeps its normal `online-mode=true` authentication and unmodified Forge
handshake. Gate Lite is attached to the managed rootless bridge
network `game-platform`; managed Minecraft containers are addressed by stable
private aliases without publishing their game ports to the LAN. A unique direct
port remains an optional compatibility fallback.
The Servers tab has a separate routing editor: users enter a hostname and select
a registered Minecraft server. They never enter container names, bridge
addresses, systemd ports, or YAML. The backend derives `container:25565` for
managed bridge-network Podman targets and uses the published host port for
adopted Podman or systemd targets.
Exactly one explicit `*` fallback is required and kept last. Saving routes
restarts an already deployed Gate; a failed apply restores the previous config.
Long-running deployment operations publish safe phase and percentage updates;
the Servers card polls and displays them without exposing command output or
secrets. The same operation model is used by Minecraft installation and restore
workflows.
The tested Gate image is pinned by immutable SHA-256 digest. Gate Lite is only
reported ready after its TCP listener accepts connections; a running
container alone is not considered healthy. Backend route health is evaluated
separately because a modded Forge backend may not answer a status ping even
though its authenticated RCON and player connections work.

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

### Režimy Klient, Server a spravovaná SSH administrace

V záložce **Připojení** lze zvolit režim Klient pro vzdálený read-only dohled
nebo režim Server pro místní správu. Volbu SSH správy už tato záložka
nezpřístupňuje. Vzdálená správa hostitele se otevírá v záložce **Zabezpečení** až
po místním wheel/PAM ověření. Game Mover následně vlastní SSH proces používající
jen klíč a místní forward; administrativní HTTP API tak na obou koncích zůstává
pouze na loopbacku. Po připravení tunelu se samostatně ověř jako wheel uživatel
hostitele v záložce **Timekpr** a poté uprav registr služeb. Každý řádek obsahuje
ID, zobrazovaný název, systemd jednotku, typ (`generic` nebo `minecraft`) a u
Minecraftu absolutní cestu k datovému adresáři a adresáři `mods`. Herní port se zjišťuje automaticky z běžícího workloadu. Forge a Pixelmon
tak mají vlastní cestu i porovnání modů; například Pixelmon může používat jednotku
`pixelmon-srv`. Autorizace se nastavuje samostatně v záložce **Zabezpečení**.
Přímé ovládání přes LAN/Tailscale HTTP je vždy odmítnuto.
Po úspěšném vypnutí se také vyčistí systemd stav
`failed`, který Minecraft může zanechat návratovým kódem 130.

SSH cíl se bere z vybraného profilu připojení: doma použij LAN profil hostitele,
z práce jeho Tailscale profil. Tailscale zde jen přenáší SSH; Game Mover se stále
připojuje na `http://127.0.0.1:5500` a backend hostitele vidí loopback požadavek.
Spravovaný SSH klient vyžaduje klíč nebo `ssh-agent`, odmítá heslo i
keyboard-interactive ověření, kontroluje existující záznam `known_hosts`, vypíná
agent/X11 forwarding a poslouchá výhradně na `127.0.0.1`. Nový host key nejprve
interaktivně zapiš v terminálu a ověř jeho fingerprint. Game Mover před odesláním
hostitelských PAM údajů kontroluje, že listener skutečně vlastní jeho SSH proces.
Zavřením tunelu nebo GUI se hostitelská relace zahodí a aplikace se okamžitě vrátí
do read-only režimu Klient. Do klientské konfigurace se ukládá pouze SSH uživatel
a porty; SSH heslo, privátní klíč ani hostitelský `api.token` se do ní nekopírují.

### Registr bezpečnostních zásad

Záložka **Zabezpečení** je jednotný editor autorizace vynucované backendem.
Obsahuje zásady globálních operací platformy i samostatné zásady
spuštění/vypnutí/restartu/zálohy každého registrovaného serveru. `silent` používá
místní `api.token`, `pam` vyžaduje platnou relaci wheel uživatele a `disabled`
operaci odmítne přímo backend. V režimu SSH tunelu může PAM relace autorizovat i
tichou operaci, takže `api.token` nikdy neopustí hostitele. Správa Timekpr a změny
samotného zabezpečení jsou kvůli jednotnému vzhledu zobrazené ve stejné tabulce
jako pevné neaktivní volby **Vyžaduje PAM**; přes GUI ani API je nelze oslabit.

Zásady se atomicky ukládají do `/etc/game_mover/security.json`. Při prvním
načtení se serverové hodnoty v paměti převezmou ze starších polí `permissions`
nebo `control_auth` v `/etc/game_mover/servers.json`; prvním uložením záložky
Zabezpečení vznikne centrální soubor. Registr služeb už autorizaci neupravuje.
Ve stejné záložce jsou jinak skryté ovládací prvky spravovaného tunelu. Jejich
zobrazení vyžaduje PAM proti místní službě Game Mover na laptopu; otevření tunelu
tuto relaci nepřenáší na hostitele. Pro administrativní operace je vždy nutné
druhé PAM přihlášení vůči hostiteli.

### Správa jednotlivých serverů

Záložka **Servery** zůstává stručným denním přehledem. Karta serveru v režimu
správy hostitele ponechává jedno kontextové tlačítko Spustit/Vypnout a otevírá
jedinou zavíratelnou kartu **Správa: název**. Opakované otevření stejného serveru
aktivuje existující kartu a nevytváří duplicitu. Přehled správy obsahuje stav,
připojení, hráče, runtime, účinná oprávnění a všechny lifecycle příkazy. Servery
s persistentními daty navíc ukazují ověřený katalog záloh a vytvoření zálohy;
Minecraft servery obsahují také inventář modů a porovnání s klientem. Vzdálený
režim Klient může zobrazit bezpečný read-only obsah, ale nemůže provádět operace
hostitele.

Sekce **Logy** u Minecraftu přednostně čte omezený konec persistentního
`data/logs/latest.log` (tedy i Forge/mod výstup); registrovaná systemd jednotka
nebo Podman container zůstává bezpečným záložním zdrojem. Výpis lze volitelně
obnovovat každých pět sekund.
Minecraft sekce **Hráči** zobrazuje operátory z registrovaného `ops.json`.
Přidání a odebrání OP používá pouze validované příkazy `op`/`deop` přes interní
RCON. Podman používá `rcon-cli` uvnitř image, takže se RCON port ani heslo
nezveřejňují mimo container. Samostatná zásada `minecraft.operators` má výchozí
režim PAM.
Samostatná sekce **Whitelist** zobrazuje účinný stav a bezpečně čte registrovaný
`whitelist.json`. Formulář se mapuje pouze na příkazy `whitelist on`, `off`,
`add`, `remove` a `reload`; samostatná zásada `minecraft.whitelist` má výchozí
režim PAM. Zapnutí whitelistu neodpojí hráče, kteří už jsou online.

Obnova do existujícího workloadu zatím záměrně není nabízena jako funkční
tlačítko. Současná ověřená obnova vytváří přes instalátor nový izolovaný Minecraft
workload. In-place obnova přibude později jako samostatná destruktivní operace s
vlastní bezpečnostní zásadou.

Při vypršení PAM relace hostitele první odmítnutá chráněná operace okamžitě zamkne
ovládací prvky správy a vyzve k novému ověření v záložce Timekpr. Aplikace ji
nikdy nezkouší potichu opakovat s širšími oprávněními.

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

ID workloadu určuje povolenou datovou cestu `<data root>/<id>/data`.
Autorizace jeho akcí žije v centrálním registru Zabezpečení a nikdy nepovoluje
vzdálené ovládání. Běžící workload se
korektně zastaví, zazálohuje
se celý datový adresář, archiv se ověří a následně se obnoví původní stav běhu.
Ke gzip archivu vznikne SHA-256 soubor a JSON manifest s vybranými nesekretními
údaji o image/runtime. Lokální instalační dialog chráněný PAM umí vytvořit nový
spravovaný rootless Podman server s prázdnými daty nebo do jeho izolovaného
adresáře obnovit jednu z těchto ověřených záloh. Zdrojový server ani jeho data
se obnovou nemění. Rozhraní ověřuje ID workloadu, verze Minecraftu a loaderu,
Java image, paměť, unikátní přímý port a volitelný Gate hostname; před instalací
také vyžaduje výslovný souhlas s Minecraft EULA.

V přehledu správy lze nevratně odstranit pouze Minecraft instance vytvořené a
vlastněné platformou (`podman` a `management_mode=managed`). Mazání má vlastní
zásadu v Zabezpečení, vyžaduje opsání přesného ID workloadu, vždy odstraní
container a registraci a nabízí oddělenou volbu pro persistentní data a katalog
záloh. Pojmenované Gate trasy se odstraní spolu se serverem. Pokud server stále
vlastní výchozí trasu `*`, mazání se odmítne, dokud správce fallback výslovně
nepřesměruje jinam. Systemd ani adoptovaný Podman workload tímto rozhraním
zničit nelze.
Průběh mazání backend hlásí po jednotlivých fázích na kartě serveru i v jeho
přehledu správy. Dokončení zavře správu odstraněného serveru bez opožděného
modálního dialogu nad jinou prací; chyba zůstane viditelná přímo v přehledu pro
diagnostiku a opakování.

U systemd Minecraftu se port čte ze `server.properties`, u Podmanu z publikovaného
mapování containerového portu `25565/tcp`. Zjištěný port slouží pro standardní
read-only Minecraft status ping a vzdálený klient jej spojí s hostitelem ze svého
profilu. Ze stejné odpovědi se načte přesná verze Minecraftu oznamovaná klientům
a číslo protokolu. GUI ji ukazuje na kartě serveru i v přehledu správy, aby bylo
před připojením jasné, jakou verzi launcheru použít. Selhání dotazu nic neblokuje
a verze se nikdy neodhaduje z tagu container image.
Pokud se persistentní Gate trasa bezpečně spáruje s registrovaným Minecraft
workloadem, karta Servery ji ukáže jako doporučenou adresu pro hráče. Přehled
správy pak zobrazí adresu přes Gate i přímou adresu backendu. Bez použitelné Gate
trasy se všude nadále ukazuje pouze přímá adresa.
U lokálních systemd serverů se zapnutým RCON se počet hráčů přednostně
načítá autentizovaným read-only příkazem `list`; RCON heslo backend nikdy
neposílá do API. Standardní status protokol zůstává fallbackem, takže RCON je
volitelný. Celkový počet známých hráčů se počítá ze UUID v persistentních datech
světa a v `usercache.json`.

### Příprava směrování Gate Lite

Game Mover umí připravit Gate Lite jako rootless Podman workload. Zpočátku
poslouchá na TCP `25581` a transparentně směruje na existující Forge na `25565`,
takže nepřebírá produkční provoz. Do generovaného
`/var/lib/game-platform/proxies/gate/config.yml` se dostanou jen ověřené
hostname, adresy a porty backendů; API nikdy nepřijímá volné YAML. Nasazení je
dostupné jen místně a vyžaduje PAM. Gate Lite neukončuje hráčskou relaci ani
nepřestavuje handshake mod loaderu, takže backend zachovává běžnou autentizaci
`online-mode=true` i nezměněný Forge handshake. Gate Lite je připojený do
spravované rootless bridge sítě `game-platform`; budoucí Minecraft backendy lze
směrovat přes stabilní privátní aliasy bez publikování herních portů do LAN.
Unikátní přímý port zůstává volitelným kompatibilním fallbackem.
V záložce Servery je samostatný editor směrování: uživatel zadá hostname a vybere
registrovaný Minecraft server. Nezadává názvy containerů, bridge adresy, systemd
porty ani YAML. Backend pro spravovaný Podman na bridge síti odvodí
`container:25565`; u adoptovaného Podmanu a systemd použije publikovaný port
hostitele. Právě jedna výchozí `*` trasa je
povinná a zůstává poslední. Uložení restartuje již nasazený Gate; pokud aplikace
nové konfigurace selže, obnoví se předchozí stav.
Dlouhé operace zveřejňují bezpečný popis fáze a procenta; karta v záložce Servery
je průběžně zobrazuje bez zpřístupnění výstupu příkazů nebo tajných údajů. Stejný
model používají také instalace a obnovy Minecraft serverů do Podmanu.
Ověřený Gate image je připnutý neměnným SHA-256 digestem. Gate Lite se označí
jako připravený až ve chvíli, kdy jeho TCP listener přijímá spojení;
samotný stav běžícího containeru není dostatečný health check. Zdraví backendové
trasy se vyhodnocuje samostatně, protože modovaný Forge nemusí odpovídat na
status ping, přestože funguje RCON i připojení hráčů.

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
