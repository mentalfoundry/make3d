#!/usr/bin/env python3
"""Turn an image into a textured 3D model (.glb) with Lemonade's TRELLIS-3D.

Usage: img2glb.py IMAGE [-o OUT.glb] [--resolution 512|1024|1536]
                  [--bg threshold|birefnet] [--uv xatlas|box] [--seed N]
"""
import argparse, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lemon  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image")
    p.add_argument("-o", "--out")
    p.add_argument("--resolution", type=int, choices=[512, 1024, 1536], default=1024)
    p.add_argument("--bg", choices=["threshold", "birefnet"], default="birefnet")
    p.add_argument("--uv", choices=["xatlas", "box"], default="xatlas")
    p.add_argument("--seed", type=int, default=42)
    a = p.parse_args()

    out = a.out or os.path.splitext(a.image)[0] + f"-{a.resolution}.glb"
    t = time.time()
    print(f"Generating {out} (resolution {a.resolution}, bg {a.bg}, uv {a.uv}, seed {a.seed})...", flush=True)
    try:
        data = lemon.image_to_3d(a.image, resolution=a.resolution, bg=a.bg, uv=a.uv, seed=a.seed)
    except RuntimeError as e:
        sys.exit(str(e))
    with open(out, "wb") as f:
        f.write(data)
    print(f"Saved {out} ({len(data)/1e6:.1f} MB) in {time.time()-t:.0f}s")


if __name__ == "__main__":
    main()
