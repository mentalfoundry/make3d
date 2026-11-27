"""Shots mode for storyboard: plan a film the way editors cut AI video.

Instead of one continuous take with a keyframe every N seconds (which drifts, holds on one
framing and has to animate everything the video model is bad at), the chat model writes a
shot list: 1-8 s shots sized to the story's beats, a mix of wides, mediums, close-ups,
inserts and reactions, one action and one camera move each, one speaker per dialogue shot.
Each shot gets its own start frame, is rendered on its own from that frame (no chaining, so
nothing drifts), in one or more takes that the vision model ranks, and the chosen takes are
joined with hard cuts over one ambience track for the whole scene.

storyboard.py calls into this module when storyboard.json has "kind": "shots". Each shot is
stored as an entry of "keyframes" (so drawing, redraw, undo and the portal work unchanged)
with extra fields: size, camera, who, action, line, sound, seconds.
"""
import hashlib, json, os, re, shutil, subprocess, time

import lemon
import makevideo

SIZES = ["wide", "medium", "close-up", "insert", "over-the-shoulder", "reaction"]
SIZE_WORDS = {   # how each size reads in an image or video prompt
    "wide": "Wide shot", "medium": "Medium shot", "close-up": "Close-up",
    "insert": "Close-up insert shot", "over-the-shoulder": "Over-the-shoulder shot",
    "reaction": "Close-up reaction shot",
}
FRAMING = {   # spelled out for Flux, which otherwise draws most shots full-length
    "wide": "full bodies, with plenty of the location around them",
    "medium": "framed from the waist up, the person fills most of the frame height; legs and feet are NOT visible",
    "close-up": "head and shoulders fill the frame; nothing below the chest is visible",
    "insert": "extreme close-up of the hands or object only, filling the frame; faces are not visible",
    "over-the-shoulder": "seen past the back of one person's shoulder and head, which fill one side of the frame in the foreground, out of focus; the other person faces the camera, framed from the waist up",
    "reaction": "close-up of one face reacting, head and shoulders fill the frame",
}
ANCHORS = {"center": (0.5, 0.5), "left": (0, 0.5), "right": (1, 0.5), "top": (0.5, 0), "bottom": (0.5, 1)}
MIN_SECONDS, MAX_SECONDS = 1.0, 8.0
WORDS_PER_SECOND = 2.5          # comfortable speech; a line needs words / 2.5 + ~1 s
TAIL = 0.25                     # rendered past the cut: the last frames are the weakest

PLAN = """You are a film director and editor. Turn this idea into a shot list for a short film.

"{idea}"

Target length: about {seconds} seconds.
Reply with JSON only, no other text, in exactly this shape:
{{
 "title": "short title",
 "style": "one sentence: visual style, lighting, colour palette, lens (same for every shot)",
 "cast": [{{"name": "the character's name as used below", "look": "fixed, detailed visual description: build, skin, face, hair, every piece of clothing with colours", "tag": "4-8 word visual label that tells them apart, e.g. 'the man in the navy suit and round glasses'", "voice": "how they sound, e.g. 'warm, calm male voice, mid-thirties'"}}],
 "setting": "fixed description of the location, time of day and weather",
 "location_facts": "the fixed physical facts of this place that must not change between shots: road layout and number of lanes, paving, what lines each side, where trees and parked vehicles are (or that there are none), landmarks; one or two sentences",
 "views": [{{"id": "short-id", "looking": "which way the camera looks and what part of the place it sees; describe only the place, never people"}}],
 "ambience": "the constant background sound of this place for the whole scene (room tone, traffic, wind); no music, no voices",
 "shots": [{{
   "size": "one of: wide, medium, close-up, insert, over-the-shoulder, reaction",
   "view": "the id of the view this shot's camera faces",
   "camera": "ONE camera move: static, slow push in, slow pull back, pan left, pan right, tilt up, tilt down, tracking alongside, or handheld",
   "who": ["names of the cast visible in this shot; [] for none"],
   "image": "the FIRST frame of this shot as a film still: framing, who is where (left/right), pose, expression, key props",
   "action": "what happens during the shot, in order, present tense: ONE simple action",
   "line": {{"speaker": "name", "text": "the exact words spoken"}},
   "sound": "sound effects heard in this shot (not the ambience, not music)",
   "seconds": 3
 }}]
}}
Rules (this is how good AI films are cut):
- about {lo}-{hi} shots adding up to about {seconds} seconds;
- give each story beat the time it needs and no more, and VARY the rhythm like an editor:
  an insert of hands or an object, or a quick glance or reaction: 1-2 s;
  an ordinary action or movement: 2.5-4 s;
  a spoken line: 1 s plus 0.4 s per word;
  a moment that has to land (an arrival, a bow, a final look, the last shot): 5-8 s;
  cut faster as tension builds, hold longer on the emotional beats and the ending;
  never three shots in a row of the same length; never more than 8 s;
- define 1-3 camera views of the location (camera DIRECTIONS, not shot sizes): the direction
  most shots face, plus the reverse angle if any shot looks the other way; give every shot
  the view its camera faces;
- shot 1 is a WIDE establishing shot showing the location and where everyone is; use another
  wide when people move to a new place;
- mix shot sizes; never two shots in a row with the same size and the same people;
- video models are bad at hands passing objects, people touching, doors opening, getting in
  or out of vehicles, and crowds. Cut around them: show such a moment as a wider lead-in shot,
  then a close-up INSERT of the hands or object, then a REACTION, instead of one long shot;
- people inside vehicles are hard to draw: show the car arriving with tinted windows and nobody
  visible, show the person once they lean out of or step out of the open door, and show doors
  closing as an INSERT of the door, not of the people;
- at most two people in a shot, one where you can. Two-person shots are wide or
  over-the-shoulder. Extras (crowds, passengers) only in wide shots;
- dialogue: "line" is null unless someone speaks. One speaker per shot, framed medium or
  close-up on the speaker, at most 15 words; split longer speeches over several shots and cut
  to the listener's reaction between them;
- one action and one camera move per shot; describe movement, not states;
- keep screen direction: whoever is on the left stays on the left (180-degree rule);
- follow the idea exactly: keep every event it has, in order, and add no new ones;
- nothing on screen is text; names never appear as text.{fixed}"""


