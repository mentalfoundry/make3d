"""make3d web portal: run make3d, art3d, makevideo, makesound and storyboard from a browser.

  portal --set-password        choose the portal password (needed once)
  portal                       serve on 127.0.0.1:7870 (put a reverse proxy in front for the LAN)
  portal --host 0.0.0.0        serve to the LAN directly (plain HTTP)

Jobs run one at a time (they share the GPU) and keep running if the browser closes. The
Lemonade server address and API key are set on the Settings page and passed to every job,
so the tools can use a Lemonade on another machine. Video rendering always runs here.

Settings live in ~/.config/make3d/portal.json (or $MAKE3D_PORTAL_CONFIG), readable by you only.
Uploads and job logs go in out/portal/.
"""
import argparse, base64, getpass, hashlib, hmac, http.server, json, mimetypes, os, queue, re
import secrets, shutil, signal, socketserver, subprocess, sys, threading, time
import urllib.error, urllib.parse, urllib.request

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
OUT = os.path.join(ROOT, "out")
PORTAL_OUT = os.path.join(OUT, "portal")
JOBS_DIR = os.path.join(PORTAL_OUT, "jobs")
UPLOADS = os.path.join(PORTAL_OUT, "uploads")
STATIC = os.path.join(ROOT, "portal")
CONFIG = os.environ.get("MAKE3D_PORTAL_CONFIG", os.path.expanduser("~/.config/make3d/portal.json"))
DEFAULT_LEMONADE = "http://127.0.0.1:13305/api/v1"
LEMONADE_KEY_FILE = os.path.expanduser("~/.config/lemonade/api-key")
SESSION_DAYS = 30
MAX_BODY = 200 * 1024 * 1024
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
NAMED_DIRS = {"video", "sound", "story", "portal"}   # out/<x> that are not 3D models

sys.path.insert(0, TOOLS)
import lemon   # noqa: E402  (model names only; requests go through the configured URL)
from presets import PRESETS, DEFAULTS  # noqa: E402

MODELS_NEEDED = {
    lemon.IMAGE_MODEL: "concept art, keyframes",
    lemon.MODEL_3D: "image to 3D",
    lemon.CHAT_MODEL: "vision checks, storyboard planning",
    lemon.MUSIC_MODEL: "music",
    lemon.SFX_MODEL: "sound effects",
}

for mt, ext in (("model/gltf-binary", ".glb"), ("video/webm", ".webm"), ("video/ogg", ".ogv"),
                ("audio/ogg", ".ogg"), ("audio/wav", ".wav"), ("video/x-matroska", ".mkv"),
                ("text/plain", ".log"), ("text/plain", ".dgl"), ("image/webp", ".webp")):
    mimetypes.add_type(mt, ext)


# ---------------------------------------------------------------- settings

_cfg_lock = threading.Lock()


def load_config():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=1)
    os.replace(tmp, CONFIG)


def hash_password(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1).hex()
    return f"scrypt${salt}${h}"


def check_password(pw, stored):
    try:
        _, salt, _ = stored.split("$")
    except (AttributeError, ValueError):
        return False
    return hmac.compare_digest(hash_password(pw, salt), stored)


def lemonade_env(cfg):
    """Environment for a job: the configured Lemonade URL and key."""
    env = {"LEMONADE_URL": cfg.get("lemonade_url") or DEFAULT_LEMONADE}
    if cfg.get("lemonade_key"):
        env["LEMONADE_API_KEY"] = cfg["lemonade_key"]
    return env


def key_source(cfg):
    if cfg.get("lemonade_key"):
        return "portal"
    if os.environ.get("LEMONADE_API_KEY"):
        return "env"
    return "file" if os.path.exists(LEMONADE_KEY_FILE) else "none"


def effective_key(cfg):
    if cfg.get("lemonade_key"):
        return cfg["lemonade_key"]
    if os.environ.get("LEMONADE_API_KEY"):
        return os.environ["LEMONADE_API_KEY"]
    try:
        return open(LEMONADE_KEY_FILE).read().strip()
    except OSError:
        return ""


