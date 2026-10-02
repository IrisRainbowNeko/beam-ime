#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -f /etc/arch-release ]; then
  sudo pacman -U -- ./*.pkg.tar.zst
else
  sudo apt install -- ./*.deb
fi
beamctl model-install --source "$PWD/beam-0.6b-q8_0.gguf"
beamctl setup
