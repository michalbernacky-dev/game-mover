#!/usr/bin/env bash
set -euo pipefail

# Rychlý deploy pro vývoj na stejném PC.
# Použití:
#   ./deploy.sh        # vyžádá sudo jednou na začátku
#   sudo ./deploy.sh   # celý deploy už běží s oprávněním root
# Předpokládá, že repo je už aktuální a že chceš z něj sestavit a nainstalovat
# lokální RPM balíček.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID}" -eq 0 ]]; then
  AS_ROOT=()
else
  echo "Ověřuji oprávnění pro instalaci RPM a restart služby..."
  sudo -v
  AS_ROOT=(sudo)
fi

cd "${SCRIPT_DIR}"
rpm_path="$("${SCRIPT_DIR}/build_rpm.sh")"
"${AS_ROOT[@]}" rpm -Uvh --replacepkgs --replacefiles "${rpm_path}"
"${AS_ROOT[@]}" systemctl enable game_mover.service
"${AS_ROOT[@]}" systemctl restart game_mover.service

echo "Deploy hotový: nainstalován ${rpm_path} a game_mover.service povolena a restartována."
