#!/usr/bin/env bash
set -euo pipefail

# Installer pro Game Mover na Fedoře (systemd + launcher).
# Idempotentní: lze spouštět opakovaně pro update z repa.
# Skript musí běžet jako root.

INSTALL_DIR="/opt/steam_mover"
SERVICE_NAME="steam_mover.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
APP_DESKTOP="/usr/share/applications/game-mover.desktop"
AUTOSTART_DESKTOP="/etc/xdg/autostart/game-mover.desktop"
GROUP_NAME="gemers"
RESTART_SERVICE=1
AUTOSTART=0

show_help() {
  cat <<EOF
Použití: sudo ./install.sh [--no-restart] [--enable-autostart]

--no-restart        nahraje soubory a daemon-reload, ale neprovede enable/now
--enable-autostart  vytvoří autostart položku pro GUI (výchozí je bez autostartu)
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

echo "[1/7] Kopíruji aplikaci do ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
install -Dm755 steam_flask.py "${INSTALL_DIR}/steam_flask.py"
install -Dm755 steam_mover.py "${INSTALL_DIR}/steam_mover.py"
install -Dm755 game-mover "${INSTALL_DIR}/game-mover"
install -Dm644 "requirements.txt" "${INSTALL_DIR}/requirements.txt"
install -Dm644 steam_mover_logo.jpg "${INSTALL_DIR}/steam_mover_logo.jpg"

echo "[2/7] Vytvářím skupinu a adresáře pro sdílené hry"
if ! getent group "${GROUP_NAME}" >/dev/null; then
  groupadd --system "${GROUP_NAME}"
fi
mkdir -p /var/Games /var/Games_links /var/Games/steam-cache
chgrp "${GROUP_NAME}" /var/Games /var/Games_links /var/Games/steam-cache
chmod 2775 /var/Games /var/Games_links /var/Games/steam-cache

echo "[3/7] Instaluji systemd službu pro Flask API (${SERVICE_PATH})"
service_tmp="$(mktemp)"
cat >"${service_tmp}" <<'EOF'
[Unit]
Description=Flask Server for Game Mover
After=network.target

[Service]
ExecStart=/usr/bin/env python3 /opt/steam_mover/steam_flask.py
WorkingDirectory=/opt/steam_mover
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

echo "[4/7] Desktop launcher (${APP_DESKTOP})"
desktop_tmp="$(mktemp)"
cat >"${desktop_tmp}" <<'EOF'
[Desktop Entry]
Type=Application
Name=Game Mover
Comment=Správa sdílené herní knihovny
Exec=/usr/bin/env python3 /opt/steam_mover/steam_mover.py
Icon=/opt/steam_mover/steam_mover_logo.jpg
Terminal=false
Categories=Game;Utility;
EOF
copy_if_changed "${desktop_tmp}" "${APP_DESKTOP}" 644
rm -f "${desktop_tmp}"

echo "[5/7] (Volitelně) GNOME autostart (${AUTOSTART_DESKTOP})"
autostart_tmp="$(mktemp)"
cat >"${autostart_tmp}" <<'EOF'
[Desktop Entry]
Type=Application
Name=Game Mover
Comment=Správa sdílené herní knihovny
Exec=/usr/bin/env python3 /opt/steam_mover/steam_mover.py
Icon=/opt/steam_mover/steam_mover_logo.jpg
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

echo "[6/7] CLI symlink /usr/local/bin/game-mover"
ln -sf "${INSTALL_DIR}/game-mover" /usr/local/bin/game-mover
chmod +x /usr/local/bin/game-mover

echo "[7/7] systemd daemon-reload"
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
