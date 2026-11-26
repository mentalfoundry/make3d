#!/usr/bin/env python3
"""Prompt (and optionally a start/end image) -> short video with matching sound, locally.

Uses LTX-2.3 (distilled 1.1) through stable-diffusion.cpp's sd-cli on Vulkan: the same
engine Lemonade uses for Flux, called directly because Lemonade has no video endpoint.

Examples:
  makevideo "a knight walks through a misty forest, footsteps on leaves, birdsong"
  makevideo "the knight raises his sword and shouts" --image ~/3d/out/knight/concept.png
  makevideo "a flower blooms" --image bud.png --end-image flower.png
  makevideo "waves crashing on rocks at sunset" --seconds 8 --size 1024x576
  makevideo "a campfire crackling" --draft        # quick low-res preview
  makevideo "the <knight> raises his sword, steel ringing" --refs ~/chars   # knight.png in ~/chars:
                                       # Flux draws the start frame with that character first

Output: ~/3d/out/video/<name>/: <name>.webm (VP8/Opus: browsers, Unity, VLC), <name>.ogv
(Theora/Vorbis: Godot), <name>.mp4 (H.264/AAC: Unreal, phones, editors; needs an H.264
encoder in ffmpeg), plus prompt.json with the settings and the sd-cli log.
"""
import argparse, json, os, re, shutil, subprocess, sys, time

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
import lemon  # noqa: E402

MODELS = os.environ.get("LTX_MODELS", os.path.join(ROOT, "models", "ltx2"))
FILES = {
    "diffusion": "ltx-2.3-22b-distilled-1.1-Q4_K_M.gguf",
    "connectors": "ltx-2.3-22b-distilled_embeddings_connectors.safetensors",
    "vae": "ltx-2.3-22b-distilled_video_vae.safetensors",
    "audio_vae": "ltx-2.3-22b-distilled_audio_vae.safetensors",
    "llm": "gemma-3-12b-it-qat-UD-Q4_K_XL.gguf",
}
NEGATIVE = "worst quality, low quality, blurry, distorted, artifacts, jittery, watermark, text"
NEED_GPU_GB = 36   # weights ~26 GB + working memory; measured, see README


def find_sd_cli():
    for p in (os.environ.get("SD_CLI"), "/var/cache/lemonade/bin/sd-cpp/vulkan/sd-cli",
              shutil.which("sd-cli")):
        if p and os.path.exists(p):
            return p
    sys.exit("sd-cli not found. It comes with Lemonade's sd-cpp Vulkan backend, "
             "or set SD_CLI=/path/to/sd-cli")


def gpu_free_gb():
    """Free GPU memory on AMD (sysfs); None if unknown."""
    best = None
    for card in os.listdir("/sys/class/drm"):
        d = f"/sys/class/drm/{card}/device"
        try:
            total = int(open(f"{d}/mem_info_vram_total").read())
            used = int(open(f"{d}/mem_info_vram_used").read())
        except OSError:
            continue
        best = max(best or 0, (total - used) / 2**30)
    return best


def make_room():
    """Unload the 3D pipeline's own models (they reload on demand) if the GPU is short.
    Chat, voice and other shared models are left alone."""
    free = gpu_free_gb()
    if free is None or free >= NEED_GPU_GB:
        return
    if not lemon.is_local():
        print(f"  warning: only {free:.0f} GB GPU memory free (need ~{NEED_GPU_GB}); "
              f"close other GPU programs if the run fails")
        return
    print(f"  GPU memory free: {free:.0f} GB, need ~{NEED_GPU_GB}; unloading TRELLIS-3D and Flux")
    for m in (lemon.MODEL_3D, lemon.IMAGE_MODEL):
        try:
            lemon.unload(m)
        except Exception:
            pass
    time.sleep(3)
    free = gpu_free_gb()
    if free is not None and free < NEED_GPU_GB - 4:
        print(f"  warning: still only {free:.0f} GB free; the run may fail. "
              f"Unload other models in Lemonade if it does.")


def ffmpeg(*args):
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                       capture_output=True, text=True)
    return r.returncode == 0, r.stderr.strip()