def slug_names(sb):
    return {c["name"]: c for c in sb.get("cast", [])}


def normalise(sb):
    """Clamp shot fields, make room for dialogue, and set each shot's start time t."""
    names = slug_names(sb)
    t = 0.0
    for s in sb["keyframes"]:
        s["size"] = s.get("size") if s.get("size") in SIZES else "medium"
        s["camera"] = (s.get("camera") or "static").strip()
        s["who"] = [w for w in (s.get("who") or []) if w in names]
        s["view"] = view_of(sb, s)
        try:
            s["punch"] = min(1.5, max(1.0, float(s.get("punch") or 1.0)))
        except (TypeError, ValueError):
            s["punch"] = 1.0
        s["anchor"] = s.get("anchor") if s.get("anchor") in ANCHORS else "center"
        s["push"] = bool(s.get("push"))
        line = s.get("line")
        if not (isinstance(line, dict) and str(line.get("text", "")).strip()):
            s["line"] = None
        try:
            secs = float(s.get("seconds") or 3)
        except (TypeError, ValueError):
            secs = 3.0
        if s["line"]:
            words = len(str(s["line"]["text"]).split())
            secs = max(secs, 1 + words / WORDS_PER_SECOND)
        s["seconds"] = round(min(MAX_SECONDS, max(MIN_SECONDS, secs)) * 2) / 2
        s["t"] = round(t, 2)
        s.setdefault("sound", "")
        t += s["seconds"]
    return t


def plan(idea, seconds, refs, style=None):
    """Ask the chat model for a shot list. refs: [{name, description}] for given characters."""
    fixed = ""
    if refs:
        fixed = ("\n- these characters are given; use exactly these names and looks (you still "
                 "write their tag and voice):\n" +
                 "\n".join(f"  {r['name']}: {r['description']}" for r in refs))
    lo, hi = max(2, round(seconds / 5)), max(3, round(seconds / 2.5))
    prompt = PLAN.format(idea=idea, seconds=f"{seconds:g}", lo=lo, hi=hi, fixed=fixed)
    raw = lemon.chat_json(prompt, max_tokens=8000)
    # Lines quoted in the idea must be spoken in the film; the planner sometimes drops them
    norm = lambda t: re.sub(r"[^a-z0-9 ]", "", t.lower()).split()
    for attempt in range(2):
        spoken = " ".join(" ".join(norm(str((s.get("line") or {}).get("text", ""))))
                          for s in raw.get("shots") or [] if isinstance(s.get("line"), dict))
        missing = [q for q in re.findall(r'"([^"]{3,})"', idea) if " ".join(norm(q)) not in spoken]
        if not missing:
            break
        print(f"  the plan left out {', '.join(repr(m) for m in missing)}; asking again", flush=True)
        raw = lemon.chat_json(prompt + "\n- IMPORTANT: your previous plan left out these spoken lines; "
                              "each must be a shot's \"line\", word for word: "
                              + "; ".join(f'"{m}"' for m in missing), max_tokens=8000)
    shots = raw.get("shots") or []
    if len(shots) < 2:
        raise RuntimeError("the plan came back with fewer than two shots; run again")
    cast = raw.get("cast") or []
    for r in refs:   # given characters keep their looks
        c = next((c for c in cast if c.get("name") == r["name"]), None)
        if c is None:
            cast.append(c := {"name": r["name"], "tag": f"the {r['name']}", "voice": ""})
        c["look"] = r["description"]
    for c in cast:
        c.setdefault("tag", f"the {c['name']}")
        c.setdefault("voice", "")
        c.setdefault("look", "")
    sb = {"cast": cast}
    vs = clean_views(raw.get("views"), sb)
    sb = {"kind": "shots", "title": raw.get("title", ""), "style": style or raw.get("style", ""),
          "cast": cast, "setting": raw.get("setting", ""), "ambience": raw.get("ambience", ""),
          "location_facts": str(raw.get("location_facts", "")).strip(), "views": vs,
          "keyframes": shots, "segments": []}
    sb["characters"] = " ".join(f"{c['name']}: {c['look']}" for c in cast)
    normalise(sb)
    return sb


# ---------------------------------------------------------------- keyframes

