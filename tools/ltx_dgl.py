"""LTX-2.3 segment rendering through stable-diffusion.cpp's DGL scripting mode.

The plain sd-cli command line can only pin a start image and an end image. DGL exposes the
latent conditioning directly, which allows *continuing* a video: the last frames of the
previous segment are encoded and held fixed at the start of the new one, so movement carries
on through the join instead of restarting from a still. The next keyframe can be placed at
the end with adjustable strength (1 = exact, lower = a softer guide).

Needs an sd-cli with `-M dgl` (upstream master-1111 or newer, Nov 2026); Lemonade's bundled
copy is older. Set SD_CLI_DGL, or it uses the newest ~/apps/sd-cpp-master-*/sd-cli.
"""
import glob, os, re, subprocess, tempfile, time

import makevideo   # model paths, make_room()

CONTEXT_FRAMES = 17   # frames carried over from the previous segment (8k+1: 3 latent frames)


def find_dgl_cli():
    p = os.environ.get("SD_CLI_DGL")
    if p and os.path.exists(p):
        return p
    found = sorted(glob.glob(os.path.expanduser("~/apps/sd-cpp-master-*/sd-cli")),
                   key=lambda s: int(re.search(r"master-(\d+)", s).group(1)))
    for cand in reversed(found):
        if "dgl" in subprocess.run([cand, "--help"], capture_output=True, text=True,
                                   env=_env(cand)).stdout:
            return cand
    raise RuntimeError("no sd-cli with DGL support found. Download an upstream release "
                       "(master-1111 or newer) into ~/apps/sd-cpp-master-NNNN-xxxxxxx/ "
                       "or set SD_CLI_DGL (see README, 'Continuous segments').")


def _env(cli):
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = os.path.dirname(cli) + ":" + env.get("LD_LIBRARY_PATH", "")
    return env


def _q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_script(out_avi, prompt, W, H, frames, fps, steps, seed, start_image=None,
                 context_pngs=None, end_image=None, end_strength=1.0, audio=True):
    m = lambda k: os.path.join(makevideo.MODELS, makevideo.FILES[k])
    v = [f"model_path = {_q(m('diffusion'))}", f"text_path = {_q(m('llm'))}",
         f"connectors_path = {_q(m('connectors'))}", f"vae_path = {_q(m('vae'))}",
         f"audio_vae_path = {_q(m('audio_vae'))}", f"output_path = {_q(out_avi)}",
         f"prompt = {_q(prompt)}", f"seed = {seed}", 'rng_type = "cuda"', f"width = {W}",
         f"height = {H}", f"frames = {frames}", f"fps = {fps}", f"steps = {steps}",
         'method = "euler"', 'scheduler = "ltx2"', "audio_scale = 1",
         f"end_strength = {end_strength}"]
    body = []
    # Prefix: a start image, or the previous segment's last frames, held fixed (mask 0).
    if context_pngs:
        for k, p in enumerate(context_pngs):
            body.append(f"    c{k} = tensor::unsqueeze(image::to_f32(fit_canvas(image::load({_q(p)}), "
                        f"width, height, mode = \"nearest\"), channels = 3), axis = 2)")
        body.append("    prefix_pixels = tensor::concat([" +
                    ", ".join(f"c{k}" for k in range(len(context_pngs))) + "], axis = 2)")
    else:
        body.append(f"    prefix_pixels = tensor::unsqueeze(image::to_f32(fit_canvas(image::load("
                    f"{_q(start_image)}), width, height, mode = \"nearest\"), channels = 3), axis = 2)")
    body += [
        "    prefix_latent = vae_to_dit_latents(vae, vae_encode(vae, prefix_pixels, rng = rng, tiling = vt))",
        "    n_prefix = tensor::shape(prefix_latent)[2]",
        "    video_first = tensor::replace_slice(video_base, prefix_latent, axis = 2, start = 0)",
    ]
    n_prefix_frames = 1 + (len(context_pngs) - 1) // 8 if context_pngs else 1
    idx = ", ".join(str(k) for k in range(n_prefix_frames))
    # DGL bindings are immutable: mark every held latent frame in one call (0 = keep as given)
    body.append(f"    mask_first = tensor::index_fill(video_mask, axis = 2, indices = [{idx}], value = 0)")
    if end_image:
        body += [
            f"    last_pixels = tensor::unsqueeze(image::to_f32(fit_canvas(image::load({_q(end_image)}), "
            f"width, height, mode = \"nearest\"), channels = 3), axis = 2)",
            "    last_latent = vae_to_dit_latents(vae, vae_encode(vae, last_pixels, rng = rng, tiling = vt))",
            "    tail_mask = tensor::full([video_shape[0], video_shape[1], tensor::shape(last_latent)[2], 1, 1], 1 - end_strength)",
            "    video_conditioned = tensor::concat([video_first, last_latent], axis = 2)",
            "    mask_conditioned = tensor::concat([mask_first, tail_mask], axis = 2)",
            "    positions = ltx::video_positions(video_first, keyframe_latent_frames = tensor::shape(last_latent)[2], "
            "keyframe_frame_index = frames - 1, keyframe_pixel_frames = 1, fps = fps)",
        ]
    else:
        body += ["    video_conditioned = video_first", "    mask_conditioned = mask_first"]
    pos_arg = ", video_positions = positions" if end_image else ""
    script = f"""# generated by make3d tools/ltx_dgl.py
var {{
    {chr(10).join('    ' + x for x in v).strip()}
}}

fn fit_canvas(input, width, height, mode) {{
    shape = tensor::shape(input)
    crop_width = math::min(shape[1], math::floor(shape[2] * width / height))
    crop_height = math::min(shape[2], math::floor(shape[1] * height / width))
    start = [math::floor((shape[1] - crop_width) / 2), math::floor((shape[2] - crop_height) / 2)]
    cropped = tensor::crop(input, axes = [1, 2], start = start, size = [crop_width, crop_height])
    return tensor::interpolate(cropped, size = [width, height], axes = [1, 2], mode = mode)
}}

fn pack_av(video, audio, audio_scale) {{
    shape = tensor::shape(video)
    count = tensor::numel(audio)
    tail_channels = math::ceil_div(count, shape[0] * shape[1] * shape[2])
    tail_shape = [shape[0], shape[1], shape[2], tail_channels, 1]
    flat = tensor::reshape(tensor::mul(audio, audio_scale), [count])
    padded = tensor::pad(flat, [0, math::product(tail_shape) - count])
    return tensor::concat([video, tensor::reshape(padded, tail_shape)], axis = 3)
}}

fn unpack_audio(packed, channels, audio_shape, audio_scale) {{
    tail = packed[:, :, :, channels:]
    flat = tensor::reshape(tail, [tensor::numel(tail)])
    return tensor::div(tensor::reshape(flat[:math::product(audio_shape)], audio_shape), audio_scale)
}}

fn main() {{
    model = load_dit(model_path, family = "ltxav")
    text = load_text_encoder(text_path, connectors_path = connectors_path, family = "ltxav", projection_path = model_path)
    vae = load_vae(vae_path, family = "ltxav")
    rng = make_rng(rng_type, seed = seed)
    # Tiled VAE, like the CLI's --vae-tiling. Untiled, one huge decode outlasted the GPU
    # driver's time limit: the kernel reset the queue and the video came out as noise.
    vt = tiling(temporal_tiling = true)
    video_base = tensor::zeros(latent_shape(vae, width = width, height = height, frames = frames))
    video_shape = tensor::shape(video_base)
    video_mask = tensor::full([video_shape[0], video_shape[1], video_shape[2], 1, 1], 1)
{chr(10).join(body)}
    audio_vae = load_audio_vae(audio_vae_path, family = "ltxav", backend = "cpu")   # its GPU path hung on RADV (build 1122)
    audio_shape = audio_latent_shape(audio_vae, frames = frames, fps = fps)
    base = pack_av(video_conditioned, tensor::zeros(audio_shape), audio_scale = audio_scale)
    packed_shape = tensor::shape(base)
    full_video_mask = tensor::broadcast_to(mask_conditioned, tensor::shape(video_conditioned))
    packed_mask = tensor::concat([full_video_mask, tensor::full([packed_shape[0], packed_shape[1], packed_shape[2], packed_shape[3] - video_shape[3], 1], 1)], axis = 3)
    positive = encode_prompt(text, prompt, width = width, height = height)
    guided = predictor(model, positive, audio_length = audio_shape[1], fps = fps{pos_arg})
    sigmas = make_sigmas(guided, latent = base, steps = steps, scheduler = scheduler)
    start = add_noise(guided, base, tensor::randn_like(base, rng = rng), sigma = sigmas[0])
    latent = sample(start, sigmas, predict = guided, method = method, rng = rng, base = base, denoise_mask = packed_mask, mask_input = true)
    video_latent = latent[:, :, :video_shape[2], :video_shape[3]]
    output_frames = vae_decode(vae, dit_to_vae_latents(vae, video_latent), tiling = vt)
    sound_latent = unpack_audio(latent, channels = video_shape[3], audio_shape = audio_shape, audio_scale = audio_scale)
    audio = audio_vae_decode(audio_vae, sound_latent)
    video::save(output_path, {{frames = output_frames, fps_num = fps, fps_den = 1}}, audio = audio)
}}
"""
    return script


