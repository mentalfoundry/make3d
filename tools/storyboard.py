#!/usr/bin/env python3
"""Storyboard -> keyframes every N seconds -> long video stitched from LTX segments.

  1. new:     the chat model writes a shot list (one keyframe every --every seconds) with
              one fixed description of the characters and setting. Flux draws keyframe 0,
              then every other keyframe using keyframe 0 as its reference so the characters
              stay consistent. Makes a contact sheet and stops so you can check them.
  2. frames:  redraw some or all keyframes, after editing storyboard.json or for a new take.
  3. render:  each segment is rendered by LTX-2.3 from its start keyframe to its end
              keyframe, with action and sound prompts; the segments are joined into one
              video, optionally with a music bed (ACE-Step).

Examples:
  storyboard new knight-dragon "a knight fights a dragon on a cliff at sunset" --seconds 30
  storyboard new duel "a <knight> fights a <dragon> in a ruined castle" --refs ~/chars
  storyboard frames knight-dragon --only 3,4          # redraw keyframes 3 and 4 (new seed)
  storyboard render knight-dragon --draft             # quick low-res pass to check the motion
  storyboard render knight-dragon --music "epic orchestral battle music, drums, brass"
  storyboard render knight-dragon --only 2            # re-render segment 2 with a new seed

Edit ~/3d/out/story/<name>/storyboard.json to change any keyframe, action or sound text;
render only re-renders segments whose keyframes or text changed.
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
import lemon  # noqa: E402
import makevideo  # noqa: E402

FRAME_SIZE = "1152x768"   # Flux keyframes (3:2); video is rendered at the same shape
PLAN = """You are a film storyboard artist. Plan a short continuous video of this idea:

"{idea}"

It lasts {seconds} seconds. Draw a keyframe every {every} seconds, at t = {times}.
Reply with JSON only, no other text, in exactly this shape:
{{
 "title": "short title",
 "style": "one sentence: visual style, lighting, colour palette, camera lens (same for every frame)",
 "characters": "fixed, detailed visual description of every character: species, build, colours, clothing, armour, distinguishing features. Written once, used for every frame, so be specific.",
 "setting": "fixed description of the location, time of day and weather",
 "keyframes": [{{"t": 0, "image": "what this exact moment shows: where each character is, their pose and action, camera angle and framing"}}],
 "segments": [{{"from": 0, "to": {every}, "action": "the motion that happens between these two keyframes, as one continuous shot", "sound": "what we hear: effects, voices, ambience"}}]
}}
Rules:
- exactly {n_frames} keyframes and {n_segments} segments, in order;
- the story must progress with a clear beginning, escalation and ending;
- each keyframe must follow physically from the previous one {every} seconds earlier: no
  teleporting, keep the same characters, location and camera side throughout;