def shot_refs(sb, i, story_dir):
    """Reference sheets for the cast in shot i only (others would leak into the frame)."""
    who = set(sb["keyframes"][i].get("who") or [])
    return [(r["name"], os.path.join(story_dir, r["image"])) for r in sb.get("refs", []) if r["name"] in who]


def tagged(text, cast, who=None):
    """'devman1 hands...' -> 'devman1 (the man in the navy suit) hands...' (first mention):
    ties each name to its look, so Flux doesn't swap faces and costumes between people.
    People named but not in the shot (who) become 'someone off-screen', or Flux draws them."""
    for c in cast.values():
        if who is not None and c["name"] not in who:
            text = re.sub(rf"\b(the\s+)?{re.escape(c['name'])}('s)?\b", "someone off-screen", text, flags=re.I)
            continue
        text = re.sub(rf"\b{re.escape(c['name'])}\b", f"{c['name']} ({c['tag']})", text, count=1)
    return text


# How much of the view's plate each shot size shows (centre crop) and how blurred it is:
# closer shots see less of the street, out of focus, the way a longer lens would.
PLATE_CROP = {"wide": 1.0, "medium": 0.62, "over-the-shoulder": 0.62, "close-up": 0.45,
              "reaction": 0.45, "insert": 0.4}
PLATE_BLUR = {"wide": 0, "medium": 2, "over-the-shoulder": 3, "close-up": 6, "reaction": 6, "insert": 8}
DOF_TEXT = "Shallow depth of field: the background is softly out of focus."


def view_id(v, k=0):
    return re.sub(r"[^a-z0-9-]", "", str(v or "").lower()) or f"view{k}"


def clean_views(raw, sb):
    """Views from the chat model: ids normalised, at most 3, and no people in the description
    (it's used to draw the EMPTY set): sentences naming a cast member are dropped."""
    names = [c["name"].lower() for c in sb.get("cast", [])]
    out = []
    for k, v in enumerate(raw or []):
        if not isinstance(v, dict):
            continue
        looking = " ".join(x for x in re.split(r"(?<=[.;])\s+", str(v.get("looking", "")))
                           if not any(n in x.lower() for n in names)).strip()
        out.append({"id": view_id(v.get("id"), k), "looking": looking or "the location"})
    return out[:3]


def views(sb):
    return sb.get("views") or [{"id": "main", "looking": "the main view of the location"}]


def view_of(sb, s):
    ids = [v["id"] for v in views(sb)]
    v = view_id(s.get("view"))
    return v if v in ids else ids[0]


def frame_prompt(sb, i, n_refs_names, plate=False):
    """Flux prompt for shot i's first frame. n_refs_names: names of the reference sheets
    passed, in order; with plate=True the shot's background plate follows them."""
    s, cast = sb["keyframes"][i], slug_names(sb)
    who = [cast[w] for w in s.get("who", []) if w in cast]
    people = " ".join(f"{c['name']}: {c['look']}" for c in who)
    intro = ""
    if n_refs_names:
        which = ", ".join(f"image {k + 1} shows {n}" for k, n in enumerate(n_refs_names))
        intro = (f"Reference images: {which}. They are character sheets: copy each character's "
                 f"exact face, hair, build, colours and costume, but NOT the sheet's pose, framing "
                 f"or plain background. Never mix features between characters. ")
    if plate:
        blur = PLATE_BLUR[s["size"]] > 0
        intro += (f"Image {len(n_refs_names) + 1} is the background plate for this shot: keep its "
                  f"background exactly as it is, with the same buildings, trees, road, lanes, parked "
                  f"vehicles and lighting in the same places{', softly out of focus' if blur else ''}, "
                  f"and place the people into it. Do not add, remove or move background objects. ")
    count = {0: "No people in this shot, except hands if the description mentions them.",
             1: "Exactly one person is the subject of this shot."}.get(
        len(who), f"Exactly {len(who)} people are the subject of this shot, each appearing once.")
    facts = f"Location facts that must stay true: {sb['location_facts']} " if sb.get("location_facts") else ""
    dof = DOF_TEXT + " " if s["size"] != "wide" else ""
    return (f"{intro}{SIZE_WORDS[s['size']]} ({FRAMING[s['size']]}): {tagged(s['image'], cast, s.get('who') or []).strip().rstrip('.')}. {count} "
            + (f"Characters: {people} " if people else "")
            + f"Setting: {sb['setting']} {facts}{dof}Style: {sb['style']} "
            f"Cinematic film still, no text, no watermark, no borders.")


LOCATION = """A film's shots all take place in one location:
Setting: "{setting}"
Shots:
{shots}
Reply with JSON only:
{{"location_facts": "the fixed physical facts of this place that must not change between shots: road layout and number of lanes, paving, what lines each side, where trees and parked vehicles are (or that there are none), landmarks; one or two sentences",
 "views": [{{"id": "short-id", "looking": "which way the camera looks and what part of the place it sees; describe only the place, never people"}}],
 "shot_views": ["the view id for each shot, in order"]}}
Views are camera DIRECTIONS in the place (not shot sizes, not close-ups): use 1-3, the
main direction most shots face plus the reverse angle if any shot looks the other way.
Exactly {n} entries in shot_views, each one of your view ids."""


