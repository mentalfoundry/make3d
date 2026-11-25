"""Small helpers for calling Lemonade (Flux images and TRELLIS 3D) from scripts."""
import base64, json, mimetypes, os, uuid, urllib.request, urllib.error

LEMONADE = os.environ.get("LEMONADE_URL", "http://127.0.0.1:13305/api/v1")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "Flux-2-Klein-4B")
MODEL_3D = os.environ.get("MODEL_3D", "TRELLIS-3D")


def _key():
    key = os.environ.get("LEMONADE_API_KEY")
    if key:
        return key
    try:
        with open(os.path.expanduser("~/.config/lemonade/api-key")) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""   # server without an API key


def _post(path, body, content_type, timeout=3600):
    headers = {"Content-Type": content_type}
    if _key():
        headers["Authorization"] = f"Bearer {_key()}"
    req = urllib.request.Request(f"{LEMONADE}/{path}", data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        hint = (" (wrong or missing API key: see docs/lemonade.md)" if e.code == 401 else "")
        raise RuntimeError(f"{path}: HTTP {e.code}{hint}: {e.read().decode(errors='replace')[:2000]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"can't reach Lemonade at {LEMONADE} ({e.reason}). Is it running? "
                           "For a server on another machine set LEMONADE_URL.") from None


def _images_from(resp):
    return [base64.b64decode(d["b64_json"]) for d in json.loads(resp)["data"]]


def generate_image(prompt, size="640x1024", seed=42, n=1):
    """Text to image. Returns a list of PNG bytes."""
    body = json.dumps({"model": IMAGE_MODEL, "prompt": prompt, "size": size, "seed": seed,
                       "n": n, "response_format": "b64_json"}).encode()
    return _images_from(_post("images/generations", body, "application/json"))


def edit_image(prompt, images, size="640x1024", seed=42, n=1):
    """Edit/redraw using one or more reference image paths. Returns a list of PNG bytes."""
    boundary = uuid.uuid4().hex
    parts = []
    fields = {"model": IMAGE_MODEL, "prompt": prompt, "size": size, "seed": str(seed),
              "n": str(n), "response_format": "b64_json"}
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    field = "image[]" if len(images) > 1 else "image"
    for path in images:
        ctype = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as f:
            data = f.read()
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
                     f'filename="{os.path.basename(path)}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
                     + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    resp = _post("images/edits", b"".join(parts), f"multipart/form-data; boundary={boundary}")
    return _images_from(resp)


def unload(model):
    _post("unload", json.dumps({"model_name": model}).encode(), "application/json", timeout=120)


def _trellis_rss_gb():
    """Resident RAM of the TRELLIS backend process, in GB (0 if not running)."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if b"trellis-server" not in f.read():
                    continue
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1e6
        except OSError:
            continue
    return 0.0


def image_to_3d(image_path, resolution=1024, bg="birefnet", uv="xatlas", seed=42, max_ram_gb=None):
    """Image to textured mesh. Returns .glb bytes.

    The machine has only ~30 GB of system RAM (the rest is GPU memory), and some
    shapes make TRELLIS's CPU stage balloon until the kernel kills it, which also
    restarts Lemonade and unloads every model. A watchdog unloads just TRELLIS
    first if its RAM passes max_ram_gb (default: TRELLIS_MAX_RAM_GB or 14)."""
    import threading
    limit = max_ram_gb or float(os.environ.get("TRELLIS_MAX_RAM_GB", "14"))
    with open(image_path, "rb") as f:
        img = base64.b64encode(f.read()).decode()
    body = json.dumps({"model": MODEL_3D, "image": img, "resolution": resolution,
                       "bg_removal": bg, "uv": uv, "seed": seed}).encode()
    done, tripped, peak = threading.Event(), threading.Event(), [0.0]

    def watchdog():
        while not done.wait(2):
            rss = _trellis_rss_gb()
            peak[0] = max(peak[0], rss)
            if rss > limit:
                tripped.set()
                try:
                    unload(MODEL_3D)
                except Exception:
                    pass
                return

    threading.Thread(target=watchdog, daemon=True).start()
    try:
        data = _post("3d/generations", body, "application/json")
    except RuntimeError as e:
        if tripped.is_set():
            raise RuntimeError(f"TRELLIS used more than {limit:.0f} GB of RAM on this image and was "
                               f"stopped to protect the other models. Try --resolution 512, "
                               f"or a simpler image.") from None
        raise
    finally:
        done.set()
    print(f"  (TRELLIS peak RAM {peak[0]:.1f} GB)")
    if data[:4] != b"glTF":
        j = json.loads(data)
        data = base64.b64decode((j.get("data") or [{}])[0].get("b64_json") or j.get("b64_json"))
    return data


CHAT_MODEL = os.environ.get("CHAT_MODEL", "Qwen3.6-35B-A3B-MTP-GGUF")


def describe_image(image_path, question, max_tokens=800):
    """Ask the vision chat model about an image. Returns its text answer."""
    ctype = mimetypes.guess_type(image_path)[0] or "image/png"
    with open(image_path, "rb") as f:
        url = f"data:{ctype};base64,{base64.b64encode(f.read()).decode()}"
    body = json.dumps({"model": CHAT_MODEL, "max_tokens": max_tokens, "temperature": 0.2,
                       "chat_template_kwargs": {"enable_thinking": False},
                       "messages": [{"role": "user", "content": [
                           {"type": "image_url", "image_url": {"url": url}},
                           {"type": "text", "text": question}]}]}).encode()
    resp = json.loads(_post("chat/completions", body, "application/json", timeout=600))
    return resp["choices"][0]["message"]["content"].strip()


MUSIC_MODEL = os.environ.get("MUSIC_MODEL", "ACE-Step-Music")
SFX_MODEL = os.environ.get("SFX_MODEL", "MOSS-SoundEffect")


def generate_audio(prompt, model, duration=None, seed=42, lyrics=None, language=None):
    """Music (ACE-Step) or a sound effect from a text prompt. Returns WAV bytes."""
    body = {"model": model, "prompt": prompt, "seed": seed, "response_format": "wav"}
    if duration:
        body["duration"] = duration
    if lyrics:
        body["lyrics"] = lyrics
    if language:
        body["vocal_language"] = language
    data = _post("audio/generations", json.dumps(body).encode(), "application/json")
    if data[:4] == b"RIFF":
        return data
    j = json.loads(data)   # some backends wrap the file in JSON
    b64 = (j.get("data") or [{}])[0].get("b64_json") or j.get("b64_json") or j.get("audio")
    if not b64:
        raise RuntimeError(f"audio/generations: unexpected response: {str(j)[:300]}")
    return base64.b64decode(b64)
