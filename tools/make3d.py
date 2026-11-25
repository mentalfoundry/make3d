#!/usr/bin/env python3
"""Description -> concept image -> 3D model -> game-ready (and rigged) files.

Characters (default): the concept is drawn over a pose template, so it comes
out in an A- or T-pose with spread fingers, then it is auto-rigged.
Props (--prop): plain concept image, no template, no rig.

Examples:
  make3d "a knight in silver plate armour with a blue tabard"
  make3d "a stocky green goblin in ragged leather" --template a-pose-stocky
  make3d "a wooden treasure chest with iron bands" --prop
  make3d "a knight" --options 4          # just make 4 concepts to choose from
  make3d --image my-concept.png --name hero   # start from your own image
  make3d --raw ~/3d/out/hero/raw.glb --name hero   # redo only the Blender steps

Output: ~/3d/out/<name>/  (concept.png, raw.glb, <name>.glb/.fbx/.blend, preview.png)
"""
import argparse, os, re, shutil, subprocess, sys, time

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import lemon  # noqa: E402

HOME3D = os.path.dirname(TOOLS)   # the repo root
BLENDER = os.environ.get("BLENDER") or os.path.expanduser("~/apps/blender/blender")

STYLE = ("Flat even studio lighting, matte colours, no reflections, no specular highlights, "
         "no shadows. Plain flat white background, no text, no watermark. "
         "3D game asset concept, clean readable shapes.")
HANDS = ("Hands are very important: both hands open and flat, palms turned as in the reference, all five "
         "fingers on each hand spread wide apart like a starfish, with a clear visible gap of "
         "background between every finger and between the thumb and the index finger. Hands are "
         "empty, not holding anything, not clenched, not curled. If wearing gloves or gauntlets, "
         "they have five separate fingers, never mittens. ")
CHARACTER = ("Redraw the mannequin in the reference image as {desc}. Keep exactly the same pose, "
             "body proportions, camera angle and framing: front view, full body from head to feet, "
             "arms held away from the body exactly as in the reference, all five fingers on each "
             "hand spread apart and clearly separated. " + HANDS + STYLE)
PROP = ("{desc}, a single object shown whole and centered, three-quarter view from slightly "
        "above. " + STYLE)


def log(msg):
    print(f"\n== {msg}", flush=True)