def ensure_location(sb):
    """Storyboards planned before location plates had no location facts or camera views: ask
    the chat model for them from the shot list. Returns True if the storyboard changed."""
    if sb.get("location_facts") and sb.get("views"):
        return False
    shots = sb["keyframes"]
    lines = "\n".join(f"{i}. {s['size']}: {s['image']}" for i, s in enumerate(shots))
    r = lemon.chat_json(LOCATION.format(setting=sb.get("setting", ""), shots=lines, n=len(shots)))
    sb["location_facts"] = sb.get("location_facts") or str(r.get("location_facts", "")).strip()
    sb["views"] = clean_views(r.get("views"), sb) or views(sb)
    for s, v in zip(shots, r.get("shot_views") or []):
        s["view"] = v
    for s in shots:
        s["view"] = view_of(sb, s)
    print(f"  location facts: {sb['location_facts']}")
    for v in sb["views"]:
        print(f"  view {v['id']}: {v['looking']}  (shots {', '.join(str(i) for i, s in enumerate(shots) if s['view'] == v['id'])})")
    return True


def _plate_key(sb, v):
    return "|".join([sb.get("setting", ""), sb.get("style", ""), sb.get("location_facts", ""), v["id"], v["looking"]])


def location_plate(sb, story_dir, view_id, seed):
    """The empty set seen from one camera view (location-<view>.png), drawn once. The first
    view is drawn from text; the others from the first, so it stays the same place."""
    from storyboard import FRAME_SIZE
    vs = views(sb)
    v = next(x for x in vs if x["id"] == view_id)
    path = os.path.join(story_dir, f"location-{v['id']}.png")
    key = _plate_key(sb, v)
    if os.path.exists(path) and os.path.exists(path + ".key") and open(path + ".key").read() == key:
        return path
    facts = f" Location facts: {sb['location_facts']}" if sb.get("location_facts") else ""
    text = (f"Wide establishing shot of an empty location, no people at all. Camera view: {v['looking']}. "
            f"{sb['setting']}{facts} Style: {sb['style']} Everything sharp, deep focus. Cinematic "
            f"film still, no text, no watermark, no borders.")
    print(f"  location plate: {v['id']} ({v['looking']})", flush=True)
    if v is vs[0]:
        png = lemon.generate_image(text, size=FRAME_SIZE, seed=seed)[0]
    else:   # the same place from another angle
        first = location_plate(sb, story_dir, vs[0]["id"], seed)
        png = lemon.edit_image(f"Image 1 shows a location. Draw the SAME location from a different camera "
                               f"view, with the same paving, road and lanes, buildings, trees, street "
                               f"furniture and lighting. {text}", [first], size=FRAME_SIZE, seed=seed)[0]
    open(path, "wb").write(png)
    open(path + ".key", "w").write(key)
    return path


def shot_plate(sb, story_dir, i, seed, python):
    """Shot i's background plate: its view's plate, centre-cropped to the shot size and blurred
    for close shots (plate-<view>-<size>.png)."""
    s = sb["keyframes"][i]
    view = view_of(sb, s)
    full = location_plate(sb, story_dir, view, seed)
    crop, blur = PLATE_CROP[s["size"]], PLATE_BLUR[s["size"]]
    if crop >= 1 and not blur:
        return full
    out = os.path.join(story_dir, f"plate-{view}-{s['size']}.png")
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(full):
        return out
    script = """
import sys
from PIL import Image, ImageFilter
src, dst, crop, blur = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
im = Image.open(src).convert('RGB')
w, h = im.size
cw, ch = int(w * crop), int(h * crop)
x, y = (w - cw) // 2, int((h - ch) * 0.6)   # a little below centre: keep the ground people stand on
im = im.crop((x, y, x + cw, y + ch)).resize((w, h), Image.LANCZOS)
if blur:
    im = im.filter(ImageFilter.GaussianBlur(blur))
im.save(dst)
"""
    subprocess.run([python, "-I", "-c", script, full, out, str(crop), str(blur)], check=True)
    return out


def draw(sb, i, story_dir, seed, note="", adjust_from=None, python="python3"):
    """Draw shot i's first frame. Returns PNG bytes."""
    refs = shot_refs(sb, i, story_dir)
    names, paths = [n for n, _ in refs], [p for _, p in refs]
    from storyboard import FRAME_SIZE   # noqa: avoid a circular import at load time
    if adjust_from:
        which = ", ".join(f"image {k + 1} shows {n}" for k, n in enumerate(names))
        prompt = ((f"Reference images: {which}: keep each character's exact appearance. " if names else "")
                  + f"Image {len(names) + 1} is the current version of a film shot showing: "
                  f"{sb['keyframes'][i]['image']} Keep its composition, people, setting, lighting "
                  f"and style, and change only this: {note} Style: {sb['style']} Cinematic film "
                  f"still, no text, no watermark, no borders.")
        return lemon.edit_image(prompt, paths + [adjust_from], size=FRAME_SIZE, seed=seed)[0]
    plate = shot_plate(sb, story_dir, i, seed, python)
    prompt = frame_prompt(sb, i, names, plate=True)
    if note:
        prompt += f" Director's note for this drawing, follow it closely: {note}"
    return lemon.edit_image(prompt, paths + [plate], size=FRAME_SIZE, seed=seed)[0]