def test_lemonade(url, key):
    url = url.rstrip("/")
    req = urllib.request.Request(url + "/models", headers={"Authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        msg = "wrong or missing API key" if e.code == 401 else f"HTTP {e.code}"
        return {"ok": False, "error": msg}
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "error": f"can't connect: {getattr(e, 'reason', e)}"}
    except ValueError:
        return {"ok": False, "error": "that address answered, but not like a Lemonade server (expected .../api/v1)"}
    ids = {m.get("id") for m in data.get("data", [])}
    return {"ok": True, "models": [{"id": m, "use": use, "present": m in ids} for m, use in MODELS_NEEDED.items()]}


def local_check():
    """What runs on this machine whatever Lemonade is used: video (LTX-2.3 via sd-cli), Blender, ffmpeg."""
    import makevideo, ltx_dgl   # imported here: only needed for this page
    items = []

    def add(name, use, ok, detail="", fix=""):
        items.append({"name": name, "use": use, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})

    uses = {"diffusion": "LTX-2.3 video model", "connectors": "LTX-2.3 text connectors",
            "vae": "LTX-2.3 video decoder", "audio_vae": "LTX-2.3 audio decoder",
            "llm": "Gemma 3 prompt encoder for LTX-2.3"}
    for key, f in makevideo.FILES.items():
        p = os.path.join(makevideo.MODELS, f)
        size = os.path.getsize(p) if os.path.isfile(p) else 0
        add(f, uses.get(key, "video"), size > 0, f"{size / 2**30:.1f} GB" if size else "", "./setup.sh --video")
    try:
        cli = makevideo.find_sd_cli()
    except SystemExit:
        cli = None
    add("sd-cli", "renders video", cli, cli or "", "comes with Lemonade's sd-cpp Vulkan backend, or set SD_CLI")
    try:
        dgl = ltx_dgl.find_dgl_cli()
    except (RuntimeError, OSError):
        dgl = None
    add("sd-cli with DGL", "storyboard continuous mode", dgl, dgl or "", "./setup.sh --video")
    blender = os.environ.get("BLENDER") or os.path.expanduser("~/apps/blender/blender")
    add("Blender", "3D clean-up, textures, rigging", os.access(blender, os.X_OK), blender, "./setup.sh")
    ff = shutil.which("ffmpeg")
    add("ffmpeg", "video and sound encoding", ff, ff or "", "install ffmpeg with your package manager")
    free = makevideo.gpu_free_gb()
    return {"items": items, "gpu_free_gb": round(free, 1) if free is not None else None,
            "gpu_needed_gb": makevideo.NEED_GPU_GB}


# ---------------------------------------------------------------- jobs

def slug(text, n=40):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:n]


class BadRequest(Exception):
    pass


def _s(form, k, required=False, maxlen=4000):
    v = form.get(k)
    v = "" if v is None else str(v).strip()
    if required and not v:
        raise BadRequest(f"'{k}' is required")
    if len(v) > maxlen:
        raise BadRequest(f"'{k}' is too long")
    return v


def _num(form, k, kind, lo, hi):
    v = form.get(k)
    if v in (None, ""):
        return None
    try:
        v = kind(v)
    except (TypeError, ValueError):
        raise BadRequest(f"'{k}' must be a number")
    if not lo <= v <= hi:
        raise BadRequest(f"'{k}' must be between {lo} and {hi}")
    return v


def _save_upload(up, dest_dir, stem):
    if not isinstance(up, dict) or "data" not in up:
        raise BadRequest("bad upload")
    ext = os.path.splitext(str(up.get("filename", "")).lower())[1]
    if ext not in IMAGE_EXT:
        raise BadRequest(f"{up.get('filename')}: use a png, jpg or webp image")
    data = base64.b64decode(str(up["data"]).split(",", 1)[-1])
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, stem + ext)
    with open(path, "wb") as f:
        f.write(data)
    return path


def templates():
    d = os.path.join(ROOT, "templates")
    return sorted(os.path.splitext(f)[0] for f in os.listdir(d) if f.lower().endswith(IMAGE_EXT)) \
        if os.path.isdir(d) else []