def blender(script, *args):
    cmd = [BLENDER, "-b", "--factory-startup", "--python-exit-code", "1",
           "--python", os.path.join(TOOLS, script), "--", *args]
    r = subprocess.run(cmd, capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith(("[gameready", "[rig", "GAMEREADY", "RIG_DONE", "PREVIEW_STATS")):
            print("  " + line)
    if r.returncode != 0:
        sys.exit(f"{script} failed:\n{(r.stdout + r.stderr)[-3000:]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("description", nargs="?", help="what to make")
    ap.add_argument("--name", help="output folder/file name (default: from the description)")
    ap.add_argument("--prop", action="store_true", help="an object, not a character (no template, no rig)")
    ap.add_argument("--template", default="a-pose-male",
                    help="pose template in ~/3d/templates (a-pose-male, a-pose-female, "
                         "a-pose-stocky, t-pose-male, ...) or a path to an image")
    ap.add_argument("--image", help="skip concept generation and use this image")
    ap.add_argument("--raw", help="skip concept and TRELLIS; reprocess this raw .glb")
    ap.add_argument("--options", type=int, default=1,
                    help="make this many concepts and stop, so you can pick one (use with --image)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--resolution", type=int, choices=[512, 1024, 1536],
                    help="TRELLIS detail level (default 1024 characters, 512 props; "
                         "higher uses more RAM and time)")
    ap.add_argument("--height", type=float, help="final height in metres (default 1.8 characters, 1.0 props)")
    ap.add_argument("--tris", type=int, default=30000, help="triangle budget")
    ap.add_argument("--tex", type=int, default=2048, help="texture size")
    ap.add_argument("--no-rig", action="store_true")
    a = ap.parse_args()
    if not a.description and not a.image and not a.raw:
        ap.error("give a description, --image or --raw")

    name = a.name or re.sub(r"[^a-z0-9]+", "-", (a.description or "model").lower()).strip("-")[:40]
    out = os.path.join(HOME3D, "out", name)
    os.makedirs(out, exist_ok=True)
    t0 = time.time()

    # 1. Concept image
    if a.raw:
        pass
    elif a.image:
        concept = os.path.join(out, "concept" + os.path.splitext(a.image)[1].lower())
        if os.path.abspath(a.image) != os.path.abspath(concept):
            shutil.copy(a.image, concept)
    else:
        if a.prop:
            log(f"concept: generating {a.options} image(s) with Flux")
            make = lambda seed: lemon.generate_image(PROP.format(desc=a.description), size="1024x1024", seed=seed)[0]
        else:
            tpl = a.template if os.path.exists(a.template) else os.path.join(HOME3D, "templates", f"{a.template}.png")
            if not os.path.exists(tpl):
                sys.exit(f"template not found: {tpl}")
            size = "1024x1024" if "t-pose" in os.path.basename(tpl) else "640x1024"
            log(f"concept: drawing over template {os.path.basename(tpl)} with Flux")
            make = lambda seed: lemon.edit_image(CHARACTER.format(desc=a.description), [tpl], size=size, seed=seed)[0]
        if a.options > 1:
            for i in range(a.options):
                path = os.path.join(out, f"concept-option-{i + 1}.png")
                open(path, "wb").write(make(a.seed + i))
                print(f"  {path}")
            print(f"\nPick one and run:\n  make3d --image {out}/concept-option-N.png --name {name}"
                  + (" --prop" if a.prop else ""))
            return
        concept = os.path.join(out, "concept.png")
        open(concept, "wb").write(make(a.seed))
        print(f"  {concept}")

    # 2. Image -> 3D
    raw = os.path.join(out, "raw.glb")
    a.resolution = a.resolution or (512 if a.prop else 1024)
    if a.raw:
        if os.path.abspath(a.raw) != os.path.abspath(raw):
            shutil.copy(a.raw, raw)
    else:
        log(f"3D: TRELLIS at resolution {a.resolution} (takes a few minutes)")
        t = time.time()
        open(raw, "wb").write(lemon.image_to_3d(concept, resolution=a.resolution, seed=a.seed))
        print(f"  {raw} ({time.time() - t:.0f}s)")

    # 3. Game-ready
    log("game-ready: scale, rebuild, reduce, bake textures")
    height = a.height or (1.0 if a.prop else 1.8)
    game_dir = os.path.join(out, "unrigged")
    blender("gameready.py", raw, game_dir, "--height", str(height), "--tris", str(a.tris),
            "--tex", str(a.tex), "--name", name)
    final = os.path.join(game_dir, f"{name}.glb")

    # 4. Rig
    if not a.prop and not a.no_rig:
        log("rig: skeleton, fingers and skin weights")
        blender("rig.py", final, out, "--name", name)
        final = os.path.join(out, f"{name}.glb")
    else:
        for ext in ("glb", "fbx", "blend"):
            shutil.copy(os.path.join(game_dir, f"{name}.{ext}"), out)
        final = os.path.join(out, f"{name}.glb")

    # 5. Preview
    blender("preview.py", final, os.path.join(out, "preview.png"), "512")
    for f in os.listdir(out):
        if f.startswith("preview-") and f.endswith(".png"):
            os.remove(os.path.join(out, f))

    log(f"done in {(time.time() - t0) / 60:.1f} min")
    print(f"  model:   {out}/{name}.glb  (also .fbx, .blend)")
    print(f"  preview: {out}/preview.png")
    print(f"  open:    {BLENDER} --python {TOOLS}/open_in_blender.py -- {out}/{name}.glb")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(f"\nerror: {e}")
