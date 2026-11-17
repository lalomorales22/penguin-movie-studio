#!/usr/bin/env python3
"""Penguin's Storyboard.

A simple storyboard app that sits on top of ComfyUI. You make a project out of shots,
each shot gets a picture (Qwen-Image) and then a video (Wan2.2), and every piece of work
goes into one queue. Night mode holds the queue until a set time, then works through it
while keeping the Mac awake.

Runs on the Python that ComfyUI already uses. Standard library only, plus aiohttp
(which ComfyUI installs) for the live progress bar.
"""
import json
import math
import mimetypes
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import models  # the Models section (models.py and models.html): kits, downloads, which file each job uses
import music  # the Music section (music.py and the music/ folder)

PORT = int(os.environ.get("STORYBOARD_PORT", "8777"))
HERE = Path(__file__).resolve().parent
COMFY_DIR = Path(os.environ.get("COMFY_DIR", HERE.parent / "ComfyUI")).resolve()
COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
DATA = Path(os.environ.get("STORYBOARD_DATA", HERE / "data")).resolve()
PROJECTS = DATA / "projects"
TRASH = DATA / "trash"
CLIENT_ID = "storyboard-" + uuid.uuid4().hex[:10]
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or str(Path(FFMPEG).with_name("ffprobe"))
# Extra options for the ComfyUI this app starts, e.g. COMFY_EXTRA_ARGS="--cache-none".
# (Tried --cache-none on this 24 GB Mac: no gain for 5-second clips, and models reload for every job.)
COMFY_EXTRA_ARGS = os.environ.get("COMFY_EXTRA_ARGS", "").split()

# Which model files do the work is chosen on the Models page (models.py: kits and slots); the graphs below
# ask models.slot(...) for the names. The settings here belong to the model families the app knows how to drive:
# Qwen-Image 2.1 Turbo for pictures and Wan2.2 TI2V-5B for video.
QWEN_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"
# Character pictures. Only the official Qwen-Image 2.1 prompt reader can look at pictures (a GGUF one cannot),
# so a picture that is given a character's reference picture is always made with an official reader.
REF_SIZE = (1024, 1024)  # the size a character picture is made at
MAX_REFS = 3             # at most this many characters are shown to the model for one picture
# The size each reference picture is shown to the model at (as a square's side), by how many there are.
# Every reference is held in memory for the whole picture, so more of them means each one smaller.
# Measured here: one at 768 adds about a minute to a picture and little memory. The one run at 1024
# went 8-10 GB into swap on this 24 GB Mac (it was also the encoder's first load, so not a clean comparison).
REF_RESOLUTION = {1: 768, 2: 640, 3: 512}
REF_BASE, REF_EACH = 290, 20  # rough seconds: a picture drawn from reference pictures, plus each one of them
# The words around a character picture. REF_POSE describes the character's own picture; REF_LEAD opens
# the prompt of a shot that is drawn from character pictures.
# (An earlier wording, "full body from head to feet, standing", gave a rose two legs and shoes: a script's
# "characters" are sometimes things.)
REF_POSE = ("Shown whole from top to bottom, upright, facing the viewer, alone and centered, exactly as described "
            "with nothing added. Plain light gray studio background, soft even lighting, nothing else in the picture.")
REF_LEAD = "A new scene with the same characters as in the reference pictures, in a new pose and a new place."
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".heic")
WAN_MODEL = "wan2.2_ti2v_5B_fp16.safetensors"  # the video model the Fast copy below is baked from
WAN_STEPS = 20
# Fast mode: the Wan2.2-TI2V-5B-Turbo add-on (4 steps, no guidance) instead of 20 guided steps.
WAN_TURBO_LORA = "Wan22_TI2V_5B_Turbo_lora_rank_64_fp16.safetensors"
# The same add-on baked into its own copy of the video model. Preferred when present: applying the
# add-on on the fly makes ComfyUI hold two copies of the 10 GB model, which swaps badly on 24 GB.
WAN_TURBO_MODEL = "wan2.2_ti2v_5B_turbo_fp16.safetensors"
FAST_STEPS = 4
FAST_SAMPLER = "lcm"
FAST_SHIFT = 5
WAN_NEGATIVE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，"
                "丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，"
                "静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")
FPS = 24
MAX_PART = 5  # the video model makes at most 5 seconds in one go; longer clips are made in parts and joined
SECONDS_CHOICES = (1, 2, 3, 4, 5, 10, 15)

SHAPES = {
    "wide": {"label": "Wide (16:9)", "video": (832, 480), "image": (1344, 768)},
    "tall": {"label": "Tall (9:16)", "video": (480, 832), "image": (768, 1344)},
    "square": {"label": "Square", "video": (640, 640), "image": (1024, 1024)},
}
KINDS = {
    "story": {"label": "Short story", "unit": "Page"},
    "book": {"label": "Book", "unit": "Page"},
    "commercial": {"label": "Commercial", "unit": "Shot"},
    "movie": {"label": "Movie", "unit": "Shot"},
    "music": {"label": "Music video", "unit": "Shot"},
    "other": {"label": "Something else", "unit": "Shot"},
}
START_FROM = ("picture", "previous", "text")
# Music for a movie: the instrument line-ups the composer can be asked for (music.py knows what each one means).
MUSIC_STYLES = ("film", "orchestra", "strings", "quartet", "piano", "chamber", "brass", "choir", "jazz", "rock", "pop",
                "electronic", "lofi", "latin", "folk", "world", "auto")
KIND_WORDS = {"story": "story film", "book": "picture-book film", "commercial": "commercial", "movie": "film",
              "music": "music video", "other": "film"}
AUDIO_EXT = (".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg")

LOCK = threading.RLock()
S = {
    "settings": {"mode": "now", "night_start": "22:00", "night_end": "07:00", "text_model": ""},
    "jobs": [],
    "calib": {"image": 1.0, "image_ref": 1.0, "video": 1.0, "video_fast": 1.0, "music": 1.0},
}
P = {}  # project id -> project
PROGRESS = {}  # prompt_id -> (step, steps, first_step_time, last_time)
RUNTIME = {"comfy": "unknown", "comfy_proc": None, "awake_proc": None, "note": "",
           "bake": {"state": "", "error": "", "started": 0},
           "ollama": "unknown", "ollama_proc": None, "text_models": []}
# One heavy thing at a time on this Mac: a picture/video job or the script writer, never both.
HEAVY = threading.Lock()
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class JobFailed(Exception):
    pass


class JobCancelled(Exception):
    pass


class ComfyDown(Exception):
    pass


# ---------------------------------------------------------------- storage