def build_job(tool, form, job_id):
    """Turn a form into (script, argv, title, output dir relative to out/). Validates everything."""
    up_dir = os.path.join(UPLOADS, job_id)
    args, flag = [], lambda k, f: args.append(f) if form.get(k) else None

    def opt(k, f, kind=None, lo=None, hi=None, choices=None):
        v = _num(form, k, kind, lo, hi) if kind else _s(form, k)
        if v in (None, ""):
            return
        if choices and v not in choices:
            raise BadRequest(f"'{k}' must be one of {', '.join(map(str, choices))}")
        args.extend([f, str(v)])

    if tool == "make3d":
        desc = _s(form, "description")
        if not desc:
            raise BadRequest("describe what to make (to start from an image, use Art to 3D)")
        name = slug(_s(form, "name") or desc or "model")
        if not name or name in NAMED_DIRS:
            raise BadRequest("choose another name")
        if desc:
            args.append(desc)
        args += ["--name", name]
        opt("preset", "--preset", choices=list(PRESETS))
        flag("prop", "--prop")
        opt("template", "--template", choices=templates())
        opt("options", "--options", int, 1, 6)
        opt("seed", "--seed", int, 0, 2 ** 31)
        opt("resolution", "--resolution", int, 512, 1536, choices=[512, 1024, 1536])
        opt("height", "--height", float, 0.05, 50)
        opt("tris", "--tris", int, 500, 500000)
        opt("tex", "--tex", int, 256, 8192, choices=[512, 1024, 2048, 4096])
        flag("no_rig", "--no-rig")
        return "make3d", args, f"3D: {desc}", name
    if tool == "art3d":
        # mode: "pose" = character art in any pose, redrawn onto a template (art3d);
        #       "posed" = character already in an A/T-pose, used as-is; "object" = used as-is, no rig.
        mode = _s(form, "mode") or "pose"
        if mode not in ("pose", "posed", "object"):
            raise BadRequest("unknown mode")
        src = _s(form, "from")   # an image already in the library, e.g. knight/concept-option-2.png
        if src:
            p = safe_out_path(src)
            if not p or not os.path.isfile(p) or not p.lower().endswith(IMAGE_EXT):
                raise BadRequest("that image isn't in the library")
            default_name = os.path.relpath(p, OUT).split(os.sep)[0]
        elif form.get("art"):
            default_name = os.path.splitext(str(form["art"].get("filename", "")))[0]
        else:
            raise BadRequest("upload an image")
        name = slug(_s(form, "name") or default_name or "art")
        if not name or name in NAMED_DIRS:
            raise BadRequest("choose another name")
        if src and mode == "pose" and name == default_name and not os.path.basename(p).startswith("art."):
            raise BadRequest(f"give it a new name: redrawing into '{name}' would replace its art.png")
        image = p if src else _save_upload(form["art"], up_dir, "art")
        kind = "prop" if mode == "object" else "character"
        presets = [k for k, v in PRESETS.items() if v["kind"] == kind]
        if mode == "pose":
            args += [image, "--name", name]
            opt("pose", "--pose", choices=["a", "t"])
            opt("template", "--template", choices=templates())
            opt("hint", "--hint")
            opt("options", "--options", int, 1, 6)
            flag("stop", "--stop")
            flag("face", "--face")
        else:
            args += ["--image", image, "--name", name]
            if mode == "object":
                args.append("--prop")
        opt("preset", "--preset", choices=presets)
        opt("height", "--height", float, 0.05, 50)
        opt("tris", "--tris", int, 500, 500000)
        opt("tex", "--tex", int, 256, 8192, choices=[512, 1024, 2048, 4096])
        opt("seed", "--seed", int, 0, 2 ** 31)
        if mode == "pose":
            return "art3d", args, f"Art to 3D: {name}", name
        return "make3d", args, f"Art to 3D: {name} (" + ("object" if mode == "object" else "as-is") + ")", name
    if tool == "makevideo":
        prompt = _s(form, "prompt", required=True)
        name = slug(_s(form, "name") or re.sub(r"<([\w-]+)>", r"\1", prompt))
        args += [prompt, "--name", name]
        if form.get("image"):
            args += ["--image", _save_upload(form["image"], up_dir, "start")]
        elif placeholders(prompt):   # <knight>: Flux draws the start frame from the library model
            copy_library_refs(placeholders(prompt), os.path.join(up_dir, "refs"))
            args += ["--refs", os.path.join(up_dir, "refs")]
        if form.get("end_image"):
            if not form.get("image"):
                raise BadRequest("an end image needs a start image")
            args += ["--end-image", _save_upload(form["end_image"], up_dir, "end")]
        opt("seconds", "--seconds", float, 1, 20)
        size = _s(form, "size")
        if size:
            if not re.fullmatch(r"\d{3,4}x\d{3,4}", size):
                raise BadRequest("size looks like 768x512")
            args += ["--size", size]
        opt("seed", "--seed", int, 0, 2 ** 31)
        flag("no_audio", "--no-audio")
        flag("draft", "--draft")
        return "makevideo", args, f"Video: {prompt}", f"video/{name}"
    if tool == "makesound":
        prompt = _s(form, "prompt", required=True)
        name = slug(_s(form, "name") or prompt)
        args += [prompt, "--name", name]
        flag("music", "--music")
        lyrics = _s(form, "lyrics", maxlen=20000)
        if lyrics:
            if not form.get("music"):
                raise BadRequest("lyrics need Music turned on")
            os.makedirs(up_dir, exist_ok=True)
            lp = os.path.join(up_dir, "lyrics.txt")
            open(lp, "w").write(lyrics)
            args += ["--lyrics", lp]
        opt("language", "--language")
        opt("seconds", "--seconds", float, 1, 240)
        opt("takes", "--takes", int, 1, 5)
        opt("seed", "--seed", int, 0, 2 ** 31)
        return "makesound", args, f"{'Music' if form.get('music') else 'Sound'}: {prompt}", f"sound/{name}"
    if tool == "storyboard":
        step = _s(form, "step", required=True)
        name = slug(_s(form, "name", required=True))
        if not name:
            raise BadRequest("give the storyboard a name")
        if step == "new":
            idea = _s(form, "idea", required=True)
            args += ["new", name, idea]
            refs = form.get("refs") or []
            rdir, uploaded = os.path.join(up_dir, "refs"), set()
            for r in refs:
                stem = slug(os.path.splitext(str(r.get("filename", "")))[0], 30)
                if not stem:
                    raise BadRequest("name each reference image after its placeholder, e.g. knight.png")
                _save_upload(r, rdir, stem)
                uploaded.add(stem)
            names = placeholders(idea)
            if names:   # uploads win; the rest come from 3D models in the library
                copy_library_refs(names, rdir, have={u.lower() for u in uploaded})
            if names or refs:
                args += ["--refs", rdir]
            opt("seconds", "--seconds", float, 5, 120)
            opt("every", "--every", float, 2, 10)
            opt("style", "--style")
            opt("seed", "--seed", int, 0, 2 ** 31)
            opt("tries", "--tries", int, 1, 5)
            flag("force", "--force")
            title = f"Storyboard: {idea}"
        elif step == "frames":
            args += ["frames", name]
            only = _s(form, "only")
            if only:
                if not re.fullmatch(r"\d+(,\d+)*", only):
                    raise BadRequest("keyframes look like 2 or 1,3")
                args += ["--only", only]
            opt("seed", "--seed", int, 0, 2 ** 31)
            opt("tries", "--tries", int, 1, 5)
            note = _s(form, "note")
            if note:
                args += ["--note", note[:1000]]
            if form.get("adjust"):
                if not (only and note):
                    raise BadRequest("to adjust a keyframe, say what to change")
                args.append("--adjust")
            title = f"Keyframe {only or 'all'} {'adjust' if form.get('adjust') else 'redraw'}: {name}" + (f" ({note[:60]})" if note else "")
        elif step == "render":
            args += ["render", name]
            flag("draft", "--draft")
            only = _s(form, "only")
            if only:
                if not re.fullmatch(r"\d+(,\d+)*", only):
                    raise BadRequest("segments look like 2 or 1,3")
                args += ["--only", only]
            opt("music", "--music")
            opt("music_db", "--music-db", float, 0, 40)
            opt("end_strength", "--end-strength", float, 0, 1)
            flag("classic", "--classic")
            title = f"Render {'draft' if form.get('draft') else 'full'}: {name}"
        else:
            raise BadRequest("unknown storyboard step")
        return "storyboard", args, title, f"story/{name}"
    raise BadRequest("unknown tool")