CHECK = """{sheets}{plate}The last image is the first frame of a film shot. It was supposed to show:
"{size}: {image}"
{people}
Reply with JSON only:
{{"matches": 0-10, "people": N, "set": 0-10, "set_diff": "...", "severe": "..."}}
matches: how well the image shows what it was supposed to show (framing, who, poses, props).
people: how many distinct people are clearly visible as subjects (ignore distant background
  extras and hands at the frame edge).
set: how well the background matches the empty set (image {plate_n}), allowing for the shot being
  closer or out of focus: 10 = the same place; deduct for extra or missing road lanes, buildings,
  trees or parked vehicles, a different road layout or different paving. 10 if it can't be seen.
set_diff: the main background difference in a few words, or "".
severe: ONLY these defects, otherwise "": two people fused into one body or sharing a head;
  one person appearing twice; a person with the wrong head{swap}; captions, subtitles or a watermark
  over the picture (logos and labels on objects are fine)."""


def check(sb, i, path, story_dir=None):
    """Score shot i's frame alone (shots are meant to differ, so no 'moved' test).
    Returns (score, note, ok)."""
    s = sb["keyframes"][i]
    want = len(s.get("who") or [])
    people = (f"It should show exactly {want} {'person' if want == 1 else 'people'} as subjects."
              if s["size"] != "insert" else "It is an insert: hands may be visible, no faces needed.")
    try:
        refs = shot_refs(sb, i, story_dir) if story_dir else []
        sheets = (" ".join(f"Image {k + 1} is the character sheet of {n}." for k, (n, _) in enumerate(refs))
                  + " " if refs else "")
        swap = ("; a person with another character's face, hair or clothes (compare with the "
                "character sheets)" if len(refs) > 1 else "")
        plate = (os.path.join(story_dir, f"location-{view_of(sb, s)}.png") if story_dir else None)
        plate = plate if plate and os.path.exists(plate) else None
        plate_n = len(refs) + 1
        ptxt = f"Image {plate_n} is the empty set this shot takes place in, seen from further back. " if plate else ""
        txt = lemon.describe_image([p for _, p in refs] + ([plate] if plate else []) + [path],
                                   CHECK.format(sheets=sheets, plate=ptxt, plate_n=plate_n, swap=swap,
                                                size=SIZE_WORDS[s["size"]],
                                                image=tagged(s["image"], slug_names(sb), s.get("who") or []), people=people),
                                   max_tokens=300)
        r = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
        match = float(r.get("matches", 0))
        setm = float(r.get("set", 10)) if plate else 10.0
        set_diff = str(r.get("set_diff", "")).strip().strip(".")
        got = int(float(r.get("people", want)))
        severe = str(r.get("severe", "")).strip().strip(".")
    except Exception as e:
        return 0, f"check failed ({str(e)[:80]})", True
    bad = severe and severe.lower() not in ("none", "no", "n/a", "false")
    wrong_count = s["size"] not in ("insert", "wide") and want and got != want
    # the background only has to match where it's sharp enough to notice
    wrong_set = s["size"] in ("wide", "medium", "over-the-shoulder") and setm < 6
    ok = match >= 5 and not bad and not wrong_count and not wrong_set
    note = (f"matches {match:g}/10, set {setm:g}/10" + (f" ({set_diff})" if set_diff and setm < 8 else "")
            + (f", {got} people (want {want})" if wrong_count else "") + (f", {severe}" if bad else ""))
    return match + 0.5 * setm - (20 if bad else 0) - (5 if wrong_count else 0), note, ok


# ---------------------------------------------------------------- animatic

def animatic(sb, story_dir, name, frame_path, python):
    """The keyframes held for each shot's length with a caption: check pacing before rendering."""
    shots = sb["keyframes"]
    if not all(os.path.exists(frame_path(i)) for i in range(len(shots))):
        return None
    tmp = os.path.join(story_dir, "animatic-frames")
    os.makedirs(tmp, exist_ok=True)
    caps = []
    for i, s in enumerate(shots):
        c = f"{i}  {s['size']}, {s['camera']}  {s['seconds']:g}s"
        if s.get("line"):
            c += f'   {s["line"].get("speaker", "")}: "{s["line"]["text"]}"'
        caps += [frame_path(i), os.path.join(tmp, f"{i:02d}.png"), c]
    script = """
import sys
from PIL import Image, ImageDraw
a = sys.argv[1:]
for src, dst, cap in zip(a[0::3], a[1::3], a[2::3]):
    im = Image.open(src).convert('RGB').resize((960, 640))
    out = Image.new('RGB', (960, 704), 'black')
    out.paste(im, (0, 0))
    ImageDraw.Draw(out).text((12, 650), cap[:150], fill='white', font_size=22)
    out.save(dst)
"""
    subprocess.run([python, "-I", "-c", script, *caps], check=True)
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w") as f:
        for i, s in enumerate(shots):
            f.write(f"file '{i:02d}.png'\nduration {s['seconds']:g}\n")
        f.write(f"file '{len(shots) - 1:02d}.png'\n")   # concat needs the last one twice
    out = os.path.join(story_dir, f"{name}-animatic.webm")
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", lst, "-vf", "fps=12,format=yuv420p", "-t", f"{sum(s['seconds'] for s in shots):g}",
                        "-c:v", "libvpx", "-b:v", "1M", out],
                       capture_output=True, text=True)
    shutil.rmtree(tmp, ignore_errors=True)
    if r.returncode != 0:
        print(f"  animatic failed: {r.stderr.strip()[-300:]}")
        return None
    total = sum(s["seconds"] for s in shots)
    print(f"  animatic: {out}  ({len(shots)} shots, {total:g} s)")
    return out