def log(*a):
    line = datetime.now().strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)
    try:
        with open(DATA / "app.log", "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


music.log = log
models.hooks["log"] = log

def new_id(prefix):
    return prefix + uuid.uuid4().hex[:8]


def atomic_write(path, obj):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


def save_state():
    with LOCK:
        atomic_write(DATA / "state.json", S)


def save_project(p):
    with LOCK:
        d = PROJECTS / p["id"]
        (d / "media").mkdir(parents=True, exist_ok=True)
        atomic_write(d / "project.json", p)


def load_all():
    for d in (DATA, PROJECTS, TRASH):
        d.mkdir(parents=True, exist_ok=True)
    sf = DATA / "state.json"
    if sf.exists():
        try:
            saved = json.loads(sf.read_text())
            S["settings"].update(saved.get("settings", {}))
            S["calib"].update(saved.get("calib", {}))
            S["jobs"] = saved.get("jobs", [])
        except (ValueError, OSError) as e:
            log("could not read state.json:", e)
    for j in S["jobs"]:
        if j.get("status") == "running":  # the app was closed mid-job
            j["status"] = "waiting"
            j.pop("prompt_id", None)
    for pf in sorted(PROJECTS.glob("*/project.json")):
        try:
            p = json.loads(pf.read_text())
            if p.pop("writing", None):
                p["script_error"] = "The app was closed while the script was being written. Press Write the script again."
            p.setdefault("seed", random.getrandbits(31))
            p.setdefault("music", {"on": False, "style": "film", "note": ""})
            P[p["id"]] = p
            if any("id" not in c for c in p.setdefault("cast", [])):  # projects from before character pictures
                for c in p["cast"]:
                    c.setdefault("id", new_id("c"))
                save_project(p)
        except (ValueError, OSError, KeyError) as e:
            log("could not read", pf, e)
    save_state()


def media_dir(pid):
    return PROJECTS / pid / "media"


def find_shot(p, sid):
    for i, s in enumerate(p["shots"]):
        if s["id"] == sid:
            return i, s
    return -1, None


def new_shot(p):
    return {"id": new_id("s"), "text": "", "motion": "", "caption": "", "who": [], "seconds": 3, "start_from": "picture",
            "image": None, "video": None, "images": [], "videos": []}


def find_cast(p, cid):
    return next((c for c in (p or {}).get("cast") or [] if c.get("id") == cid), None)


def merge_cast(p, rows, rewritten=False):
    """Replace the project's characters with `rows` ({id?, name, look}), keeping each one's reference
    picture. Rows from the page carry the character's id. A rewritten script has only names: a character
    with the same name keeps a picture you chose yourself, or a made one if the description is unchanged."""
    old = list(p.get("cast") or [])
    by_id = {c.get("id"): c for c in old}
    by_name = {(c.get("name") or "").strip().lower(): c for c in old if (c.get("name") or "").strip()}
    out, used = [], set()
    for r in rows[:20]:
        if not isinstance(r, dict):
            continue
        name, look = clean(r.get("name"), 40), clean(r.get("look"), 400)
        prev = by_name.get(name.lower()) if rewritten else by_id.get(r.get("id"))
        if prev is not None and prev["id"] in used:
            prev = None
        c = {"id": prev["id"] if prev else new_id("c"), "name": name, "look": look}
        if prev:
            used.add(prev["id"])
            if not rewritten or prev.get("ref_own") or (prev.get("look") or "") == look:
                for k in ("ref", "refs", "ref_own"):
                    if prev.get(k):
                        c[k] = prev[k]
        out.append(c)
    p["cast"] = out


def new_project(name, kind, shape):
    return {"id": new_id("p"), "name": name, "kind": kind, "shape": shape, "style": "", "idea": "", "cast": [],
            "seed": random.getrandbits(31), "created": time.time(), "shots": [],
            "make_videos": kind not in ("book",), "music": {"on": False, "style": "film", "note": ""}}


# ---------------------------------------------------------------- estimates

def video_frames(seconds):
    return int(seconds) * FPS + 1


def split_seconds(seconds):
    """10 -> [5, 5]. Each part after the first continues from the last frame of the part before."""
    seconds = int(seconds)
    parts = [MAX_PART] * (seconds // MAX_PART)
    if seconds % MAX_PART:
        parts.append(seconds % MAX_PART)
    return parts or [1]


def turbo_kind():
    if (COMFY_DIR / "models" / "diffusion_models" / WAN_TURBO_MODEL).exists():
        return "merged"
    if (COMFY_DIR / "models" / "loras" / WAN_TURBO_LORA).exists():
        return "lora"
    return None


def turbo_available():
    return turbo_kind() is not None


def is_fast(p):
    """Fast video is the default whenever the Turbo add-on is installed."""
    return turbo_available() and (p or {}).get("speed", "fast") != "best"


def refs_available():
    """Character pictures need a prompt reader that can look at pictures (the official one)."""
    return bool(models.vision_reader())


def uses_refs(p):
    """Character pictures are on for every project unless they were switched off in it."""
    return (p or {}).get("refs", True) is not False and refs_available()


NAME_FILLER = {"the", "old", "young", "little", "big", "small", "tall", "short", "baby", "mr", "mrs", "ms", "miss", "dr",
               "sir", "captain", "uncle", "aunt", "red", "blue", "green", "orange", "yellow", "black", "white", "grey",
               "gray", "brown", "pink", "purple", "dark", "light", "good", "evil", "great"}


def name_keys(cast):
    """The words that pick each character out in a shot's text: the whole name, and one short form.
    The short form is the name's last word when no other character shares it ("the penguin" for
    Cyborg Penguin), else its first word that is theirs alone ("the whale" for Whale Cyborg).
    Scripts often say "the penguin" rather than the full name."""
    words = {c["id"]: re.findall(r"[a-z0-9]+", (c.get("name") or "").lower()) for c in cast}
    out = {}
    for c in cast:
        ws = words[c["id"]]
        keys = [(c.get("name") or "").strip().lower()] if ws else []
        if len(ws) > 1:
            others = {w for cid, o in words.items() if cid != c["id"] for w in o}
            own = [w for w in ws if w not in others and len(w) >= 3 and w not in NAME_FILLER]
            if own:
                keys.append(ws[-1] if ws[-1] in own else own[0])
        out[c["id"]] = keys
    return out


def shot_cast(p, s):
    """The characters in a shot: the ones listed for it, or named in its text."""
    low = (s.get("text") or "").lower()
    listed = {str(w).strip().lower() for w in s.get("who") or ()}
    cast = p.get("cast") or []
    keys = name_keys(cast)
    return [c for c in cast if keys[c["id"]] and (
        keys[c["id"]][0] in listed or any(re.search(r"\b" + re.escape(k) + r"\b", low) for k in keys[c["id"]]))]


def has_ref(p, c):
    return bool(c.get("ref")) and (media_dir(p["id"]) / c["ref"]).exists()


def can_draw(c):
    """A character picture can be made for anyone with a name and a description."""
    return bool((c.get("name") or "").strip() and (c.get("look") or "").strip())


def shot_refs(p, s):
    """The characters whose reference picture is shown to the picture model for this shot."""
    if not uses_refs(p):
        return []
    return [c for c in shot_cast(p, s) if has_ref(p, c)][:MAX_REFS]


def refs_expected(p, s):
    """How many reference pictures this shot's picture will use, counting character pictures still to be made."""
    if not uses_refs(p):
        return 0
    return min(MAX_REFS, sum(1 for c in shot_cast(p, s) if has_ref(p, c) or can_draw(c)))


def job_refs(job):
    """Reference pictures for a picture job: what it ran with, or what it is expected to use while it waits."""
    if job["kind"] != "image":
        return 0
    if job["status"] == "waiting":
        p = P.get(job["project"])
        _, s = find_shot(p, job["shot"]) if p else (-1, None)
        return refs_expected(p, s) if s else 0
    return int(job.get("nrefs") or 0)


def job_params(job):
    """(seconds, start_from, fast) for a video job: the current settings while it waits,
    the values it actually ran with afterwards."""
    if job["kind"] != "video":
        return 0, "", False
    if job["status"] == "waiting":
        p = P.get(job["project"])
        _, s = find_shot(p, job["shot"]) if p else (-1, None)
        if s:
            return int(s.get("seconds", 3)), s.get("start_from", "picture"), is_fast(p)
    return int(job.get("seconds", 3)), job.get("start_from", "picture"), bool(job.get("fast"))


def calib_key(job):
    if job["kind"] == "image":
        return "image_ref" if job_refs(job) else "image"
    if job["kind"] == "ref":
        return "image"
    if job["kind"] == "movie":
        return None
    if job["kind"] == "music":
        return "music" if job.get("compose", True) else None
    return "video_fast" if job_params(job)[2] else "video"


def estimate(kind, shape_key, seconds=3, fast=False, refs=0):
    """Rough seconds a job takes on this Mac, corrected by how past jobs actually went."""
    if kind == "image":
        if refs:  # each reference picture is extra work in every step, and a bigger encoder is loaded
            return (REF_BASE + REF_EACH * refs) * S["calib"]["image_ref"]
        return 230 * S["calib"]["image"]
    w, h = SHAPES.get(shape_key, SHAPES["wide"])["video"]
    size = (w * h) / (832 * 480)
    total = 0.0
    for part in split_seconds(seconds):
        frames = video_frames(part)
        latent = (frames - 1) // 4 + 1
        if fast:  # 4 steps and no guidance pass: about a tenth of the sampling work
            total += (150 + FAST_STEPS * latent * 1.6 * size + frames) * S["calib"]["video_fast"]
        else:
            total += (150 + WAN_STEPS * latent * 3.2 * size + frames) * S["calib"]["video"]
    return total


def estimate_job(job):
    if job["kind"] == "movie":
        return 30
    if job["kind"] == "music":  # composing is Claude's time and grows with the movie; recording the audio is seconds
        if not job.get("compose", True):
            return 25
        p = P.get(job["project"]) or {}
        movie = sum(int(x.get("seconds", 3)) for x in p.get("shots", []) if (x.get("text") or "").strip())
        return (90 + 1.6 * movie) * S["calib"]["music"]
    if job["kind"] == "ref":
        return estimate("image", "square")
    p = P.get(job["project"])
    seconds, _, fast = job_params(job)
    return estimate(job["kind"], (p or {}).get("shape", "wide"), seconds, fast, job_refs(job))


def calibrate(job, actual):
    key = calib_key(job)
    if key is None:
        return
    raw = estimate_job(job) / S["calib"][key]
    if raw > 0 and actual > 20:
        ratio = max(0.3, min(3.0, actual / raw))
        S["calib"][key] = round(0.6 * S["calib"][key] + 0.4 * ratio, 3)


# ---------------------------------------------------------------- night mode

def hm_to_min(s):
    m = re.match(r"^(\d{1,2}):(\d{2})$", str(s))
    if not m:
        return 0
    return (int(m.group(1)) % 24) * 60 + int(m.group(2)) % 60


def in_night_window(dt, start, end):
    s, e, t = hm_to_min(start), hm_to_min(end), dt.hour * 60 + dt.minute
    if s == e:
        return True
    if s < e:
        return s <= t < e
    return t >= s or t < e


def next_window_start(dt, start):
    s = hm_to_min(start)
    cand = dt.replace(hour=s // 60, minute=s % 60, second=0, microsecond=0)
    if cand <= dt:
        cand += timedelta(days=1)
    return cand


def allowed_to_run():
    st = S["settings"]
    if st["mode"] == "paused":
        return False
    if st["mode"] == "now":
        return True
    return in_night_window(datetime.now(), st["night_start"], st["night_end"])


# ---------------------------------------------------------------- ComfyUI

def comfy_get(path, timeout=6):
    with OPENER.open(COMFY_URL + path, timeout=timeout) as r:
        return json.loads(r.read().decode() or "null")


def comfy_post(path, obj, timeout=60):
    req = urllib.request.Request(COMFY_URL + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=timeout) as r:
            body = r.read().decode()
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        raise JobFailed(explain_comfy_error(e.read().decode(errors="replace")))


def explain_comfy_error(body):
    try:
        d = json.loads(body)
    except ValueError:
        return body[:300]
    parts = []
    for nid, ne in (d.get("node_errors") or {}).items():
        for er in ne.get("errors", []):
            det = str(er.get("details", ""))
            if "not in" in str(er.get("message", "")) and "name" in det:
                parts.append("A model file is missing from ComfyUI: " + det[:160])
            else:
                parts.append(f"{ne.get('class_type', nid)}: {er.get('message', '')} {det}"[:200])
    if not parts and d.get("error"):
        parts.append(str(d["error"].get("message", d["error"]))[:300])
    return "; ".join(parts) or body[:300]


def comfy_up(timeout=3):
    try:
        comfy_get("/system_stats", timeout=timeout)
        return True
    except Exception:
        return False


COMFY_START_LOCK = threading.Lock()


def install_nodes():
    """Copy the app's own small ComfyUI nodes (the comfy_nodes folder) into ComfyUI, if missing or out of date."""
    dest = COMFY_DIR / "custom_nodes"
    for f in (HERE / "comfy_nodes").glob("*.py"):
        try:
            if dest.is_dir() and (not (dest / f.name).exists() or (dest / f.name).read_bytes() != f.read_bytes()):
                shutil.copyfile(f, dest / f.name)
        except OSError as e:
            log("could not install", f.name, "into ComfyUI:", e)


def ensure_comfy():
    """Make sure ComfyUI is running; start it ourselves if it is not."""
    if comfy_up():
        RUNTIME["comfy"] = "up"
        return
    with COMFY_START_LOCK:
        if comfy_up():
            RUNTIME["comfy"] = "up"
            return
        py = COMFY_DIR / ".venv" / "bin" / "python"
        if not py.exists():
            RUNTIME["comfy"] = "down"
            raise ComfyDown(f"ComfyUI was not found at {COMFY_DIR}")
        proc = RUNTIME.get("comfy_proc")
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        port = str(urlparse(COMFY_URL).port or 8188)
        install_nodes()
        RUNTIME["comfy"] = "starting"
        log("starting ComfyUI")
        logf = open(DATA / "comfyui.log", "ab")
        env = dict(os.environ, PYTORCH_ENABLE_MPS_FALLBACK="1")
        proc = subprocess.Popen([str(py), "main.py", "--port", port] + COMFY_EXTRA_ARGS, cwd=str(COMFY_DIR),
                                stdout=logf, stderr=subprocess.STDOUT, env=env)
        RUNTIME["comfy_proc"] = proc
        for _ in range(180):
            if comfy_up(timeout=2):
                RUNTIME["comfy"] = "up"
                log("ComfyUI is up")
                return
            if proc.poll() is not None:
                break
            time.sleep(1)
        RUNTIME["comfy"] = "down"
        raise ComfyDown("ComfyUI did not start. See storyboard/data/comfyui.log")


def qwen_graph(text, w, h, seed, prefix, refs=()):
    """A picture from words. `refs` are picture files in ComfyUI's input folder (character reference
    pictures); the prompt calls them <image1>, <image2>, ... in the same order."""
    # Which files do the work is chosen on the Models page. A GGUF prompt reader goes through the app's own
    # loader node (comfy_nodes/); the official one is loaded the ordinary way.
    reader = models.slot("picture_reader")
    g = {
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": models.slot("picture_model"), "weight_dtype": "default"}},
        "3": ({"class_type": "CLIPLoaderGGUFQwenImage21", "inputs": {"clip_name": reader}}
              if models.slot_kind("picture_reader") == "gguf" else
              {"class_type": "CLIPLoader", "inputs": {"clip_name": reader, "type": "qwen_image", "device": "default"}}),
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": models.slot("picture_vae")}},
        "6": {"class_type": "QwenImage21Cache", "inputs": {"device": "auto", "dtype": "default", "model": ["2", 0]}},
        "7": {"class_type": "TextEncodeQwenImage21",
              "inputs": {"prompt": text, "negative_prompt": "", "resolution": 1024, "clip": ["3", 0]}},
        "8": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "10": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "11": {"class_type": "ManualSigmas", "inputs": {"sigmas": QWEN_SIGMAS}},
        "12": {"class_type": "CFGGuider",
               "inputs": {"cfg": 1, "model": ["6", 0], "positive": ["7", 0], "negative": ["7", 1]}},
        "13": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["9", 0], "guider": ["12", 0], "sampler": ["10", 0], "sigmas": ["11", 0],
                          "latent_image": ["8", 0]}},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["4", 0]}},
        "15": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["14", 0]}},
    }
    if refs:
        g["3"] = {"class_type": "CLIPLoader",  # only an official reader can look at pictures
                  "inputs": {"clip_name": models.vision_reader(), "type": "qwen_image", "device": "default"}}
        enc = g["7"]["inputs"]
        enc["vae"], enc["resolution"] = ["4", 0], REF_RESOLUTION.get(len(refs), 512)
        for n, name in enumerate(refs, 1):
            g[str(20 + n)] = {"class_type": "LoadImage", "inputs": {"image": name}}
            enc[f"images.image_{n}"] = [str(20 + n), 0]
    return g


