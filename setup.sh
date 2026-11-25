#!/usr/bin/env bash
# One-time setup on a new Linux x86-64 machine. Safe to re-run.
#   ./setup.sh
# Installs: portable Blender (unless BLENDER points at one), a Python venv built from
# Blender's Python, the MediaPipe models, and the make3d/art3d commands in ~/.local/bin.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BLENDER_VER=5.2.4
BLENDER_SERIES=5.2
APPS="${APPS:-$HOME/apps}"

say() { printf '\n== %s\n' "$*"; }

# 1. Blender
if [ -n "${BLENDER:-}" ]; then
    say "Blender: using \$BLENDER=$BLENDER"
    BLENDER_DIR="$(dirname "$(readlink -f "$BLENDER")")"
else
    BLENDER_DIR="$APPS/blender-$BLENDER_VER-linux-x64"
    if [ ! -x "$BLENDER_DIR/blender" ]; then
        say "Blender $BLENDER_VER: downloading (~400 MB) to $APPS"
        mkdir -p "$APPS/dl"
        base="https://download.blender.org/release/Blender$BLENDER_SERIES"
        tarball="blender-$BLENDER_VER-linux-x64.tar.xz"
        curl -fL --progress-bar -o "$APPS/dl/$tarball" "$base/$tarball"
        curl -fsSL -o "$APPS/dl/blender-$BLENDER_VER.sha256" "$base/blender-$BLENDER_VER.sha256"
        (cd "$APPS/dl" && grep " $tarball\$" "blender-$BLENDER_VER.sha256" | sha256sum -c -)
        tar -xJf "$APPS/dl/$tarball" -C "$APPS"
    else
        say "Blender $BLENDER_VER: already in $BLENDER_DIR"
    fi
    ln -sfn "$BLENDER_DIR" "$APPS/blender"
fi

# 2. venv from Blender's Python (MediaPipe needs Python <= 3.13)
BPY="$(ls "$BLENDER_DIR"/*/python/bin/python3.* 2>/dev/null | grep -v config | head -1)"
[ -x "$BPY" ] || { echo "Blender's Python not found under $BLENDER_DIR"; exit 1; }
if [ ! -x "$ROOT/venv/bin/python" ]; then
    say "venv: creating from $BPY"
    "$BPY" -m venv "$ROOT/venv"
fi
say "venv: installing packages"
"$ROOT/venv/bin/python" -m pip install -q --upgrade pip
"$ROOT/venv/bin/python" -m pip install -q -r "$ROOT/requirements.txt"

# 3. MediaPipe models
mkdir -p "$ROOT/models"
mp="https://storage.googleapis.com/mediapipe-models"
for m in "pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task" \
         "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"; do
    f="$ROOT/models/$(basename "$m")"
    [ -s "$f" ] || { say "model: $(basename "$m")"; curl -fL --progress-bar -o "$f" "$mp/$m"; }
done

# 4. Commands
mkdir -p "$HOME/.local/bin"
ln -sfn "$ROOT/bin/make3d" "$HOME/.local/bin/make3d"
ln -sfn "$ROOT/bin/art3d" "$HOME/.local/bin/art3d"
say "commands: make3d, art3d -> ~/.local/bin"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "  add ~/.local/bin to your PATH";; esac

# 5. Lemonade check
URL="${LEMONADE_URL:-http://127.0.0.1:13305/api/v1}"
say "Lemonade: checking $URL"
KEY="${LEMONADE_API_KEY:-$(cat "$HOME/.config/lemonade/api-key" 2>/dev/null || true)}"
[ -n "$KEY" ] || echo "  no API key: put it in ~/.config/lemonade/api-key (no trailing newline) or LEMONADE_API_KEY"
if models="$(curl -fsS -m 10 -H "Authorization: Bearer $KEY" "$URL/models" 2>/dev/null)"; then
    for m in Flux-2-Klein-4B TRELLIS-3D Qwen3.6-35B-A3B-MTP-GGUF; do
        if grep -q "\"$m\"" <<<"$models"; then echo "  ok      $m"; else echo "  MISSING $m  (lemonade pull $m)"; fi
    done
else
    echo "  can't reach Lemonade. Run it here, or point LEMONADE_URL at another machine (see docs/lemonade.md)"
fi
say "done. Try: make3d \"a knight in silver plate armour\""