- describe characters the same way every time; nothing on screen is text.{fixed}"""


def log(msg):
    print(f"\n== {msg}", flush=True)


def story_dir(name):
    return os.path.join(ROOT, "out", "story", name)


def load(name):
    p = os.path.join(story_dir(name), "storyboard.json")
    if not os.path.exists(p):
        sys.exit(f"no storyboard called '{name}' ({p}). Start one with: storyboard new {name} \"idea\"")
    return json.load(open(p))


def save(name, sb):
    json.dump(sb, open(os.path.join(story_dir(name), "storyboard.json"), "w"), indent=1)


def frame_path(name, i):
    return os.path.join(story_dir(name), f"key-{i:02d}.png")


DESCRIBE = ("Describe this character's appearance for an artist in two or three dense sentences: "
            "species, build, skin or scale colour, face, hair, every piece of clothing and armour "
            "with colours and materials, and distinguishing features. Ignore the pose, the "
            "background and the art style. Reply with the description only.")


def ref_paths(name, sb):
    return [os.path.join(story_dir(name), r["image"]) for r in sb.get("refs", [])]


def frame_prompt(sb, i):
    """Keyframe 0 is drawn from text (plus the character reference images, if any). Later
    keyframes use keyframe 0 as a reference for appearance only: with the full previous
    frame as a reference, Flux copied its layout and poses so the story never moved."""
    kf = sb["keyframes"][i]
    refs = sb.get("refs", [])
    look = (f"Characters: {sb['characters']} Setting: {sb['setting']} Style: {sb['style']} "
            f"Cinematic film still, no text, no watermark, no borders.")
    intro = ""
    if refs:
        which = ", ".join(f"image {k + 1} shows the {r['name']}" for k, r in enumerate(refs))
        intro = (f"Reference images: {which}. They are character sheets: copy each character's "
                 f"exact appearance, proportions, colours, costume and details from its sheet, but "
                 f"NOT the sheet's neutral standing pose, framing or plain background. Never mix "
                 f"features between characters: each keeps its own head, face, skin and body from "
                 f"its own sheet. ")
        if i > 0:
            intro += (f"Image {len(refs) + 1} is an earlier shot of this scene: use it only for "
                      f"the location, lighting and style. ")
    if i == 0:
        return f"{intro}{kf['image']} {look}"
    return (f"{intro}A new shot from the same film: {kf['image']} Do NOT copy the composition of "
            f"any reference image: the camera angle, framing, distance, poses and positions must "
            f"change to match this description. Each character appears exactly once. {look}")


def draw_frames(name, sb, only=None, seed=None):
    seed = sb.get("seed", 42) if seed is None else seed
    refs = ref_paths(name, sb)
    for i in range(len(sb["keyframes"])):
        if only and i not in only:
            continue
        t = time.time()
        if i == 0:
            if refs:
                png = lemon.edit_image(frame_prompt(sb, 0), refs, size=FRAME_SIZE, seed=seed)[0]
            else:
                png = lemon.generate_image(frame_prompt(sb, 0), size=FRAME_SIZE, seed=seed)[0]
        else:
            if not os.path.exists(frame_path(name, 0)):
                raise RuntimeError("keyframe 0 is needed first (it's the reference for the others)")
            png = lemon.edit_image(frame_prompt(sb, i), refs + [frame_path(name, 0)],
                                   size=FRAME_SIZE, seed=seed + i)[0]
        open(frame_path(name, i), "wb").write(png)
        print(f"  key-{i:02d}.png  t={sb['keyframes'][i]['t']}s  ({time.time() - t:.0f}s)")
    contact_sheet(name, sb)


def load_refs(idea, folder, out):
    """<name> placeholders in the idea -> character reference images from `folder`."""
    names = list(dict.fromkeys(re.findall(r"<([\w-]+)>", idea)))
    if not names:
        raise RuntimeError("--refs needs placeholders in the idea, e.g. \"a <knight> fights a <dragon>\"")
    files = {os.path.splitext(f)[0].lower(): f for f in os.listdir(folder)
             if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))}
    missing = [n for n in names if n.lower() not in files]
    if missing:
        raise RuntimeError(f"no image for {', '.join('<' + m + '>' for m in missing)} in {folder} "
                           f"(found: {', '.join(sorted(files)) or 'no images'})")
    unused = sorted(set(files) - {n.lower() for n in names})
    if unused:
        print(f"  not in the idea, ignored: {', '.join(unused)}")
    os.makedirs(os.path.join(out, "refs"), exist_ok=True)
    refs = []
    for n in names:
        src = os.path.join(folder, files[n.lower()])
        rel = os.path.join("refs", f"{n}{os.path.splitext(src)[1].lower()}")
        shutil.copy(src, os.path.join(out, rel))
        desc = lemon.describe_image(src, DESCRIBE).strip()
        print(f"  <{n}>: {desc}")
        refs.append({"name": n, "image": rel, "description": desc})
    return refs


def contact_sheet(name, sb):
    paths = [frame_path(name, i) for i in range(len(sb["keyframes"]))]
    if not all(os.path.exists(p) for p in paths):
        return
    labels = [f"{i}: t={kf['t']}s" for i, kf in enumerate(sb["keyframes"])]
    script = """
