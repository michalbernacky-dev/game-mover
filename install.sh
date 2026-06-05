#!/usr/bin/env bash
set -euo pipefail

# Installer pro Game Mover na Fedoře (systemd + launcher).
# Idempotentní: lze spouštět opakovaně pro update z repa.
# Skript musí běžet jako root.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/game_mover"
SERVICE_NAME="game_mover.service"
LEGACY_SERVICE_NAME="steam_mover.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
APP_DESKTOP="/usr/share/applications/game-mover.desktop"
AUTOSTART_DESKTOP="/etc/xdg/autostart/game-mover.desktop"
LOCAL_ADMIN_DIR="/etc/game_mover"
LOCAL_ADMIN_TOKEN_PATH="${LOCAL_ADMIN_DIR}/api.token"
GROUP_NAME="gemers"
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
}

echo "[1/8] Kopíruji aplikaci do ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
rsync -a --delete \
  --include='/game_mover_flask.py' \
  --include='/game_mover.py' \
  --include='/game-mover' \
  --include='/requirements.txt' \
  --include='/game_mover_logo.jpg' \
  --exclude='*' \
  "${SCRIPT_DIR}/" "${INSTALL_DIR}/"
chmod 0755 "${INSTALL_DIR}/game_mover_flask.py" "${INSTALL_DIR}/game_mover.py" "${INSTALL_DIR}/game-mover"

echo "[2/8] Odstraňuji starou službu ${LEGACY_SERVICE_NAME} (pokud existuje)"
systemctl disable --now "${LEGACY_SERVICE_NAME}" >/dev/null 2>&1 || true
rm -f "/etc/systemd/system/${LEGACY_SERVICE_NAME}"

echo "[3/8] Vytvářím skupinu a adresáře pro sdílené hry"
if ! getent group "${GROUP_NAME}" >/dev/null; then
  groupadd --system "${GROUP_NAME}"
fi
ensure_local_admin_token
mkdir -p /var/Games /var/Games_links /var/Games/steam-cache
chgrp "${GROUP_NAME}" /var/Games /var/Games_links /var/Games/steam-cache
chmod 2775 /var/Games /var/Games_links /var/Games/steam-cache

echo "[4/8] Instaluji systemd službu pro Flask API (${SERVICE_PATH})"
service_tmp="$(mktemp)"
cat >"${service_tmp}" <<'EOF'
[Unit]
Description=Flask Server for Game Mover
After=network.target

[Service]
ExecStart=/usr/bin/env python3 /opt/game_mover/game_mover_flask.py
WorkingDirectory=/opt/game_mover
User=root
Group=gemers
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
copy_if_changed "${service_tmp}" "${SERVICE_PATH}" 644
rm -f "${service_tmp}"

echo "[5/8] Desktop launcher (${APP_DESKTOP})"
desktop_tmp="$(mktemp)"
cat >"${desktop_tmp}" <<'EOF'
[Desktop Entry]
Type=Application
Name=Game Mover
Comment=Správa sdílené herní knihovny
Exec=/usr/bin/env python3 /opt/game_mover/game_mover.py
Icon=/opt/game_mover/game_mover_logo.jpg
Terminal=false
Categories=Game;Utility;
EOF
copy_if_changed "${desktop_tmp}" "${APP_DESKTOP}" 644
rm -f "${desktop_tmp}"

echo "[6/8] (Volitelně) GNOME autostart (${AUTOSTART_DESKTOP})"
autostart_tmp="$(mktemp)"
cat >"${autostart_tmp}" <<'EOF'
[Desktop Entry]
Type=Application
Name=Game Mover
Comment=Správa sdílené herní knihovny
Exec=/usr/bin/env python3 /opt/game_mover/game_mover.py
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

echo "[7/8] CLI symlink /usr/local/bin/game-mover"
ln -sf "${INSTALL_DIR}/game-mover" /usr/local/bin/game-mover
chmod +x /usr/local/bin/game-mover

echo "[8/8] systemd daemon-reload"
systemctl daemon-reload
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
