#!/usr/bin/env bash
set -euo pipefail

# Rychlý deploy pro vývoj na stejném PC.
# Použití:
#   ./deploy.sh
# Předpokládá, že repo je už aktuální (git pull) a že chceš jen syncnout
# zdroj do /opt/game_mover a restartovat službu.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}"
sudo bash "${SCRIPT_DIR}/install.sh" --no-restart
sudo systemctl restart game_mover.service

echo "Deploy hotový: /opt/game_mover syncnuto a game_mover.service restartována."
