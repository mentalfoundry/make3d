#!/usr/bin/env python3
"""Your own character art (any pose, any angle) -> A/T-pose concept -> 3D -> rigged files.

  1. The vision chat model describes the character and picks the closest body template.
  2. Flux redraws the character onto the pose template (several candidates).
  3. Each candidate is scored with pose detection against the template, and the hands
     are checked; the best one wins. A contact sheet shows them all.
  4. The winner goes through the normal make3d chain (TRELLIS, game-ready, rig).

Examples:
  art3d my-hero.png
  art3d my-hero.png --pose t --name hero
  art3d my-hero.png --stop                      # only make and score the candidates
  art3d my-hero.png --hint "she carries no weapon, the cloak is dark green"
  make3d --image ~/3d/out/hero/pose-option-3.png --name hero   # use a different candidate

Output: ~/3d/out/<name>/  (art copy, pose-option-N.png, pose-options.png, then make3d files)
"""
import argparse, json, os, re, shutil, subprocess, sys, time

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import lemon  # noqa: E402
from make3d import HANDS, STYLE, HOME3D, log  # noqa: E402

VENV_PY = os.path.join(HOME3D, "venv", "bin", "python")
BUILDS = ("male", "female", "stocky")

ASK = ("This is concept art of a game character. Reply with JSON only, no other text:\n"
       '{"description": "...", "build": "male|female|stocky", "humanoid": true}\n'
       "description: one dense paragraph a 3D artist could model from: species, age, body shape, "
       "face, hair, every piece of clothing and armour with its colours and materials, from head "
       "to feet. Describe only the character, not the pose, action, background or art style. "
       "Leave out anything held in the hands (weapons, tools, shields).\n"
       "build: male = average or slim masculine body, female = feminine body, stocky = short, "
       "wide, heavy or chubby body (dwarves, goblins, ogres).\n"
       "humanoid: false if it is not a two-armed, two-legged figure.")

PROMPT = ("The first image is a mannequin in the target pose. The second image is concept art of "
          "a character. Redraw the mannequin from the first image as the character from the "
          "second image: {desc} Copy the character's design, outfit, colours and materials from "
          "the second image, but keep exactly the mannequin's pose, body proportions, camera angle "
          "and framing from the first image: front view facing the viewer, full body from head to "
          "feet, arms held away from the body exactly as the mannequin. Nothing in the hands. "
          + HANDS + STYLE)

KEY = ["l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist", "r_wrist",
       "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle"]


def joints(img, out_json):
    r = subprocess.run([VENV_PY, "-I", os.path.join(TOOLS, "detect_joints.py"), img, out_json],
                       capture_output=True, text=True)
    return json.load(open(out_json)) if r.returncode == 0 else None


def normalised(j):
    """Key landmarks relative to the hip centre, scaled by shoulder-to-ankle height."""
    b = j["body"]
    W, H = j["size"]
    pt = lambda n: (b[n][0] * W, b[n][1] * H)
    mid = lambda a, c: ((pt(a)[0] + pt(c)[0]) / 2, (pt(a)[1] + pt(c)[1]) / 2)
    hip, sh, an = mid("l_hip", "r_hip"), mid("l_shoulder", "r_shoulder"), mid("l_ankle", "r_ankle")
    scale = max(1.0, ((an[0] - sh[0]) ** 2 + (an[1] - sh[1]) ** 2) ** 0.5)
    return {n: ((pt(n)[0] - hip[0]) / scale, (pt(n)[1] - hip[1]) / scale) for n in KEY}, \
        min(b[n][2] for n in KEY)


def score(cand, ref):
    """Lower is better: pose distance to the template, plus penalties."""
    if not cand:
        return 9.0, "no body found"
    c, vis = normalised(cand)
    r, _ = normalised(ref)
    dist = sum(((c[n][0] - r[n][0]) ** 2 + (c[n][1] - r[n][1]) ** 2) ** 0.5 for n in KEY) / len(KEY)
    s, notes = dist, [f"pose {dist:.3f}"]
    if vis < 0.5:
        s += 0.3
        notes.append(f"low visibility {vis:.2f}")
    hands = len(cand.get("hands", {}))
    s -= 0.02 * hands
    notes.append(f"hands seen {hands}/2")
    return s, ", ".join(notes)