# ---------------------------------------------------------------- video

def _sub_names(text, cast, who=None):
    """'the devman1 bows' -> 'the man in the navy suit bows': video models don't know names.
    People not in the shot (who) become 'someone off-screen': naming them made LTX cut away
    to a new scene to show them."""
    for c in sorted(cast.values(), key=lambda c: -len(c["name"])):
        if who is not None and c["name"] not in who:
            text = re.sub(rf"\b((the|a|an)\s+)?{re.escape(c['name'])}('s)?\b", "someone off-screen", text, flags=re.I)
            continue
        tag = c["tag"].strip()
        bare = re.sub(r"^(the|a|an)\s+", "", tag, flags=re.I)
        text = re.sub(rf"\b(the|a|an)\s+{re.escape(c['name'])}\b", f"the {bare}", text, flags=re.I)
        text = re.sub(rf"\b{re.escape(c['name'])}\b", f"the {bare}", text, flags=re.I)
    return text


def video_prompt(sb, k):
    """LTX prompt for shot k: main action first, then who, camera, sound; in order, present
    tense, under ~200 words, describing movement rather than what the frame already shows."""
    s, cast = sb["keyframes"][k], slug_names(sb)
    sentence = lambda x: (x.strip().rstrip(".") + ".") if x and x.strip() else ""
    who = s.get("who") or []
    parts = [sentence(_sub_names(s["action"], cast, who))]
    line = s.get("line")
    if line:
        c = cast.get(line.get("speaker"))
        speaker = _sub_names(line.get("speaker", "someone"), cast) if c else "someone"
        voice = f" in a {c['voice'].rstrip('.')}" if c and c.get("voice") else ""
        parts.append(f"{speaker[0].upper() + speaker[1:]} speaks{voice}, saying: \"{line['text'].strip()}\""
                     + (" The other person listens silently." if len(who) > 1 else ""))
    cam = s.get("camera", "static").lower()
    size = SIZE_WORDS[s["size"]].lower()
    parts.append(f"{size[0].upper() + size[1:]}, the camera holds still." if cam in ("static", "locked off", "none")
                 else f"{size[0].upper() + size[1:]}, camera movement: {s['camera']}, smooth and steady.")
    snd = [x.strip().rstrip(".") for x in (_sub_names(s.get("sound") or "", cast, who), sb.get("ambience")) if x and x.strip()]
    if snd:
        parts.append("Sound: " + "; ".join(x[0].lower() + x[1:] for x in snd) + ".")
    if s["size"] != "wide":
        parts.append(DOF_TEXT)
    parts.append(sb.get("style", ""))
    text = " ".join(p.strip() for p in parts if p)
    text = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    words = text.split()
    return " ".join(words[:200])


TAKE_CHECK = """Image 1 is the planned first frame of a film shot. Image 2 shows four frames of
the generated video clip, left to right in time. The clip should show: "{action}"
Reply with JSON only:
{{"identity": 0-10, "motion": 0-10, "clean": 0-10, "note": "a few words"}}
identity: the people keep the same face, hair and clothes as in image 1 all the way through.
motion: the planned action visibly happens (0 = frozen or something else happens).
clean: 10 = no artefacts; deduct for morphing or melting bodies, extra or missing limbs, faces
  changing, objects appearing from nowhere, flicker."""


def score_take(keyframe, clip, seconds, action, work, fps=24):
    """Vision-model score for one rendered take (higher is better)."""
    strip = os.path.join(work, os.path.basename(clip) + ".strip.png")
    times = [seconds * f for f in (0.1, 0.4, 0.7, 0.95)]
    sel = "+".join(f"eq(n\\,{max(0, round(t * fps))})" for t in times)
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", clip,
                        "-vf", f"select='{sel}',scale=384:-2,tile=4x1", "-frames:v", "1", "-fps_mode", "passthrough", strip],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return 0, "couldn't read the clip"
    try:
        txt = lemon.describe_image([keyframe, strip], TAKE_CHECK.format(action=action), max_tokens=200)
        j = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
        ident, mot, clean = (float(j.get(k, 0)) for k in ("identity", "motion", "clean"))
        return ident + mot + clean, f"identity {ident:g}, motion {mot:g}, clean {clean:g}" + (
            f": {j['note']}" if j.get("note") else "")
    except Exception as e:
        return 0, f"check failed ({str(e)[:60]})"
    finally:
        if os.path.exists(strip):
            os.remove(strip)


def _take_key(prompt, W, H, frames, seed, keyframe):
    return hashlib.sha256((prompt + f"shots1{W}x{H}{frames}{seed}").encode()
                          + open(keyframe, "rb").read()).hexdigest()