class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}
        self.queue = queue.Queue()
        self.procs = {}
        os.makedirs(JOBS_DIR, exist_ok=True)
        for d in sorted(os.listdir(JOBS_DIR)):
            try:
                j = json.load(open(os.path.join(JOBS_DIR, d, "job.json")))
            except (OSError, ValueError):
                continue
            if j["status"] in ("queued", "running"):
                j["status"] = "interrupted"   # the portal restarted
                self._save(j)
            self.jobs[j["id"]] = j
        threading.Thread(target=self._worker, daemon=True).start()

    def _save(self, j):
        d = os.path.join(JOBS_DIR, j["id"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "job.json.tmp"), "w") as f:
            json.dump(j, f)
        os.replace(os.path.join(d, "job.json.tmp"), os.path.join(d, "job.json"))

    def log_path(self, jid):
        return os.path.join(JOBS_DIR, jid, "log.txt")

    def submit(self, tool, form):
        jid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        script, argv, title, outdir = build_job(tool, form, jid)
        j = {"id": jid, "tool": tool, "script": script, "args": argv, "title": title[:160],
             "outdir": outdir, "form": {k: v for k, v in form.items() if not isinstance(v, (dict, list))},
             "status": "queued", "created": time.time(), "started": None, "ended": None, "code": None}
        with self.lock:
            self.jobs[jid] = j
            self._save(j)
        self.queue.put(jid)
        return j

    def busy(self, outdir):
        with self.lock:
            return any(j["outdir"] == outdir and j["status"] in ("queued", "running") for j in self.jobs.values())

    def moved(self, old, new):
        """Point finished jobs at an item's new folder (None: it was deleted)."""
        with self.lock:
            for j in self.jobs.values():
                if j["outdir"] == old:
                    j["outdir"] = new if new else old
                    j["removed"] = new is None
                    self._save(j)

    def cancel(self, jid):
        with self.lock:
            j = self.jobs.get(jid)
            if not j:
                return None
            if j["status"] == "queued":
                j["status"], j["ended"] = "cancelled", time.time()
                self._save(j)
            elif j["status"] == "running" and jid in self.procs:
                j["cancel"] = True
                p = self.procs[jid]
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                threading.Timer(15, self._kill, args=(p,)).start()
            return j

    @staticmethod
    def _kill(p):
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _worker(self):
        while True:
            jid = self.queue.get()
            with self.lock:
                j = self.jobs.get(jid)
                if not j or j["status"] != "queued":
                    continue
                j["status"], j["started"] = "running", time.time()
                self._save(j)
            with _cfg_lock:
                cfg = load_config()
            env = dict(os.environ, **lemonade_env(cfg))
            # -u: unbuffered, so the log fills in as it goes (the launchers' -I would ignore
            # PYTHONUNBUFFERED)
            cmd = ["python3", "-I", "-u", os.path.join(TOOLS, j["script"] + ".py"), *j["args"]]
            with open(self.log_path(jid), "ab") as log:
                log.write(("$ " + " ".join(cmd[3:]) + "\n").encode())
                log.flush()
                try:
                    p = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, env=env, start_new_session=True)
                    with self.lock:
                        self.procs[jid] = p
                    code = p.wait()
                except OSError as e:
                    log.write(f"\ncould not start: {e}\n".encode())
                    code = -1
            with self.lock:
                self.procs.pop(jid, None)
                j["code"], j["ended"] = code, time.time()
                j["status"] = "cancelled" if j.get("cancel") else ("done" if code == 0 else "failed")
                self._save(j)

    def list(self):
        with self.lock:
            js = sorted(self.jobs.values(), key=lambda j: j["created"], reverse=True)
            return [dict(j) for j in js[:200]]

    def get(self, jid):
        with self.lock:
            j = self.jobs.get(jid)
            return dict(j) if j else None