def contact_sheet(paths, labels, out):
    sheet = [VENV_PY, "-I", "-c", """
import sys
from PIL import Image, ImageDraw
out, items = sys.argv[1], sys.argv[2:]
ims = [Image.open(p).convert('RGB') for p in items[0::2]]
ims = [i.resize((int(i.width * 700 / i.height), 700)) for i in ims]
s = Image.new('RGB', (sum(i.width for i in ims), 760), 'white')
d = ImageDraw.Draw(s)
x = 0
for im, lab in zip(ims, items[1::2]):
    s.paste(im, (x, 60))
    d.text((x + 10, 10), lab, fill='black', font_size=22)
    x += im.width
s.save(out)
""", out]
    for p, l in zip(paths, labels):
        sheet += [p, l]
    subprocess.run(sheet, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("art", help="your character art (png/jpg/webp)")
    ap.add_argument("--name", help="output name (default: from the file name)")
    ap.add_argument("--pose", choices=["a", "t"], default="a", help="A-pose (default) or T-pose")
    ap.add_argument("--template", help="force a template (name in ~/3d/templates or a path) "
                                       "instead of letting the vision model pick the build")
    ap.add_argument("--hint", default="", help="extra description to add (fixes what the vision model gets wrong)")
    ap.add_argument("--options", type=int, default=3, help="candidates to make (default 3)")
    ap.add_argument("--stop", action="store_true", help="stop after making and scoring the candidates")
    ap.add_argument("--seed", type=int, default=42)
    a, passthrough = ap.parse_known_args()   # --resolution, --height, --tris, --tex, --no-rig go to make3d

    if not os.path.exists(a.art):
        sys.exit(f"not found: {a.art}")
    name = a.name or re.sub(r"[^a-z0-9]+", "-", os.path.splitext(os.path.basename(a.art))[0].lower()).strip("-")
    out = os.path.join(HOME3D, "out", name)
    os.makedirs(out, exist_ok=True)
    art = os.path.join(out, "art" + os.path.splitext(a.art)[1].lower())
    if os.path.abspath(a.art) != os.path.abspath(art):
        shutil.copy(a.art, art)
    t0 = time.time()

    # 1. Describe
    log("describe: reading your art with the vision model")
    desc, build = a.hint, "male"
    try:
        txt = lemon.describe_image(art, ASK)
        info = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
        desc = (info.get("description", "").strip() + " " + a.hint).strip()
        build = info.get("build") if info.get("build") in BUILDS else "male"
        if info.get("humanoid") is False:
            print("  warning: this doesn't look like a two-armed, two-legged figure; "
                  "the pose templates and rig assume one")
    except Exception as e:
        print(f"  (vision model unavailable: {str(e)[:200]}; using only the art and --hint)")
    if not desc.endswith("."):
        desc += "."
    print(f"  {desc}")
    open(os.path.join(out, "description.txt"), "w").write(desc + "\n")

    # 2. Template
    if a.template:
        tpl = a.template if os.path.exists(a.template) else os.path.join(HOME3D, "templates", f"{a.template}.png")
    else:
        tpl = os.path.join(HOME3D, "templates", f"{a.pose}-pose-{build}.png")
        if not os.path.exists(tpl):   # no t-pose-stocky yet
            print(f"  no {os.path.basename(tpl)}; using {a.pose}-pose-male")
            tpl = os.path.join(HOME3D, "templates", f"{a.pose}-pose-male.png")
    if not os.path.exists(tpl):
        sys.exit(f"template not found: {tpl}")
    print(f"  template: {os.path.basename(tpl)}")
    ref = joints(tpl, os.path.join(out, "template-joints.json"))
    if not ref:
        sys.exit("pose detection failed on the template")

    # 3. Candidates
    size = "1024x1024" if "t-pose" in os.path.basename(tpl) else "640x1024"
    log(f"pose: redrawing your character onto the template ({a.options} candidates, ~1 min each)")
    results = []
    for i in range(a.options):
        path = os.path.join(out, f"pose-option-{i + 1}.png")
        open(path, "wb").write(lemon.edit_image(PROMPT.format(desc=desc), [tpl, art], size=size,
                                                seed=a.seed + i)[0])
        s, note = score(joints(path, os.path.join(out, f"pose-option-{i + 1}.json")), ref)
        results.append((s, i + 1, path, note))
        print(f"  option {i + 1}: score {s:.3f} ({note})")
    for f in os.listdir(out):
        if re.fullmatch(r"(pose-option-\d+|template-joints)\.json", f):
            os.remove(os.path.join(out, f))
    best = min(results)
    contact_sheet([art] + [r[2] for r in results],
                  ["your art"] + [f"option {r[1]}  score {r[0]:.2f}" + ("  BEST" if r is best else "")
                                  for r in results],
                  os.path.join(out, "pose-options.png"))
    print(f"  best: option {best[1]}   sheet: {out}/pose-options.png")
    if best[0] > 0.5:
        print("  warning: no candidate matched the pose well; check the sheet before using it")

    if a.stop:
        print(f"\nTo continue with a candidate:\n  make3d --image {out}/pose-option-N.png --name {name}")
        return

    # 4. The rest of the chain
    log(f"handing option {best[1]} to make3d ({(time.time() - t0) / 60:.1f} min so far)")
    sys.stdout.flush()
    r = subprocess.run([sys.executable, "-I", os.path.join(TOOLS, "make3d.py"),
                        "--image", best[2], "--name", name, "--seed", str(a.seed), *passthrough])
    if r.returncode == 0:
        print(f"  other candidates: {out}/pose-options.png  (rerun with make3d --image ... --name {name})")
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
