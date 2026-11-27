#!/usr/bin/env python3
"""Storyboard -> long video with sound.

Default (shots mode, see shots.py): the chat model plans a shot list (2-6 s shots of mixed
sizes, one action each, cut around what video models do badly); Flux draws each shot's first
frame; an animatic shows the pacing; render makes each shot on its own from its first frame
(takes ranked by the vision model) and joins them with hard cuts over one ambience track.

--continuous (the older mode): one take with a keyframe every N seconds:
  1. new:     the chat model writes a shot list (one keyframe every --every seconds, each
              caught mid-movement) with one fixed description of the characters and
              setting. Flux draws keyframe 0, then the others using keyframe 0 as an
              appearance reference. The vision model checks each new keyframe against the
              previous one (moved enough? characters fused or doubled?) and redraws weak
              ones. Makes a contact sheet and stops so you can check them.
  2. frames:  redraw some or all keyframes, after editing storyboard.json or for a new take.
  3. render:  continuous by default: segment 0 runs keyframe 0 -> 1, every later segment
              continues the previous one's last frames and is guided to its next keyframe
              (needs a DGL-capable sd-cli, see ltx_dgl.py); --classic pins each segment
              to both keyframes. Segments are cross-faded together, optionally with a
              music bed (ACE-Step).

Examples:
  storyboard new gpu "a <devman> is handed a GPU by a <techman> from a limo" --refs ~/chars
  storyboard render gpu --draft                       # one take per shot, low res
  storyboard render gpu --takes 3                     # full quality, best of three per shot
  storyboard pick gpu 4 2                             # use take 2 of shot 4 instead
  storyboard new knight-dragon "a knight fights a dragon on a cliff at sunset" --seconds 30
  storyboard new duel "a <knight> fights a <dragon> in a ruined castle" --refs ~/chars
  storyboard frames duel --only 2 --note "camera lower, the dragon on the right"
  storyboard frames duel --only 2 --adjust --note "add sparks where the sword hits"
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
import ltx_dgl  # noqa: E402
import shots  # noqa: E402

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
 "keyframes": [{{"t": 0, "image": "this exact moment, caught MID-MOVEMENT: where each character is, their pose part-way through an action (mid-swing, mid-leap, wings mid-beat, cape and dust flying), camera angle and distance", "change": "what is visibly different from the previous keyframe (empty for the first)"}}],
 "segments": [{{"from": 0, "to": {every}, "action": "the motion between these two keyframes as one continuous shot, already in motion at the start and still moving at the end", "sound": "what we hear: effects, voices, ambience"}}]
}}
Rules:
- exactly {n_frames} keyframes and {n_segments} segments, in order;
- the story must progress with a clear beginning, escalation and ending;
- each keyframe must follow physically from the previous one {every} seconds earlier: no
  teleporting, keep the same characters, location and camera side throughout;
- every keyframe must differ clearly from the previous one in AT LEAST TWO of: where the
  characters are, their poses, and the camera angle or distance. Never repeat a pose;
- catch every keyframe in the middle of a movement, never a still or resting pose
  (not "stands", "holds", "waits", "poses"); a video model animates between keyframes and
  lingers on frames that look static;
- segment actions describe continuous motion that is already happening when the segment
  starts and carries on past its end; keep the camera moving smoothly too;
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


CHECK = """Image 1 is a film keyframe. Image 2 is the next keyframe, {every} seconds later.
Image 2 was supposed to show: "{image}"
Reply with JSON only:
{{"moved": 0-10, "matches": 0-10, "severe": "..."}}
moved: how much changed between image 1 and image 2 in the characters' positions, their poses
  and the camera angle or distance (0 = the same picture, 3 = small pose change, 6 = clearly
  different moment, 10 = completely different shot).
