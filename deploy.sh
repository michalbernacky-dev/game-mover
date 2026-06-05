#!/usr/bin/env bash
set -euo pipefail

# Rychlý deploy pro vývoj na stejném PC.
# Použití:
#   ./deploy.sh
# Předpokládá, že repo je už aktuální (git pull) a že chceš jen syncnout
# zdroj do /opt/steam_mover a restartovat službu.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}"
sudo bash "${SCRIPT_DIR}/install.sh" --no-restart
sudo systemctl restart steam_mover.service

echo "Deploy hotový: /opt/steam_mover syncnuto a steam_mover.service restartována."
