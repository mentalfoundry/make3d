# Offline 3D asset pipeline

Text description or your own art → concept image (Flux) → 3D model (TRELLIS.2) →
game-ready, rigged files (Blender). Everything runs locally against a
[Lemonade](https://github.com/lemonade-sdk/lemonade) server.

## Setup (new machine)

Linux x86-64 with `git`, `curl` and Python 3.8+.

```sh
git clone https://github.com/mentalfoundry/make3d.git ~/3d
cd ~/3d && ./setup.sh
```

`setup.sh` downloads portable Blender 5.2.4 to `~/apps` (or uses `$BLENDER`), builds
`venv/` from Blender's Python, installs the packages in `requirements.txt`, downloads
the MediaPipe pose models into `models/`, links `make3d` and `art3d` into
`~/.local/bin`, then checks Lemonade has the models it needs. It's safe to re-run.

You also need a Lemonade server with Flux-2-Klein-4B and TRELLIS-3D, on this machine or
another one: see [docs/lemonade.md](docs/lemonade.md). Put its API key in
`~/.config/lemonade/api-key`, and for a remote server set `LEMONADE_URL`.

Not in git: `out/` (your results), `venv/`, `models/`, `tests/`.

## Quick start

```sh
make3d "a knight in silver plate armour with a blue tabard"
make3d "a stocky goblin in ragged leather" --template a-pose-stocky
make3d "a sorceress in a long purple robe" --template a-pose-female
make3d "a wooden treasure chest with iron bands" --prop
```

Results go to `out/<name>/` in the repo folder (`~/3d/out/<name>/`):

| File | What it is |
|---|---|
| `concept.png` | The Flux concept image TRELLIS worked from |
| `raw.glb` | TRELLIS output (≈290k triangles, not game-ready) |
| `<name>.glb` / `.fbx` | Final model: 30k triangles, 2048 textures, rigged (characters) |
| `<name>.blend` | Same, for editing in Blender |
| `unrigged/` | Game-ready but without a skeleton, plus the texture PNGs |
| `preview.png` | Front, ¾, side and back renders |
| `<name>-joints.png` | Where the rigger found the joints (for checking) |

## From your own art: `art3d`

```sh
art3d my-hero.png                 # A-pose (default)
art3d my-hero.png --pose t --name hero
art3d my-hero.png --stop          # just make and score the pose candidates
art3d my-hero.png --hint "no weapon; the cloak is dark green"
```

Your art can be in any pose, angle or style, with a background. `art3d`:

1. asks the vision chat model (Qwen) to describe the character and pick the closest
   body build (male, female or stocky; saved as `description.txt`),
2. has Flux redraw your character onto that pose template (3 candidates, `--options N`),
3. scores each candidate with pose detection against the template and checks the hands,
4. sends the best one through the normal `make3d` chain.

`pose-options.png` shows your art next to every candidate. To use a different one:
`make3d --image ~/3d/out/<name>/pose-option-2.png --name <name>`.
Use `--hint` to correct the description; `--template` to force a template.
`make3d` options such as `--resolution`, `--height` and `--no-rig` are passed through.
Things held in the hands are dropped (they'd fuse with the fingers). The body follows the
template's height, so a dwarf comes out man-sized: use `--height 1.3`.
Expect about 3 extra minutes for the candidates.

A character takes about 7 minutes (~40 s image, ~5 min TRELLIS, ~1 min Blender).

## Useful options

- `--options 4` makes 4 concept images and stops. Pick one, then
  `make3d --image ~/3d/out/<name>/concept-option-2.png --name <name>`
- `--image my-art.png` starts from your own concept art (front view, plain background;
  for characters: A- or T-pose, empty hands with fingers spread and gaps between them,
  or the hands get a single bone each).
- `--raw ~/3d/out/<name>/raw.glb --name <name>` redoes only the Blender steps (~1 min).
- `--tris 15000` / `--tex 1024` for lighter models; `--height 2.4` for big creatures.
- `--resolution` 512 (props default), 1024 (characters default) or 1536. Higher = more detail, time and RAM. A RAM watchdog stops TRELLIS above 14 GB (system RAM is only ~30 GB) so it can't crash Lemonade; if that happens, retry at 512.
- `--seed N` for a different take.
- `--no-rig` skips the skeleton.

## Pose templates (`~/3d/templates/`)

`a-pose-male`, `a-pose-female`, `a-pose-stocky`, `t-pose-male`, `t-pose-female`.
The concept copies the template's pose **and body build**, so pick the closest one.
All were generated with Flux (`tools/make_templates.py`), so they're free to use.
`templates/test-only/` (not in git) holds a watermarked stock image used for early testing: don't use it for real assets.

## The rig

52 bones with Mixamo-style names (`Hips`, `Spine`, `LeftArm`, `LeftHandIndex1`, …),
which Unity (Humanoid), Unreal (IK Retargeter) and Godot (SkeletonProfileHumanoid)
recognise, so you can retarget standard animation libraries onto it.
Body joints come from pose detection (MediaPipe) on a front render; fingers from the
mesh shape. If fingers can't be found (mittens, claws, fused fingers), the hand gets a
single bone. Check `<name>-joints.png` and pose it in Blender before relying on it.

## Tools (`~/3d/tools/`)

| Script | Runs in | Does |
|---|---|---|
| `make3d.py` | system Python | The whole chain (the `make3d` command) |
| `art3d.py` | system Python | Your art → posed concept → `make3d` (the `art3d` command) |
| `lemon.py` | system Python | Calls Lemonade: Flux generate/edit, TRELLIS |
| `img2glb.py` | system Python | Just image → raw .glb |
| `gameready.py` | Blender | Scale, voxel rebuild, decimate, xatlas UVs, texture transfer, export |
| `rig.py` | Blender | Skeleton, finger detection, skin weights, export |
| `detect_joints.py` | `~/3d/venv` | MediaPipe body/hand joints on an image |
| `xatlas_unwrap.py` | `~/3d/venv` | Overlap-free UV unwrap |
| `preview.py` | Blender | 4-view turnaround render |
| `pose_test.py` | Blender | Renders a rig with the skeleton drawn + a test pose |
| `open_in_blender.py` | Blender GUI | Open a model ready to inspect |
| `make_templates.py` | system Python | Generate new pose template candidates |

Blender 5.2.4 LTS (portable) is at `~/apps/blender` (override with `BLENDER=/path/to/blender`).
The venv uses Blender's Python 3.13 because MediaPipe doesn't support newer Pythons yet.
The scripts find the venv, models and templates relative to the repo, so it can live anywhere;
the docs assume `~/3d`.