def make_copies(raw, out, name, pixels):
    """sd-cli writes VP8 with uncompressed PCM audio, which browsers and many players
    reject. Make: <name>.webm (VP8 + Opus), <name>.ogv (Theora + Vorbis, for Godot) and
    <name>.mp4 (H.264 + AAC) when an H.264 encoder is available."""
    made = []
    webm = os.path.join(out, f"{name}.webm")
    ok, err = ffmpeg("-i", raw, "-c:v", "copy", "-c:a", "libopus", "-b:a", "160k", webm)
    if ok:
        os.remove(raw)
        made.append(webm)
    else:
        os.replace(raw, webm)
        made.append(webm + "  (audio left as PCM: " + err[-120:] + ")")
    ogv = os.path.join(out, f"{name}.ogv")
    if ffmpeg("-i", webm, "-c:v", "libtheora", "-q:v", "8", "-c:a", "libvorbis", "-q:a", "6", ogv)[0]:
        made.append(ogv)
    mp4 = os.path.join(out, f"{name}.mp4")
    for enc in (["-c:v", "libx264", "-crf", "18"],
                ["-c:v", "libopenh264", "-b:v", f"{max(2, pixels * 24 // 100000)}M"]):
        if ffmpeg("-i", webm, *enc, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                  "-movflags", "+faststart", mp4)[0]:
            made.append(mp4)
            break
    else:
        if os.path.exists(mp4):
            os.remove(mp4)
        made.append("(no .mp4: ffmpeg has no working H.264 encoder. On Fedora: "
                    "sudo dnf swap noopenh264 openh264)")
    return made


def draw_start_frame(prompt, names, folder, out, size, seed):
    """Flux draws the opening frame with the referenced characters (like storyboard keyframes)."""
    files = {os.path.splitext(f)[0].lower(): f for f in os.listdir(folder)
             if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))}
    missing = [n for n in names if n.lower() not in files]
    if missing:
        sys.exit(f"no image for {', '.join('<' + m + '>' for m in missing)} in {folder}")
    refs = [os.path.join(folder, files[n.lower()]) for n in names]
    which = ", ".join(f"image {k + 1} shows the {n.replace('-', ' ')}" for k, n in enumerate(names))
    text = re.sub(r"<([\w-]+)>", lambda m: "the " + m.group(1).replace("-", " "), prompt)
    text = re.split(r"\bSounds?:", text)[0].strip()
    flux = (f"Reference images: {which}. They are character sheets: copy each character's exact "
            f"appearance, proportions, colours, costume and details from its sheet, but NOT the "
            f"sheet's neutral standing pose, framing or plain background. Never mix features "
            f"between characters. Draw the opening frame of this film shot: {text} "
            f"Cinematic film still, no text, no watermark, no borders.")
    # Flux draws ~0.9 MP at the video's shape; the video is then made at its usual size
    m = re.fullmatch(r"(\d+)x(\d+)", size or "768x512")
    w, h = (int(v) for v in m.groups()) if m else (768, 512)
    k = (1152 * 768 / (w * h)) ** 0.5
    fsize = f"{round(w * k / 16) * 16}x{round(h * k / 16) * 16}"
    print(f"== start frame: Flux with {', '.join('<' + n + '>' for n in names)} at {fsize}", flush=True)
    path = os.path.join(out, "start.png")
    open(path, "wb").write(lemon.edit_image(flux, refs, size=fsize, seed=seed)[0])
    for n, r in zip(names, refs):
        shutil.copy(r, os.path.join(out, f"ref-{n}{os.path.splitext(r)[1].lower()}"))
    print(f"  {path}")
    return path


