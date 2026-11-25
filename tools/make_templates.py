#!/usr/bin/env python3
"""Generate candidate licence-free pose templates (mannequins) with Flux.

Usage: python3 ~/3d/tools/make_templates.py [OUT_DIR] [--seeds N]
Writes OUT_DIR/<pose>-<build>-s<seed>.png; pick the best and copy it to
~/3d/templates/<pose>-<build>.png
"""
import argparse, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lemon

POSES = {
    "a-pose": ("standing in a symmetrical A-pose: arms straight and angled about 45 degrees down "
               "and away from the body, a clear gap between arms and torso, palms facing forward",
               "640x1024"),
    "t-pose": ("standing in a symmetrical T-pose: both arms stretched straight out sideways, "
               "horizontal at shoulder height, palms facing down",
               "1024x1024"),
}
BUILDS = {
    "male": "an average-build adult male",
    "female": "an average-build adult female",
    "stocky": "a short, stocky, broad and heavy-set",
}
BASE = ("Full body front view of {build} smooth featureless matte light grey clay mannequin, "
        "{pose}, all five fingers on each hand spread wide apart and clearly separated, "
        "legs straight with feet shoulder-width apart, looking straight ahead. "
        "Entire figure from head to feet in frame and centered, orthographic front camera, "
        "plain flat white background, soft even diffuse lighting, no shadows, no reflections, "
        "no text, no watermark.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "candidates"))
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--only", nargs="*", help="e.g. a-pose-male t-pose-male")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    combos = [("a-pose", b) for b in BUILDS] + [("t-pose", "male"), ("t-pose", "female")]
    for pose, build in combos:
        if a.only and f"{pose}-{build}" not in a.only:
            continue
        desc, size = POSES[pose]
        prompt = BASE.format(build=BUILDS[build], pose=desc)
        for seed in range(1, a.seeds + 1):
            t = time.time()
            png = lemon.generate_image(prompt, size=size, seed=seed)[0]
            path = os.path.join(a.out_dir, f"{pose}-{build}-s{seed}.png")
            open(path, "wb").write(png)
            print(f"{path} ({time.time() - t:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
