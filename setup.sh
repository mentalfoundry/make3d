#!/usr/bin/env bash
# One-time setup on a new Linux x86-64 machine. Safe to re-run.
#   ./setup.sh            3D pipeline (make3d, art3d)
#   ./setup.sh --video    also download the LTX-2.3 video models (~26 GB) for makevideo
# Installs: portable Blender (unless BLENDER points at one), a Python venv built from
# Blender's Python, the MediaPipe models, and the make3d/art3d commands in ~/.local/bin.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BLENDER_VER=5.2.4
BLENDER_SERIES=5.2
APPS="${APPS:-$HOME/apps}"

say() { printf '\n== %s\n' "$*"; }

[ "$(uname -sm)" = "Linux x86_64" ] || { echo "Only Linux x86-64 is supported (this is $(uname -sm))"; exit 1; }
for c in curl tar xz sha256sum python3; do
    command -v "$c" >/dev/null || { echo "Missing '$c': install it with your package manager"; exit 1; }
done

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

# Blender needs the usual desktop graphics libraries (X11, OpenGL, Vulkan, xkbcommon)
missing="$(ldd "$BLENDER_DIR/blender" 2>/dev/null | awk '/not found/ {print $1}' | tr '\n' ' ')"
if [ -n "$missing" ]; then
    echo "  Blender is missing system libraries: $missing"
    echo "  Install your distro's packages for them (see README, 'Troubleshooting'), then re-run."
    exit 1
fi
"$BLENDER_DIR/blender" -b --factory-startup --python-expr "import bpy" >/dev/null 2>&1 \
    || { echo "  Blender failed to start in background mode; run $BLENDER_DIR/blender -b to see why"; exit 1; }

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

# 3b. Video models (optional)
if [ "${1:-}" = "--video" ]; then
    V="$ROOT/models/ltx2"; mkdir -p "$V"
    L=https://huggingface.co/unsloth/LTX-2.3-GGUF/resolve/main
    G=https://huggingface.co/unsloth/gemma-3-12b-it-qat-GGUF/resolve/main
    for pair in "ltx-2.3-22b-distilled-1.1-Q4_K_M.gguf $L/distilled-1.1" \
                "ltx-2.3-22b-distilled_embeddings_connectors.safetensors $L/text_encoders" \
                "ltx-2.3-22b-distilled_video_vae.safetensors $L/vae" \
                "ltx-2.3-22b-distilled_audio_vae.safetensors $L/vae" \
                "gemma-3-12b-it-qat-UD-Q4_K_XL.gguf $G"; do
        set -- $pair
        [ -s "$V/$1" ] && continue
        say "video model: $1"
        curl -fL -C - --retry 5 --progress-bar -o "$V/$1.part" "$2/$1" && mv "$V/$1.part" "$V/$1"
    done
fi

# 4. Commands
mkdir -p "$HOME/.local/bin"
ln -sfn "$ROOT/bin/make3d" "$HOME/.local/bin/make3d"
ln -sfn "$ROOT/bin/art3d" "$HOME/.local/bin/art3d"
ln -sfn "$ROOT/bin/makevideo" "$HOME/.local/bin/makevideo"
ln -sfn "$ROOT/bin/makesound" "$HOME/.local/bin/makesound"
ln -sfn "$ROOT/bin/storyboard" "$HOME/.local/bin/storyboard"
say "commands: make3d, art3d, makevideo, storyboard, makesound -> ~/.local/bin"
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