def _take_ok(stamp, prompt, W, H, frames, seed, keyframe):
    """Is the take recorded in `stamp` usable for a shot needing `frames` frames? A take that
    is long enough is reused (trimmed at the cut), so shortening a shot never re-renders it.
    Stamps are 'v2|<key without length>|<frames rendered>'; older ones are a key that
    includes the frame count."""
    if not os.path.exists(stamp):
        return False
    st = open(stamp).read()
    if st.startswith("v2|"):
        _, key, n = st.split("|")
        return key == _take_key(prompt, W, H, 0, seed, keyframe) and int(n) >= frames
    return any(st == _take_key(prompt, W, H, f, seed, keyframe) for f in range(9, 8 * 26 + 2, 8) if f >= frames)


def punch_filter(s, W, H, d):
    """Punch in: crop into the shot and enlarge it back to W x H, keeping the anchor side.
    With push the zoom grows from 1 to `punch` over the shot (a slow digital push-in).
    Applied at the join, so changing it never re-renders the shot."""
    z, push = float(s.get("punch") or 1.0), bool(s.get("push"))
    if z <= 1.0:
        return ""
    ax, ay = ANCHORS.get(s.get("anchor"), (0.5, 0.5))
    zt = f"(1+{z - 1:.4f}*t/{d:.4f})" if push else f"{z:.4f}"
    return (f",scale=w='trunc({W}*{zt}/2)*2':h='trunc({H}*{zt}/2)*2':eval=frame:flags=lanczos,"
            f"crop={W}:{H}:x='(iw-{W})*{ax}':y='(ih-{H})*{ay}'")


def take_files(seg_dir, k):
    """Existing take numbers for shot k with a valid stamp file."""
    found = []
    if os.path.isdir(seg_dir):
        for f in os.listdir(seg_dir):
            m = re.fullmatch(rf"shot-{k:02d}-t(\d+)\.webm", f)
            if m and os.path.exists(os.path.join(seg_dir, f + ".key")):
                found.append(int(m.group(1)))
    return sorted(found)