# ---------------------------------------------------------------- outputs

def safe_out_path(rel):
    """Resolve a path under out/, refusing anything that escapes it."""
    p = os.path.realpath(os.path.join(OUT, urllib.parse.unquote(rel).lstrip("/")))
    if p != OUT and not p.startswith(OUT + os.sep):
        return None
    return p


def entry(path):
    st = os.stat(path)
    return {"name": os.path.basename(path), "dir": os.path.isdir(path), "size": st.st_size, "mtime": st.st_mtime}


def library():
    def items(base, kind):
        res = []
        if not os.path.isdir(base):
            return res
        for n in os.listdir(base):
            d = os.path.join(base, n)
            if not os.path.isdir(d) or (kind == "model" and n in NAMED_DIRS):
                continue
            files = sorted(os.listdir(d))
            rel = os.path.relpath(d, OUT)
            thumb = next((f for f in (f"{n}-front.png", "concept.png", "key-01.png", "key-00.png",
                                      "pose-option-1.png", "art.png", "preview.png") if f in files), None)
            res.append({"name": n, "path": rel, "kind": kind, "mtime": os.stat(d).st_mtime,
                        "thumb": f"{rel}/{thumb}" if thumb else None,
                        "files": [f for f in files if not f.startswith(".")]})
        return res
    allitems = (items(OUT, "model") + items(os.path.join(OUT, "video"), "video") +
                items(os.path.join(OUT, "sound"), "sound") + items(os.path.join(OUT, "story"), "story"))
    return sorted(allitems, key=lambda x: x["mtime"], reverse=True)


def model_refs():
    """3D models usable as <name> references in video prompts and storyboards: their A-pose
    concept sheet if there is one, otherwise a front render of the model."""
    res = []
    for x in library():
        if x["kind"] != "model":
            continue
        n, files = x["name"], x["files"]
        img = next((f for f in ("concept.png", "concept.jpg", "concept.jpeg", "concept.webp",
                                f"{n}-front.png", "preview.png") if f in files), None)
        if img and any(f.endswith(".glb") for f in files):
            res.append({"name": n, "image": f"{x['path']}/{img}"})
    return sorted(res, key=lambda r: r["name"])


def placeholders(text):
    return list(dict.fromkeys(re.findall(r"<([\w-]+)>", text or "")))


def copy_library_refs(names, dest, have=()):
    """Copy the library reference image for each <name> (not already uploaded) into dest."""
    refs = {r["name"]: r for r in model_refs()}
    missing = [n for n in names if n.lower() not in have and n.lower() not in refs]
    if missing:
        raise BadRequest(f"no 3D model called {', '.join('<' + m + '>' for m in missing)} in the library. "
                         "Type < to pick one, or rename a model to match")
    os.makedirs(dest, exist_ok=True)
    for n in names:
        if n.lower() not in have:
            src = os.path.join(OUT, refs[n.lower()]["image"])
            shutil.copy(src, os.path.join(dest, n + os.path.splitext(src)[1].lower()))


def library_item(path):
    """The library entry at this out/-relative path, or a 404-style error. Only top-level items."""
    path = str(path or "").strip("/")
    for x in library():
        if x["path"] == path:
            return x
    raise BadRequest("no such item in the library")


TRASH = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "Trash")


