# Lemonade (the model server)

The pipeline does no AI itself: it calls a [Lemonade](https://github.com/lemonade-sdk/lemonade)
server for three models. That server can be this machine or another one on your network.

| Model | Used for | Needed by |
|---|---|---|
| `Flux-2-Klein-4B` | Concept images and pose redraws (`/images/generations`, `/images/edits`) | make3d, art3d |
| `TRELLIS-3D` | Image to textured mesh (`/3d/generations`) | make3d, art3d |
| `Qwen3.6-35B-A3B-MTP-GGUF` (any vision chat model) | Describing your art (`/chat/completions`) | art3d only; optional |

Download them on the server with `lemonade pull <name>` (TRELLIS is ~15 GB, Flux ~8 GB,
Qwen ~22 GB). They load on demand.

## Pointing at the server

The scripts read:

- `LEMONADE_URL`: default `http://127.0.0.1:13305/api/v1`. For a server elsewhere on
  your network: `export LEMONADE_URL=http://<server>:13305/api/v1`
  (the server needs `host=0.0.0.0` in its config and its port open in the firewall).
- the API key: `LEMONADE_API_KEY`, or the file `~/.config/lemonade/api-key`
  (no trailing newline: a newline breaks the auth header). Never commit it.
- optional overrides: `IMAGE_MODEL`, `MODEL_3D`, `CHAT_MODEL`.

## Memory

TRELLIS's CPU stage can use a lot of **system** RAM (one prop at resolution 1024 went
past 16 GB). On a unified-memory machine where most memory is given to the GPU, that can
get TRELLIS killed and restart Lemonade, unloading every model for everyone using it.
`tools/lemon.py` watches TRELLIS's RAM and unloads only TRELLIS above 14 GB
(`TRELLIS_MAX_RAM_GB`). The watchdog only works when Lemonade runs on the **same**
machine as make3d; with a remote server, keep props at `--resolution 512`.
Lemonade also gives up on a 3D request after 30 minutes.

## Notes from the original machine (Fedora 45, AMD Strix Halo)

- TRELLIS-3D runs on Vulkan, so it works on AMD. The ComfyUI TRELLIS.2 nodes are CUDA-only.
- Lemonade's bundled llama.cpp builds link OpenSSL 3. Fedora 45 only ships OpenSSL 4, so
  OpenSSL 3 libraries from Fedora 44 were placed in `/opt/openssl3/lib64` and added with a
  systemd drop-in (`LD_LIBRARY_PATH`) for the `lemond` service.
- Blender bakes on the CPU there (no HIP libraries); that's fine, baking takes ~15 s.
