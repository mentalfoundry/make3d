# Lemonade (the model server)

The pipeline does no AI itself: it calls a [Lemonade](https://github.com/lemonade-sdk/lemonade)
server for three models. That server can be the same machine or another one on your network.
Tested with **Lemonade 2026.47.1**. The 3D endpoint (`/api/v1/3d/generations`) is recent,
so use that version or newer.

| Model | Used for | Needed by | Download |
|---|---|---|---|
| `Flux-2-Klein-4B` | Concept images and pose redraws | make3d, art3d | ~8 GB |
| `TRELLIS-3D` | Image to textured 3D mesh | make3d, art3d | ~15 GB |
| `Qwen3.6-35B-A3B-MTP-GGUF` (or any vision chat model) | Describing your art | art3d only, optional | ~22 GB |
| `MOSS-SoundEffect` | Sound effects | makesound, optional | ~6.7 GB |
| `ACE-Step-Music` | Music, with or without vocals | makesound --music, optional | ~10.5 GB |

`makevideo` doesn't use the Lemonade server: it runs `sd-cli` (from Lemonade's `sd-cpp`
Vulkan backend) directly on the machine running it, with its own models in `models/ltx2/`.

## 1. Install Lemonade

Follow the install instructions for your OS on the
[Lemonade releases page](https://github.com/lemonade-sdk/lemonade/releases) or its docs.
On Linux it installs a background service called `lemond` and a `lemonade` command.
Check it's running:

```sh
lemonade status
```

The models run on the GPU through Vulkan, so a recent GPU driver with Vulkan support
is needed (`vulkaninfo --summary` should list your GPU).

## 2. Download the models

```sh
lemonade pull Flux-2-Klein-4B
lemonade pull TRELLIS-3D
lemonade pull Qwen3.6-35B-A3B-MTP-GGUF     # optional, for art3d's description step
lemonade list --downloaded
```

They load on demand the first time the pipeline calls them (the first run is slower).

## 3. API key (recommended)

The "API key" is a password **you choose** for the server; nothing to sign up for.
Without one, anyone on your network who can reach the port can use your models.

1. Make one: `openssl rand -hex 24`
2. Give it to the server: on the Linux service, add `LEMONADE_API_KEY=<key>` to the
   service's environment file (`/etc/default/lemond` on the Fedora RPM; needs sudo),
   then `sudo systemctl restart lemond`. Keep a backup of that file: package upgrades
   may replace it.
3. Give it to the pipeline on every machine that runs make3d:
   ```sh
   mkdir -p ~/.config/lemonade
   printf '%s' '<key>' > ~/.config/lemonade/api-key    # printf: no trailing newline
   chmod 600 ~/.config/lemonade/api-key
   ```
   or `export LEMONADE_API_KEY=<key>`. Never commit it to git.

With no key on the server, skip this: the scripts work without one.

## 4. Using a server on another machine

On the **server**: listen on the network, not just locally, and open the port.

```sh
lemonade config set host=0.0.0.0
sudo firewall-cmd --permanent --add-port=13305/tcp && sudo firewall-cmd --reload   # Fedora
```

On the **machine running make3d**:

```sh
export LEMONADE_URL=http://<server-name-or-ip>:13305/api/v1   # add to ~/.bashrc
```

Other optional overrides: `IMAGE_MODEL`, `MODEL_3D`, `CHAT_MODEL`.

## Memory

TRELLIS's CPU stage uses a lot of **system** RAM, and it varies with the image: a chest
prop peaked at 3.6 GB at resolution 512, but the same prop at 1024 went past 16 GB.
make3d prints the peak after each run. If the system runs out, Linux kills TRELLIS and Lemonade
restarts, unloading every model for everyone using it.
`tools/lemon.py` watches TRELLIS's RAM and unloads only TRELLIS above 14 GB
(`TRELLIS_MAX_RAM_GB`). The watchdog only works when Lemonade runs on the **same**
machine as make3d; with a remote server, keep props at `--resolution 512`.
Lemonade gives up on a 3D request after 30 minutes.

## Notes from the development machine (Fedora 45, AMD Strix Halo)

- TRELLIS-3D runs on Vulkan, so it works on AMD. (The ComfyUI TRELLIS.2 nodes are CUDA-only.)
- There was no Fedora 45 package yet, so the Fedora 44 RPM was used.
- Lemonade's bundled llama.cpp builds link OpenSSL 3, but Fedora 45 only ships OpenSSL 4.
  The Fedora 44 `libssl.so.3`/`libcrypto.so.3` were copied to `/opt/openssl3/lib64` and
  added to the `lemond` service with a systemd drop-in setting `LD_LIBRARY_PATH`.
  Symptom without it: chat models fail to load with an OpenSSL library error.
- The BIOS gives 96 GB to the GPU, leaving ~30 GB system RAM. Lemonade's auto-tuning only
  sees the 30 GB, so `ctx_size` was raised manually for chat models.
- Blender bakes on the CPU there (no HIP libraries); that's fine, baking takes ~15 s.