def render(name, story_dir, frame_path, load, save, a, log):
    """Render every shot from its own first frame (takes ranked by the vision model) and join
    the chosen takes with hard cuts over one ambience track."""
    sb = load(name)
    shots = sb["keyframes"]
    n = len(shots)
    for i in range(n):
        if not os.path.exists(frame_path(i)):
            raise RuntimeError(f"shot {i} has no first frame yet: storyboard frames {name}")
    fps = a.fps
    tag = "draft" if a.draft else "full"
    pixels = 512 * 320 if a.draft else 768 * 512
    W, H = (int(v) for v in makevideo.size_like(frame_path(0), pixels).split("x"))
    takes = a.takes or (1 if a.draft else 2)
    seg_dir = os.path.join(story_dir, f"shots-{tag}")
    os.makedirs(seg_dir, exist_ok=True)
    only = {int(v) for v in a.only.split(",")} if a.only else set()
    base = sb.get("seed", 42)
    reseed = sb.setdefault("reseed", {})
    for k in only:   # a new seed for these shots, kept so later runs reuse the new takes
        reseed[str(k)] = int(time.time()) % 100000 + k
    total = sum(s["seconds"] for s in shots)
    log(f"render ({tag}): {n} shots, {total:g} s, {W}x{H}, {takes} take(s) per shot")
    t0 = time.time()
    picks = {}
    for k, s in enumerate(shots):
        prompt = video_prompt(sb, k)
        frames = makevideo.frames_for(s["seconds"] + TAIL, fps)
        label = f"  shot {k} ({s['size']}, {s['seconds']:g}s)"
        valid, fresh = [], False
        have = len(take_files(seg_dir, k))
        # with --only, other shots keep the takes they have (or get one if they have none)
        want = takes if (not only or k in only) else max(1, have)
        for t in range(max(want, have)):
            seed = base + 100 * k + t + reseed.get(str(k), 0)
            clip = os.path.join(seg_dir, f"shot-{k:02d}-t{t}.webm")
            stamp = clip + ".key"
            key = f"v2|{_take_key(prompt, W, H, 0, seed, frame_path(k))}|{frames}"
            need = round(s["seconds"] * fps) + 1   # frames used at the cut
            if os.path.exists(clip) and _take_ok(stamp, prompt, W, H, need, seed, frame_path(k)):
                valid.append(t)
                continue
            if t >= want:   # an old extra take that's now stale: drop it
                for p in (clip, stamp, clip + ".score"):
                    if os.path.exists(p):
                        os.remove(p)
                continue
            print(f"{label} take {t + 1}/{want}: rendering...", end="", flush=True)
            raw = os.path.join(seg_dir, f"shot-{k:02d}-t{t}-raw.webm")
            secs = makevideo.render(prompt, raw, W, H, frames, fps=fps, seed=seed, image=frame_path(k))
            # browsers can't play the PCM audio sd-cli writes: keep VP8, make Opus
            ok = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", raw,
                                 "-c:v", "copy", "-c:a", "libopus", "-b:a", "160k", clip]).returncode == 0
            if ok:
                os.remove(raw)
            else:
                os.replace(raw, clip)
            open(stamp, "w").write(key)
            if os.path.exists(clip + ".score"):
                os.remove(clip + ".score")
            valid.append(t)
            fresh = True
            print(f" {secs / 60:.1f} min")
        # rank the takes (cached per take)
        scores = {}
        for t in valid:
            clip = os.path.join(seg_dir, f"shot-{k:02d}-t{t}.webm")
            sp = clip + ".score"
            if os.path.exists(sp):
                scores[t] = json.load(open(sp))
            elif len(valid) > 1:
                sc, note = score_take(frame_path(k), clip, s["seconds"], s["action"], seg_dir, fps)
                scores[t] = {"score": sc, "note": note}
                json.dump(scores[t], open(sp, "w"))
            else:
                scores[t] = {"score": 0, "note": ""}
        chosen = load(name).get("takes", {}).get(tag, {}).get(str(k)) or {}   # a pick made on the page
        mine = chosen.get("by") == "you" and chosen.get("take") in valid and not fresh
        pick = chosen["take"] if mine else max(valid, key=lambda t: (scores[t]["score"], -t))
        best = scores[pick]
        weak = len(valid) > 1 and best["score"] < 21   # e.g. every take frozen, or morphing
        picks[str(k)] = {"take": pick, "by": "you" if mine else "auto", "weak": weak,
                         "scores": {str(t): scores[t] for t in valid}}
        if len(valid) > 1:
            print(f"{label}: using take {pick + 1} of {len(valid)}"
                  f"{' (your pick)' if picks[str(k)]['by'] == 'you' else ''}: {scores[pick]['note']}"
                  + (f"  << WEAK: re-render with --only {k}, or make its action more visible" if weak else ""))
        else:
            print(f"{label}: ready")

    # Save the picks onto the storyboard as it is now (the page may have edited text meanwhile)
    cur = load(name)
    cur.setdefault("takes", {})[tag] = picks
    cur["reseed"] = reseed
    save(name, cur)

    log("joining shots with hard cuts")
    inputs, chains = [], []
    for k, s in enumerate(shots):
        clip = os.path.join(seg_dir, f"shot-{k:02d}-t{picks[str(k)]['take']}.webm")
        inputs += ["-i", clip]
        nf = round(s["seconds"] * fps)
        d = nf / fps
        # trim to the shot's length (drops the tail); tiny audio fades stop clicks at the cuts
        chains.append(f"[{k}:v]trim=end_frame={nf},setpts=PTS-STARTPTS,scale={W}:{H},setsar=1,fps={fps}"
                      f"{punch_filter(s, W, H, d)}[v{k}];"
                      f"[{k}:a]atrim=duration={d:.4f},asetpts=PTS-STARTPTS,aresample=48000,"
                      f"afade=t=in:d=0.02,afade=t=out:st={max(0, d - 0.03):.4f}:d=0.03[a{k}]")
    total = sum(round(s["seconds"] * fps) for s in shots) / fps
    graph = ";".join(chains) + ";" + "".join(f"[v{k}][a{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    amix, extra = ["[a]"], n
    if sb.get("ambience") and not a.no_ambience:
        amb = os.path.join(story_dir, "ambience.wav")
        stamp = amb + ".key"
        akey = sb["ambience"]
        if not (os.path.exists(amb) and os.path.exists(stamp) and open(stamp).read() == akey):
            log(f"ambience: {sb['ambience']}")
            open(amb, "wb").write(lemon.generate_audio(sb["ambience"], lemon.SFX_MODEL,
                                                       duration=min(30, round(total) + 1), seed=base))
            open(stamp, "w").write(akey)
        inputs += ["-stream_loop", "-1", "-i", amb]
        vol = 10 ** (-a.ambience_db / 20)
        graph += (f";[{extra}:a]aresample=48000,atrim=duration={total:.3f},volume={vol:.3f},"
                  f"afade=t=in:d=1,afade=t=out:st={max(0, total - 1.5):.3f}:d=1.5[amb]")
        amix.append("[amb]")
        extra += 1
    if a.music:
        log(f"music bed: ACE-Step, {total:.0f} s")
        music = os.path.join(story_dir, "music.wav")
        mkey = f"{a.music}|{round(total) + 1}|{base}"
        if not (os.path.exists(music) and os.path.exists(music + ".key") and open(music + ".key").read() == mkey):
            open(music, "wb").write(lemon.generate_audio(a.music, lemon.MUSIC_MODEL, duration=round(total) + 1,
                                                         seed=base))
            open(music + ".key", "w").write(mkey)
        inputs += ["-i", music]
        vol = 10 ** (-a.music_db / 20)
        graph += (f";[{extra}:a]aresample=48000,atrim=duration={total:.3f},volume={vol:.3f},"
                  f"afade=t=out:st={max(0, total - 2):.3f}:d=2[mus]")
        amix.append("[mus]")
    # mix, then bring the whole film to a normal listening level (LTX's sound comes out quiet)
    graph += (f";{''.join(amix)}amix=inputs={len(amix)}:duration=first:normalize=0," if len(amix) > 1 else ";[a]")
    graph += "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[am]"
    amap = "[am]"
    out_name = f"{name}-{tag}"
    joined = os.path.join(story_dir, f"{out_name}-raw.mkv")
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
                        "-filter_complex", graph, "-map", "[v]", "-map", amap,
                        "-c:v", "libvpx", "-b:v", "8M", "-crf", "6", "-c:a", "pcm_s16le", joined],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-800:]}")
    made = makevideo.make_copies(joined, story_dir, out_name, W * H)
    log(f"done: {total:.0f} s of video, {n} shots, in {(time.time() - t0) / 60:.1f} min")
    for line in made:
        print(f"  {line}")