def last_frames_as_png(video, n, out_dir):
    """Extract the last n frames of a video as PNGs (in order)."""
    os.makedirs(out_dir, exist_ok=True)
    for f in glob.glob(os.path.join(out_dir, "ctx-*.png")):
        os.remove(f)
    count = int(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                                "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", video],
                               capture_output=True, text=True).stdout.strip())
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-vf",
                    f"select='gte(n\\,{count - n})'", "-fps_mode", "passthrough",
                    os.path.join(out_dir, "ctx-%03d.png")], check=True)
    pngs = sorted(glob.glob(os.path.join(out_dir, "ctx-*.png")))
    if len(pngs) != n:
        raise RuntimeError(f"expected {n} context frames from {video}, got {len(pngs)}")
    return pngs


def render(out_avi, prompt, W, H, frames, fps=24, steps=8, seed=42, start_image=None,
           context_pngs=None, end_image=None, end_strength=1.0, check_only=False):
    """Render one segment to `out_avi` (MJPEG/RGB AVI with PCM audio). Returns seconds taken."""
    makevideo.check_models()
    cli = find_dgl_cli()
    script = build_script(out_avi, prompt, W, H, frames, fps, steps, seed, start_image=start_image,
                          context_pngs=context_pngs, end_image=end_image, end_strength=end_strength)
    dgl = os.path.splitext(out_avi)[0] + ".dgl"
    open(dgl, "w").write(script)
    cmd = [cli, "-M", "dgl", "--script", dgl] + (["--check"] if check_only else [])
    if not check_only:
        makevideo.make_room()
    t = time.time()
    log_path = os.path.splitext(out_avi)[0] + ".log"
    with open(log_path, "w") as log:
        r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=_env(cli))
    if r.returncode != 0 or (not check_only and not os.path.exists(out_avi)):
        tail = open(log_path, errors="replace").read()[-2500:]
        raise RuntimeError(f"sd-cli (DGL) failed (exit {r.returncode}). Last lines of {log_path}:\n{tail}")
    return time.time() - t
