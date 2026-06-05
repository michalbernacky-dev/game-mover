#!/usr/bin/env bash
set -euo pipefail

# Rychlý deploy pro vývoj na stejném PC.
# Použití:
#   ./deploy.sh
# Předpokládá, že repo je už aktuální a že chceš z něj sestavit a nainstalovat
# lokální RPM balíček.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}"
rpm_path="$("${SCRIPT_DIR}/build_rpm.sh")"
sudo rpm -Uvh --replacepkgs --replacefiles "${rpm_path}"
sudo systemctl restart game_mover.service

echo "Deploy hotový: nainstalován ${rpm_path} a game_mover.service restartována."