def wan_graph(text, w, h, frames, seed, prefix, start_image=None, fast=False):
    g = {
        "37": {"class_type": "UNETLoader", "inputs": {"unet_name": models.slot("video_model"), "weight_dtype": "default"}},
        "38": {"class_type": "CLIPLoader", "inputs": {"clip_name": models.slot("video_reader"), "type": "wan", "device": "default"}},
        "39": {"class_type": "VAELoader", "inputs": {"vae_name": models.slot("video_vae")}},
        "48": {"class_type": "ModelSamplingSD3", "inputs": {"shift": 8, "model": ["37", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["38", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": WAN_NEGATIVE, "clip": ["38", 0]}},
        "55": {"class_type": "Wan22ImageToVideoLatent",
               "inputs": {"width": w, "height": h, "length": frames, "batch_size": 1, "vae": ["39", 0]}},
        "3": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": WAN_STEPS, "cfg": 5, "sampler_name": "uni_pc", "scheduler": "simple",
                         "denoise": 1, "model": ["48", 0], "positive": ["6", 0], "negative": ["7", 0],
                         "latent_image": ["55", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["39", 0]}},
        "57": {"class_type": "CreateVideo",
               "inputs": {"fps": FPS, "bit_depth": "auto", "color_space": "sRGB", "chroma_subsampling": "auto",
                          "codec": "none", "images": ["8", 0]}},
        "58": {"class_type": "SaveVideo",
               "inputs": {"filename_prefix": prefix, "format": "auto", "format.codec": "auto", "codec": "auto",
                          "video": ["57", 0]}},
    }
    if start_image:
        g["56"] = {"class_type": "LoadImage", "inputs": {"image": start_image}}
        g["55"]["inputs"]["start_image"] = ["56", 0]
    if fast:
        if turbo_kind() == "merged":
            g["37"]["inputs"]["unet_name"] = WAN_TURBO_MODEL
        else:
            g["40"] = {"class_type": "LoraLoaderModelOnly",
                       "inputs": {"lora_name": WAN_TURBO_LORA, "strength_model": 1.0, "model": ["37", 0]}}
            g["48"]["inputs"]["model"] = ["40", 0]
        g["48"]["inputs"]["shift"] = FAST_SHIFT
        g["3"]["inputs"].update(steps=FAST_STEPS, cfg=1, sampler_name=FAST_SAMPLER)
    return g


def bake_fast_model():
    """Make the Fast video model: bake the Turbo add-on into its own copy of the video model (a 10 GB file).
    Applying the add-on on the fly instead makes ComfyUI hold two copies of the model, which swaps badly on a
    24 GB Mac. Runs in its own thread, one heavy thing at a time like everything else."""
    st = RUNTIME["bake"]
    out = COMFY_DIR / "models" / "diffusion_models" / WAN_TURBO_MODEL
    with HEAVY:
        st.update(state="running", error="", started=time.time())
        made = None
        try:
            if out.exists():
                st.update(state="done")
                return
            base, lora = COMFY_DIR / "models" / "diffusion_models" / WAN_MODEL, COMFY_DIR / "models" / "loras" / WAN_TURBO_LORA
            if not base.exists() or not lora.exists():
                raise ValueError("The video model and the Fast video add-on have to be downloaded first.")
            if shutil.disk_usage(out.parent).free < base.stat().st_size + 3 * 1024 ** 3:
                raise ValueError("There is not enough free disk space: this step writes a 10 GB file.")
            ensure_comfy()
            tag = "bake_" + uuid.uuid4().hex[:8]
            graph = {
                "1": {"class_type": "UNETLoader", "inputs": {"unet_name": WAN_MODEL, "weight_dtype": "default"}},
                "2": {"class_type": "LoraLoaderModelOnly",
                      "inputs": {"lora_name": WAN_TURBO_LORA, "strength_model": 1.0, "model": ["1", 0]}},
                "3": {"class_type": "ModelSave", "inputs": {"model": ["2", 0], "filename_prefix": f"storyboard/{tag}"}},
            }
            res = comfy_post("/prompt", {"prompt": graph, "client_id": CLIENT_ID})
            prompt_id = res.get("prompt_id")
            if not prompt_id:
                raise ValueError(explain_comfy_error(json.dumps(res)))
            deadline = time.time() + 3600
            while True:
                time.sleep(3)
                if time.time() > deadline:
                    raise ValueError("It took too long and was stopped.")
                entry = (comfy_get(f"/history/{prompt_id}") or {}).get(prompt_id)
                if not entry:
                    continue
                status = entry.get("status", {})
                if status.get("status_str") == "success":
                    break
                msg = "ComfyUI reported an error."
                for m in status.get("messages", []):
                    if m[0] == "execution_error":
                        msg = f"{m[1].get('node_type', '')}: {str(m[1].get('exception_message', '')).strip()[:300]}"
                raise ValueError(msg)
            files = sorted((COMFY_DIR / "output" / "storyboard").glob(tag + "*.safetensors"))
            if not files:
                raise ValueError("ComfyUI finished but the new model file was not found.")
            made = files[-1]
            shutil.move(str(made), str(out.with_name(out.name + ".part")))
            os.replace(out.with_name(out.name + ".part"), out)
            try:  # the model it just built is still in memory; hand that back
                comfy_post("/free", {"unload_models": True, "free_memory": True}, timeout=20)
            except Exception:
                pass
            st.update(state="done")
            log("the Fast video model is ready:", out.name)
        except Exception as e:
            st.update(state="failed", error=str(e) or "It did not work.")
            log("making the Fast video model failed:", e)
            if made is not None:
                Path(made).unlink(missing_ok=True)


def start_bake():
    """Queue the Fast video finishing step (see bake_fast_model). Returns at once."""
    st = RUNTIME["bake"]
    if st["state"] in ("waiting", "running"):
        return {"ok": True, "state": st["state"]}
    st.update(state="waiting", error="")
    threading.Thread(target=bake_fast_model, daemon=True).start()
    return {"ok": True, "state": "waiting"}


def progress_listener():
    """Follow ComfyUI's step-by-step progress over its websocket (for the progress bar only)."""
    try:
        import asyncio
        import aiohttp
    except ImportError:
        log("aiohttp not available; progress bar will be time-based")
        return
    ws_url = COMFY_URL.replace("http", "ws", 1) + "/ws?clientId=" + CLIENT_ID

    async def main():
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, heartbeat=30) as ws:
                        async for msg in ws:
                            if msg.type != aiohttp.WSMsgType.TEXT:
                                continue
                            d = json.loads(msg.data)
                            if d.get("type") == "progress":
                                dd = d.get("data", {})
                                pid, val, mx = dd.get("prompt_id"), dd.get("value", 0), dd.get("max", 0)
                                old = PROGRESS.get(pid)
                                first = old[2] if old and old[0] <= val else time.time()
                                first_val = old[4] if old and old[0] <= val else val
                                PROGRESS[pid] = (val, mx, first, time.time(), first_val)
            except Exception:
                pass
            await asyncio.sleep(3)

    asyncio.run(main())


# ---------------------------------------------------------------- the script writer (Ollama)

def ollama_call(path, obj=None, timeout=10):
    req = urllib.request.Request(OLLAMA_URL + path, data=None if obj is None else json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            body = json.loads(body).get("error", body)
        except ValueError:
            pass
        raise ValueError(str(body)[:300])


def ollama_up():
    try:
        ollama_call("/api/version", timeout=2)
        return True
    except Exception:
        return False


def ollama_bin():
    for cand in (shutil.which("ollama"), "/usr/local/bin/ollama", "/opt/homebrew/bin/ollama",
                 "/Applications/Ollama.app/Contents/Resources/ollama"):
        if cand and Path(cand).exists():
            return cand
    return None


OLLAMA_START_LOCK = threading.Lock()


def ensure_ollama():
    """Make sure Ollama (the program that runs the writing models) is up; start it if it is installed."""
    if ollama_up():
        RUNTIME["ollama"] = "up"
        return True
    with OLLAMA_START_LOCK:
        if ollama_up():
            RUNTIME["ollama"] = "up"
            return True
        binary = ollama_bin()
        if not binary:
            RUNTIME["ollama"] = "missing"
            return False
        RUNTIME["ollama"] = "starting"
        log("starting Ollama")
        logf = open(DATA / "ollama.log", "ab")
        RUNTIME["ollama_proc"] = subprocess.Popen([binary, "serve"], stdout=logf, stderr=subprocess.STDOUT)
        for _ in range(60):
            if ollama_up():
                RUNTIME["ollama"] = "up"
                return True
            time.sleep(0.5)
        RUNTIME["ollama"] = "down"
        return False


def refresh_text_models():
    """The models Ollama has that can write text. Online ones are marked: using one sends the idea to ollama.com."""
    seen, out = set(), []
    for m in ollama_call("/api/tags", timeout=8).get("models", []):
        name = m.get("name", "")
        if not name or name in seen or name.startswith("llamacpp:") or "completion" not in (m.get("capabilities") or []):
            continue
        seen.add(name)
        out.append({"name": name, "gb": round(m.get("size", 0) / 1e9, 1), "online": bool(m.get("remote_host"))})
    # best first: on this Mac, not online, big enough to write well, small enough to load quickly
    out.sort(key=lambda m: (m["online"], abs(m["gb"] - 7.0)))
    RUNTIME["text_models"] = out
    return out


def default_text_model():
    names = [m["name"] for m in RUNTIME["text_models"]]
    chosen = S["settings"].get("text_model")
    if chosen in names:
        return chosen
    return names[0] if names else ""


def ollama_watch():
    ensure_ollama()
    while True:
        try:
            if ollama_up():
                RUNTIME["ollama"] = "up"
                refresh_text_models()
            elif RUNTIME["ollama"] not in ("missing", "starting"):
                RUNTIME["ollama"] = "down"
        except Exception:
            pass
        time.sleep(30)


def script_schema(n):
    text = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "title": text, "look": text,
            "characters": {"type": "array", "items": {
                "type": "object", "properties": {"name": text, "appearance": text}, "required": ["name", "appearance"]}},
            "shots": {"type": "array", "minItems": n, "maxItems": n, "items": {
                "type": "object", "properties": {"who": {"type": "array", "items": text}, "picture": text,
                                                 "motion": text, "narration": text},
                "required": ["who", "picture", "motion", "narration"]}},
        },
        "required": ["title", "look", "characters", "shots"],
    }


KIND_NOTES = {
    "story": "It is a short story: the narration lines, read in order, tell the whole story.",
    "book": "It is a picture book: the narration lines are the words printed on each page.",
    "commercial": "It is a commercial: hook the viewer in the first shot and end on the product or message.",
    "movie": "It is a short film: show, don't tell, and give it a satisfying ending.",
    "music": "It is a music video: each narration line is a lyric, and the pictures should have rhythm and energy.",
    "other": "",
}


def script_messages(idea, kind, n, shot_seconds):
    total = n * shot_seconds
    system = ("You are a film director and storyboard artist. You turn an idea into a shot list for AI picture "
              "and video generators. You answer with JSON only.")
    user = f"""Write the storyboard for a {KINDS[kind]['label'].lower()}.

The idea: {idea}

It is {total} seconds long: exactly {n} shots of {shot_seconds} seconds each. Tell one complete story across the shots, with a clear beginning, middle and end. {KIND_NOTES.get(kind, '')}

Return JSON with:
- "title": a short title.
- "look": one sentence giving the visual style of the whole film (the medium or art style, the lighting, the colour palette). It is added to every shot, so keep it general.
- "characters": every character or important object that appears in more than one shot. For each one give "name" (one or two words) and "appearance": one sentence of plain visual facts only (what it is, age or size, build, colours, clothing, one distinctive feature). No personality, no backstory, and nothing about where they are or what they are doing (that belongs in the shots). Write it so that it reads correctly after "<name> is", for example "a small boxy silver robot with green glowing arms and round black eyes". That exact sentence is pasted into every shot the character is in, which is how they keep looking the same.
- "shots": exactly {n} items, in order. For each one:
  - "who": the names of the characters visible in this shot, exactly as written in "characters". An empty list if there are none.
  - "picture": what the camera sees in the first frame, written for a picture generator: who is there, what they are doing, where they are, the framing (wide shot, close-up and so on) and the time of day. 20 to 40 words. Always call characters by their names, never "the robot", "the girl" or "they". Do not describe what the characters look like again, and do not mention the art style.
  - "motion": one sentence saying what moves during these {shot_seconds} seconds and how the camera moves. One simple action.
  - "narration": the line a narrator would say over this shot, 18 words at most.

Rules: no text, captions or logos inside the pictures. Each shot is one continuous moment in one place. Keep the same characters all the way through."""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def clean(x, limit):
    return re.sub(r"\s+", " ", str(x or "")).strip()[:limit]


def write_script(idea, kind, n, shot_seconds, model):
    """Ask the chosen Ollama model for the whole script. Returns title, look, cast and shots."""
    if not ensure_ollama():
        raise ValueError("Ollama is not running and could not be started. Install it from ollama.com, then try again.")
    if not model:
        refresh_text_models()
        model = default_text_model()
    if not model:
        raise ValueError("Ollama has no writing model yet. In Terminal, run: ollama pull qwen3.5:9b")
    body = {"model": model, "stream": False, "format": script_schema(n), "keep_alive": 0, "think": False,
            "options": {"temperature": 0.8, "num_ctx": 8192, "num_predict": 600 + 260 * n},
            "messages": script_messages(idea, kind, n, shot_seconds)}
    try:
        res = ollama_call("/api/chat", body, timeout=1800)
    except ValueError as e:
        if "think" not in str(e).lower():
            raise
        body.pop("think")  # some models cannot switch their thinking off
        res = ollama_call("/api/chat", body, timeout=1800)
    raw = (res.get("message") or {}).get("content", "")
    try:
        data = json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise ValueError("The writing model did not return a script. Try again, or pick another model.")
        data = json.loads(m.group(0))
    cast = []
    for c in data.get("characters") or []:
        name, look = clean(c.get("name"), 40), clean(c.get("appearance"), 400)
        if look.lower().startswith(name.lower() + " is "):
            look = look[len(name) + 4:]
        if name and look:
            cast.append({"name": name, "look": look})
    names = {c["name"].lower(): c["name"] for c in cast}
    shots = [{"text": clean(x.get("picture"), 1500), "motion": clean(x.get("motion"), 600),
              "caption": clean(x.get("narration"), 600),
              "who": [names[w] for w in dict.fromkeys(clean(v, 40).lower() for v in (x.get("who") or [])) if w in names]}
             for x in (data.get("shots") or [])[:n] if isinstance(x, dict)]
    shots = [x for x in shots if x["text"]]
    if not shots:
        raise ValueError("The writing model returned an empty script. Try again, or pick another model.")
    return {"title": clean(data.get("title"), 80), "look": clean(data.get("look"), 600), "cast": cast, "shots": shots}


def script_task(pid, model, n, shot_seconds, begin):
    """Runs in its own thread: waits for the picture/video job in progress (if any), then writes the script."""
    with HEAVY:
        with LOCK:
            p = P.get(pid)
            if not p:
                return
            p["writing"] = "writing"
            idea, kind = p.get("idea", ""), p.get("kind", "other")
        try:
            try:  # hand ComfyUI's memory back first so the writing model has room
                if comfy_up(timeout=2):
                    comfy_post("/free", {"unload_models": True, "free_memory": True}, timeout=20)
            except Exception:
                pass
            log("writing script for", pid, "with", model or "(default model)")
            sc = write_script(idea, kind, n, shot_seconds, model)
            with LOCK:
                p = P.get(pid)
                if not p:
                    return
                p["name"] = sc["title"] or p["name"]
                p["style"] = sc["look"]
                merge_cast(p, sc["cast"], rewritten=True)
                p["shots"] = []
                for x in sc["shots"]:
                    shot = new_shot(p)
                    shot.update(text=x["text"], motion=x["motion"], caption=x["caption"], who=x["who"],
                                seconds=shot_seconds)
                    p["shots"].append(shot)
                p.pop("writing", None)
                p.pop("script_error", None)
                if (p.get("music") or {}).get("current") == "piece":
                    # music that was written for the old script no longer fits; Begin has new music written
                    p.pop("soundtrack", None)
                    p.pop("soundtrack_name", None)
                    p["music"]["current"] = None
                save_project(p)
                log("script written for", pid, "-", len(p["shots"]), "shots")
                if begin:
                    begin_project(pid)
        except Exception as e:
            log("script failed for", pid, e)
            with LOCK:
                p = P.get(pid)
                if p:
                    p.pop("writing", None)
                    p["script_error"] = str(e) or "The script could not be written."
                    save_project(p)


def start_script(pid, model, total_seconds, shot_seconds, begin):
    with LOCK:
        p = P[pid]
        n = max(1, min(60, math.ceil(total_seconds / shot_seconds)))
        p["writing"] = "waiting"
        p.pop("script_error", None)
        p["length"], p["shot_seconds"] = n * shot_seconds, shot_seconds
        if model:
            S["settings"]["text_model"] = model
            save_state()
        save_project(p)
    threading.Thread(target=script_task, args=(pid, model, n, shot_seconds, begin), daemon=True).start()
    return n


# ---------------------------------------------------------------- the queue

def job_label(job):
    p = P.get(job["project"])
    if not p:
        return "(deleted project)"
    if job["kind"] == "movie":
        return f"Finished movie: {p['name']}"
    if job["kind"] == "music":
        return ("Music: " if job.get("compose", True) else "Music from the studio: ") + p["name"]
    if job["kind"] == "ref":
        c = find_cast(p, job.get("cast"))
        return f"Character picture: {(c or {}).get('name') or '(removed character)'}, {p['name']}"
    i, s = find_shot(p, job["shot"])
    unit = KINDS.get(p.get("kind"), KINDS["other"])["unit"].lower()
    what = "Picture" if job["kind"] == "image" else f"Video ({job_params(job)[0]} s{', fast' if job_params(job)[2] else ''})"
    return f"{what}: {p['name']}, {unit} {i + 1}" if s else f"{what}: {p['name']} (deleted {unit})"


def pending(pid, sid, kind):
    return any(j["project"] == pid and j["shot"] == sid and j["kind"] == kind and j["status"] in ("waiting", "running")
               for j in S["jobs"])


def pending_ref(pid, cid):
    return any(j["project"] == pid and j["kind"] == "ref" and j.get("cast") == cid and j["status"] in ("waiting", "running")
               for j in S["jobs"])


def shots_needing_picture(p):
    return [s for s in p["shots"] if (s.get("text") or "").strip() and not s.get("image")
            and not pending(p["id"], s["id"], "image")
            and (s.get("start_from") == "picture" or p.get("kind") in ("book", "story"))]


def refs_to_make(p, shots=None):
    """The characters who still need a picture of their own: they have a description, no picture yet,
    and are in one of `shots` (default: every shot that still needs its picture)."""
    if not uses_refs(p):
        return []
    shots = shots_needing_picture(p) if shots is None else shots
    wanted = {c["id"] for s in shots if (s.get("text") or "").strip() for c in shot_cast(p, s)}
    return [c for c in p.get("cast") or []
            if c["id"] in wanted and can_draw(c) and not has_ref(p, c) and not pending_ref(p["id"], c["id"])]


def queue_refs(pid, shots=None):
    """Queue the character pictures that are still missing. They go in before the pictures that use them."""
    todo = refs_to_make(P[pid], shots)
    for c in todo:
        add_job(pid, None, "ref", cast=c["id"])
    return len(todo)


def add_job(pid, sid, kind, front=False, cast=None, **extra):
    p = P[pid]
    _, s = find_shot(p, sid)
    job = {"id": new_id("j"), "project": pid, "shot": sid, "kind": kind, "status": "waiting",
           "created": time.time(), "attempts": 0, **extra}
    if kind == "ref":
        job["cast"] = cast
    if kind == "video" and s:
        job["seconds"] = int(s.get("seconds", 3))
        job["start_from"] = s.get("start_from", "picture")
    if front:
        first_wait = next((i for i, j in enumerate(S["jobs"]) if j["status"] == "waiting"), len(S["jobs"]))
        S["jobs"].insert(first_wait, job)
    else:
        S["jobs"].append(job)
    return job


def queue_shot(pid, sid, what):
    """Queue a picture or a video for one shot. A video that starts from the shot's picture
    gets the picture queued first when there is none yet."""
    with LOCK:
        p = P[pid]
        _, s = find_shot(p, sid)
        if s is None:
            raise ValueError("That shot no longer exists.")
        if not (s.get("text") or "").strip():
            raise ValueError("Describe what happens in this shot first.")
        added = []
        if what == "image":
            queue_refs(pid, [s])
            added.append(add_job(pid, sid, "image"))
        else:
            if s.get("start_from") == "picture" and not s.get("image") and not pending(pid, sid, "image"):
                queue_refs(pid, [s])
                added.append(add_job(pid, sid, "image"))
            added.append(add_job(pid, sid, "video"))
        save_state()
        return added


def queue_everything(pid, videos=None):
    """Queue whatever is still missing: all pictures first (one model), then all videos (the other).
    Loading a model takes a while, so doing each kind in one run is the quickest order."""
    with LOCK:
        p = P[pid]
        n = 0
        if videos is None:
            videos = p.get("make_videos", True)
        need = shots_needing_picture(p)
        n += queue_refs(pid, need)  # character pictures first: the shots' pictures are drawn from them
        for s in need:
            add_job(pid, s["id"], "image")
            n += 1
        if videos:
            for s in p["shots"]:
                if not (s.get("text") or "").strip():
                    continue
                if not s.get("video") and not pending(pid, s["id"], "video"):
                    add_job(pid, s["id"], "video")
                    n += 1
        save_state()
        return n


def begin_project(pid):
    """The one button: every missing picture, then every missing video, then join it all into one movie."""
    with LOCK:
        p = P[pid]
        if not any((s.get("text") or "").strip() for s in p["shots"]):
            raise ValueError("There is nothing to make yet. Write the script first.")
        n = queue_everything(pid, videos=True)
        if (p.get("music") or {}).get("on") and not p.get("soundtrack") and not pending(pid, None, "music"):
            add_job(pid, None, "music", compose=True)  # after the videos, before the movie is put together
            n += 1
        S["jobs"] = [j for j in S["jobs"]
                     if not (j["project"] == pid and j["kind"] == "movie" and j["status"] == "waiting")]
        add_job(pid, None, "movie")
        save_state()
        return n + 1


def queue_music(pid, compose=True):
    """Queue the music for a project, and the movie again after it when there are videos to lay it under."""
    p = P[pid]
    add_job(pid, None, "music", compose=compose)
    if any(s.get("video") for s in p["shots"]) or any(
            j["project"] == pid and j["kind"] == "video" and j["status"] in ("waiting", "running") for j in S["jobs"]):
        S["jobs"] = [j for j in S["jobs"]
                     if not (j["project"] == pid and j["kind"] == "movie" and j["status"] == "waiting")]
        add_job(pid, None, "movie")
    save_state()


def remake_plan(p):
    """What a remake makes again, as (shots that get a new picture, shots that get a new video).
    A shot with a character in it gets a new picture drawn from the character pictures, and a new video
    filmed from that picture. A shot that starts from the end of a remade shot gets a new video too.
    Shots without characters are left alone: nothing about them would change."""
    pics, vids, redone = [], [], set()
    for i, s in enumerate(p["shots"]):
        if not (s.get("text") or "").strip():
            continue
        from_pic = s.get("start_from") == "picture"
        new_pic = (from_pic or p.get("kind") in ("book", "story")) and refs_expected(p, s) > 0
        if new_pic:
            pics.append(s)
        chained = s.get("start_from") == "previous" and i > 0 and p["shots"][i - 1]["id"] in redone
        if (s.get("video") or p.get("make_videos", True)) and ((new_pic and from_pic) or chained):
            vids.append(s)
            redone.add(s["id"])
    return pics, vids


def remake_project(pid, preview=False):
    """Make a project's shots again from the character pictures. The pictures and videos it has now
    stay as earlier versions. With preview=True it only reports what would be made."""
    with LOCK:
        p = P[pid]
        if not refs_available():
            raise ValueError("Character pictures are not installed.")
        if not uses_refs(p):
            raise ValueError("Character pictures are switched off for this project. Tick the box under Characters first.")
        if p.get("writing"):
            raise ValueError("The script is being written. Try again when it is done.")
        if any(j["project"] == pid and j["status"] in ("waiting", "running") for j in S["jobs"]):
            raise ValueError("This project has work in the queue. Remove it or let it finish first.")
        pics, vids = remake_plan(p)
        if not pics:
            raise ValueError("No shot has one of the characters in it. Use their names in the shots, "
                             "or list them under “Who is in it?”, and give each a description or a picture.")
        chars = refs_to_make(p, pics)
        shape, fast = p.get("shape", "wide"), is_fast(p)
        secs = (len(chars) * estimate("image", "square")
                + sum(estimate("image", shape, refs=refs_expected(p, s)) for s in pics)
                + sum(estimate("video", shape, int(s.get("seconds", 3)), fast) for s in vids) + (30 if vids else 0))
        out = {"characters": len(chars), "pictures": len(pics), "videos": len(vids), "seconds": round(secs),
               "shots": sum(1 for s in p["shots"] if (s.get("text") or "").strip())}
        if preview:
            return out
        queue_refs(pid, pics)
        for s in pics:
            add_job(pid, s["id"], "image")
        for s in vids:
            add_job(pid, s["id"], "video")
        if vids:
            add_job(pid, None, "movie")
        save_state()
        log("remake", pid, out)
        return out


def blocked(job):
    """None = ready to run, 'wait' = something it needs is still in the queue, other text = cannot run."""
    p = P.get(job["project"])
    if not p:
        return "The project was deleted."
    if job["kind"] == "movie":
        if any(j["project"] == p["id"] and j["kind"] in ("image", "video", "music") and j["status"] in ("waiting", "running")
               for j in S["jobs"]):
            return "wait"
        return None if any(s.get("video") for s in p["shots"]) else "There are no finished videos to join."
    if job["kind"] == "music":
        return None if any((s.get("text") or "").strip() for s in p["shots"]) else "There are no shots to write music for."
    if job["kind"] == "ref":
        c = find_cast(p, job.get("cast"))
        if c is None:
            return "The character was removed."
        return None if can_draw(c) else "Give this character a name and a description first."
    i, s = find_shot(p, job["shot"])
    if s is None:
        return "The shot was deleted."
    if job["kind"] == "image":  # its characters' pictures come first
        return "wait" if any(pending_ref(p["id"], c["id"]) for c in shot_cast(p, s)) else None
    if job["kind"] != "video":
        return None
    # A video waits for whatever it starts from, also when that is being made again (a remake):
    # otherwise it would be filmed from the old picture.
    src = job_params(job)[1]
    if src == "picture":
        if pending(p["id"], s["id"], "image"):
            return "wait"
        if not s.get("image"):
            return "This shot has no picture to start from."
    if src == "previous" and i > 0:
        prev = p["shots"][i - 1]
        if pending(p["id"], prev["id"], "video"):
            return "wait"
        if not prev.get("video"):
            return "The shot before this one has no video yet."
    return None


def next_job():
    with LOCK:
        for j in S["jobs"]:
            if j["status"] != "waiting":
                continue
            b = blocked(j)
            if b is None:
                return j
            if b != "wait":
                j["status"] = "failed"
                j["error"] = b
                j["finished"] = time.time()
                save_state()
    return None


def lower_first(look):
    """'A tall robot' -> 'a tall robot', so it reads on after 'Name is'."""
    if look and (look.split(" ", 1)[0].lower() in ("a", "an", "the") or (look[0].isupper() and look[1:2].islower())):
        return look[0].lower() + look[1:]
    return look


def cast_for(p, s, refs=()):
    """The characters in a shot, each with its fixed description. The picture model has no memory, so
    the same words go into every prompt the character is in. A character whose reference picture is
    shown to the model (`refs`, in order) is also pointed at as <image1>, <image2>, ..."""
    out = []
    shown = {c["id"]: n for n, c in enumerate(refs, 1)}
    for c in shot_cast(p, s):
        name, look = c["name"].strip(), (c.get("look") or "").strip().rstrip(".")
        n = shown.get(c["id"])
        if n and look:
            out.append(f"{name} is the character shown in <image{n}>: {lower_first(look)}.")
        elif n:
            out.append(f"{name} is the character shown in <image{n}>.")
        elif look:
            out.append(f"{name} is {lower_first(look)}.")
    return " ".join(out)


def sentence(text):
    text = (text or "").strip().rstrip(".").strip()
    return text + "." if text else ""


def picture_prompt(p, s, refs=()):
    lead = REF_LEAD if refs else ""
    return " ".join(x for x in (lead, sentence(s.get("text")), cast_for(p, s, refs), sentence(p.get("style"))) if x)


def ref_prompt(p, c):
    """The words for a character's own picture: alone, whole body, plain background, in the project's look."""
    look = (c.get("look") or "").strip().rstrip(".")
    return " ".join(x for x in (f"Character design picture of {c['name'].strip()}: {lower_first(look)}.", REF_POSE,
                                sentence(p.get("style"))) if x)


def video_prompt(p, s):
    return " ".join(x for x in (sentence(s.get("motion")), sentence(s.get("text")), sentence(p.get("style"))) if x)


def shot_seed(p, s, key):
    """Every shot's first picture uses the project's one seed (same starting noise, closer-looking results).
    Making a shot again moves to a new seed so the retake is different."""
    return (int(p.get("seed") or 1) + 1000 * len(s.get(key, []))) % (2 ** 48)


def last_frame(video_path, out_path):
    r = subprocess.run([FFMPEG, "-y", "-v", "error", "-sseof", "-1", "-i", str(video_path), "-update", "1",
                        "-q:v", "1", str(out_path)], capture_output=True, text=True)
    if r.returncode != 0 or not out_path.exists():
        raise JobFailed("Could not read the last frame of the previous video. " + r.stderr[-200:])


def wait_for(job, prompt_id):
    misses = 0
    while True:
        time.sleep(2)
        if job.get("cancel"):
            try:
                comfy_post("/interrupt", {})
                comfy_post("/queue", {"delete": [prompt_id]})
            except Exception:
                pass
            raise JobCancelled()
        try:
            h = comfy_get(f"/history/{prompt_id}")
            misses = 0
        except Exception:
            misses += 1
            if misses >= 15:
                raise ComfyDown("ComfyUI stopped answering.")
            continue
        entry = (h or {}).get(prompt_id)
        if not entry:
            continue
        status = entry.get("status", {})
        if status.get("status_str") == "success":
            for out in entry.get("outputs", {}).values():
                for f in out.get("images", []) + out.get("gifs", []) + out.get("video", []):
                    if f.get("type") == "output":
                        return f
            raise JobFailed("ComfyUI finished but saved no file.")
        msg = "ComfyUI reported an error."
        for m in status.get("messages", []):
            if m[0] == "execution_error":
                msg = f"{m[1].get('node_type', '')}: {str(m[1].get('exception_message', '')).strip()[:300]}"
            if m[0] == "execution_interrupted":
                msg = "It was interrupted."
        raise JobFailed(msg)


def submit_and_wait(job, graph):
    res = comfy_post("/prompt", {"prompt": graph, "client_id": CLIENT_ID})
    prompt_id = res.get("prompt_id")
    if not prompt_id:
        raise JobFailed(explain_comfy_error(json.dumps(res)))
    job["prompt_id"] = prompt_id
    out = wait_for(job, prompt_id)
    PROGRESS.pop(prompt_id, None)
    produced = COMFY_DIR / "output" / out.get("subfolder", "") / out["filename"]
    if not produced.exists():
        raise JobFailed("The finished file was not found in ComfyUI's output folder.")
    return produced


def join_parts(parts, out_path):
    """Join the parts of a long clip. Each later part starts on the frame the one before ended on,
    so its first frame is dropped."""
    cmd = [FFMPEG, "-y", "-v", "error"]
    for part in parts:
        cmd += ["-i", str(part)]
    chain = "".join(f"[{n}:v]{'trim=start_frame=1,' if n else ''}setpts=PTS-STARTPTS[v{n}];" for n in range(len(parts)))
    chain += "".join(f"[v{n}]" for n in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0[out]"
    cmd += ["-filter_complex", chain, "-map", "[out]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-crf", "16", "-movflags", "+faststart", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out_path.exists():
        raise JobFailed("Joining the parts of the clip failed. " + r.stderr[-200:])


def to_png(src, dst, limit=1536):
    """Any picture file -> a PNG no bigger than `limit` on its long side (macOS's own converter, else ffmpeg)."""
    scale = f"scale='min({limit},iw)':'min({limit},ih)':force_original_aspect_ratio=decrease"
    for cmd in (["/usr/bin/sips", "-s", "format", "png", str(src), "--out", str(dst)],
                [FFMPEG, "-y", "-v", "error", "-i", str(src), "-vf", scale, "-frames:v", "1", str(dst)]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
                if cmd[0].endswith("sips"):  # shrink only when it is too big (sips -Z would also enlarge)
                    q = subprocess.run([cmd[0], "-g", "pixelWidth", "-g", "pixelHeight", str(dst)],
                                       capture_output=True, text=True, timeout=30)
                    dims = [int(x) for x in re.findall(r"pixel(?:Width|Height): (\d+)", q.stdout)]
                    if dims and max(dims) > limit:
                        subprocess.run([cmd[0], "-Z", str(limit), str(dst)], capture_output=True, timeout=90)
                return
        except (OSError, subprocess.TimeoutExpired):
            continue
    raise ValueError("That file could not be read as a picture.")


def run_ref_job(job):
    """Make a character's own picture. Shots that character is in are then drawn with it as a reference."""
    with LOCK:
        p = P[job["project"]]
        c = find_cast(p, job.get("cast"))
        if c is None:
            raise JobFailed("The character was removed.")
        text = ref_prompt(p, c)
        seed = (int(p.get("seed") or 1) + 1000 * len(c.get("refs", []))) % (2 ** 48)
        mdir = media_dir(p["id"])
        mdir.mkdir(parents=True, exist_ok=True)
    produced = submit_and_wait(job, qwen_graph(text, REF_SIZE[0], REF_SIZE[1], seed, f"storyboard/{job['id']}"))
    try:
        with LOCK:
            p = P.get(job["project"])
            c = find_cast(p, job.get("cast"))
            if c is None:
                raise JobFailed("The character was removed while the picture was being made.")
            set_ref(p, c, produced)
    finally:
        produced.unlink(missing_ok=True)


def set_ref(p, c, src, own=False):
    """Store `src` as the character's reference picture (keeping the earlier ones as versions)."""
    name = f"{c['id']}_ref_{len(c.get('refs', [])) + 1}{Path(src).suffix.lower()}"
    shutil.copyfile(src, media_dir(p["id"]) / name)
    c.setdefault("refs", []).append(name)
    c["ref"] = name
    c["ref_own"] = bool(own)
    save_project(p)


def music_brief(idea, kind, look, shots):
    """The feeling of the movie, shot by shot, in a few neutral words, written by the local writing model.
    It is all the composer is told about the story: Claude runs online, and the script stays on this computer.
    Returns (overall feeling, [(first shot, feeling), ...]); both empty when there is no local model to ask."""
    try:
        if not ensure_ollama():
            return "", []
        refresh_text_models()
        model = default_text_model()
        if not model:
            return "", []
        n = len(shots)
        schema = {"type": "object", "required": ["overall", "shots"], "properties": {
            "overall": {"type": "string"},
            "shots": {"type": "array", "minItems": n, "maxItems": n, "items": {"type": "string"}}}}
        system = ("You help a composer score a short film. You are given the film's shots in order. For each shot, answer "
                  "with three to six plain words for the feeling the music should have there: mood, energy and pace only, "
                  "for example 'quiet, curious, slow' or 'fast, tense, building'. Never mention people, bodies, animals, "
                  "objects, places, names or anything that happens. 'overall' is one short phrase for the mood of the "
                  "whole film, in the same kind of words. Give exactly one answer per shot, in order.")
        listing = "\n".join(f"{i}. {s['text']}" + (f" ({s['motion']})" if s.get("motion") else "") for i, s in enumerate(shots, 1))
        body = {"model": model, "stream": False, "format": schema, "keep_alive": 0, "think": False,
                "options": {"temperature": 0.4, "num_ctx": 8192, "num_predict": 200 + 40 * n},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": f"Kind of film: {kind}\nThe idea: {idea}\nThe look: {look}\n\nThe shots:\n{listing}"}]}
        try:
            res = ollama_call("/api/chat", body, timeout=900)
        except ValueError as e:
            if "think" not in str(e).lower():
                raise
            body.pop("think")
            res = ollama_call("/api/chat", body, timeout=900)
        raw = (res.get("message") or {}).get("content", "")
        try:
            data = json.loads(raw)
        except ValueError:
            data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))

        def mood(x):  # a handful of plain words, nothing else gets through
            return " ".join(re.sub(r"[^A-Za-z ,'\-]", " ", str(x or "")).lower().split()[:8])[:70].strip(" ,")

        sections, last = [], None
        for i, f in enumerate([mood(x) for x in (data.get("shots") or [])][:n], 1):
            if f and f != last:
                sections.append((i, f))
                last = f
        while len(sections) > 16:  # a long movie: every other turn is enough for the composer
            sections = sections[::2]
        return mood(data.get("overall")), sections
    except Exception as e:
        log("the mood brief for the music could not be written:", e)
        return "", []


def run_music_job(job):
    """Write the music for a movie (or record the piece again after it was changed in the studio) and make it
    the project's soundtrack. The length comes from the shots, so the music ends with the picture."""
    def stage(text):
        job["stage"] = text

    def stop_if_asked():
        if job.get("cancel"):
            raise JobCancelled()

    with LOCK:
        p = P[job["project"]]
        shots = [s for s in p["shots"] if (s.get("text") or "").strip()]
        if not shots:
            raise JobFailed("There are no shots to write music for.")
        secs = [int(s.get("seconds", 3)) for s in shots]
        m = dict(p.get("music") or {})
        style = m.get("style") if m.get("style") in MUSIC_STYLES else "film"
        note = clean(m.get("note"), 300)
        piece_id = None if job.get("compose", True) else m.get("piece")
        snap = {"idea": p.get("idea", ""), "kind": p.get("kind", "other"), "look": p.get("style", ""), "name": p["name"],
                "shots": [{"text": s.get("text", ""), "motion": s.get("motion", "")} for s in shots]}
        mdir = media_dir(p["id"])
        mdir.mkdir(parents=True, exist_ok=True)
    target, brief = float(sum(secs)), None
    try:
        if not piece_id:
            t = music.tools()
            why = ("LilyPond is not installed (run setup.command)." if not t["lilypond"]
                   else "Claude Code is not installed, so nothing can compose (run setup.command)." if not t["claude"]
                   else "Claude Code is not signed in. Open Terminal, run claude once and sign in." if t["claudeSignedIn"] is False
                   else "")
            if why:
                raise JobFailed(why)
            plan = music.cue_plan(secs, style)
            stage("Working out the mood")
            try:  # hand ComfyUI's memory back first so the writing model has room
                if comfy_up(timeout=2):
                    comfy_post("/free", {"unload_models": True, "free_memory": True}, timeout=20)
            except Exception:
                pass
            feel, sections = music_brief(snap["idea"], KINDS.get(snap["kind"], KINDS["other"])["label"], snap["look"], snap["shots"])
            stop_if_asked()
            brief = music.cue_message(plan, sections, feel, note, KIND_WORDS.get(snap["kind"], "film"))
            log("composing music for", job["project"], f"({style}, {plan['bars']} bars at {plan['tempo']} bpm)")
            cue = music.write_cue(plan, style, brief, on_stage=stage)
            stop_if_asked()
            rec = music.save_piece(None, {
                "name": cue["name"], "origin": f"Soundtrack for “{snap['name']}” ({music.STYLE_LABEL[style].lower()})",
                "movie": job["project"], "ly": cue["ly"], "sound": cue["sound"], "beat": cue["beat"], "desk": cue["desk"],
                "scoreBpm": cue["scoreBpm"], "withBeat": cue["withBeat"]})
            piece_id = rec["id"]
        stage("Recording the audio")
        piece = music.read_piece(piece_id)
        wav, info = music.piece_audio(piece_id, tail=4.0)
        off = abs(info["music"] - target) / target
        if 0.002 < off <= 0.04:  # a hair off: nudge the speed so the last bar ends exactly with the picture
            wav, info = music.piece_audio(piece_id, seconds=target, tail=4.0)
        stop_if_asked()
        name = f"soundtrack_{int(time.time())}.m4a"
        music.encode(wav, mdir / name)
    except (RuntimeError, ValueError) as e:
        raise JobFailed(str(e))
    with LOCK:
        p = P.get(job["project"])
        if not p:
            raise JobFailed("The project was deleted.")
        p["soundtrack"], p["soundtrack_name"] = name, piece.get("name") or "Soundtrack"
        mm = p.setdefault("music", {})
        mm.update(piece=piece_id, title=piece.get("name") or "Soundtrack", current="piece", made=time.time(),
                  print=music.fingerprint(piece_id), seconds=round(info["music"], 2), target=target)
        if brief:
            mm["brief"] = brief
        save_project(p)
    log("music ready for", job["project"], f"piece {piece_id}, {info['music']:.1f}s of music for a {target:.0f}s movie")


def run_job(job):
    if job["kind"] == "movie":
        try:
            export_movie(job["project"])
        except ValueError as e:
            raise JobFailed(str(e))
        return
    if job["kind"] == "music":
        return run_music_job(job)
    ensure_comfy()
    if job["kind"] == "ref":
        return run_ref_job(job)
    with LOCK:
        p = P[job["project"]]
        i, s = find_shot(p, job["shot"])
        refs = shot_refs(p, s) if job["kind"] == "image" else []
        job["nrefs"] = len(refs)
        text = picture_prompt(p, s, refs) if job["kind"] == "image" else video_prompt(p, s)
        seed = shot_seed(p, s, "images" if job["kind"] == "image" else "videos")
        shape = SHAPES[p.get("shape", "wide")]
        mdir = media_dir(p["id"])
        mdir.mkdir(parents=True, exist_ok=True)
        ref_files = [mdir / c["ref"] for c in refs]
        src_image = None
        if job["kind"] == "video":
            job["seconds"], job["start_from"] = int(s.get("seconds", 3)), s.get("start_from", "picture")
            job["fast"] = is_fast(p)
            src = job["start_from"]
            if src == "picture" and s.get("image"):
                src_image = ("copy", mdir / s["image"])
            elif src == "previous" and i > 0 and p["shots"][i - 1].get("video"):
                src_image = ("frame", mdir / p["shots"][i - 1]["video"])
    prefix = f"storyboard/{job['id']}"
    leftovers = []  # temporary files to remove whatever happens
    input_dir = COMFY_DIR / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    try:
        if job["kind"] == "image":
            w, h = shape["image"]
            ref_names = []
            for n, f in enumerate(ref_files, 1):  # ComfyUI reads pictures from its own input folder
                ref_names.append(f"storyboard_{job['id']}_ref{n}{f.suffix}")
                leftovers.append(input_dir / ref_names[-1])
                shutil.copyfile(f, input_dir / ref_names[-1])
            produced = submit_and_wait(job, qwen_graph(text, w, h, seed, prefix, ref_names))
            leftovers.append(produced)
        else:
            w, h = shape["video"]
            start_name = None
            if src_image:
                start_name = f"storyboard_{job['id']}_0.png"
                leftovers.append(input_dir / start_name)
                if src_image[0] == "copy":
                    shutil.copyfile(src_image[1], input_dir / start_name)
                else:
                    last_frame(src_image[1], input_dir / start_name)
            parts = split_seconds(job["seconds"])
            made = []
            for n, part in enumerate(parts):
                job["part"], job["parts"] = n + 1, len(parts)
                graph = wan_graph(text, w, h, video_frames(part), seed + n, f"{prefix}_{n}", start_name,
                                  fast=bool(job.get("fast")))
                clip = submit_and_wait(job, graph)
                made.append(clip)
                leftovers.append(clip)
                if n + 1 < len(parts):  # the next part picks up where this one ended
                    start_name = f"storyboard_{job['id']}_{n + 1}.png"
                    leftovers.append(input_dir / start_name)
                    last_frame(clip, input_dir / start_name)
            if len(made) == 1:
                produced = made[0]
            else:
                produced = made[0].with_name(f"{job['id']}_joined.mp4")
                leftovers.append(produced)
                join_parts(made, produced)
        with LOCK:
            p = P.get(job["project"])
            _, s = find_shot(p, job["shot"]) if p else (-1, None)
            if s is None:
                raise JobFailed("The shot was deleted while this was being made.")
            key, hist = ("image", "images") if job["kind"] == "image" else ("video", "videos")
            name = f"{s['id']}_{key}_{len(s.get(hist, [])) + 1}{produced.suffix}"
            shutil.move(str(produced), str(mdir / name))
            s.setdefault(hist, []).append(name)
            s[key] = name
            save_project(p)
    finally:
        for f in leftovers:
            f.unlink(missing_ok=True)


def worker():
    while True:
        time.sleep(1.5)
        try:
            if not allowed_to_run():
                continue
            job = next_job()
            if job is None:
                continue
            with LOCK:
                if job["kind"] == "video":
                    job["seconds"], job["start_from"], job["fast"] = job_params(job)
                job["status"] = "running"
                job["started"] = time.time()
                job["attempts"] = job.get("attempts", 0) + 1
                job.pop("error", None)
                job.pop("cancel", None)
                save_state()
            log("start", job["id"], job_label(job))
            try:
                with HEAVY:
                    run_job(job)
                with LOCK:
                    job["status"] = "done"
                    job["finished"] = time.time()
                    calibrate(job, job["finished"] - job["started"])
                log("done", job["id"], f"{job['finished'] - job['started']:.0f}s")
            except JobCancelled:
                with LOCK:
                    job["status"] = "cancelled"
                    job["finished"] = time.time()
                log("cancelled", job["id"])
            except ComfyDown as e:
                with LOCK:
                    if job["attempts"] < 3:
                        job["status"] = "waiting"  # ComfyUI crashed or was closed: restart it and try again
                        job["note"] = "ComfyUI stopped; trying again."
                    else:
                        job["status"] = "failed"
                        job["error"] = str(e)
                        job["finished"] = time.time()
                log("comfy down during", job["id"], e)
                time.sleep(5)
            except JobFailed as e:
                with LOCK:
                    job["status"] = "failed"
                    job["error"] = str(e)
                    job["finished"] = time.time()
                log("failed", job["id"], e)
            except Exception as e:  # never let one bad job stop the night's work
                with LOCK:
                    job["status"] = "failed"
                    job["error"] = f"Unexpected problem: {e}"
                    job["finished"] = time.time()
                log("error", job["id"], traceback.format_exc())
            finally:
                PROGRESS.pop(job.get("prompt_id"), None)
                job.pop("part", None)
                job.pop("parts", None)
                job.pop("stage", None)
                save_state()
        except Exception:
            log("worker loop error", traceback.format_exc())
            time.sleep(5)


def keep_awake():
    """Hold the Mac awake (caffeinate) while there is work queued and the queue is not paused."""
    while True:
        time.sleep(4)
        try:
            with LOCK:
                work = any(j["status"] in ("waiting", "running") for j in S["jobs"])
                want = work and S["settings"]["mode"] != "paused"
            proc = RUNTIME.get("awake_proc")
            alive = proc is not None and proc.poll() is None
            if want and not alive and shutil.which("caffeinate"):
                RUNTIME["awake_proc"] = subprocess.Popen(["caffeinate", "-i", "-m", "-s"])
            elif not want and alive:
                proc.terminate()
                RUNTIME["awake_proc"] = None
        except Exception:
            log("keep_awake error", traceback.format_exc())


def comfy_watch():
    while True:
        proc = RUNTIME.get("comfy_proc")
        if RUNTIME["comfy"] != "starting":
            RUNTIME["comfy"] = "up" if comfy_up() else "down"
        time.sleep(6 if proc is None else 4)


# ---------------------------------------------------------------- views for the page

def job_view(job, now):
    v = {k: job.get(k) for k in ("id", "project", "shot", "cast", "kind", "status", "error", "started", "finished")}
    v["label"] = job_label(job)
    est = estimate_job(job)
    v["estimate"] = round(est)
    if job["status"] == "running":
        elapsed = now - job.get("started", now)
        v["elapsed"] = round(elapsed)
        remaining = max(est - elapsed, 20)
        pr = PROGRESS.get(job.get("prompt_id"))
        frac = min(elapsed / est, 0.97) if est else 0
        phase = ("Joining the clips" if job["kind"] == "movie" else job.get("stage") or "Getting ready"
                 if job["kind"] == "music" else "Getting the model ready")
        part, parts = job.get("part", 1), job.get("parts", 1)
        later = est * (parts - part) / parts  # parts of a long clip that have not started yet
        if pr and pr[1]:
            step, steps, first, last, first_val = pr
            phase = f"Step {step} of {steps}"
            tail = 20 if job["kind"] in ("image", "ref") else 30 + video_frames(split_seconds(job_params(job)[0])[part - 1]) * 0.8
            if step > first_val and last > first:
                per = (last - first) / (step - first_val)
                remaining = (steps - step) * per + tail + later
            if step >= steps:
                phase = "Finishing up"
                remaining = max(tail - (now - last), 5) + later
            frac = max(0.02, min(0.99, elapsed / (elapsed + remaining)))
        if parts > 1:
            phase = f"Part {part} of {parts}: {phase[0].lower()}{phase[1:]}"
        v["phase"], v["progress"], v["remaining"] = phase, round(frac, 3), round(remaining)
    b = blocked(job) if job["status"] == "waiting" else None
    if b == "wait":
        v["note"] = "Waits for an earlier job"
    return v


def state_view():
    now = time.time()
    with LOCK:
        jobs = [job_view(j, now) for j in S["jobs"]]
        left = 0.0
        for v in jobs:
            if v["status"] == "running":
                left += v.get("remaining", v["estimate"])
            elif v["status"] == "waiting":
                left += v["estimate"]
        st = S["settings"]
        dt = datetime.now()
        night = {"active": in_night_window(dt, st["night_start"], st["night_end"])}
        if st["mode"] == "night" and not night["active"]:
            nxt = next_window_start(dt, st["night_start"])
            night["starts_in"] = round((nxt - dt).total_seconds())
            start_at = nxt.timestamp()
        else:
            start_at = now
        proc = RUNTIME.get("awake_proc")
        return {
            "now": now, "settings": st, "jobs": jobs, "seconds_left": round(left),
            "finish_at": start_at + left if left else None, "night": night,
            "running": allowed_to_run(), "comfy": RUNTIME["comfy"],
            "awake": proc is not None and proc.poll() is None,
            "projects": list(P.values()), "shapes": {k: v["label"] for k, v in SHAPES.items()},
            "kinds": KINDS, "seconds_choices": SECONDS_CHOICES,
            "turbo": turbo_available(),
            "kits": models.summary().get("kits", {}),  # which model kits are here (the Models page has the details)
            # music: can it be written here, in which styles, and whose score was changed in the studio since
            "music": dict(zip(("ready", "why"), music.composer_ready()),
                          styles=[[k, music.STYLE_LABEL[k]] for k in MUSIC_STYLES]),
            "music_stale": {p["id"]: True for p in P.values()
                            if (p.get("music") or {}).get("current") == "piece" and p["music"].get("print")
                            and music.fingerprint(p["music"].get("piece") or "") not in ("", p["music"]["print"])},
            # character pictures: is the add-on installed, and how many each shot's picture will use
            "refs": refs_available(),
            "ref_counts": {p["id"]: {s["id"]: refs_expected(p, s) for s in p["shots"]} for p in P.values()},
            "ref_todo": {p["id"]: len(refs_to_make(p)) for p in P.values()},
            "ollama": RUNTIME["ollama"], "text_models": RUNTIME["text_models"], "text_model": default_text_model(),
            "estimates": {k: {"image": round(estimate("image", k)),
                              "image_refs": [round(estimate("image", k, refs=n)) for n in range(MAX_REFS + 1)],
                              "ref": round(estimate("image", "square")),
                              "video": {str(n): round(estimate("video", k, n)) for n in SECONDS_CHOICES},
                              "video_fast": {str(n): round(estimate("video", k, n, True)) for n in SECONDS_CHOICES}}
                          for k in SHAPES},
        }


# ---------------------------------------------------------------- exports

def safe_name(name):
    return re.sub(r"[^A-Za-z0-9 _-]+", "", name).strip().replace(" ", "_")[:60] or "project"


def media_seconds(path):
    """How long a video or audio file lasts, in seconds (0 if it cannot be read)."""
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def export_movie(pid):
    with LOCK:
        p = P[pid]
        mdir = media_dir(pid)
        clips = [mdir / s["video"] for s in p["shots"] if s.get("video") and (mdir / s["video"]).exists()]
        audio = mdir / p["soundtrack"] if p.get("soundtrack") else None
        mm = p.get("music") or {}
        music_len = mm.get("seconds") if mm.get("current") == "piece" else None  # the music itself, without its ring-out
    if not clips:
        raise ValueError("There are no finished videos to join yet.")
    out_dir = PROJECTS / pid / "exports"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{safe_name(p['name'])}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    audio = audio if audio and audio.exists() else None
    picture = out.with_name(out.stem + "_picture.mp4") if audio else out
    listing = out_dir / "_join.txt"
    listing.write_text("".join("file '" + str(c).replace("'", "'\\''") + "'\n" for c in clips))
    r = subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-an", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-r", str(FPS), "-crf", "17", "-movflags", "+faststart", str(picture)],
                       capture_output=True, text=True)
    listing.unlink(missing_ok=True)
    if r.returncode != 0 or not picture.exists():
        raise ValueError("Joining the videos failed: " + r.stderr[-300:])
    if audio:
        # The music runs under the whole picture and never changes its length: silence is added if the music is
        # shorter, and it fades out with the last shot if it is longer.
        length = media_seconds(picture)
        runs_on = media_seconds(audio) > length + 5 or (music_len or length) > length + 0.75
        fade = min(2.5 if runs_on else 1.2, length / 3)
        r = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(picture), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                            "-c:v", "copy", "-af", f"apad,afade=t=out:st={max(0.0, length - fade):.3f}:d={fade:.3f}",
                            "-c:a", "aac", "-b:a", "192k", "-t", f"{length:.3f}", "-movflags", "+faststart", str(out)],
                           capture_output=True, text=True)
        picture.unlink(missing_ok=True)
        if r.returncode != 0 or not out.exists():
            raise ValueError("Putting the music under the movie failed: " + r.stderr[-300:])
    with LOCK:
        p = P.get(pid)
        if p:
            p["movie"] = out.name
            save_project(p)
    return out


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def export_book(pid):
    with LOCK:
        p = json.loads(json.dumps(P[pid]))
        mdir = media_dir(pid)
    pages = [s for s in p["shots"] if s.get("image") and (mdir / s["image"]).exists()]
    if not pages:
        raise ValueError("There are no finished pictures to put in a book yet.")
    out_dir = PROJECTS / pid / "exports" / f"{safe_name(p['name'])}_book"
    out_dir.mkdir(parents=True, exist_ok=True)
    body = []
    for n, s in enumerate(pages, 1):
        jpg = f"page_{n:02d}.jpg"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(mdir / s["image"]), "-q:v", "2", str(out_dir / jpg)],
                       capture_output=True)
        words = esc(s.get("caption") or "").replace("\n", "<br>")
        body.append(f'<section><img src="{jpg}" alt="">' + (f"<p>{words}</p>" if words else "") + "</section>")
    html = ("<!doctype html><html lang=en><meta charset=utf-8><title>" + esc(p["name"]) + "</title><style>"
            "body{margin:0;font:20px/1.6 Georgia,serif;color:#222;background:#fbf8f1}"
            "h1{text-align:center;font-size:44px;margin:14vh 8vw}"
            "section{max-width:900px;margin:0 auto;padding:40px 24px;page-break-after:always;break-after:page}"
            "img{width:100%;border-radius:10px;display:block}p{margin:22px 6px 0}"
            "@media print{body{background:#fff}}</style><h1>" + esc(p["name"]) + "</h1>" + "".join(body) + "</html>")
    (out_dir / "index.html").write_text(html)
    return out_dir / "index.html"