def move_to_trash(src):
    """Freedesktop trash, so the desktop's file manager can restore it."""
    files, info = os.path.join(TRASH, "files"), os.path.join(TRASH, "info")
    os.makedirs(files, exist_ok=True)
    os.makedirs(info, exist_ok=True)
    base = os.path.basename(src)
    for i in range(1000):
        name = base if i == 0 else f"{base}.{i}"
        try:   # claim the name atomically, as the spec asks
            fd = os.open(os.path.join(info, name + ".trashinfo"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            continue
    else:
        raise BadRequest("the trash is too full of items with this name")
    with os.fdopen(fd, "w") as f:
        f.write(f"[Trash Info]\nPath={urllib.parse.quote(os.path.abspath(src))}\n"
                f"DeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    try:
        shutil.move(src, os.path.join(files, name))
    except OSError:
        os.unlink(os.path.join(info, name + ".trashinfo"))
        raise
    return name


def rename_item(item, new):
    """Rename an output folder and the files named after it (knight.glb, knight-front.png, ...).
    Textures keep their names: the .blend files point at them."""
    old, src = item["name"], os.path.join(OUT, item["path"])
    new = slug(new, 60)
    if not new:
        raise BadRequest("give a name using letters, numbers or dashes")
    if new == old:
        return item["path"]
    if item["kind"] == "model" and new in NAMED_DIRS:
        raise BadRequest("that name is reserved, choose another")
    dst = os.path.join(os.path.dirname(src), new)
    if os.path.exists(dst):
        raise BadRequest(f"'{new}' already exists")
    os.rename(src, dst)
    for root, dirs, files in os.walk(dst):
        dirs[:] = [d for d in dirs if d != "textures"]
        for f in files:
            if f.startswith(old) and f[len(old):len(old) + 1] in (".", "-", "_"):
                os.rename(os.path.join(root, f), os.path.join(root, new + f[len(old):]))
    return os.path.relpath(dst, OUT)


def story_info(name):
    d = os.path.join(OUT, "story", slug(name))
    sbp = os.path.join(d, "storyboard.json")
    if not os.path.exists(sbp):
        return None
    files = sorted(os.listdir(d))
    return {"name": slug(name), "storyboard": json.load(open(sbp)),
            "keyframes": [f for f in files if re.fullmatch(r"key-\d+\.png", f)],
            "previous": [int(m.group(1)) for f in files if (m := re.fullmatch(r"key-(\d+)-prev\.png", f))],
            "videos": [f for f in files if f.endswith((".webm", ".ogv", ".mp4"))],
            "mtimes": {f: os.stat(os.path.join(d, f)).st_mtime for f in files}}


# ---------------------------------------------------------------- HTTP

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "make3d-portal"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if self.server.verbose:
            super().log_message(fmt, *args)

    # -- helpers
    def send(self, code, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise BadRequest("upload too large (200 MB max)")
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise BadRequest("expected JSON")
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise BadRequest("bad JSON")

    def cookie_path(self):
        prefix = self.headers.get("X-Forwarded-Prefix", "").rstrip("/")
        return (prefix or "") + "/"

    def session_ok(self):
        cfg = self.server.cfg()
        raw = ""
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "m3d_session":
                raw = v
        exp, _, sig = raw.partition(".")
        if not exp.isdigit() or int(exp) < time.time():
            return False
        good = hmac.new(cfg["secret"].encode(), (exp + cfg.get("password", "")).encode(), "sha256").hexdigest()
        return hmac.compare_digest(sig, good)

    def make_cookie(self, clear=False):
        cfg = self.server.cfg()
        exp = str(int(time.time()) + SESSION_DAYS * 86400)
        sig = hmac.new(cfg["secret"].encode(), (exp + cfg.get("password", "")).encode(), "sha256").hexdigest()
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        val = "" if clear else f"{exp}.{sig}"
        age = 0 if clear else SESSION_DAYS * 86400
        return f"m3d_session={val}; Path={self.cookie_path()}; Max-Age={age}; HttpOnly; SameSite=Strict{secure}"

    # -- routing
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self.route_get()
        except BadRequest as e:
            self.send(400, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            self.route_post()
        except BadRequest as e:
            self.send(400, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def route_get(self):
        url = urllib.parse.urlsplit(self.path)
        path, qs = url.path, urllib.parse.parse_qs(url.query)
        if path in ("/", "/index.html") or path.startswith("/static/"):
            name = "index.html" if path in ("/", "/index.html") else os.path.basename(path)
            f = os.path.join(STATIC, name)
            if not os.path.isfile(f):
                return self.send(404, "not found", "text/plain")
            ctype = mimetypes.guess_type(f)[0] or "application/octet-stream"
            return self.send(200, open(f, "rb").read(), ctype + ("; charset=utf-8" if ctype.startswith("text") or ctype.endswith("javascript") else ""))
        if path == "/api/me":
            return self.send(200, {"ok": self.session_ok()})
        if not self.session_ok():
            return self.send(401, {"error": "log in"})
        if path == "/api/jobs":
            return self.send(200, self.server.jobs.list())
        m = re.fullmatch(r"/api/jobs/([\w-]+)", path)
        if m:
            j = self.server.jobs.get(m.group(1))
            if not j:
                return self.send(404, {"error": "no such job"})
            since = int(qs.get("since", ["0"])[0] or 0)
            lp = self.server.jobs.log_path(j["id"])
            text, size = "", 0
            if os.path.exists(lp):
                with open(lp, "rb") as f:
                    size = os.fstat(f.fileno()).st_size
                    if since > size:
                        since = 0
                    if size - since > 400_000:   # first fetch of a long log: just the tail
                        since = size - 400_000
                    f.seek(since)
                    text = f.read().decode(errors="replace")
            j["log"], j["log_size"] = text, size
            j["log_start"] = since
            return self.send(200, j)
        if path == "/api/library":
            return self.send(200, library())
        if path == "/api/refs":
            return self.send(200, model_refs())
        if path == "/api/templates":
            return self.send(200, templates())
        if path == "/api/presets":
            return self.send(200, {"presets": PRESETS, "defaults": DEFAULTS})
        m = re.fullmatch(r"/api/story/([\w-]+)", path)
        if m:
            info = story_info(m.group(1))
            if not info:
                return self.send(404, {"error": "no such storyboard"})
            info["active"] = []
            for j in self.server.jobs.list():
                if j["outdir"] != f"story/{info['name']}" or j["status"] not in ("queued", "running"):
                    continue
                a = j["args"]
                frames = None   # keyframes this job draws: a list, "all", or None (not a frames job)
                if a and a[0] in ("frames", "new"):
                    frames = [int(v) for v in a[a.index("--only") + 1].split(",")] if "--only" in a else "all"
                info["active"].append({"id": j["id"], "title": j["title"], "status": j["status"],
                                       "started": j.get("started"), "created": j["created"], "frames": frames})
            return self.send(200, info)
        if path == "/api/local":
            return self.send(200, local_check())
        if path == "/api/settings":
            cfg = self.server.cfg()
            return self.send(200, {"lemonade_url": cfg.get("lemonade_url") or "", "default_url": DEFAULT_LEMONADE,
                                   "key_source": key_source(cfg), "key_file": LEMONADE_KEY_FILE})
        if path.startswith("/out/"):
            return self.send_file(path[len("/out/"):])
        self.send(404, {"error": "not found"})

    def route_post(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/login":
            body = self.body_json()
            cfg = self.server.cfg()
            if not self.server.login_allowed():
                return self.send(429, {"error": "too many attempts, wait a minute"})
            if check_password(str(body.get("password", "")), cfg.get("password")):
                return self.send(200, {"ok": True}, headers={"Set-Cookie": self.make_cookie()})
            self.server.login_failed()
            time.sleep(1)
            return self.send(403, {"error": "wrong password"})
        if path == "/api/logout":
            return self.send(200, {"ok": True}, headers={"Set-Cookie": self.make_cookie(clear=True)})
        if not self.session_ok():
            return self.send(401, {"error": "log in"})
        body = self.body_json()
        m = re.fullmatch(r"/api/run/(\w+)", path)
        if m:
            return self.send(200, self.server.jobs.submit(m.group(1), body))
        m = re.fullmatch(r"/api/jobs/([\w-]+)/cancel", path)
        if m:
            j = self.server.jobs.cancel(m.group(1))
            return self.send(200, j) if j else self.send(404, {"error": "no such job"})
        m = re.fullmatch(r"/api/story/([\w-]+)/undo/(\d+)", path)
        if m:   # swap a keyframe with the version before its last redraw (again = redo)
            name, i = slug(m.group(1)), int(m.group(2))
            d = os.path.join(OUT, "story", name)
            cur, prev = os.path.join(d, f"key-{i:02d}.png"), os.path.join(d, f"key-{i:02d}-prev.png")
            if not (os.path.isfile(cur) and os.path.isfile(prev)):
                return self.send(404, {"error": "no earlier version of this keyframe"})
            if self.server.jobs.busy(f"story/{name}"):
                raise BadRequest("a job is working on this storyboard; wait for it first")
            tmp = cur + ".swap"
            os.replace(cur, tmp)
            os.replace(prev, cur)
            os.replace(tmp, prev)
            sbp = os.path.join(d, "storyboard.json")
            sb = json.load(open(sbp))
            checks, pc = sb.setdefault("checks", {}), sb.setdefault("prev_checks", {})
            checks[str(i)], pc[str(i)] = pc.get(str(i)), checks.get(str(i))
            if checks[str(i)] is None:
                checks.pop(str(i))
            with open(sbp + ".tmp", "w") as f:
                json.dump(sb, f, indent=1)
            os.replace(sbp + ".tmp", sbp)
            return self.send(200, {"ok": True})
        m = re.fullmatch(r"/api/story/([\w-]+)", path)
        if m:
            sb = body.get("storyboard")
            if not isinstance(sb, dict) or not isinstance(sb.get("keyframes"), list) \
                    or not isinstance(sb.get("segments"), list):
                raise BadRequest("the storyboard needs 'keyframes' and 'segments' lists")
            if len(sb["segments"]) != len(sb["keyframes"]) - 1:
                raise BadRequest("there must be one segment fewer than keyframes")
            p = os.path.join(OUT, "story", slug(m.group(1)), "storyboard.json")
            if not os.path.exists(p):
                return self.send(404, {"error": "no such storyboard"})
            shutil.copy(p, p + ".bak")
            with open(p + ".tmp", "w") as f:
                json.dump(sb, f, indent=1)
            os.replace(p + ".tmp", p)
            return self.send(200, {"ok": True})
        if path in ("/api/library/rename", "/api/library/delete"):
            item = library_item(body.get("path"))
            if self.server.jobs.busy(item["path"]):
                raise BadRequest("a job is still working on this; wait for it or cancel it first")
            if path.endswith("delete"):
                move_to_trash(os.path.join(OUT, item["path"]))
                self.server.jobs.moved(item["path"], None)
                return self.send(200, {"ok": True})
            newpath = rename_item(item, str(body.get("name", "")))
            self.server.jobs.moved(item["path"], newpath)
            return self.send(200, {"ok": True, "path": newpath, "name": os.path.basename(newpath)})
        if path == "/api/settings":
            with _cfg_lock:
                cfg = load_config()
                url = str(body.get("lemonade_url", "")).strip().rstrip("/")
                if url and not re.fullmatch(r"https?://[^\s/]+(/\S*)?", url):
                    raise BadRequest("the address looks like http://host:13305/api/v1")
                cfg["lemonade_url"] = url
                if body.get("clear_key"):
                    cfg.pop("lemonade_key", None)
                elif body.get("lemonade_key"):
                    cfg["lemonade_key"] = str(body["lemonade_key"]).strip()
                save_config(cfg)
                self.server._cfg = cfg
            return self.send(200, {"ok": True, "key_source": key_source(cfg)})
        if path == "/api/settings/test":
            cfg = self.server.cfg()
            url = str(body.get("lemonade_url") or "").strip() or cfg.get("lemonade_url") or DEFAULT_LEMONADE
            if not re.fullmatch(r"https?://[^\s/]+(/\S*)?", url):
                raise BadRequest("the address looks like http://host:13305/api/v1")
            key = str(body.get("lemonade_key") or "").strip() or effective_key(cfg)
            return self.send(200, test_lemonade(url, key))
        self.send(404, {"error": "not found"})

    def send_file(self, rel):
        p = safe_out_path(rel)
        if not self.session_ok():
            return self.send(401, {"error": "log in"})
        if not p or not os.path.exists(p):
            return self.send(404, {"error": "not found"})
        if os.path.isdir(p):
            return self.send(200, [entry(os.path.join(p, n)) for n in sorted(os.listdir(p))
                                   if not n.startswith(".")])
        size = os.path.getsize(p)
        ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range", "")
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
        if m and size:
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            elif m.group(2):
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                return self.send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if self.headers.get("X-Download") or "dl=1" in self.path:
            self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(p)}"')
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(p, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, verbose=False):
        super().__init__(addr, Handler)
        self.verbose = verbose
        self._cfg = load_config()
        self.jobs = Jobs()
        self.fails = []
        self.fail_lock = threading.Lock()

    def cfg(self):
        return self._cfg

    def login_allowed(self):
        with self.fail_lock:
            self.fails = [t for t in self.fails if t > time.time() - 60]
            return len(self.fails) < 10

    def login_failed(self):
        with self.fail_lock:
            self.fails.append(time.time())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=7870)
    ap.add_argument("--set-password", action="store_true", help="choose the portal password and exit")
    ap.add_argument("--lemonade-url", help="save this Lemonade address in the settings and exit")
    ap.add_argument("--verbose", action="store_true", help="log every request")
    a = ap.parse_args()

    cfg = load_config()
    if not cfg.get("secret"):
        cfg["secret"] = secrets.token_hex(32)
        save_config(cfg)
    if a.set_password:
        pw = getpass.getpass("New portal password: ")
        if len(pw) < 8:
            sys.exit("use at least 8 characters")
        if getpass.getpass("Again: ") != pw:
            sys.exit("they don't match")
        cfg["password"] = hash_password(pw)   # also logs out existing sessions
        save_config(cfg)
        print(f"saved to {CONFIG}")
        return
    if a.lemonade_url is not None:
        cfg["lemonade_url"] = a.lemonade_url.strip().rstrip("/")
        save_config(cfg)
        print(f"Lemonade: {cfg['lemonade_url'] or DEFAULT_LEMONADE}")
        return
    if not cfg.get("password"):
        sys.exit("set a password first: portal --set-password")
    os.makedirs(PORTAL_OUT, exist_ok=True)
    srv = Server((a.host, a.port), verbose=a.verbose)
    where = f"http://{'localhost' if a.host in ('127.0.0.1', '::1') else a.host}:{a.port}/"
    print(f"make3d portal on {where}  (Lemonade: {cfg.get('lemonade_url') or DEFAULT_LEMONADE})", flush=True)
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print("  note: plain HTTP. On a shared network, put an HTTPS proxy in front (see README).", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