matches: how well image 2 shows what it was supposed to show.
severe: ONLY these defects in image 2, otherwise "": two characters fused into one body or
  sharing a head; one character with another character's head; a character appearing twice;
  a character missing entirely. Ignore small anatomy, prop or detail mistakes."""

MIN_MOVED = 5


def check_frame(name, sb, i):
    """Score keyframe i against keyframe i-1 with the vision model. Returns (score, note, ok).
    Passes when it moved enough and has no severe defect; `matches` only ranks attempts."""
    kf = sb["keyframes"][i]
    try:
        txt = lemon.describe_image([frame_path(name, i - 1), frame_path(name, i)],
                                   CHECK.format(every=f"{sb['every']:g}", image=kf["image"]),
                                   max_tokens=300)
        r = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
        moved, match = float(r.get("moved", 0)), float(r.get("matches", 0))
        severe = str(r.get("severe", "")).strip().strip(".")
    except Exception as e:
        return 0, f"check failed ({str(e)[:80]})", True   # don't block drawing on the checker
    bad = severe and severe.lower() not in ("none", "no", "n/a", "false")
    ok = moved >= MIN_MOVED and not bad
    note = f"moved {moved:g}/10, matches {match:g}/10" + (f", {severe}" if bad else "")
    return moved + 0.5 * match - (20 if bad else 0), note, ok


def prev_path(name, i):
    return os.path.join(story_dir(name), f"key-{i:02d}-prev.png")


def adjust_prompt(sb, i, n_refs, note):
    """Edit the current keyframe (the last reference image) as the note says."""
    kf, refs = sb["keyframes"][i], sb.get("refs", [])
    intro = ""
    if refs:
        which = ", ".join(f"image {k + 1} shows the {r['name']}" for k, r in enumerate(refs))
        intro = (f"Reference images: {which}: keep each character's exact appearance from its "
                 f"sheet. ")
    return (f"{intro}Image {n_refs + 1} is the current version of a film keyframe showing: "
            f"{kf['image']} Keep its composition, characters, setting, lighting and style, and "
            f"change only this: {note} Style: {sb['style']} Cinematic film still, no text, "
            f"no watermark, no borders.")


def is_shots(sb):
    return sb.get("kind") == "shots"


def draw_one(name, sb, i, seed, note="", adjust=False):
    if is_shots(sb):
        return shots.draw(sb, i, story_dir(name), seed, note=note,
                          python=os.path.join(ROOT, "venv", "bin", "python"),
                          adjust_from=prev_path(name, i) if adjust else None)
    refs = ref_paths(name, sb)
    if adjust:   # edit the previous version of this keyframe
        return lemon.edit_image(adjust_prompt(sb, i, len(refs), note), refs + [prev_path(name, i)],
                                size=FRAME_SIZE, seed=seed)[0]
    prompt = frame_prompt(sb, i) + (f" Director's note for this drawing, follow it closely: {note}" if note else "")
    if i == 0:
        if refs:
            return lemon.edit_image(prompt, refs, size=FRAME_SIZE, seed=seed)[0]
        return lemon.generate_image(prompt, size=FRAME_SIZE, seed=seed)[0]
    if not os.path.exists(frame_path(name, 0)):
        raise RuntimeError("keyframe 0 is needed first (it's the reference for the others)")
    return lemon.edit_image(prompt, refs + [frame_path(name, 0)], size=FRAME_SIZE, seed=seed)[0]


def draw_frames(name, sb, only=None, seed=None, tries=3, note="", adjust=False):
    """Draw keyframes. Each one after the first is checked against the previous one by the
    vision model (did it move enough, does it match the plan, any merged/doubled characters)
    and redrawn with a new seed up to `tries` times; the best attempt is kept."""
    seed = sb.get("seed", 42) if seed is None else seed
    checks = sb.setdefault("checks", {})
    if is_shots(sb) and not adjust and shots.ensure_location(sb):   # older plans: add the set facts
        shots.normalise(sb)
        save(name, sb)
    for i in range(len(sb["keyframes"])):
        if only and i not in only:
            continue
        t, best = time.time(), None
        if only and os.path.exists(frame_path(name, i)):   # keep the old one for undo / adjust
            shutil.copy(frame_path(name, i), prev_path(name, i))
            sb.setdefault("prev_checks", {})[str(i)] = checks.get(str(i))
        if adjust and not os.path.exists(prev_path(name, i)):
            raise RuntimeError(f"keyframe {i} isn't drawn yet, so there is nothing to adjust")
        checked = tries > 1 and (i > 0 or is_shots(sb))
        for attempt in range(max(1, tries) if checked else 1):
            png = draw_one(name, sb, i, seed + i + 1000 * attempt, note=note, adjust=adjust)
            open(frame_path(name, i), "wb").write(png)
            if not checked:
                best = (0, "", True, png)
                break
            score, msg, ok = (shots.check(sb, i, frame_path(name, i), story_dir(name)) if is_shots(sb)
                              else check_frame(name, sb, i))
            if best is None or score > best[0]:
                best = (score, msg, ok, png)
            if ok:
                break
            print(f"  key-{i:02d}.png  try {attempt + 1}: {msg}; redrawing", flush=True)
        open(frame_path(name, i), "wb").write(best[3])
        checks[str(i)] = {"note": best[1], "ok": best[2]}
        flag = "" if best[2] else "  << WEAK: edit its text in storyboard.json and redraw"
        print(f"  key-{i:02d}.png  t={sb['keyframes'][i]['t']}s  ({time.time() - t:.0f}s)"
              + (f"  {best[1]}" if best[1] else "") + flag, flush=True)
    save(name, sb)
    contact_sheet(name, sb)
    if is_shots(sb):
        shots.animatic(sb, story_dir(name), name, lambda k: frame_path(name, k),
                       os.path.join(ROOT, "venv", "bin", "python"))


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
    checks = sb.get("checks", {})
    labels = [f"{i}: t={kf['t']}s" + (f" {kf['size']} {kf['seconds']:g}s" if is_shots(sb) else "") + ("  WEAK" if not checks.get(str(i), {}).get("ok", True) else "")
              for i, kf in enumerate(sb["keyframes"])]
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
    """Motion words up front: LTX otherwise tends to hold still near each keyframe."""
    seg = sb["segments"][k]
    sound = f" Sound: {seg['sound']}" if seg.get("sound") else ""
    return (f"Continuous fast-moving action, already in motion from the first frame and still "
            f"moving in the last frame, no pauses, smooth moving camera. {seg['action']}{sound} "
            f"Characters: {sb['characters']} {sb['style']}")


def cmd_new(a):
    out = story_dir(a.name)
    if os.path.exists(os.path.join(out, "storyboard.json")) and not a.force:
        sys.exit(f"'{a.name}' already exists. Use: storyboard frames/render {a.name}, or --force to start over")
    os.makedirs(out, exist_ok=True)
    if not a.continuous:
        return new_shots(a, out)
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
    draw_frames(a.name, sb, tries=a.tries)
    print(f"\nCheck the sheet, edit {out}/storyboard.json if needed, then:\n"
          f"  storyboard frames {a.name} --only N     # redraw a keyframe\n"
          f"  storyboard render {a.name} --draft      # quick motion check\n"
          f"  storyboard render {a.name}              # full quality "
          f"(~{n * 6} min on the development laptop)")


def new_shots(a, out):
    refs, idea = [], a.idea
    if a.refs:
        log(f"characters: reading reference images in {a.refs}")
        refs = load_refs(a.idea, a.refs, out)
        idea = re.sub(r"<([\w-]+)>", r"\1", a.idea)
    log(f"plan: shot list for about {a.seconds:g} s")
    sb = shots.plan(idea, a.seconds, refs, style=a.style)
    if refs:
        sb["refs"] = refs
    sb.update({"idea": a.idea, "seed": a.seed, "every": 0})
    save(a.name, sb)
    total = sum(s["seconds"] for s in sb["keyframes"])
    print(f"  {sb.get('title', '')}  ({len(sb['keyframes'])} shots, {total:g} s)")
    for c in sb["cast"]:
        print(f"  cast: {c['name']} = {c['tag']}; voice: {c['voice']}")
    print(f"  setting: {sb['setting']}\n  ambience: {sb['ambience']}")
    for i, s in enumerate(sb["keyframes"]):
        line = f'  "{s["line"]["text"]}" ({s["line"].get("speaker")})' if s.get("line") else ""
        print(f"  {i:>2} {s['t']:>5g}s {s['seconds']:>3g}s  {s['size']:<17} {s['camera']:<18} "
              f"[{', '.join(s['who'])}] {s['action']}{line}")
    log(f"first frames: drawing {len(sb['keyframes'])} with Flux (~40-70 s each, more if redrawn)")
    draw_frames(a.name, sb, tries=a.tries)
    print(f"\nWatch {out}/{a.name}-animatic.webm for pacing, edit storyboard.json if needed, then:\n"
          f"  storyboard frames {a.name} --only N     # redraw a shot's first frame\n"
          f"  storyboard render {a.name} --draft      # quick check, one take per shot\n"
          f"  storyboard render {a.name}              # full quality, two takes per shot, best kept")


def cmd_pick(a):
    sb = load(a.name)
    if not is_shots(sb):
        sys.exit("takes only exist in shots mode")
    tag = "draft" if a.draft else "full"
    if not 0 <= a.shot < len(sb["keyframes"]):
        sys.exit(f"there is no shot {a.shot}")
    if not os.path.exists(os.path.join(story_dir(a.name), f"shots-{tag}", f"shot-{a.shot:02d}-t{a.take - 1}.webm")):
        sys.exit(f"shot {a.shot} has no {tag} take {a.take}")
    sb.setdefault("takes", {}).setdefault(tag, {}).setdefault(str(a.shot), {}).update(take=a.take - 1, by="you")
    save(a.name, sb)
    print(f"shot {a.shot}: using take {a.take}. Run: storyboard render {a.name}{' --draft' if a.draft else ''} (only re-joins)")


def cmd_frames(a):
    sb = load(a.name)
    only = {int(v) for v in a.only.split(",")} if a.only else None
    seed = a.seed if a.seed is not None else (sb.get("seed", 42) + int(time.time()) % 1000 if only else None)
    if a.adjust and not (only and a.note):
        sys.exit("--adjust needs --only and --note (what to change)")
    log(f"keyframes: {'adjusting' if a.adjust else 'drawing'} {'all' if not only else sorted(only)}"
        + (f"  (note: {a.note})" if a.note else ""))
    draw_frames(a.name, sb, only=only, seed=seed, tries=a.tries, note=a.note or "", adjust=a.adjust)
    if only and 0 in only and len(only) < len(sb["keyframes"]):
        print("  note: keyframe 0 is the reference for all the others; "
              "redraw them too if they no longer match it")


def ffmpeg(*args):
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-800:]}")


def cmd_render(a):
    """Render every segment and join them.

    continuous (default when a DGL-capable sd-cli is available): segment 0 runs from
    keyframe 0 to keyframe 1; every later segment *continues* the previous one, starting
    from its last 17 frames held fixed, and is guided to its next keyframe. Movement carries
    through the joins instead of pausing on each keyframe.
    classic (--classic): each segment is pinned to its start and end keyframes."""
    sb = load(a.name)
    out = story_dir(a.name)
    if is_shots(sb):
        return shots.render(a.name, out, lambda k: frame_path(a.name, k), load, save, a, log)
    n = len(sb["segments"])
    for i in range(n + 1):
        if not os.path.exists(frame_path(a.name, i)):
            sys.exit(f"keyframe {i} is missing: storyboard frames {a.name}")
    continuous = not a.classic
    if continuous:
        try:
            ltx_dgl.find_dgl_cli()
        except RuntimeError as e:
            print(f"  continuous mode unavailable ({e}); using classic segments")
            continuous = False
    fps, every = a.fps, float(sb["every"])
    pixels = 512 * 320 if a.draft else 768 * 512
    W, H = (int(v) for v in makevideo.size_like(frame_path(a.name, 0), pixels).split("x"))
    frames = makevideo.frames_for(every, fps)            # 121 for 5 s at 24 fps
    ctx_n = ltx_dgl.CONTEXT_FRAMES
    tag = ("draft" if a.draft else "full") + ("" if continuous else "-classic")
    seg_dir = os.path.join(out, f"segments-{tag}")
    os.makedirs(seg_dir, exist_ok=True)
    only = {int(v) for v in a.only.split(",")} if a.only else set()
    ext = "avi" if continuous else "webm"

    log(f"render ({tag}): {n} segments, {W}x{H}" +
        (f", each continuing the last {ctx_n} frames of the one before" if continuous else ""))
    t0, prev_key = time.time(), ""
    for k in range(n):
        start, end = frame_path(a.name, k), frame_path(a.name, k + 1)
        prompt = segment_prompt(sb, k)
        seed = sb.get("seed", 42) + k + (int(time.time()) % 1000 if k in only else 0)
        seg_frames = frames if (k == 0 or not continuous) else frames + ctx_n - 1
        key = hashlib.sha256((prompt + f"v2{W}x{H}{seg_frames}{a.end_strength}{continuous}{prev_key}").encode()
                             + open(start, "rb").read() + open(end, "rb").read()).hexdigest()
        raw = os.path.join(seg_dir, f"seg-{k:02d}.{ext}")
        stamp = raw + ".key"
        label = f"  segment {k} ({sb['segments'][k]['from']:g}-{sb['segments'][k]['to']:g}s)"
        if (k not in only and os.path.exists(raw) and os.path.exists(stamp)
                and open(stamp).read() == key):
            print(f"{label}: unchanged")
            prev_key = key
            continue
        print(f"{label}: rendering...", end="", flush=True)
        if not continuous:
            secs = makevideo.render(prompt, raw, W, H, frames, fps=fps, seed=seed, image=start, end_image=end)
        elif k == 0:
            secs = ltx_dgl.render(raw, prompt, W, H, frames, fps=fps, seed=seed, start_image=start,
                                  end_image=end, end_strength=a.end_strength)
        else:
            prev = os.path.join(seg_dir, f"seg-{k - 1:02d}.{ext}")
            ctx = ltx_dgl.last_frames_as_png(prev, ctx_n, os.path.join(seg_dir, "context"))
            secs = ltx_dgl.render(raw, prompt, W, H, seg_frames, fps=fps, seed=seed, context_pngs=ctx,
                                  end_image=end, end_strength=a.end_strength)
        open(stamp, "w").write(key)
        prev_key = key
        print(f" {secs / 60:.1f} min")

    # Join. Classic: each segment ends on the frame the next one starts with, so trim each to
    # exactly `every` seconds and cut. Continuous: each later segment's first ctx_n frames
    # re-create the end of the previous one; cross-fade over the last BLEND of those so the
    # join has no pop, and drop the rest. Audio is cross-faded the same way.
    log("joining segments")
    inputs, chains, total = [], [], 0.0
    BLEND = 8
    for k in range(n):
        inputs += ["-i", os.path.join(seg_dir, f"seg-{k:02d}.{ext}")]
        if continuous:
            skip = 0 if k == 0 else ctx_n - BLEND
            count = frames if k == 0 else frames + ctx_n - 1 - skip
        else:
            skip, count = 0, (round(every * fps) if k < n - 1 else frames)
        chains.append(f"[{k}:v]trim=start_frame={skip}:end_frame={skip + count},setpts=PTS-STARTPTS[v{k}];"
                      f"[{k}:a]atrim=start={skip / fps:.4f}:duration={count / fps:.4f},asetpts=PTS-STARTPTS[a{k}]")
    if continuous and n > 1:
        D = BLEND / fps
        vprev, aprev, length = "v0", "a0", frames / fps
        for k in range(1, n):
            seg_len = (frames + ctx_n - 1 - (ctx_n - BLEND)) / fps
            chains.append(f"[{vprev}][v{k}]xfade=transition=fade:duration={D:.4f}:offset={length - D:.4f}[vx{k}];"
                          f"[{aprev}][a{k}]acrossfade=d={D:.4f}:c1=tri:c2=tri[ax{k}]")
            vprev, aprev, length = f"vx{k}", f"ax{k}", length + seg_len - D
        graph = ";".join(chains) + f";[{vprev}]null[v];[{aprev}]anull[a]"
        total = length
    else:
        for k in range(n):
            chains[k] = chains[k].replace(f"asetpts=PTS-STARTPTS[a{k}]",
                                          f"asetpts=PTS-STARTPTS,afade=t=in:d=0.03[a{k}]")
        labels = "".join(f"[v{k}][a{k}]" for k in range(n))
        graph = ";".join(chains) + f";{labels}concat=n={n}:v=1:a=1[v][a]"
        total = sum((round(every * fps) if k < n - 1 else frames) for k in range(n)) / fps
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
    p.add_argument("--tries", type=int, default=3, help="redraws per keyframe if the check fails (1 = no check)")
    p.add_argument("--continuous", action="store_true",
                   help="plan one continuous take with a keyframe every --every seconds (the old mode) "
                        "instead of a shot list")
    p = sub.add_parser("frames", help="redraw keyframes")
    p.add_argument("name")
    p.add_argument("--only", help="comma-separated keyframe numbers, e.g. 3,4 (default: all)")
    p.add_argument("--seed", type=int, help="seed (default: a new one when using --only)")
    p.add_argument("--tries", type=int, default=3, help="redraws per keyframe if the check fails (1 = no check)")
    p.add_argument("--note", help="extra direction for this redraw, e.g. 'camera lower, the dragon on the right'")
    p.add_argument("--adjust", action="store_true",
                   help="edit the current keyframe as --note says instead of drawing a new one")
    p = sub.add_parser("render", help="render the segments and join them")
    p.add_argument("name")
    p.add_argument("--draft", action="store_true", help="low resolution, about 2.5x faster, to check the motion")
    p.add_argument("--only", help="re-render these segments with a new seed, e.g. 2,5")
    p.add_argument("--music", help="add a music bed: style text for ACE-Step")
    p.add_argument("--music-db", type=float, default=14, help="how far below the scene sound the music sits (dB, default 14)")
    p.add_argument("--fps", type=int, default=24)
    p.add_argument("--takes", type=int, help="shots mode: takes per shot, the vision model keeps the best "
                                             "(default: 1 for --draft, 2 for full)")
    p.add_argument("--no-ambience", action="store_true", help="shots mode: no background ambience track")
    p.add_argument("--ambience-db", type=float, default=18,
                   help="shots mode: how far below the shots' own sound the ambience sits (dB, default 18)")
    p.add_argument("--classic", action="store_true",
                   help="pin every segment to its start and end keyframes (old mode; no DGL needed)")
    p.add_argument("--end-strength", type=float, default=0.4,
                   help="continuous mode: how firmly each segment must land on its next keyframe "
                        "(1 = exactly, lower = looser, smoother; default 0.4. Above ~0.6 segments tend to rush to the keyframe and then hold still)")
    p = sub.add_parser("pick", help="shots mode: choose which take of a shot to use")
    p.add_argument("name")
    p.add_argument("shot", type=int)
    p.add_argument("take", type=int, help="take number, from 1")
    p.add_argument("--draft", action="store_true", help="pick among the draft takes")
    a = ap.parse_args()
    {"new": cmd_new, "frames": cmd_frames, "render": cmd_render, "pick": cmd_pick}[a.cmd](a)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(f"\nerror: {e}")