# ---------------------------------------------------------------- web server

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200, raw=None):
        body = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def fail(self, msg, code=400):
        self.send_json({"error": msg}, code)

    def host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def send_file(self, path, download=False):
        if not path.is_file():
            return self.fail("Not found", 404)
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range") or "")
        if rng and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1))
                end = min(int(rng.group(2)), size - 1) if rng.group(2) else size - 1
            else:
                start = max(size - int(rng.group(2)), 0)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "max-age=3600" if self.path.startswith(("/media/", "/movie/")) else "no-store")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(262144, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.host_ok():
            return self.fail("Forbidden", 403)
        # Another web site may link here, but it may not fetch things in the background: some GETs do work
        # (an instrument is downloaded the first time it is asked for, a piece is turned into audio).
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and self.headers.get("Sec-Fetch-Mode") != "navigate":
            return self.fail("Forbidden", 403)
        path = unquote(urlparse(self.path).path)
        if path == "/music" or path.startswith("/music/"):
            return music.handle_get(self, path, parse_qs(urlparse(self.path).query))
        if path == "/models" or path.startswith("/models/"):
            if models.handle_get(self, path, parse_qs(urlparse(self.path).query)):
                return
            return self.fail("Not found", 404)
        if path in ("/", "/index.html"):
            return self.send_file(HERE / "index.html")
        if path == "/api/state":
            with LOCK:
                body = json.dumps(state_view()).encode()
            return self.send_json(None, raw=body)
        m = re.match(r"^/(media|movie)/([A-Za-z0-9]+)/([A-Za-z0-9_.\-]+)$", path)
        if m and m.group(2) in P:
            folder = media_dir(m.group(2)) if m.group(1) == "media" else PROJECTS / m.group(2) / "exports"
            return self.send_file(folder / m.group(3))
        self.fail("Not found", 404)

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_POST(self):
        if not self.host_ok() or self.headers.get("X-Storyboard") != "1":
            self.read_body()
            return self.fail("Forbidden", 403)
        path = unquote(urlparse(self.path).path)
        raw = self.read_body()
        if path.startswith("/music/"):
            return music.handle_post(self, path, raw)
        try:
            if path.startswith("/models/api/"):
                return self.send_json(models.handle_post(self, path, json.loads(raw.decode() or "{}") if raw else {}))
            if re.match(r"^/api/project/[A-Za-z0-9]+/soundtrack$", path):
                return self.send_json(self.soundtrack(path.split("/")[3], raw))
            m = re.match(r"^/api/project/([A-Za-z0-9]+)/cast/([A-Za-z0-9]+)/picture$", path)
            if m:
                return self.send_json(self.cast_picture(m.group(1), m.group(2), raw))
            body = json.loads(raw.decode() or "{}") if raw else {}
            self.send_json(self.route(path, body))
        except (ValueError, KeyError) as e:
            self.fail(str(e) or "That did not work.")
        except Exception as e:
            log("request error", path, traceback.format_exc())
            self.fail(f"Something went wrong: {e}", 500)

    def soundtrack(self, pid, raw):
        ext = Path(self.headers.get("X-Filename") or "").suffix.lower()
        if ext not in AUDIO_EXT:
            raise ValueError("Use a music file: " + ", ".join(AUDIO_EXT))
        if not raw:
            raise ValueError("That file was empty.")
        with LOCK:
            p = P[pid]
            name = f"soundtrack_{int(time.time())}{ext}"
            (media_dir(pid) / name).write_bytes(raw)
            p["soundtrack"] = name
            p.setdefault("music", {})["current"] = "file"
            p["soundtrack_name"] = Path(unquote(self.headers.get("X-Filename"))).name[:80]
            save_project(p)
        return {"ok": True}

    def cast_picture(self, pid, cid, raw):
        """Use the person's own picture as a character's reference picture."""
        ext = Path(unquote(self.headers.get("X-Filename") or "")).suffix.lower()
        if ext not in IMAGE_EXT:
            raise ValueError("Use a picture file: PNG, JPEG, WebP or HEIC.")
        if not raw:
            raise ValueError("That file was empty.")
        if len(raw) > 60_000_000:
            raise ValueError("That picture is too big (over 60 MB).")
        with LOCK:
            if find_cast(P[pid], cid) is None:
                raise ValueError("That character no longer exists.")
        tmp_in = DATA / f"_upload_{new_id('u')}{ext}"
        tmp_out = tmp_in.with_name(tmp_in.stem + "_out.png")
        try:
            tmp_in.write_bytes(raw)
            to_png(tmp_in, tmp_out)
            with LOCK:
                p = P.get(pid)
                c = find_cast(p, cid)
                if c is None:
                    raise ValueError("That character no longer exists.")
                set_ref(p, c, tmp_out, own=True)
        finally:
            tmp_in.unlink(missing_ok=True)
            tmp_out.unlink(missing_ok=True)
        return {"ok": True}

    def route(self, path, b):
        parts = [x for x in path.split("/") if x]
        if len(parts) < 2 or parts[0] != "api":
            raise ValueError("Unknown request")
        what = parts[1]
        with LOCK:
            if what == "settings":
                st = S["settings"]
                if b.get("mode") in ("now", "night", "paused"):
                    st["mode"] = b["mode"]
                for k in ("night_start", "night_end"):
                    if re.match(r"^\d{1,2}:\d{2}$", str(b.get(k, ""))):
                        st[k] = b[k]
                if "text_model" in b:
                    st["text_model"] = str(b["text_model"])[:200]
                save_state()
                return {"ok": True}

            if what == "idea":  # prompt to movie: make a project and let the writing model fill it in
                idea = str(b.get("idea") or "").strip()[:4000]
                if not idea:
                    raise ValueError("Say what the movie is about first.")
                kind = b.get("kind") if b.get("kind") in KINDS else "movie"
                shape = b.get("shape") if b.get("shape") in SHAPES else "wide"
                shot_seconds = int(b.get("shot_seconds") or 5)
                if shot_seconds not in SECONDS_CHOICES:
                    shot_seconds = 5
                total = max(shot_seconds, min(600, int(b.get("seconds") or 30)))
                p = new_project(" ".join(idea.split()[:6])[:60] or "New movie", kind, shape)
                p["idea"], p["make_videos"] = idea, True
                style = b.get("music") if b.get("music") in MUSIC_STYLES else ""
                p["music"] = {"on": bool(style), "style": style or "film", "note": ""}
                P[p["id"]] = p
                save_project(p)
                n = start_script(p["id"], str(b.get("model") or ""), total, shot_seconds, bool(b.get("begin")))
                return {"id": p["id"], "shots": n}

            if what == "projects":  # create
                name = str(b.get("name") or "").strip()[:80] or "Untitled"
                kind = b.get("kind") if b.get("kind") in KINDS else "other"
                shape = b.get("shape") if b.get("shape") in SHAPES else "wide"
                p = new_project(name, kind, shape)
                p["shots"] = [new_shot(p) for _ in range(3)]
                P[p["id"]] = p
                save_project(p)
                return {"id": p["id"]}

            if what == "project":
                p = P[parts[2]]
                pid = p["id"]
                rest = parts[3:]
                if not rest:  # update project fields
                    if "name" in b:
                        p["name"] = str(b["name"]).strip()[:80] or p["name"]
                    if "style" in b:
                        p["style"] = str(b["style"])[:1000]
                    if b.get("shape") in SHAPES:
                        p["shape"] = b["shape"]
                    if "make_videos" in b:
                        p["make_videos"] = bool(b["make_videos"])
                    if b.get("speed") in ("fast", "best"):
                        p["speed"] = b["speed"]
                    if "idea" in b:
                        p["idea"] = str(b["idea"])[:4000]
                    if isinstance(b.get("cast"), list):
                        merge_cast(p, b["cast"])
                    if "refs" in b:
                        p["refs"] = bool(b["refs"])
                    if isinstance(b.get("music"), dict):
                        mm = p.setdefault("music", {})
                        if "on" in b["music"]:
                            mm["on"] = bool(b["music"]["on"])
                        if b["music"].get("style") in MUSIC_STYLES:
                            mm["style"] = b["music"]["style"]
                        if "note" in b["music"]:
                            mm["note"] = str(b["music"]["note"])[:300]
                    save_project(p)
                    return {"ok": True}
                if len(rest) == 3 and rest[0] == "cast":  # a character's reference picture
                    c = find_cast(p, rest[1])
                    if c is None:
                        raise ValueError("That character no longer exists.")
                    if rest[2] == "make":
                        if not refs_available():
                            raise ValueError("Character pictures are not installed.")
                        if not can_draw(c):
                            raise ValueError("Give this character a name and a description first.")
                        if not pending_ref(pid, c["id"]):
                            add_job(pid, None, "ref", cast=c["id"])
                            save_state()
                        return {"ok": True}
                    if rest[2] == "remove_picture":
                        c["ref"], c["ref_own"] = None, False
                        save_project(p)
                        return {"ok": True}
                    if rest[2] == "pick":  # flip between the versions of the picture
                        lst = [f for f in c.get("refs", []) if (media_dir(pid) / f).exists()]
                        if lst:
                            cur = lst.index(c["ref"]) if c.get("ref") in lst else len(lst) - 1
                            c["ref"] = lst[(cur + int(b.get("dir", 1))) % len(lst)]
                            save_project(p)
                        return {"ok": True}
                    raise ValueError("Unknown request")
                if rest == ["delete"]:
                    for j in S["jobs"]:
                        if j["project"] == pid and j["status"] in ("waiting", "running"):
                            j["cancel"] = True
                            if j["status"] == "waiting":
                                j["status"] = "cancelled"
                    S["jobs"] = [j for j in S["jobs"] if j["project"] != pid or j["status"] == "running"]
                    dest = TRASH / f"{pid}_{int(time.time())}"
                    shutil.move(str(PROJECTS / pid), str(dest))
                    del P[pid]
                    save_state()
                    return {"ok": True}
                if rest == ["make_all"]:
                    return {"added": queue_everything(pid)}
                if rest == ["begin"]:
                    return {"added": begin_project(pid)}
                if rest == ["remake"]:
                    return remake_project(pid, preview=bool(b.get("preview")))
                if rest == ["rewrite"]:
                    if p.get("writing"):
                        raise ValueError("The script is already being written.")
                    if not (p.get("idea") or "").strip():
                        raise ValueError("Write the idea for this project first.")
                    if any(j["project"] == pid and j["status"] in ("waiting", "running") for j in S["jobs"]):
                        raise ValueError("This project has work in the queue. Remove it or let it finish first.")
                    shot_seconds = int(b.get("shot_seconds") or p.get("shot_seconds") or 5)
                    total = int(b.get("seconds") or p.get("length") or max(len(p["shots"]), 1) * shot_seconds)
                    n = start_script(pid, str(b.get("model") or ""), total, shot_seconds, False)
                    return {"shots": n}
                if rest == ["add_shot"]:
                    s = new_shot(p)
                    if p["shots"]:
                        s["seconds"] = p["shots"][-1].get("seconds", 3)
                    i, _ = find_shot(p, b.get("after"))
                    p["shots"].insert(i + 1 if i >= 0 else len(p["shots"]), s)
                    save_project(p)
                    return {"id": s["id"]}
                if rest == ["remove_soundtrack"]:
                    p.pop("soundtrack", None)
                    p.pop("soundtrack_name", None)
                    p.setdefault("music", {})["current"] = None
                    save_project(p)
                    return {"ok": True}
                if rest == ["music", "make"]:  # have the music written now
                    if p.get("writing"):
                        raise ValueError("The script is still being written. Try again when it is done.")
                    if pending(pid, None, "music"):
                        raise ValueError("The music is already in the queue.")
                    if not any((s.get("text") or "").strip() for s in p["shots"]):
                        raise ValueError("Write the shots first: the music is written to their length.")
                    ok, why = music.composer_ready()
                    if ok is False:
                        raise ValueError(why)
                    queue_music(pid, compose=True)
                    return {"ok": True}
                if rest == ["music", "use"]:  # use a piece from the Music studio (again) as this movie's music
                    piece = str(b.get("piece") or (p.get("music") or {}).get("piece") or "")
                    music.read_piece(piece)
                    if pending(pid, None, "music"):
                        raise ValueError("The music is already in the queue.")
                    p.setdefault("music", {})["piece"] = piece
                    save_project(p)
                    queue_music(pid, compose=False)
                    return {"ok": True}
                if rest == ["reveal"]:
                    subprocess.Popen(["open", str(PROJECTS / pid)])
                    return {"ok": True}
                if rest and rest[0] == "shot" and len(rest) >= 2:
                    i, s = find_shot(p, rest[1])
                    if s is None:
                        raise ValueError("That shot no longer exists.")
                    act = rest[2] if len(rest) > 2 else "update"
                    if act == "update":
                        if "text" in b:
                            s["text"] = str(b["text"])[:2000]
                        if "caption" in b:
                            s["caption"] = str(b["caption"])[:4000]
                        if "motion" in b:
                            s["motion"] = str(b["motion"])[:1000]
                        if "who" in b:
                            raw = b["who"] if isinstance(b["who"], list) else str(b["who"]).split(",")
                            s["who"] = [w for w in (clean(x, 40) for x in raw[:20]) if w]
                        if "seconds" in b and int(b["seconds"]) in SECONDS_CHOICES:
                            s["seconds"] = int(b["seconds"])
                        if b.get("start_from") in START_FROM:
                            s["start_from"] = b["start_from"]
                        save_project(p)
                        return {"ok": True}
                    if act == "delete":
                        for j in S["jobs"]:
                            if j["project"] == pid and j["shot"] == s["id"] and j["status"] in ("waiting", "running"):
                                j["cancel"] = True
                                if j["status"] == "waiting":
                                    j["status"] = "cancelled"
                        p["shots"].pop(i)
                        save_project(p)
                        save_state()
                        return {"ok": True}
                    if act == "move":
                        k = i + (1 if int(b.get("dir", 1)) > 0 else -1)
                        if 0 <= k < len(p["shots"]):
                            p["shots"][i], p["shots"][k] = p["shots"][k], p["shots"][i]
                            save_project(p)
                        return {"ok": True}
                    if act == "pick":  # flip between earlier versions of the picture or video
                        key, hist = ("image", "images") if b.get("kind") == "image" else ("video", "videos")
                        lst = s.get(hist, [])
                        if lst:
                            cur = lst.index(s[key]) if s.get(key) in lst else len(lst) - 1
                            s[key] = lst[(cur + int(b.get("dir", 1))) % len(lst)]
                            save_project(p)
                        return {"ok": True}
                    if act == "make":
                        added = queue_shot(pid, s["id"], "image" if b.get("what") == "image" else "video")
                        return {"added": len(added)}
                raise ValueError("Unknown request")

            if what == "job" and len(parts) >= 4:
                job = next((j for j in S["jobs"] if j["id"] == parts[2]), None)
                if job is None:
                    raise ValueError("That job is no longer in the queue.")
                act = parts[3]
                if act == "cancel":
                    if job["status"] == "running":
                        job["cancel"] = True
                    elif job["status"] == "waiting":
                        job["status"] = "cancelled"
                        job["finished"] = time.time()
                    else:
                        S["jobs"].remove(job)
                elif act == "retry" and job["status"] in ("failed", "cancelled"):
                    job.update(status="waiting", attempts=0)
                    job.pop("error", None)
                    job.pop("cancel", None)
                elif act == "first" and job["status"] == "waiting":
                    S["jobs"].remove(job)
                    k = next((n for n, j in enumerate(S["jobs"]) if j["status"] == "waiting"), len(S["jobs"]))
                    S["jobs"].insert(k, job)
                save_state()
                return {"ok": True}

            if what == "jobs" and parts[2:] == ["clear"]:
                S["jobs"] = [j for j in S["jobs"] if j["status"] in ("waiting", "running")]
                save_state()
                return {"ok": True}

        # slower things run outside the lock
        if what == "export" and len(parts) == 4 and parts[2] in P:
            out = export_movie(parts[2]) if parts[3] == "movie" else export_book(parts[2])
            subprocess.Popen(["open", "-R", str(out)])
            return {"file": out.name}
        raise ValueError("Unknown request")


def main():
    load_all()
    models.hooks["bake"] = start_bake
    models.hooks["baking"] = lambda: RUNTIME["bake"]["state"] in ("waiting", "running")
    for target in (worker, keep_awake, comfy_watch, progress_listener, ollama_watch):
        threading.Thread(target=target, daemon=True).start()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"Storyboard is already running. Opening http://127.0.0.1:{PORT}")
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        return
    server.daemon_threads = True
    url = f"http://127.0.0.1:{PORT}"
    log(f"Penguin's Storyboard is running at {url}")
    print("Leave this window open. Close it (or press Ctrl+C) to stop.", flush=True)
    def stop(*_):
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    threading.Thread(target=ensure_comfy_quietly, daemon=True).start()
    if os.environ.get("STORYBOARD_NO_BROWSER") != "1":
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        models.shutdown()  # stop any download in progress (it resumes next time)
        for key in ("awake_proc", "comfy_proc", "ollama_proc"):
            proc = RUNTIME.get(key)
            if proc is not None and proc.poll() is None:
                proc.terminate()


def ensure_comfy_quietly():
    try:
        ensure_comfy()
    except Exception as e:
        log("ComfyUI is not up yet:", e)


if __name__ == "__main__":
    main()
