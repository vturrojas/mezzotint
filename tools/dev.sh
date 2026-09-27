#!/usr/bin/env bash
# Run the whole system on one laptop with a fake Ollama and a virtual panel.
# Open http://localhost:8080 afterwards. Ctrl-C stops everything.
set -euo pipefail
cd "$(dirname "$0")/.."
export MEZZOTINT_TOKEN=dev-token
DATA="${DATA:-/tmp/mezzotint-dev}"
mkdir -p "$DATA"

python3 tools/fake_ollama.py & P1=$!
sleep 1
OLLAMA_URL=http://127.0.0.1:11999 MEZZOTINT_IMAGE_BACKEND=ollama MEZZOTINT_HOST=127.0.0.1 mezzotint-studio & P2=$!
sleep 2
MEZZOTINT_DATA="$DATA" MEZZOTINT_DISPLAY=virtual MEZZOTINT_STUDIO_URL=http://127.0.0.1:8765 \
  MEZZOTINT_FRAME_PORT=8080 MEZZOTINT_PUBLIC_URL=http://localhost:8080/ mezzotint-frame & P3=$!
trap 'kill $P1 $P2 $P3 2>/dev/null' EXIT
echo
echo "  Mezzotint dev: open http://localhost:8080   (panel image: $DATA/virtual-panel.png)"
echo "  Swap the fake for real Ollama with: OLLAMA_URL=http://127.0.0.1:11434"
wait