import sys
from PIL import Image, ImageDraw
out, cols, items = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
ims = [Image.open(p).convert('RGB').resize((480, 320)) for p in items[0::2]]
rows = (len(ims) + cols - 1) // cols
s = Image.new('RGB', (cols * 490 + 10, rows * 360 + 10), 'white')
d = ImageDraw.Draw(s)
for k, (im, lab) in enumerate(zip(ims, items[1::2])):
    x, y = 10 + (k % cols) * 490, 10 + (k // cols) * 360
    s.paste(im, (x, y + 30))
    d.text((x, y), lab, fill='black', font_size=22)
s.save(out)
"""
    args = [os.path.join(ROOT, "venv", "bin", "python"), "-I", "-c", script,
            os.path.join(story_dir(name), "storyboard.png"), str(min(4, len(paths)))]
    for p, l in zip(paths, labels):
        args += [p, l]
    subprocess.run(args, check=True)
    print(f"  sheet: {story_dir(name)}/storyboard.png")


def segment_prompt(sb, k):
    seg = sb["segments"][k]
    sound = f" Sound: {seg['sound']}" if seg.get("sound") else ""
    return f"{seg['action']}{sound} Characters: {sb['characters']} {sb['style']}"


def cmd_new(a):
    out = story_dir(a.name)
    if os.path.exists(os.path.join(out, "storyboard.json")) and not a.force:
        sys.exit(f"'{a.name}' already exists. Use: storyboard frames/render {a.name}, or --force to start over")
    os.makedirs(out, exist_ok=True)
    n = int(round(a.seconds / a.every))
    times = [i * a.every for i in range(n + 1)]
    refs, idea, fixed = [], a.idea, ""
    if a.refs:
        log(f"characters: reading reference images in {a.refs}")
        refs = load_refs(a.idea, a.refs, out)
        idea = re.sub(r"<([\w-]+)>", r"the \1", a.idea)
        fixed = ("\n- these characters are given and must not change; use these descriptions, "
                 "and refer to each one by its name:\n" +
                 "\n".join(f"  the {r['name']}: {r['description']}" for r in refs))
    log(f"plan: {n} segments of {a.every:g} s, keyframes at {', '.join(f'{t:g}' for t in times)} s")
    sb = lemon.chat_json(PLAN.format(idea=idea, seconds=f"{n * a.every:g}", every=f"{a.every:g}",
                                     times=", ".join(f"{t:g}" for t in times),
                                     n_frames=n + 1, n_segments=n, fixed=fixed))
    if refs:
        sb["refs"] = refs
        sb["characters"] = " ".join(f"The {r['name']}: {r['description']}" for r in refs)
    if a.style:
        sb["style"] = a.style
    kfs, segs = sb.get("keyframes", []), sb.get("segments", [])
    if len(kfs) != n + 1 or len(segs) != n:
        raise RuntimeError(f"the plan has {len(kfs)} keyframes and {len(segs)} segments, "
                           f"expected {n + 1} and {n}; run again")
    for i, kf in enumerate(kfs):
        kf["t"] = times[i]
    for k, seg in enumerate(segs):
        seg["from"], seg["to"] = times[k], times[k + 1]
    sb.update({"idea": a.idea, "every": a.every, "seed": a.seed})
    save(a.name, sb)
    print(f"  {sb.get('title', '')}\n  characters: {sb['characters']}\n  setting: {sb['setting']}")
    for kf in kfs:
        print(f"  t={kf['t']:>4g}s  {kf['image']}")
    log(f"keyframes: drawing {n + 1} with Flux (~40-70 s each)")
    draw_frames(a.name, sb)
    print(f"\nCheck the sheet, edit {out}/storyboard.json if needed, then:\n"
          f"  storyboard frames {a.name} --only N     # redraw a keyframe\n"
          f"  storyboard render {a.name} --draft      # quick motion check\n"
          f"  storyboard render {a.name}              # full quality "
          f"(~{n * 6} min on the development laptop)")


def cmd_frames(a):
    sb = load(a.name)
    only = {int(v) for v in a.only.split(",")} if a.only else None
    seed = a.seed if a.seed is not None else (sb.get("seed", 42) + int(time.time()) % 1000 if only else None)
    log(f"keyframes: drawing {'all' if not only else sorted(only)}")
    draw_frames(a.name, sb, only=only, seed=seed)
    if only and 0 in only and len(only) < len(sb["keyframes"]):
        print("  note: keyframe 0 is the reference for all the others; "
              "redraw them too if they no longer match it")


def ffmpeg(*args):
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-800:]}")


def cmd_render(a):
    sb = load(a.name)
    out = story_dir(a.name)
    n = len(sb["segments"])
    for i in range(n + 1):
        if not os.path.exists(frame_path(a.name, i)):
            sys.exit(f"keyframe {i} is missing: storyboard frames {a.name}")
    fps, every = a.fps, float(sb["every"])
    pixels = 512 * 320 if a.draft else 768 * 512
    W, H = (int(v) for v in makevideo.size_like(frame_path(a.name, 0), pixels).split("x"))
    frames = makevideo.frames_for(every, fps)
    tag = "draft" if a.draft else "full"
    seg_dir = os.path.join(out, f"segments-{tag}")
    os.makedirs(seg_dir, exist_ok=True)
    only = {int(v) for v in a.only.split(",")} if a.only else set()

    log(f"render ({tag}): {n} segments, {W}x{H}, {frames} frames each")
    t0 = time.time()
    for k in range(n):
        start, end = frame_path(a.name, k), frame_path(a.name, k + 1)
        prompt = segment_prompt(sb, k)
        seed = sb.get("seed", 42) + k + (int(time.time()) % 1000 if k in only else 0)
        key = hashlib.sha256((prompt + f"{W}x{H}{frames}").encode()
                             + open(start, "rb").read() + open(end, "rb").read()).hexdigest()
        raw = os.path.join(seg_dir, f"seg-{k:02d}.webm")
        stamp = raw + ".key"
        if (k not in only and os.path.exists(raw) and os.path.exists(stamp)
                and open(stamp).read() == key):
            print(f"  segment {k} ({sb['segments'][k]['from']:g}-{sb['segments'][k]['to']:g}s): unchanged")
            continue
        print(f"  segment {k} ({sb['segments'][k]['from']:g}-{sb['segments'][k]['to']:g}s): "
              f"rendering...", end="", flush=True)
        secs = makevideo.render(prompt, raw, W, H, frames, fps=fps, seed=seed, image=start, end_image=end)
        open(stamp, "w").write(key)
        print(f" {secs / 60:.1f} min")

    # Join: each segment ends on the frame the next one starts with, so trim every segment
    # to exactly `every` seconds, fade the audio 30 ms at the joins to avoid clicks.
    log("joining segments")
    inputs, chains, labels = [], [], ""
    for k in range(n):
        inputs += ["-i", os.path.join(seg_dir, f"seg-{k:02d}.webm")]
        d = every if k < n - 1 else frames / fps
        chains.append(f"[{k}:v]trim=duration={d},setpts=PTS-STARTPTS[v{k}];"
                      f"[{k}:a]atrim=duration={d},asetpts=PTS-STARTPTS,"
                      f"afade=t=in:d=0.03,afade=t=out:st={d - 0.03:.3f}:d=0.03[a{k}]")
        labels += f"[v{k}][a{k}]"
    total = every * (n - 1) + frames / fps
    graph = ";".join(chains) + f";{labels}concat=n={n}:v=1:a=1[v][a]"
    amap = "[a]"
    if a.music:
        log(f"music bed: ACE-Step, {total:.0f} s")
        music = os.path.join(out, "music.wav")
        open(music, "wb").write(lemon.generate_audio(a.music, lemon.MUSIC_MODEL, duration=round(total) + 1,
                                                     seed=sb.get("seed", 42)))
        inputs += ["-i", music]
        vol = 10 ** (-a.music_db / 20)
        graph += (f";[{n}:a]atrim=duration={total:.3f},volume={vol:.3f},"
                  f"afade=t=out:st={max(0, total - 2):.3f}:d=2[m];[a][m]amix=inputs=2:duration=first:normalize=0[am]")
        amap = "[am]"
    name = f"{a.name}-{tag}"
    joined = os.path.join(out, f"{name}-raw.mkv")   # WebM can't hold PCM audio
    ffmpeg(*inputs, "-filter_complex", graph, "-map", "[v]", "-map", amap,
           "-c:v", "libvpx", "-b:v", "8M", "-crf", "6", "-c:a", "pcm_s16le", joined)
    made = makevideo.make_copies(joined, out, name, W * H)
    log(f"done: {total:.0f} s of video in {(time.time() - t0) / 60:.1f} min")
    for line in made:
        print(f"  {line}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new", help="plan the shots and draw the keyframes")
    p.add_argument("name")
    p.add_argument("idea", help="what happens, e.g. 'a knight fights a dragon on a cliff at sunset'; "
                                "with --refs, name characters as <knight>, <dragon>")
    p.add_argument("--refs", help="folder of character images (knight.png, dragon.png, ...) for the "
                                  "<name> placeholders in the idea; A/T-pose sheets work well")
    p.add_argument("--seconds", type=float, default=30, help="total length (default 30)")
    p.add_argument("--every", type=float, default=5, help="seconds between keyframes (default 5)")
    p.add_argument("--style", help="override the visual style, e.g. 'hand-painted fantasy art, soft light'")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--force", action="store_true", help="replace an existing storyboard")
    p = sub.add_parser("frames", help="redraw keyframes")
    p.add_argument("name")
    p.add_argument("--only", help="comma-separated keyframe numbers, e.g. 3,4 (default: all)")
    p.add_argument("--seed", type=int, help="seed (default: a new one when using --only)")
    p = sub.add_parser("render", help="render the segments and join them")
    p.add_argument("name")
    p.add_argument("--draft", action="store_true", help="low resolution, ~4x faster, to check the motion")
    p.add_argument("--only", help="re-render these segments with a new seed, e.g. 2,5")
    p.add_argument("--music", help="add a music bed: style text for ACE-Step")
    p.add_argument("--music-db", type=float, default=14, help="how far below the scene sound the music sits (dB, default 14)")
    p.add_argument("--fps", type=int, default=24)
    a = ap.parse_args()
    {"new": cmd_new, "frames": cmd_frames, "render": cmd_render}[a.cmd](a)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(f"\nerror: {e}")
