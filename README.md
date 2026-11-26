# Offline game asset pipeline

Text description or your own art → concept image (Flux) → 3D model (TRELLIS.2) →
game-ready, rigged files (Blender). Plus short videos with sound (LTX-2.3), music and
sound effects. Everything runs locally against a
[Lemonade](https://github.com/lemonade-sdk/lemonade) server.

## Development hardware

Developed and tested on one machine only:

| | |
|---|---|
| Laptop | ASUS ProArt PX13 GoPro Edition (HN7306EAC) |
| CPU / GPU | AMD Ryzen AI MAX+ 395 with Radeon 8060S (Strix Halo), Vulkan |
| Memory | 128 GB unified: BIOS gives 96 GB to the GPU, ~30 GB to the system |
| OS | Fedora 45 (kernel 7.3) |
| Software | Lemonade 2026.47.1, Blender 5.2.4 LTS (portable) |

Lemonade and the pipeline ran on the same laptop. Smaller machines haven't been tried,
so minimum requirements are unknown.

## Requirements

**Pipeline machine** (runs `make3d`):
- Linux x86-64 with a desktop graphics stack: Blender renders on the GPU (views for the
  rigger and previews), so a headless server without a GPU won't work. macOS and Windows
  aren't supported.
- `git`, `curl`, `tar`, `xz`, `sha256sum`, Python 3.8+ (`setup.sh` checks they're installed).
- ~2 GB disk (Blender ~1 GB, Python packages ~0.6 GB) plus your results (~50 MB per model).

**Lemonade server** (can be the same machine):
- A GPU with Vulkan support and enough memory for Flux and TRELLIS together.
- ~16 GB of free system RAM for TRELLIS (see the memory note in [docs/lemonade.md](docs/lemonade.md)).
- ~25 GB disk for the models (~47 GB with the optional Qwen model for `art3d`).

**Video and sound** (optional): ~26 GB more disk and ~25 GB free GPU memory for
`makevideo` (runs on the pipeline machine, not through Lemonade); ~17 GB on the Lemonade
server for `makesound`'s music and sound-effect models.

**Licences:** the models have their own licences, separate from this repo's code. Check
them before shipping generated assets in a commercial game, especially
[LTX-2.3](https://huggingface.co/Lightricks/LTX-2.3), [Gemma](https://ai.google.dev/gemma/terms)
and each Lemonade model's page.

## Setup

1. **Lemonade:** install it, download the models and set an API key:
   [docs/lemonade.md](docs/lemonade.md). Skip if you already have a server.
2. **This repo:**
   ```sh
   git clone https://github.com/mentalfoundry/make3d.git ~/3d
   cd ~/3d && ./setup.sh
   ```
   `setup.sh` downloads portable Blender 5.2.4 to `~/apps` (checksum verified; or set
   `BLENDER=/path/to/blender` to use your own 5.2), checks Blender starts, builds `venv/`
   from Blender's Python, installs `requirements.txt`, downloads the MediaPipe pose models
   into `models/` and links `make3d` and `art3d` into `~/.local/bin`. Safe to re-run.
3. **Connect to Lemonade** if it's on another machine or uses a key:
   ```sh
   export LEMONADE_URL=http://<server>:13305/api/v1     # remote server only; add to ~/.bashrc
   printf '%s' '<key>' > ~/.config/lemonade/api-key      # if the server has a key
   ```
   Re-run `./setup.sh`: its last step lists each model as `ok` or `MISSING`.
4. **PATH:** if setup says `~/.local/bin` isn't on your PATH, add
   `export PATH="$HOME/.local/bin:$PATH"` to `~/.bashrc` and open a new terminal.
5. **First test** (~7 minutes; the first run also loads the models):
   ```sh
   make3d "a wooden treasure chest with iron bands" --prop
   ```
   It should end with `== done` and a `preview.png` in `~/3d/out/a-wooden-treasure-chest-with-iron-bands/`.

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

### Asset presets: `--preset`

Pick what kind of asset it is and the triangle budget, texture size, TRELLIS detail and
height follow. Anything you set yourself (`--tris`, `--tex`, `--resolution`, `--height`,
`--template`) overrides the preset.

```sh
make3d "an old fisherman with a pipe" --preset npc
make3d "a stone watchtower with a wooden roof" --preset structure-complex
make3d "a hero knight" --preset hero --tex 2048      # preset, but smaller textures
```

| Preset | For | Triangles | Texture | TRELLIS | Height | Rig |
|---|---|---|---|---|---|---|
| `hero` | Player character, close-ups | 60,000 | 2048 | 1024 | 1.8 m | yes |
| `side` | Companions, named characters | 30,000 | 2048 | 1024 | 1.8 m | yes |
| `npc` | Townsfolk, guards, crowds | 12,000 | 1024 | 1024 | 1.8 m | yes |
| `monster` | Humanoid monsters (orcs, trolls) | 40,000 | 2048 | 1024 | 2.2 m, stocky template | yes |
| `creature` | Non-humanoid beasts, dragons | 30,000 | 2048 | 1024 | 1.5 m | no |
| `prop-simple` | Cups, books, rocks, crates | 1,500 | 512 | 512 | 0.5 m | no |
| `prop-complex` | Hero weapons, detailed furniture | 10,000 | 2048 | 1024 | 1.0 m | no |
| `structure-simple` | Huts, walls, wells, stalls | 6,000 | 2048 | 1024 | 4 m | no |
| `structure-complex` | Towers, temples, gates | 40,000 | 2048 | 1024 | 10 m | no |

No preset means 30,000 triangles and 2048 textures. The numbers are for PC and console;
for mobile, halve the triangles and go one texture size down. Set `--height` to the real size:
the height defaults are only a starting point.

- **Creatures have no rig** because the auto-rigger only knows human skeletons.
  Rig them in Blender or with a tool that handles quadrupeds.
- **Texture detail is capped by TRELLIS, so no preset goes above 2048.** TRELLIS gives
  2048 textures at resolution 1024 and 1024 textures at 512. The bake copies colour from those,
  so `--tex 4096` gives a 2.5× bigger file with no new detail. Use 4096 only if you'll paint
  over or upscale the textures afterwards.
- **`--resolution 1536` wasn't worth it in testing.** It took about 14 minutes instead of
  5–8, and produced the same 2048 textures and about the same triangle count (~272k).
- **Structures are one mesh with one texture.** That's fine for a hut or a single building.
  Real game levels build large structures from modular, tiling pieces, so for a castle
  make the pieces (wall, tower, gate) separately.

## From your own art: `art3d`

```sh
art3d my-hero.png                 # A-pose (default)
art3d my-hero.png --pose t --name hero
art3d me.png --face                # match the face closely (see below)
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

## Video with sound: `makevideo`

```sh
makevideo "a campfire crackling at night, sparks rising, an owl hoots" --draft   # ~1.5 min preview
makevideo "waves crashing on rocks at sunset, seagulls" --seconds 8
makevideo "the knight draws his sword, metal scraping, wind" --image out/knight/concept.png
makevideo "a flower opens" --image bud.png --end-image flower.png
makevideo "the <knight> raises his sword, steel ringing" --refs ~/chars   # see below
```

LTX-2.3 (distilled 1.1) makes the picture **and a matching sound track** from one prompt,
so describe the sounds too. It runs through stable-diffusion.cpp's `sd-cli` (the engine
Lemonade uses for Flux) because Lemonade has no video endpoint yet.
Download the models once with `./setup.sh --video` (~26 GB in `models/ltx2/`).

- Default 768x512, 5 s at 24 fps, 8 steps. With `--image` the video takes the image's shape.
  `--draft` = 512x320, 2 s. Options: `--seconds`, `--size WxH`, `--fps`, `--seed`, `--no-audio`.
- Speed on the development laptop: 5 s at 480x800 in ~6 min; a 2 s draft in ~1.3 min.
  It uses ~25 GB of GPU memory and ~4 GB of system RAM. If the GPU is short, it unloads
  TRELLIS and Flux from Lemonade first (they reload on demand); other models are left alone.
- Output in `out/video/<name>/`: `.webm` (VP8 + Opus: browsers, Unity, VLC), `.ogv` (Theora:
  Godot's video player) and `.mp4` (H.264: Unreal, phones, editors). Fedora's ffmpeg has no
  H.264 encoder by default; `sudo dnf swap noopenh264 openh264` enables the `.mp4`.
- Clips are short and the model invents what isn't in the picture (the knight test grew a
  sword mid-clip). Make several with `--seed` and keep the best.
- Uses `SD_CLI` if set, else Lemonade's `/var/cache/lemonade/bin/sd-cpp/vulkan/sd-cli`
  (install Lemonade's `sd-cpp:vulkan` backend), else `sd-cli` on PATH.

## Long videos from a storyboard: `storyboard`

```sh
storyboard new knight-dragon "a knight fights a dragon on a cliff at sunset" --seconds 30
storyboard frames knight-dragon --only 3,4      # redraw keyframes you don't like
storyboard render knight-dragon --draft          # quick low-res pass to check the motion
storyboard render knight-dragon --music "epic orchestral battle music, drums, brass"
```

1. **new:** the chat model (Qwen) writes a shot list with a keyframe every `--every` seconds
   (default 5) plus one fixed description of the characters, setting and style. Keyframes
   are planned as moments caught **mid-movement**, each clearly different from the last.
   Flux draws keyframe 0 from text, then every later keyframe using keyframe 0 as a
   reference for appearance only. Each new keyframe is **checked by the vision model**
   against the previous one: if it barely moved, or two characters fused or one is drawn
   twice, it's redrawn with a new seed (up to `--tries`, default 3; the best is kept and
   weak ones are marked WEAK on `storyboard.png`). Edit `storyboard.json` and redraw
   with `frames` to fix anything else.
2. **render:** by default **continuous**: segment 0 runs from keyframe 0 to keyframe 1;
   every later segment *continues* the previous one, starting from its last 17 frames held
   fixed, and is guided toward its next keyframe (`--end-strength`, default 0.4). The
   overlap is cross-faded, so there's no pop at the joins. At 0.4 the video ends each
   segment *near* its keyframe rather than exactly on it, which keeps it moving. Higher
   values land closer but make segments rush to the keyframe and then hold still: at 0.8
   every segment of a 15 s test froze for 1.5–2.5 s at its end, and at 1.0 they snap.
   `--classic` pins every segment to its start and end keyframes instead (the old mode).
   `--music` adds an ACE-Step music bed under the scene sound (`--music-db`, default 14 dB below).
   Unchanged segments are reused; in continuous mode changing a segment also re-renders
   every segment after it (each continues the one before). `--only 2` re-renders
   segment 2 with a new seed.

Continuous mode needs a newer stable-diffusion.cpp than Lemonade bundles (its DGL
scripting mode): `./setup.sh --video` installs a pinned, checksum-verified build to
`~/apps/sd-cpp-master-1122-3b12781/` (or set `SD_CLI_DGL`). Without it, render falls
back to classic. Its video decode is tiled (an untiled decode overran the GPU driver's
time limit and came out as noise) and its audio decoder runs on the CPU (~30 s).

Output in `out/story/<name>/`: `key-NN.png`, `storyboard.png`, `<name>-full.webm/.ogv`
(or `-draft`). Time on the development laptop: keyframes ~1 min each (~2 min with
`--refs`, more if redrawn); segments ~3 min each as drafts (480x320) and ~6-7 min at full
quality (768x512), so 30 s ≈ 18 min draft, ≈ 40 min full.

**Your own characters (`--refs`):** name them in angle brackets and point at a folder with
an image per name. A/T-pose sheets from `make3d`/`art3d` (`out/<name>/concept.png`) work well.

```sh
ls ~/chars        # knight.png  dragon.png
storyboard new duel "a <knight> fights a <dragon> in a ruined castle at dusk" --refs ~/chars
```

**In the portal you don't need a folder:** type `<` in the storyboard's "What happens" box, or
in a video prompt, and pick a 3D model from your library. Its A-pose concept sheet becomes the
reference (or a front render, for models without one). Uploaded images still work for characters
that aren't in the library, and win over a library model with the same name. In a single video,
`<name>` makes Flux draw the start frame with that character (`makevideo --refs`), and LTX-2.3
animates from there. Flux tends to draw the end of the action ("raises his sword" gets a
raised sword), so describe how the shot starts. A start image you upload replaces that drawn frame.

The vision model describes each image and those descriptions replace the invented ones in
the plan and in every video prompt. Flux gets the character images as references for every
keyframe (copy the look, not the standing pose or white background), plus keyframe 0 for
the location. Images are copied into the storyboard's `refs/` folder. Keyframes take ~2 min
each with references. Characters can occasionally swap features (in testing, the knight once
got a dragon's head); redraw that keyframe with `frames --only N`.

What works and what doesn't:
- **Segments only move as much as the keyframes differ.** The planner and the keyframe
  check exist to prevent near-identical keyframes; if one still slips through, rewrite its
  `image` text in `storyboard.json` as a clearly different moment and redraw it.
- With `--refs`, later keyframes tend to keep keyframe 0's layout (the check catches the
  worst cases).
- Fast action brings some smeared or garbled frames mid-movement (limbs, wings), and
  props come and go (a sword can vanish for a second).
- A keyframe that changes the camera a lot is the hardest to reach smoothly. If a segment
  still jumps or stalls, lower `--end-strength`, or rewrite that keyframe closer to the one before.
- Each segment's sound is generated separately; joins are cross-faded and a music bed
  (`--music`) hides them further.

## Music and sound effects: `makesound`

```sh
makesound "a heavy wooden door creaking open in a stone castle" --seconds 4
makesound "sword clash, two steel blades" --seconds 2 --takes 3
makesound --music "calm tavern folk, lute and flute, loopable" --seconds 60
makesound --music "upbeat pop, female vocals" --lyrics song.txt --seconds 90
```

Through Lemonade's `/audio/generations`: sound effects with `MOSS-SoundEffect`, music with
`ACE-Step-Music` (instrumental, or sung with `--lyrics`: a file or text with `[verse]`,
`[chorus]` tags). Download them once: `lemonade pull MOSS-SoundEffect` / `lemonade pull ACE-Step-Music`.
Output in `out/sound/<name>/`: `.wav` and `.ogg` (Vorbis; small and loops cleanly in game engines).
A sound effect takes ~3 minutes on the development laptop, whatever its length.

## More make3d options

- `--options 4` makes 4 concept images and stops. Pick one, then
  `make3d --image ~/3d/out/<name>/concept-option-2.png --name <name>`
- `--image my-art.png` starts from your own concept art (front view, plain background;
  for characters: A- or T-pose, empty hands with fingers spread and gaps between them,
  or the hands get a single bone each).
- `--raw ~/3d/out/<name>/raw.glb --name <name>` redoes only the Blender steps (~1 min).
- `--preset npc` etc. (see [Asset presets](#asset-presets---preset)), or set `--tris 15000` / `--tex 1024` directly; `--height 2.4` for big creatures.
- `--resolution` 512 (props default), 1024 (characters default) or 1536. 1536 takes ~2× longer for little gain (see presets). A RAM watchdog stops TRELLIS above 14 GB so it can't crash Lemonade; if that happens, retry at 512.
- `--seed N` for a different take.
- `--no-rig` skips the skeleton.

**Matching the face (`--face`, or "Match the face closely" in the portal).** By default the
redraw keeps the outfit and colours but often gives a generic face, because the face is a small
part of a full-body picture. With `--face`:
- Flux also gets a 512 px close-up of the face (cropped using the pose detector's face points)
  and a detailed face description from the vision model.
- Each candidate's face is rated for likeness against the original (0–10) and ranked on that as
  well as on the pose.

In a test with the ranger art, likeness went from 7, 7 and 4 to 7, 8 and 7, and the hair colour
came out right where it hadn't before. It adds about a minute. `face-ref.png` and
`pose-option-N-face.png` are saved so you can compare. The 3D model still loses fine facial
detail, because TRELLIS sees the face at only a few dozen pixels. Use it on real people only with
their permission.

## Pose templates (`~/3d/templates/`)

`a-pose-male`, `a-pose-female`, `a-pose-stocky`, `t-pose-male`, `t-pose-female`.
The concept copies the template's pose **and body build**, so pick the closest one.
All were generated with Flux (`tools/make_templates.py`), so they're free to use.

**A-pose or T-pose?** Use the A-pose (the default) unless your engine or animation pack needs a T-pose.
- In an A-pose the shoulders are close to how they rest, so they deform better when animated.
- The A-pose figure is tall and narrow (640×1024), so TRELLIS gets more pixels on the body,
  face and hands. The arms-out T-pose needs a square 1024×1024 image, so the figure is smaller in it.
- The T-pose leaves the armpits fully open, which helps with very bulky arms or capes.
- Unity, Unreal and Mixamo retarget animations from either pose.
`templates/test-only/` (not in git) holds a watermarked stock image used for early testing: don't use it for real assets.

## The rig

52 bones with Mixamo-style names (`Hips`, `Spine`, `LeftArm`, `LeftHandIndex1`, …),
which Unity (Humanoid), Unreal (IK Retargeter) and Godot (SkeletonProfileHumanoid)
recognise, so you can retarget standard animation libraries onto it.
Body joints come from pose detection (MediaPipe) on a front render; fingers from the
mesh shape. If fingers can't be found (mittens, claws, fused fingers), the hand gets a
single bone. Check `<name>-joints.png` and pose it in Blender before relying on it.

## Web portal: `portal`

Everything below also runs from a browser, from any machine on your network: forms for
each tool, a job queue with live logs, a library with 3D, video and audio previews, and a
storyboard editor (edit keyframe and segment text, redraw single keyframes, render).

In the library you can rename or delete anything. Renaming changes the folder and the files named
after it (`knight.glb` becomes `hero.glb`), but not the textures, because the `.blend` files point
at them by name. Delete moves the item to the desktop Trash (`~/.local/share/Trash`) on the
portal machine, so it can be restored from the file manager. Anything a job is still working on
can't be renamed or deleted until the job finishes.

```sh
./setup.sh --portal                  # installs the make3d-portal user service (127.0.0.1:7870)
portal --set-password                # choose the login password (8+ characters)
systemctl --user start make3d-portal
```

Then open `http://localhost:7870/` on this machine.

- **Text to 3D and Art to 3D.** Text to 3D always draws its own concept from your
  description, with characters in an A-pose. Art to 3D starts from an image and asks what it is:
  - a character in any pose: redrawn onto a pose template, as with `art3d`
  - a character already in an A- or T-pose: used as-is
  - an object or structure: used as-is

  Concept and pose options (from "Concept options", or "Stop after the pose candidates")
  each get a **Make 3D from this** button, which opens Art to 3D with that image already chosen.
- **Video from a prompt or a storyboard.** The Video page makes one clip from a prompt, or
  switch to *From a storyboard* to render one of your storyboards into a multi-shot scene.
  That tab shows its keyframes and warns about missing or WEAK ones, with the same render options
  as the storyboard page (draft, keyframe strength, music bed, re-render segments).
- **Live updates.** The portal pushes job and storyboard changes to open pages over one
  server-sent-events connection (`/api/events`), so pages don't poll. The Jobs list, the job
  counter, the Library and storyboard pages update by themselves. Only a job's log is fetched
  every few seconds while you watch it.
- **Storyboard page.** It shows new keyframes, check results and rendered videos as soon as a
  job finishes, without a reload, leaving any text you're editing alone. A banner shows jobs
  working on that storyboard. Each keyframe has its own redraw panel:
  - Redrawing uses the text as currently typed, saving it first.
  - An optional one-off direction can be added.
  - *New drawing* draws the keyframe again from scratch.
  - *Adjust this image* edits the current keyframe as the direction says (e.g. "make the sky
    blood red"), keeping its composition.

  *Undo redraw* swaps back to the previous version; pressing it again swaps forward. On the
  command line: `storyboard frames NAME --only 2 --note "..." [--adjust]`.
- **Jobs** run one at a time because they share the GPU. They keep running if you close the
  browser, and their logs are kept in `out/portal/jobs/`. A job that was running when the
  portal restarted shows as *interrupted*.
- **Lemonade address and key** are set on the Settings page, which tests the connection and
  lists any models that are missing. Every job gets that address and key, so the portal can
  use a Lemonade server on another machine. Leave the address empty to use this machine;
  with no key saved, it uses `~/.config/lemonade/api-key`. Video always renders on the
  portal's own GPU (`makevideo` and `storyboard render` don't go through Lemonade).
- **Settings** are stored in `~/.config/make3d/portal.json` (readable by you only): the
  password hash, the Lemonade address and any key you saved.
- **3D previews** use Google's `<model-viewer>`, which the viewer's browser loads from
  cdn.jsdelivr.net. Without internet access the page offers the `.glb` file as a download instead.

**Reaching it from other machines.** The portal only listens on this machine. Put an HTTPS
reverse proxy in front of it, so the password and cookies aren't sent in clear. With Caddy:

```
https://your-host {
	redir /make3d /make3d/
	handle_path /make3d/* {
		reverse_proxy 127.0.0.1:7870 {
			header_up X-Forwarded-Prefix /make3d
		}
	}
}
```

The portal works under a path like `/make3d/` (set `X-Forwarded-Prefix` as above) or at the
root of its own site. On a trusted network you can serve it directly instead: set
`PORTAL_HOST=0.0.0.0 ./setup.sh --portal` and open the port in your firewall
(`sudo firewall-cmd --add-port=7870/tcp --permanent && sudo firewall-cmd --reload` on Fedora). It's
plain HTTP then.

If the service should keep running while you're logged out, run
`sudo loginctl enable-linger $USER` once.

## Tools (`~/3d/tools/`)

| Script | Runs in | Does |
|---|---|---|
| `make3d.py` | system Python | The whole chain (the `make3d` command) |
| `art3d.py` | system Python | Your art → posed concept → `make3d` (the `art3d` command) |
| `makevideo.py` | system Python | Prompt/image → video with sound via `sd-cli` (the `makevideo` command) |
| `storyboard.py` | system Python | Idea → shot list → keyframes → stitched long video (the `storyboard` command) |
| `makesound.py` | system Python | Music and sound effects via Lemonade (the `makesound` command) |
| `portal.py` | system Python | Web portal for all of the above (the `portal` command; page in `portal/`) |
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

## Troubleshooting

| Message | Fix |
|---|---|
| `can't reach Lemonade at …` | Lemonade isn't running (`lemonade status`), or `LEMONADE_URL` is wrong. A remote server needs `host=0.0.0.0` and the port open ([docs/lemonade.md](docs/lemonade.md)). |
| `HTTP 401 … API key` | The key in `~/.config/lemonade/api-key` doesn't match the server's. Write it with `printf`, not `echo` (no trailing newline). |
| `HTTP 404` … `model_not_found` | The model name isn't known to the server. Check `IMAGE_MODEL`/`MODEL_3D` aren't set to something else, and that the server's Lemonade is 2026.47.1 or newer. |
| `TRELLIS used more than 14 GB of RAM` | The watchdog stopped it to protect Lemonade. Retry with `--resolution 512` or a simpler image. |
| `make3d: command not found` | `~/.local/bin` isn't on your PATH (Setup step 4), or run `~/3d/bin/make3d`. |
| `Blender is missing system libraries` (setup) | Install the listed libraries with your package manager; usually `libX11`, `libXi`, `libXxf86vm`, `libXfixes`, `libXrender`, `libxkbcommon`, `libSM`, `mesa-libGL` / `libgl1` and `vulkan-loader` / `libvulkan1`. |
| `rig.py failed` … `no body found` | Pose detection couldn't see a person in the front view. Check `concept.png` is a full-body front view; try `--seed` for another concept, or `--no-rig`. |
| `could not separate 5 fingers; hand gets a single bone` | Fingers weren't spread in the concept. Try another `--seed` or `--options 4` and pick one with open hands. |
| art3d: `vision model unavailable` | The chat model isn't downloaded, or another chat model is loaded in its place. It carries on without a description; add details with `--hint`. |
| `rig.py failed` / `preview.py failed` with a GPU or OpenGL error | Blender couldn't render. It needs a working GPU driver and a desktop session; check `vulkaninfo --summary`. |