def size_like(image, pixels):
    """WxH (multiples of 32) with the image's aspect ratio and about `pixels` pixels."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "csv=p=0", image], capture_output=True, text=True)
    try:
        w, h = (int(v) for v in r.stdout.strip().split(",")[:2])
    except ValueError:
        return "768x512"
    k = (pixels / (w * h)) ** 0.5
    return f"{max(256, round(w * k / 32) * 32)}x{max(256, round(h * k / 32) * 32)}"


def check_models():
    missing = [f for f in FILES.values() if not os.path.exists(os.path.join(MODELS, f))]
    if missing:
        raise RuntimeError(f"missing model files in {MODELS}: {', '.join(missing)}. "
                           "Download them with ./setup.sh --video (see README, 'Video').")


def frames_for(seconds, fps):
    """LTX wants 8k+1 frames."""
    return max(9, round(seconds * fps / 8) * 8 + 1)


def render(prompt, raw, W, H, frames, fps=24, steps=8, seed=42, image=None, end_image=None,
           audio=True, negative=NEGATIVE):
    """Run sd-cli once; writes `raw` (VP8 + PCM .webm) and returns seconds taken."""
    check_models()
    f = lambda k: os.path.join(MODELS, FILES[k])
    cmd = [find_sd_cli(), "-M", "vid_gen",
           "--diffusion-model", f("diffusion"), "--llm", f("llm"),
           "--embeddings-connectors", f("connectors"), "--vae", f("vae"),
           "-p", prompt, "-n", negative,
           "--cfg-scale", "1", "--sampling-method", "euler", "--steps", str(steps),
           "-W", str(W), "-H", str(H), "--video-frames", str(frames), "--fps", str(fps),
           "--seed", str(seed), "--diffusion-fa", "--vae-tiling", "-o", raw]
    if audio:
        cmd += ["--audio-vae", f("audio_vae")]
    if image:
        cmd += ["--init-img", image]
    if end_image:
        cmd += ["--end-img", end_image]
    make_room()
    t = time.time()
    log_path = os.path.splitext(raw)[0] + ".log"
    with open(log_path, "w") as log:
        r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    if r.returncode != 0 or not os.path.exists(raw):
        tail = open(log_path, errors="replace").read()[-2500:]
        raise RuntimeError(f"sd-cli failed (exit {r.returncode}). Last lines of {log_path}:\n{tail}")
    return time.time() - t


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", help="what happens, including the sounds you want to hear")
    ap.add_argument("--name", help="output name (default: from the prompt)")
    ap.add_argument("--image", help="start frame (image-to-video)")
    ap.add_argument("--end-image", help="end frame (needs --image)")
    ap.add_argument("--seconds", type=float, default=5, help="length (default 5)")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--size", help="WxH, multiples of 32 (default 768x512, or the start image's "
                                   "shape at about the same pixel count)")
    ap.add_argument("--steps", type=int, default=8, help="sampling steps (distilled model: 8)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--negative", default=NEGATIVE)
    ap.add_argument("--refs", help="folder of character images (knight.png, ...) for <knight>-style "
                    "placeholders in the prompt: Flux draws the start frame with those characters")
    ap.add_argument("--no-audio", action="store_true", help="silent video (a bit faster)")
    ap.add_argument("--draft", action="store_true", help="quick preview: 512x320, 2 seconds")
    a = ap.parse_args()
    if a.end_image and not a.image:
        ap.error("--end-image needs --image")
    names = list(dict.fromkeys(re.findall(r"<([\w-]+)>", a.prompt)))
    plain = re.sub(r"<([\w-]+)>", lambda m: m.group(1).replace("-", " "), a.prompt)
    name = a.name or re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:40]
    out = os.path.join(ROOT, "out", "video", name)
    os.makedirs(out, exist_ok=True)
    if a.refs:
        if a.image:
            ap.error("--refs draws the start frame, so it can't be used with --image")
        if not names:
            ap.error("--refs needs placeholders in the prompt, e.g. \"the <knight> draws his sword\"")
        a.image = draw_start_frame(a.prompt, names, a.refs, out, a.size, a.seed)
    if a.image and not a.size:
        a.size = size_like(a.image, 768 * 512)
    if a.draft:
        a.size, a.seconds = size_like(a.image, 512 * 320) if a.image else "512x320", min(a.seconds, 2)
    a.size = a.size or "768x512"

    m = re.fullmatch(r"(\d+)x(\d+)", a.size)
    if not m:
        ap.error("--size must look like 768x512")
    W, H = (int(v) // 32 * 32 for v in m.groups())
    frames = frames_for(a.seconds, a.fps)

    raw = os.path.join(out, f"{name}-raw.webm")
    print(f"== video: {W}x{H}, {frames} frames ({frames / a.fps:.1f} s at {a.fps} fps), "
          f"{a.steps} steps, {'no audio' if a.no_audio else 'with audio'}")
    secs = render(plain, raw, W, H, frames, fps=a.fps, steps=a.steps, seed=a.seed,
                  image=a.image, end_image=a.end_image, audio=not a.no_audio, negative=a.negative)
    print(f"  rendered in {secs / 60:.1f} min")

    has_audio = "audio" in subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", raw],
        capture_output=True, text=True).stdout
    made = make_copies(raw, out, name, W * H) if shutil.which("ffmpeg") else [raw]
    json.dump({"prompt": a.prompt, "refs": names if a.refs else [], "negative": a.negative, "image": a.image, "end_image": a.end_image,
               "size": [W, H], "frames": frames, "fps": a.fps, "steps": a.steps, "seed": a.seed,
               "audio": not a.no_audio, "render_seconds": round(secs)},
              open(os.path.join(out, "prompt.json"), "w"), indent=1)
    if not a.no_audio and not has_audio:
        print("  warning: the video came out without a sound track")
    for line in made:
        print(f"  {line}")

if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(f"\nerror: {e}")
