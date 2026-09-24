#!/usr/bin/env bash
set -euo pipefail

# Installer pro Game Mover na Fedoře (systemd + launcher).
# Idempotentní: lze spouštět opakovaně pro update z repa.
# Skript musí běžet jako root.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/game_mover"
SERVICE_NAME="game_mover.service"
PRIVILEGED_SERVICE_NAME="game-mover-privileged.service"
LEGACY_SERVICE_NAME="steam_mover.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
PRIVILEGED_SERVICE_PATH="/etc/systemd/system/${PRIVILEGED_SERVICE_NAME}"
EA_BIND_SERVICE_PATH="/etc/systemd/system/game-mover-ea-bind@.service"
APP_DESKTOP="/usr/share/applications/game-mover.desktop"
APP_METAINFO="/usr/share/metainfo/game-mover.metainfo.xml"
AUTOSTART_DESKTOP="/etc/xdg/autostart/game-mover.desktop"
LOCAL_ADMIN_DIR="/etc/game_mover"
LOCAL_ADMIN_TOKEN_PATH="${LOCAL_ADMIN_DIR}/api.token"
READ_TOKEN_PATH="${LOCAL_ADMIN_DIR}/read.token"
CURSEFORGE_KEY_PATH="${LOCAL_ADMIN_DIR}/curseforge.key"
ALLOWED_SERVICES_PATH="${LOCAL_ADMIN_DIR}/allowed-services.json"
GROUP_NAME="gemers"
PODMAN_USER="gameplatform"
PODMAN_HOME="/var/lib/game-platform"
STATE_DIR="/var/lib/game-mover"
RESTART_SERVICE=1
AUTOSTART=0

show_help() {
  cat <<EOF
Použití: sudo ./install.sh [--no-restart] [--enable-autostart]

--no-restart        nahraje soubory a daemon-reload, ale neprovede enable/now
--enable-autostart  vytvoří autostart položku pro GUI (výchozí je bez autostartu)

Pro běžný vývojový deploy na stejném PC použij:
  ./deploy.sh
EOF
}

for arg in "$@"; do
  case "$arg" in
    --help|-h) show_help; exit 0 ;;
    --no-restart) RESTART_SERVICE=0 ;;
    --enable-autostart) AUTOSTART=1 ;;
    *) echo "Neznámá volba: $arg"; show_help; exit 1 ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Spusť jako root (sudo ./install.sh)." >&2
  exit 1
fi

