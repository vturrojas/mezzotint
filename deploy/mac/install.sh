#!/usr/bin/env bash
# Mezzotint Studio installer for macOS (Apple Silicon).
# Installs the studio into ~/.mezzotint, pulls models into Ollama, generates a
# shared token, and registers a LaunchAgent so the studio starts at login.
#
#   ./deploy/mac/install.sh                 # auto-pick models by RAM
#   IMAGE_MODEL=x/flux2-klein:4b LLM=qwen3:4b ./deploy/mac/install.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
HOME_DIR="$HOME/.mezzotint"
VENV="$HOME_DIR/venv"
ENV_FILE="$HOME_DIR/studio.env"
PLIST="$HOME/Library/LaunchAgents/com.mezzotint.studio.plist"
PORT="${PORT:-8765}"

say() { printf "\033[1;31m▌\033[0m %s\n" "$*"; }
die() { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "This installer is for macOS on Apple Silicon (images are drawn with MLX)."
[[ "$(uname -m)" == "arm64" ]] || die "Apple Silicon (M1 or newer) is required: images are drawn with MLX."

command -v ollama >/dev/null || die "Ollama not found. Install it from https://ollama.com/download (or: brew install ollama), open it once, then re-run."
ollama list >/dev/null 2>&1 || die "Ollama is installed but not running. Open the Ollama app, then re-run."

# Prefer a modern Python over Apple's bundled 3.9 in /usr/bin.
if [[ -z "${PY:-}" ]]; then
  for cand in python3.12 python3.13 python3.11 python3.10 /opt/homebrew/bin/python3 python3; do
    p="$(command -v "$cand" 2>/dev/null || true)"
    if [[ -n "$p" ]] && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$p"; break
    fi
  done
fi
[[ -n "${PY:-}" ]] || die "Python 3.10+ not found. Install it with: brew install python@3.12  (or from python.org), then re-run."
say "Using $("$PY" --version) at $PY"

# ---- the art director (text model, via Ollama) ------------------------------
# Images are NOT drawn by Ollama: it disabled image generation in 0.32.6.
# They are drawn by mflux (MLX), installed into the studio's venv below.
RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
if [[ -z "${LLM:-}" ]]; then
  if (( RAM_GB >= 32 )); then LLM="qwen3:8b"; elif (( RAM_GB >= 16 )); then LLM="qwen3:4b"; else LLM="gemma3:1b"; fi
fi
IMAGE_MODEL="${IMAGE_MODEL:-filipstrand/Z-Image-Turbo-mflux-4bit}"
(( RAM_GB >= 16 )) || say "Only ${RAM_GB} GB RAM: image generation may be slow. Close other apps while it prints."
say "Mac has ${RAM_GB} GB RAM -> art director: ${LLM} (Ollama), image model: ${IMAGE_MODEL} (mflux)"

say "Pulling ${LLM} (art director)..."
ollama pull "$LLM"

# ---- install the studio and mflux ----------------------------------------------
mkdir -p "$HOME_DIR/logs"
[[ -d "$VENV" ]] || "$PY" -m venv "$VENV"
say "Installing the studio and mflux into $VENV ..."
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "${REPO}[studio]"
[[ -x "$VENV/bin/mflux-generate-z-image-turbo" ]] || die "mflux did not install its generator command. See the pip output above."

# The studio keeps its own model cache on the internal disk. A background
# service must not depend on an external volume (macOS silently blocks
# background access to removable drives) or on shell-profile settings.
STUDIO_HF="$HOME_DIR/hf"
mkdir -p "$STUDIO_HF/hub"
MODEL_DIR="models--${IMAGE_MODEL//\//--}"
if [[ ! -d "$STUDIO_HF/hub/$MODEL_DIR" ]]; then
  for src in "${HF_HUB_CACHE:-}" "${HUGGINGFACE_HUB_CACHE:-}" "${HF_HOME:+$HF_HOME/hub}" "$HOME/.cache/huggingface/hub"; do
    if [[ -n "$src" && -d "$src/$MODEL_DIR/snapshots" ]]; then
      say "Copying the model you already have from $src (no re-download)..."
      rsync -a "$src/$MODEL_DIR" "$STUDIO_HF/hub/" && break
    fi
  done
fi

say "Smoke test: first image from ${IMAGE_MODEL}."
say "The first run downloads about 6 GB from Hugging Face; progress bars below are normal."
env -u HF_HUB_CACHE -u HUGGINGFACE_HUB_CACHE HF_HOME="$STUDIO_HF" \
  "$VENV/bin/mflux-generate-z-image-turbo" --model "$IMAGE_MODEL" \
  --prompt "a red circle on white paper, minimal" --width 512 --height 512 --steps 9 --seed 7 \
  --output "$HOME_DIR/smoke-test.png" || die "mflux could not draw the test image. The lines above say why."
[[ -s "$HOME_DIR/smoke-test.png" ]] || die "mflux finished but wrote no image."
say "Image generation works: $HOME_DIR/smoke-test.png"

TOKEN=""
[[ -f "$ENV_FILE" ]] && TOKEN="$(grep -E '^MEZZOTINT_TOKEN=' "$ENV_FILE" | cut -d= -f2- || true)"
[[ -n "$TOKEN" ]] || TOKEN="$(openssl rand -hex 24)"
umask 077
cat > "$ENV_FILE" <<EOF
MEZZOTINT_TOKEN=$TOKEN
MEZZOTINT_LLM=$LLM
MEZZOTINT_IMAGE_BACKEND=mflux
MEZZOTINT_IMAGE_MODEL=$IMAGE_MODEL
MEZZOTINT_IMAGE_SIZE=1024x768
MEZZOTINT_PORT=$PORT
OLLAMA_URL=http://127.0.0.1:11434
EOF
# Point the service at the studio's own internal model cache (see above).
echo "HF_HOME=$STUDIO_HF" >> "$ENV_FILE"
if [[ -n "${HF_TOKEN:-}" ]]; then echo "HF_TOKEN=$HF_TOKEN" >> "$ENV_FILE"; fi
umask 022

# launchd does not read env files, so a tiny wrapper loads it.
cat > "$HOME_DIR/run-studio.sh" <<EOF
#!/bin/bash
unset HF_HUB_CACHE HUGGINGFACE_HUB_CACHE
set -a; source "$ENV_FILE"; set +a
exec "$VENV/bin/mezzotint-studio"
EOF
chmod +x "$HOME_DIR/run-studio.sh"

# The macOS application firewall judges the real interpreter binary, and it
# can only prompt "allow incoming connections?" on the Mac's screen, which
# nobody sees during a remote install. Allow the studio's Python explicitly.
FW=/usr/libexec/ApplicationFirewall/socketfilterfw
if [[ "$("$FW" --getglobalstate 2>/dev/null)" == *enabled* ]]; then
  # A framework Python (Homebrew, python.org) re-executes itself as
  # .../Resources/Python.app, and that bundle is what the firewall judges.
  # Allow it, and the plain interpreter too for non-framework builds.
  PYAPP="$("$VENV/bin/python" -c 'import os,sys; p=os.path.join(os.path.realpath(sys.base_prefix),"Resources","Python.app"); print(p if os.path.isdir(p) else "")')"
  PYBIN="$("$VENV/bin/python" -c 'import os,sys; print(os.path.realpath(sys.executable))')"
  say "macOS firewall is on: allowing the studio's Python to accept connections (sudo)..."
  for target in "$PYAPP" "$PYBIN"; do
    [[ -n "$target" ]] || continue
    sudo "$FW" --add "$target" >/dev/null && sudo "$FW" --unblockapp "$target" >/dev/null \
      || say "Could not allow $target. If the Pi can't reach the studio, allow it in System Settings > Network > Firewall."
  done
  say "Note: after 'brew upgrade python@3.12', re-run this installer so the firewall rule follows the new Python."
fi

mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.mezzotint.studio</string>
  <key>ProgramArguments</key><array><string>$HOME_DIR/run-studio.sh</string></array>
  <key>RunAtLoad</key><true/>
  <!-- Without a ProcessType, launchd throttles the job's CPU and I/O; image
       generation then crawls. Interactive gives it foreground priority. -->
  <key>ProcessType</key><string>Interactive</string>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME_DIR/logs/studio.log</string>
  <key>StandardErrorPath</key><string>$HOME_DIR/logs/studio.log</string>
</dict></plist>
EOF
# Reload the agent. bootout returns before the job is actually gone, and a
# bootstrap in that window fails with "5: Input/output error", so wait for it.
DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/com.mezzotint.studio" 2>/dev/null || true
for _ in $(seq 1 20); do
  launchctl print "$DOMAIN/com.mezzotint.studio" >/dev/null 2>&1 || break
  sleep 0.5
done
loaded=0
for _ in 1 2 3 4 5; do
  if launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null; then loaded=1; break; fi
  sleep 2
done
(( loaded )) || die "launchd would not load the studio. Try: launchctl bootstrap $DOMAIN $PLIST"

sleep 3
if curl -sf --noproxy '*' -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/health" >/dev/null; then
  say "Studio is running on port $PORT."
else
  die "Studio did not come up; see $HOME_DIR/logs/studio.log"
fi

HOSTNAME_LOCAL="$(scutil --get LocalHostName 2>/dev/null || hostname -s).local"
cat <<EOF

  ┌─────────────────────────────────────────────────────────────────┐
  │  Studio ready. On the Pi, run the frame installer with:         │
  └─────────────────────────────────────────────────────────────────┘

    ./deploy/pi/install.sh --studio http://$HOSTNAME_LOCAL:$PORT --token $TOKEN

  If macOS asks whether Python may accept incoming connections, click Allow.
  Logs: $HOME_DIR/logs/studio.log
EOF