copy_if_changed() {
  local src="$1" dst="$2" mode="$3"
  if [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then
    echo "    beze změny: $dst"
  else
    install -Dm"$mode" "$src" "$dst"
    echo "    aktualizováno: $dst"
  fi
}

ensure_local_admin_token() {
  mkdir -p "${LOCAL_ADMIN_DIR}"
  if [[ ! -f "${LOCAL_ADMIN_TOKEN_PATH}" ]]; then
    token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    printf '%s\n' "${token}" > "${LOCAL_ADMIN_TOKEN_PATH}"
  fi
  chown root:"${GROUP_NAME}" "${LOCAL_ADMIN_TOKEN_PATH}"
  chmod 0640 "${LOCAL_ADMIN_TOKEN_PATH}"
  if [[ ! -f "${READ_TOKEN_PATH}" ]]; then
    token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    printf '%s\n' "${token}" > "${READ_TOKEN_PATH}"
  fi
  chown root:"${GROUP_NAME}" "${READ_TOKEN_PATH}"
  chmod 0640 "${READ_TOKEN_PATH}"
  if [[ -f "${CURSEFORGE_KEY_PATH}" ]]; then
    chown root:"${PODMAN_USER}" "${CURSEFORGE_KEY_PATH}"
    chmod 0640 "${CURSEFORGE_KEY_PATH}"
  fi
  if [[ ! -f "${ALLOWED_SERVICES_PATH}" ]]; then
    printf '%s\n' '["forge-srv.service", "satisfactory.service"]' \
      > "${ALLOWED_SERVICES_PATH}"
  fi
  chown root:root "${ALLOWED_SERVICES_PATH}"
  chmod 0644 "${ALLOWED_SERVICES_PATH}"
}

echo "[1/8] Kopíruji aplikaci do ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
rsync -a --delete \
  --include='/game_mover*.py' \
  --include='/game-mover' \
  --include='/requirements.txt' \
  --include='/game_mover_logo.jpg' \
  --include='/LICENSE' \
  --include='/NOTICE' \
  --include='/SECURITY.md' \
  --include='/TRADEMARKS.md' \
  --exclude='*' \
  "${SCRIPT_DIR}/" "${INSTALL_DIR}/"
chmod 0644 "${INSTALL_DIR}"/game_mover_*.py
chmod 0644 "${INSTALL_DIR}/LICENSE" "${INSTALL_DIR}/NOTICE" \
  "${INSTALL_DIR}/SECURITY.md" "${INSTALL_DIR}/TRADEMARKS.md"
chmod 0755 "${INSTALL_DIR}/game_mover_flask.py" "${INSTALL_DIR}/game_mover.py" "${INSTALL_DIR}/game-mover"
chmod 0755 "${INSTALL_DIR}/game_mover_ea_epic.py"
chmod 0755 "${INSTALL_DIR}/game_mover_rockstar_epic.py"
find "${INSTALL_DIR}" -type d -name __pycache__ -prune -exec rm -rf -- {} +

echo "[2/8] Odstraňuji starou službu ${LEGACY_SERVICE_NAME} (pokud existuje)"
systemctl disable --now "${LEGACY_SERVICE_NAME}" >/dev/null 2>&1 || true
rm -f "/etc/systemd/system/${LEGACY_SERVICE_NAME}"

echo "[3/8] Vytvářím skupinu a adresáře pro sdílené hry"
if ! getent group "${GROUP_NAME}" >/dev/null; then
  groupadd --system "${GROUP_NAME}"
fi
if ! getent passwd "${PODMAN_USER}" >/dev/null; then
  useradd --system --create-home --home-dir "${PODMAN_HOME}" \
    --shell /usr/sbin/nologin "${PODMAN_USER}"
fi
if [[ -L "${PODMAN_HOME}/servers" ]]; then
  echo "Spravovaný adresář ${PODMAN_HOME}/servers nesmí být symbolický odkaz." >&2
  exit 1
fi
install -d -m 0750 -o "${PODMAN_USER}" -g "${PODMAN_USER}" \
  "${PODMAN_HOME}" "${PODMAN_HOME}/backups" \
  "${PODMAN_HOME}/proxies" "${PODMAN_HOME}/runtime"
# Repair legacy subordinate-ID data through descriptor-based ACL migration.
# Failure aborts installation; no broad setfacl/chown fallback is permitted.
python3 -I -B "${INSTALL_DIR}/game_mover_acl.py"
install -d -m 0750 -o "${PODMAN_USER}" -g "${PODMAN_USER}" "${STATE_DIR}"
for config in servers.json gate.json security.json dns.json dns-runtime.json dns-pihole-state.json; do
  if [[ -f "${LOCAL_ADMIN_DIR}/${config}" && ! -e "${STATE_DIR}/${config}" ]]; then
    mv "${LOCAL_ADMIN_DIR}/${config}" "${STATE_DIR}/${config}"
  fi
  if [[ -f "${STATE_DIR}/${config}" ]]; then
    chown "${PODMAN_USER}:${PODMAN_USER}" "${STATE_DIR}/${config}"
    chmod 0640 "${STATE_DIR}/${config}"
  fi
done
PYTHONPATH="${INSTALL_DIR}" python3 -c \
  'from game_mover_notes import initialize_notes_database; initialize_notes_database("/var/lib/game-platform/game-mover-notes.sqlite3")'
chown "${PODMAN_USER}:${PODMAN_USER}" "${PODMAN_HOME}/game-mover-notes.sqlite3"
chmod 0600 "${PODMAN_HOME}/game-mover-notes.sqlite3"
loginctl enable-linger "${PODMAN_USER}"
podman_uid="$(id -u "${PODMAN_USER}")"
runuser -u "${PODMAN_USER}" -- env XDG_RUNTIME_DIR="/run/user/${podman_uid}" \
  systemctl --user enable --now podman.socket podman-restart.service
ensure_local_admin_token
mkdir -p /var/Games /var/Games/EA /var/Games_links /var/Games/steam-cache
install -d -m 0700 -o root -g root "${LOCAL_ADMIN_DIR}/ea-mounts"
chgrp "${GROUP_NAME}" /var/Games /var/Games/EA /var/Games_links /var/Games/steam-cache
chmod 0775 /var/Games /var/Games/EA /var/Games_links /var/Games/steam-cache
setfacl -m "g:${GROUP_NAME}:rwx,m::rwx,d:g:${GROUP_NAME}:rwx,d:m::rwx" \
  /var/Games /var/Games/EA /var/Games_links /var/Games/steam-cache

echo "[4/8] Instaluji systemd službu pro Flask API (${SERVICE_PATH})"
copy_if_changed "${SCRIPT_DIR}/game_mover.service" "${SERVICE_PATH}" 644
copy_if_changed "${SCRIPT_DIR}/game-mover-privileged.service" \
  "${PRIVILEGED_SERVICE_PATH}" 644
copy_if_changed "${SCRIPT_DIR}/game-mover-dns.service" \
  "/etc/systemd/system/game-mover-dns.service" 644
copy_if_changed "${SCRIPT_DIR}/game-mover-ea-bind@.service" \
  "${EA_BIND_SERVICE_PATH}" 644

echo "[5/8] Desktop launcher (${APP_DESKTOP})"
desktop_tmp="$(mktemp)"
cat >"${desktop_tmp}" <<'EOF'
[Desktop Entry]
Type=Application
Name=Game Mover
Comment=Správa sdílené herní knihovny
Exec=/usr/bin/env python3 -B /opt/game_mover/game_mover.py
Icon=/opt/game_mover/game_mover_logo.jpg
Terminal=false
Categories=Game;Utility;
EOF
copy_if_changed "${desktop_tmp}" "${APP_DESKTOP}" 644
rm -f "${desktop_tmp}"
copy_if_changed "${SCRIPT_DIR}/game-mover.metainfo.xml" "${APP_METAINFO}" 644

echo "[6/8] (Volitelně) GNOME autostart (${AUTOSTART_DESKTOP})"
autostart_tmp="$(mktemp)"
cat >"${autostart_tmp}" <<'EOF'
[Desktop Entry]
Type=Application
Name=Game Mover
Comment=Správa sdílené herní knihovny
Exec=/usr/bin/env python3 -B /opt/game_mover/game_mover.py
Icon=/opt/game_mover/game_mover_logo.jpg
X-GNOME-Autostart-enabled=true
NoDisplay=false
Terminal=false
OnlyShowIn=GNOME;Unity;X-Cinnamon;MATE;XFCE;
EOF
if [[ "${AUTOSTART}" -eq 1 ]]; then
  copy_if_changed "${autostart_tmp}" "${AUTOSTART_DESKTOP}" 644
  echo "    autostart povolen"
else
  if [[ -f "${AUTOSTART_DESKTOP}" ]]; then
    rm -f "${AUTOSTART_DESKTOP}"
    echo "    autostart odstraněn (výchozí stav)"
  else
    echo "    autostart přeskočen"
  fi
fi
rm -f "${autostart_tmp}"

echo "[7/8] CLI helper symlinks"
ln -sf "${INSTALL_DIR}/game-mover" /usr/local/bin/game-mover
ln -sf "${INSTALL_DIR}/game_mover_ea_epic.py" /usr/local/bin/game-mover-ea-epic
ln -sf "${INSTALL_DIR}/game_mover_rockstar_epic.py" /usr/local/bin/game-mover-rockstar-epic
chmod +x /usr/local/bin/game-mover
chmod +x /usr/local/bin/game-mover-ea-epic
chmod +x /usr/local/bin/game-mover-rockstar-epic

echo "[8/8] systemd daemon-reload"
systemctl daemon-reload
systemctl try-restart game-mover-dns.service >/dev/null 2>&1 || true
if [[ "${RESTART_SERVICE}" -eq 1 ]]; then
  echo "    enable + restart ${SERVICE_NAME}"
  systemctl enable --now "${SERVICE_NAME}"
else
  echo "    přeskočeno (--no-restart)"
fi

if [[ "${AUTOSTART}" -eq 1 ]]; then
  autostart_status="povolen"
else
  autostart_status="zakázán"
fi

echo "Hotovo"
echo " - Flask API: ${SERVICE_NAME}"
echo " - GUI: launcher v menu (Game Mover); autostart ${autostart_status}"
echo " - Terminál: /usr/local/bin/game-mover"
echo " - Závislosti: pip install -r \"${INSTALL_DIR}/requirements.txt\""
