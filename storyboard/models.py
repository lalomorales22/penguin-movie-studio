#!/usr/bin/env python3
"""The Models section of Penguin's Storyboard.

Three things live here:

1. Kits. A kit is the exact set of files one feature needs (pictures, video, fast video, the
   script writer, the music sounds). The page shows which kits are on this computer and fetches
   the ones that are not.
2. Slots. Which file each job uses, so another file of the same model family can be swapped in.
3. The free-form downloader, folded in from Penguin's Model Downloader: paste a Hugging Face link,
   get a plain-English explanation, pick a file, and it is saved into

       MODELS/GGUF/<maker>/<model>/          llama.cpp, Ollama, LM Studio
       MODELS/MLX/<maker>/<model>/           Apple Silicon (MLX)
       MODELS/SAFETENSORS/<maker>/<model>/   original weights (ComfyUI, transformers, ...)
       MODELS/OTHER/<maker>/<model>/         anything that fits none of the above

app.py mounts handle_get / handle_post and asks slot() which files to put in its ComfyUI graphs.
Standard library only, plus huggingface_hub for looking models up. Downloads run through the `hf`
command that comes with it, one at a time, and can always be stopped and picked up again.
"""
import atexit
import fnmatch
import http.client
import json
import os
import platform
import plistlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
COMFY_DIR = Path(os.environ.get("COMFY_DIR", HERE.parent / "ComfyUI")).resolve()
DATA = Path(os.environ.get("STORYBOARD_DATA", HERE / "data")).resolve()
SETTINGS = DATA / "models.json"            # {"models_dir": ..., "slots": {...}, "adopted": {...}}
COMFY_MODELS = COMFY_DIR / "models"
STAGING = COMFY_MODELS / ".downloads"      # kit files land here first, then move to their folder
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
SYSTEM = platform.system()                 # "Darwin", "Linux" or "Windows"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# app.py fills these in: hooks["bake"]() queues the job that finishes Fast video; hooks["log"] writes to its log.
# Optional extra: hooks["baking"] = a function returning True while that job is waiting or running.
hooks = {"bake": None, "log": print}

FORMATS = ("GGUF", "MLX", "SAFETENSORS", "OTHER")
FOLDER = "MODELS"
SUBFOLDERS = ("GGUF", "MLX", "SAFETENSORS")
SAFETY_MARGIN = 2 * 1024 ** 3  # always leave 2 GB free
REPO_RE = re.compile(r"^[A-Za-z0-9][\w.\-]*/[\w.\-]+$")
OTHER_WEIGHTS = (".bin", ".pt", ".pth", ".ckpt", ".onnx", ".h5", ".msgpack", ".ot", ".tflite", ".mlmodel")
QUANT_RE = re.compile(r"(?i)(?<![a-z0-9])((?:i\d-)?(?:q\d+(?:_[a-z0-9]+)*|iq\d+(?:_[a-z0-9]+)*|f16|f32|bf16|fp16|fp32))(?![a-z0-9])")
SPLIT_RE = re.compile(r"-\d{5}-of-\d{5}")
XET_ONLY_OVER = 50 * 1000 ** 3  # huggingface_hub refuses plain HTTP downloads above 50 GB

_lock = threading.RLock()      # settings
jobs = []                      # every download, newest last
jobs_lock = threading.Lock()
queue_event = threading.Event()
_worker = None
_job_seq = 0


def log(*a):
    try:
        hooks["log"]("models:", *a)
    except Exception:
        pass


# ---------------------------------------------------------------- settings

_settings = None


def settings():
    global _settings
    with _lock:
        if _settings is None:
            try:
                _settings = json.loads(SETTINGS.read_text())
            except (OSError, ValueError):
                _settings = {}
            if not isinstance(_settings, dict):
                _settings = {}
        return _settings


def save_settings():
    with _lock:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS.with_name(SETTINGS.name + ".tmp")
        tmp.write_text(json.dumps(settings(), indent=1))
        os.replace(tmp, SETTINGS)


# ---------------------------------------------------------------- kits

# Sizes are the exact byte counts on Hugging Face (checked 2026-11-16), kept here so the page can
# tell what is installed without going online. A local file counts as present when it is at least
# 90% of that size: a file converted on this computer is a few bytes different from the download.
KITS = [
    {"id": "engine", "kind": "engine", "title": "The picture and video engine (ComfyUI)",
     "about": "The program that draws the pictures and films the videos. Everything else plugs into it."},
    {"id": "pictures", "kind": "files", "title": "Pictures (Qwen-Image 2.1 Turbo)",
     "about": "Draws every picture, and keeps characters looking the same from shot to shot.",
     "repo": "Comfy-Org/Qwen-Image-2.1",
     "files": [
         {"path": "diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors", "folder": "diffusion_models",
          "bytes": 7256787829, "what": "the picture model"},
         {"path": "text_encoders/qwen3vl_8b_w4a8.safetensors", "folder": "text_encoders",
          "bytes": 6312105364, "what": "reads your words, and the character pictures"},
         {"path": "vae/qwen_image_2.1_vae_bf16.safetensors", "folder": "vae",
          "bytes": 675509688, "what": "turns the result into a picture you can see"},
     ],
     "licence": {"name": "qwen-research", "url": "https://huggingface.co/Comfy-Org/Qwen-Image-2.1",
                 "note": "The makers mark these files with a research licence (qwen-research). "
                         "Read it on the model's page before you use the pictures commercially."}},
    {"id": "video", "kind": "files", "title": "Video (Wan2.2 5B)",
     "about": "Turns each picture into a moving clip, up to 5 seconds at a time.",
     "repo": "Comfy-Org/Wan-2.2",  # the repository once called Comfy-Org/Wan_2.2_ComfyUI_Repackaged
     "files": [
         {"path": "split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors", "folder": "diffusion_models",
          "bytes": 9999658848, "what": "the video model"},
         {"path": "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "folder": "text_encoders",
          "bytes": 6735906897, "what": "reads your words"},
         {"path": "split_files/vae/wan2.2_vae.safetensors", "folder": "vae",
          "bytes": 1409400960, "what": "turns the result into frames you can see"},
     ],
     "licence": {"name": "apache-2.0", "url": "https://huggingface.co/Comfy-Org/Wan-2.2", "note": ""}},
    {"id": "fast", "kind": "fast", "title": "Fast video (4 steps instead of 20)",
     "about": "An add-on that makes a video in a fraction of the time. Needs the Video kit.",
     "repo": "Kijai/WanVideo_comfy", "requires": "video",
     "files": [
         {"path": "LoRAs/Wan22-Turbo/Wan22_TI2V_5B_Turbo_lora_rank_64_fp16.safetensors", "folder": "loras",
          "bytes": 332348584, "what": "the speed-up add-on"},
     ],
     "merged": {"name": "wan2.2_ti2v_5B_turbo_fp16.safetensors", "folder": "diffusion_models", "bytes": 10000863280},
     "base": {"name": "wan2.2_ti2v_5B_fp16.safetensors", "folder": "diffusion_models", "bytes": 9999658848},
     "licence": {"name": "", "url": "https://huggingface.co/Kijai/WanVideo_comfy", "note": ""}},
    {"id": "writer", "kind": "ollama", "title": "Script writer (Ollama)",
     "about": "Writes the script, the characters and every shot from your idea. It runs on this computer.",
     "suggest": {"model": "qwen3.5:9b", "bytes": 6600000000}},
    {"id": "sounds", "kind": "sounds", "title": "Music sounds (orchestra and band)",
     "about": "The instrument recordings the Music section plays, and puts under your movies.",
     "wanted": 58},
]
KIT = {k["id"]: k for k in KITS}


def _have(folder, name, need):
    """Is this file in the engine's models folder, and big enough to be the real thing?"""
    p = COMFY_MODELS / folder / name
    try:
        return p.exists() and p.stat().st_size >= need * 0.9
    except OSError:
        return False


def _kit_files(kit):
    return [{"name": Path(f["path"]).name, "path": f["path"], "folder": f["folder"], "bytes": f["bytes"],
             "what": f.get("what", ""), "have": _have(f["folder"], Path(f["path"]).name, f["bytes"])}
            for f in kit.get("files", [])]


def _kit_job(kit_id, snap):
    """(the job working on this kit right now or waiting, the most recent job of any state)."""
    mine = [j for j in snap if j.get("kit") == kit_id]
    live = next((j for j in mine if j["state"] in ("queued", "running")), None)
    return live, (mine[-1] if mine else None)


def _engine_ready():
    return (COMFY_DIR / "main.py").is_file() and (COMFY_DIR / ".venv" / "bin" / "python").exists()


def _sounds_have():
    try:
        return sum(1 for p in (DATA / "music" / "sounds").iterdir() if p.suffix == ".json" and not p.name.startswith("."))
    except OSError:
        return 0


def _kit_state(kit, snap, block=False):
    """ready | missing | partial | needs_finish | downloading (a waiting download counts as downloading).
    block=True lets it wait a moment for Ollama's answer; the cheap summary never waits."""
    kind = kit["kind"]
    live, _ = _kit_job(kit["id"], snap)
    if kind == "engine":
        return "ready" if _engine_ready() else "missing"
    if kind == "sounds":
        n = _sounds_have()
        return "ready" if n >= kit["wanted"] else "partial" if n else "missing"
    if kind == "ollama":
        if live:
            return "downloading"
        w = writer_status(block=block)
        return "ready" if w["models"] else "missing"
    files = _kit_files(kit)
    if kind == "fast":
        m, b = kit["merged"], kit["base"]
        if _have(m["folder"], m["name"], m["bytes"]):
            return "ready"
        if live:
            return "downloading"
        if all(f["have"] for f in files):
            return "needs_finish" if _have(b["folder"], b["name"], b["bytes"]) else "partial"
        return "missing"
    if all(f["have"] for f in files):
        return "ready"
    if live:
        return "downloading"
    return "partial" if any(f["have"] for f in files) else "missing"


def kit_view(kit, snap):
    """Everything one kit card on the page shows."""
    live, last = _kit_job(kit["id"], snap)
    v = {"id": kit["id"], "kind": kit["kind"], "title": kit["title"], "about": kit["about"],
         "state": _kit_state(kit, snap, block=True), "requires": kit.get("requires", ""), "repo": kit.get("repo", ""),
         "licence": kit.get("licence"), "job": job_view(live) if live else None, "last": None}
    if last is not None and live is None and last["state"] in ("error", "cancelled"):
        v["last"] = {"state": last["state"], "error": last["error"]}
    kind = kit["kind"]
    if kind == "engine":
        v["detail"] = ("Installed." if v["state"] == "ready"
                       else "Not installed yet. Double-click setup.command in the app's folder.")
    elif kind == "sounds":
        v.update(have=_sounds_have(), wanted=kit["wanted"])
    elif kind == "ollama":
        v["writer"] = writer_status()
        v["suggest"] = kit["suggest"]
    else:
        files = _kit_files(kit)
        v["files"] = files
        v["bytes"] = sum(f["bytes"] for f in files)
        missing = {f["path"]: f["bytes"] for f in files if not f["have"]}
        # what is still to fetch: the missing files, less whatever a stopped download already brought in
        partial = {"kind": "kit", "dest": str(STAGING / kit["id"]), "files": missing, "total": sum(missing.values()),
                   "folders": {f["path"]: f["folder"] for f in files}}
        v["need"] = max(0, sum(missing.values()) - job_done_bytes(partial))
        if kind == "fast":
            m, b = kit["merged"], kit["base"]
            v["merged"] = _have(m["folder"], m["name"], m["bytes"])
            v["base"] = _have(b["folder"], b["name"], b["bytes"])
            v["merged_bytes"] = m["bytes"]
            if v["merged"]:
                v["need"] = 0  # the finished fast model is here, so the add-on itself is no longer needed
            # app.py may add hooks["baking"]: a function that says whether the finishing job is in its queue
            busy = hooks.get("baking")
            try:
                v["baking"] = bool(busy()) if callable(busy) else False
            except Exception:
                v["baking"] = False
    return v


def summary():
    """Small and cheap, for the Storyboard page: which kits are ready, and how many downloads are going."""
    with jobs_lock:
        snap = list(jobs)
    return {"kits": {k["id"]: _kit_state(k, snap) for k in KITS},
            "downloading": sum(1 for j in snap if j["state"] in ("queued", "running"))}


# ---------------------------------------------------------------- the script writer (Ollama)

_writer = {"at": 0.0, "value": {"running": False, "installed": False, "models": []}, "busy": False}


def _ollama_installed():
    return bool(shutil.which("ollama")) or any(Path(p).exists() for p in (
        "/usr/local/bin/ollama", "/opt/homebrew/bin/ollama", "/Applications/Ollama.app"))


def _ollama_check(timeout=1.5):
    """Ask Ollama which writing models it has. A writing model can complete text and runs on this computer."""
    out = {"running": False, "installed": _ollama_installed(), "models": []}
    try:
        with OPENER.open(OLLAMA_URL + "/api/tags", timeout=timeout) as r:
            data = json.loads(r.read() or b"{}")
    except Exception:
        return out
    out["running"] = out["installed"] = True
    for m in data.get("models") or []:
        name = m.get("name") or ""
        if not name or name.startswith("llamacpp:") or m.get("remote_host"):
            continue
        if "completion" in (m.get("capabilities") or []):
            out["models"].append(name)
    return out


def writer_status(block=True, max_age=4.0):
    """The script writer's state. block=False never waits: it returns what was seen last and looks again behind the scenes."""
    now = time.time()
    if now - _writer["at"] <= max_age:
        return _writer["value"]
    if block or not _writer["at"]:  # the very first look is a quick real one, so the answer is never a guess
        _writer["value"], _writer["at"] = _ollama_check(1.5 if block else 0.6), time.time()
        return _writer["value"]
    if not _writer["busy"]:
        _writer["busy"] = True

        def refresh():
            try:
                _writer["value"], _writer["at"] = _ollama_check(), time.time()
            finally:
                _writer["busy"] = False
        threading.Thread(target=refresh, daemon=True).start()
    return _writer["value"]


# ---------------------------------------------------------------- slots: which file each job uses

def _official_reader(n):
    return fnmatch.fnmatch(n, "qwen3vl_8b*.safetensors")


def _gguf_reader(n):
    return n.endswith(".gguf") and "qwen" in n and "mmproj" not in n


SLOTS = [
    {"name": "picture_model", "label": "Picture model", "does": "draws the pictures", "folder": "diffusion_models",
     "default": "qwen_image_2.1_turbo_int8_convrot.safetensors",
     "match": lambda n: fnmatch.fnmatch(n, "qwen_image_2.1_turbo*.safetensors")},
    {"name": "picture_reader", "label": "Picture prompt reader", "does": "reads the prompt for pictures",
     "folder": "text_encoders", "default": "qwen3vl_8b_w4a8.safetensors",
     "match": lambda n: _official_reader(n) or _gguf_reader(n)},
    {"name": "picture_vae", "label": "Picture finisher", "does": "turns the result into a picture", "folder": "vae",
     "default": "qwen_image_2.1_vae_bf16.safetensors",
     "match": lambda n: fnmatch.fnmatch(n, "qwen_image_2.1_vae*.safetensors")},
    {"name": "video_model", "label": "Video model", "does": "films the videos", "folder": "diffusion_models",
     "default": "wan2.2_ti2v_5B_fp16.safetensors",
     "match": lambda n: fnmatch.fnmatch(n, "wan2.2_ti2v_5b*.safetensors") and "turbo" not in n},
    {"name": "video_reader", "label": "Video prompt reader", "does": "reads the prompt for videos",
     "folder": "text_encoders", "default": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
     "match": lambda n: fnmatch.fnmatch(n, "umt5_xxl*.safetensors")},
    {"name": "video_vae", "label": "Video finisher", "does": "turns the result into frames", "folder": "vae",
     "default": "wan2.2_vae.safetensors",
     "match": lambda n: fnmatch.fnmatch(n, "wan2.2_vae*.safetensors")},
]
SLOT = {s["name"]: s for s in SLOTS}


def _slot_def(name):
    s = SLOT.get(name)
    if s is None:
        raise ValueError("There is no slot called " + str(name) + ".")
    return s


def _reader_kind(filename):
    return "gguf" if str(filename).lower().endswith(".gguf") else "official"


def _slot_files(s):
    """The files in the slot's folder that can go in it: the same family by name, or ones you put there yourself."""
    folder = COMFY_MODELS / s["folder"]
    adopted = set(settings().get("adopted", {}).get(s["name"], []))
    out = []
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        return out
    for n in names:
        if n.startswith(".") or not (s["match"](n.lower()) or n in adopted):
            continue
        p = folder / n
        if p.is_file():  # follows links, so a link to a missing file is left out
            out.append(n)
    return out


def slot(name):
    """The file chosen for a slot if it is still on disk, otherwise the slot's default name."""
    s = _slot_def(name)
    chosen = settings().get("slots", {}).get(name)
    if chosen and (COMFY_MODELS / s["folder"] / chosen).is_file():
        return chosen
    return s["default"]


def slot_kind(name):
    """"gguf" or "official" for the picture prompt reader, "" for every other slot."""
    return _reader_kind(slot(name)) if name == "picture_reader" else ""


def vision_reader():
    """An official picture prompt reader that is on this computer (the only kind that can look at
    character pictures): the chosen one if it is official, else any other. "" if there is none."""
    s = SLOT["picture_reader"]
    chosen = slot("picture_reader")
    if _reader_kind(chosen) == "official" and (COMFY_MODELS / s["folder"] / chosen).is_file():
        return chosen
    found = [n for n in _slot_files(s) if _official_reader(n.lower())]
    if s["default"] in found:
        return s["default"]
    return found[0] if found else ""


def slots_view():
    out = []
    for s in SLOTS:
        value, files = slot(s["name"]), _slot_files(s)
        folder = COMFY_MODELS / s["folder"]
        opts = [{"file": n, "default": n == s["default"], "link": (folder / n).is_symlink(),
                 "kind": _reader_kind(n) if s["name"] == "picture_reader" else ""} for n in files]
        out.append({"name": s["name"], "label": s["label"], "does": s["does"], "folder": s["folder"],
                    "default": s["default"], "value": value, "here": value in files, "options": opts,
                    "kind": _reader_kind(value) if s["name"] == "picture_reader" else ""})
    return out


def set_slot(name, filename):
    s = _slot_def(name)
    filename = str(filename or "")
    if filename != s["default"] and filename not in _slot_files(s):
        raise ValueError("That file is not one this job can use. Pick one from the list.")
    with _lock:
        chosen = settings().setdefault("slots", {})
        if filename == s["default"]:
            chosen.pop(name, None)
        else:
            chosen[name] = filename
        save_settings()
    log("slot", name, "->", filename)
    return {"ok": True, "name": name, "file": slot(name)}


def reset_slots():
    with _lock:
        settings()["slots"] = {}
        save_settings()
    log("slots back to the defaults")
    return {"ok": True}


def use_as(path, slot_name):
    """Put a downloaded file where the engine can see it (a link, not a copy) and choose it for a slot."""
    s = _slot_def(slot_name)
    p = Path(str(path or "")).expanduser()
    if not p.is_file():
        raise ValueError("That file is not there any more.")
    base = root().resolve()
    real = p.resolve()
    if base not in real.parents:
        raise ValueError("Only files in your models folder can be used this way.")
    low = p.name.lower()
    if low.endswith(".gguf"):
        if slot_name != "picture_reader":
            raise ValueError("A GGUF file can only be used as the picture prompt reader.")
    elif not low.endswith(".safetensors"):
        raise ValueError("Only .safetensors and .gguf files can be used in the movie maker.")
    if SPLIT_RE.search(p.name):
        raise ValueError("That model comes in several parts. The movie maker needs a single file.")
    if not COMFY_MODELS.is_dir():
        raise ValueError("The engine is not installed yet. Run setup.command in the app's folder first.")
    folder = COMFY_MODELS / s["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / p.name
    if target.is_symlink():
        target.unlink()  # an older link of the same name: point it at this file instead
    elif target.exists():
        if not target.samefile(real):
            raise ValueError(f"The engine already has a different file called {p.name} in its {s['folder']} folder. "
                             "Rename yours, or move that one away first.")
    if not target.exists():
        os.symlink(real, target)
    with _lock:
        adopted = settings().setdefault("adopted", {}).setdefault(slot_name, [])
        if p.name not in adopted:
            adopted.append(p.name)
        chosen = settings().setdefault("slots", {})
        if p.name == s["default"]:
            chosen.pop(slot_name, None)
        else:
            chosen[slot_name] = p.name
        save_settings()
    log("use", p.name, "as", slot_name)
    return {"ok": True, "slot": slot_name, "file": p.name, "label": s["label"]}


# ---------------------------------------------------------------- where free downloads are saved

def root():
    """The MODELS folder: MODELS_DIR in the environment, else the one chosen on the page, else ~/MODELS."""
    env = os.environ.get("MODELS_DIR")
    if env:
        return Path(env).expanduser()
    saved = settings().get("models_dir")
    return Path(saved) if saved else Path.home() / FOLDER


def _root_is_default():
    return not os.environ.get("MODELS_DIR") and not settings().get("models_dir")


def ready():
    """True when the save location can be written to right now. The default folder is made on first use."""
    r = root()
    if r.is_dir():
        return os.access(r, os.W_OK)
    return _root_is_default() and r.parent.is_dir() and os.access(r.parent, os.W_OK)


def ensure_root():
    r = root()
    if not ready():
        raise ValueError("Choose where to save first. If you use an external drive, check it is plugged in.")
    try:
        r.mkdir(exist_ok=True)
        for sub in SUBFOLDERS:
            (r / sub).mkdir(exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot write to {r}: {e.strerror or e}")
    return r


def set_root(path):
    p = Path(str(path or "")).expanduser()
    if not p.is_absolute():
        raise ValueError("Give the full path to a folder, for example /Users/you/MODELS.")
    if not p.parent.is_dir():
        raise ValueError(f"{p.parent} does not exist.")
    try:
        p.mkdir(exist_ok=True)
        for sub in SUBFOLDERS:
            (p / sub).mkdir(exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot write to {p}: {e.strerror or e}")
    with _lock:
        settings()["models_dir"] = str(p)
        save_settings()
    log("saving free downloads to", p)
    return p


def _diskutil(*args):
    try:
        out = subprocess.run(["diskutil", *args], capture_output=True, timeout=20)
        return plistlib.loads(out.stdout) if out.returncode == 0 and out.stdout else {}
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException, ValueError):
        return {}


def fs_family(name):
    n = (name or "").lower()
    if "exfat" in n:
        return "exfat"
    if "fat" in n or "msdos" in n:
        return "fat32"
    if "apfs" in n:
        return "apfs"
    if "hfs" in n or "mac os extended" in n:
        return "hfs"
    if "ntfs" in n:
        return "ntfs"
    if n.startswith(("ext", "btrfs", "xfs", "zfs", "f2fs")):
        return "linux"
    return "unknown"


FS_LABEL = {"exfat": "exFAT", "fat32": "FAT32", "apfs": "APFS", "hfs": "Mac OS Extended", "ntfs": "NTFS",
            "linux": "Linux format", "unknown": "Unknown format"}


def verdict(family, writable):
    """(level, message): level is good, warn or bad."""
    if family == "fat32":
        return "bad", ("FAT32 cannot hold any file larger than 4 GB, and most models are bigger than that. "
                       "This drive needs to be reformatted as exFAT before it can be used.")
    if family == "ntfs" and SYSTEM == "Darwin":
        return "bad", ("This is a Windows-only format (NTFS). A Mac can read it but cannot save to it. "
                       "Reformat it as exFAT to use it on both Mac and Windows.")
    if not writable:
        return "bad", "This drive is read-only, so nothing can be saved to it."
    if family == "exfat":
        return "good", "exFAT: no file size limit, and it works on Mac, Windows and Linux. Ideal for moving models between computers."
    if family in ("apfs", "hfs"):
        return "warn", ("Mac format: fast and reliable on a Mac, but Windows and most Linux computers cannot read it. "
                        "Fine if the models will only ever be used on Macs.")
    if family == "ntfs":
        return "warn", "NTFS: great on Windows and usable on Linux, but Macs can only read it, not save to it."
    if family == "linux":
        return "warn", "Linux format: fine on Linux, but Macs and Windows computers cannot read it."
    return "warn", "I do not recognise this format. It may work, but exFAT is the safe choice for model files."


def _usage(path):
    try:
        u = shutil.disk_usage(path)
        return u.free, u.total
    except OSError:
        return 0, 0


def _mac_volumes():
    out = []
    base = Path("/Volumes")
    try:
        root_dev = os.stat("/").st_dev
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        p = base / name
        try:
            if p.is_symlink() or not os.path.ismount(p) or os.stat(p).st_dev == root_dev:
                continue
        except OSError:
            continue
        info = _diskutil("info", "-plist", str(p))
        if info.get("BusProtocol") == "Disk Image":
            continue
        fam = fs_family(info.get("FilesystemType") or info.get("FilesystemName"))
        writable = bool(info.get("WritableVolume", os.access(p, os.W_OK)))
        out.append({"name": name, "mount": str(p), "family": fam, "writable": writable})
    return out


def _linux_volumes():
    out = []
    try:
        lines = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mount = parts[1].replace("\\040", " ")
        if not mount.startswith(("/media/", "/run/media/", "/mnt/")):
            continue
        out.append({"name": Path(mount).name, "mount": mount, "family": fs_family(parts[2]),
                    "writable": os.access(mount, os.W_OK)})
    return out


def _windows_volumes():
    out = []
    try:
        import ctypes
        import string
        k32 = ctypes.windll.kernel32
        system = os.environ.get("SystemDrive", "C:").upper()
        mask = k32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            drive = f"{letter}:"
            if not mask & (1 << i) or drive == system or k32.GetDriveTypeW(drive + "\\") not in (2, 3):
                continue
            label, fs = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(261)
            if not k32.GetVolumeInformationW(drive + "\\", label, 261, None, None, None, fs, 261):
                continue
            out.append({"name": f"{label.value or 'Drive'} ({drive})", "mount": drive + "\\", "family": fs_family(fs.value),
                        "writable": os.access(drive + "\\", os.W_OK)})
    except Exception:
        pass
    return out


def volumes():
    return {"Darwin": _mac_volumes, "Linux": _linux_volumes, "Windows": _windows_volumes}.get(SYSTEM, list)()


def locations():
    """Everything the 'where should I save?' box needs. This app never formats a drive: it only says what is wrong."""
    home = Path.home()
    free, total = _usage(home)
    local = {"kind": "local", "name": "This computer", "path": str(home / FOLDER), "free": free, "total": total,
             "level": "good" if free > 30e9 else "warn",
             "advice": ("Saves into a MODELS folder in your home folder. Simple, and the fastest to load from."
                        if free > 30e9 else
                        f"Only {free / 1e9:.0f} GB free here. Fine for small models, but big ones will not fit."),
             "format": ""}
    options = [local]
    for v in volumes():
        free, total = _usage(v["mount"])
        level, advice = verdict(v["family"], v["writable"])
        options.append({"kind": "external", "name": v["name"], "path": str(Path(v["mount"]) / FOLDER), "mount": v["mount"],
                        "free": free, "total": total, "level": level, "advice": advice, "format": FS_LABEL[v["family"]]})
    return {"current": str(root()), "ready": ready(), "system": SYSTEM, "options": options}


# ---------------------------------------------------------------- looking a model up (Hugging Face)

_hf = None


def hf_api():
    global _hf
    if _hf is None:
        try:
            from huggingface_hub import HfApi
        except ImportError:
            raise ValueError("The download helper is missing. Run setup.command in the app's folder, then try again.")
        _hf = HfApi()
    return _hf


def hf_token():
    try:
        from huggingface_hub import get_token
        return get_token()
    except Exception:
        return None


def hf_bin():
    """The `hf` command: next to this Python first (the engine's own environment has it)."""
    return (os.environ.get("MODELS_HF_BIN") or shutil.which("hf", path=str(Path(sys.executable).parent))
            or shutil.which("hf") or "")


def parse_query(q):
    """Return a repo id if the text looks like one (or a HF URL), else None."""
    q = q.strip()
    if q.startswith("hf.co/"):
        q = "https://" + q
    if "huggingface.co" in q or q.startswith("https://hf.co/"):
        if "://" not in q:
            q = "https://" + q
        parts = [p for p in urlparse(q).path.split("/") if p]
        if parts and parts[0] == "models":
            parts = parts[1:]
        if len(parts) >= 2:
            q = parts[0] + "/" + parts[1]
    return q if REPO_RE.match(q) else None


MEDIA_EXT = (".mp4", ".mov", ".webm", ".gif", ".png", ".jpg", ".jpeg", ".webp", ".avif", ".svg",
             ".wav", ".mp3", ".flac", ".pdf")
VERSION_RE = re.compile(r"(?i)(?<![a-z0-9])v(\d+(?:\.\d+)*)(?![0-9])")

GGUF_NOTES = {
    1: "Extremely squeezed. Quality suffers badly; avoid unless nothing else fits.",
    2: "Tiny. Quality drops a lot; only if nothing else fits.",
    3: "Small. Noticeably less accurate than the original.",
    4: "Good balance of size and quality. The usual pick.",
    5: "A little better than Q4, a little bigger.",
    6: "Very close to the original quality.",
    8: "Practically identical to the original. Big.",
    16: "Uncompressed original. Largest, and rarely worth it over Q8.",
    32: "Full 32-bit precision. Twice the size of the original with no visible gain.",
}

# pipeline tag -> (family, plain-English name, one-line explanation)
PIPELINES = {
    "text-generation": ("chat", "Chat / writing model", "Answers questions, writes and codes, like ChatGPT."),
    "image-text-to-text": ("chat", "Chat model that can see images", "Chats like ChatGPT and can also look at pictures you give it."),
    "any-to-any": ("chat", "Multi-purpose chat model", "Chats and can handle more than text, such as images or audio."),
    "text-to-image": ("media", "Image generator", "Makes pictures from a text description."),
    "image-to-image": ("media", "Image editor", "Changes or restyles a picture you give it."),
    "text-to-video": ("media", "Video generator", "Makes video clips from a text description."),
    "image-to-video": ("media", "Video generator", "Turns a picture (plus a description) into a video clip."),
    "video-to-video": ("media", "Video editor", "Changes or restyles a video you give it."),
    "text-to-3d": ("media", "3D model generator", "Makes 3D objects from a description."),
    "image-to-3d": ("media", "3D model generator", "Turns a picture into a 3D object."),
    "automatic-speech-recognition": ("audio", "Speech-to-text", "Writes down what is said in an audio recording."),
    "text-to-speech": ("audio", "Voice generator", "Reads text out loud in a generated voice."),
    "text-to-audio": ("audio", "Sound / music generator", "Makes music or sound effects from a description."),
    "feature-extraction": ("embed", "Embedding model", "Turns text into numbers for search tools. It does not chat."),
    "sentence-similarity": ("embed", "Embedding model", "Compares pieces of text for search tools. It does not chat."),
}


def about_model(pipeline, tags, fmts):
    family, kind, blurb = PIPELINES.get(pipeline, ("other", (pipeline or "AI model").replace("-", " ").capitalize(),
                                                   "A specialised model. Its page explains what it does."))
    comfy = "comfyui" in tags
    runs = {}
    for fmt in fmts:
        warn = ""
        if family == "chat":
            if fmt == "GGUF":
                soft, comp = "Ollama, llama.cpp, LM Studio", "Mac, Windows or Linux. Any graphics card, or none at all."
            elif fmt == "MLX":
                soft, comp = "LM Studio or mlx-lm", "Apple Silicon Macs only (M1 and newer)."
            else:
                soft, comp = "transformers, vLLM, text-generation-webui", "Best with an NVIDIA graphics card. Works on Macs, but slower."
                warn = "Ollama and llama.cpp cannot open this format directly. For those, look for a GGUF version of this model."
        elif family == "media":
            if fmt == "GGUF":
                soft = "ComfyUI with the GGUF add-on"
            elif fmt == "MLX":
                soft = "MLX apps on a Mac"
            else:
                soft = "ComfyUI" if comfy else "ComfyUI or diffusers"
            comp = "Apple Silicon Macs only (M1 and newer)." if fmt == "MLX" else \
                "Best with an NVIDIA graphics card. Macs can run it, but slower."
            warn = "This is not a chat model, so it will not run in Ollama or llama.cpp."
        else:
            soft = {"GGUF": "llama.cpp-based apps (check the model page)", "MLX": "MLX apps on a Mac"}.get(
                fmt, "transformers (check the model page for the exact app)")
            comp = "Apple Silicon Macs only (M1 and newer)." if fmt == "MLX" else "Mac, Windows or Linux."
            if family in ("audio", "embed"):
                warn = "This is not a chat model."
        runs[fmt] = {"software": soft, "computer": comp, "warn": warn}
    return {"family": family, "kind": kind, "blurb": blurb, "runs": runs}


def file_kind(name):
    low = name.lower()
    if low.endswith(".gguf"):
        return "gguf"
    if low.endswith(".safetensors"):
        return "safetensors"
    if low.endswith(OTHER_WEIGHTS):
        return "other"
    return "aux"


def weight_meta(name, kind, size):
    """Plain-English description of one weight file, worked out from its name."""
    base = Path(name).name
    low, path_low = base.lower(), name.lower()
    toks = set(re.split(r"[^a-z0-9]+", low))
    m = VERSION_RE.search(base)
    meta = {"role": "main", "tier": None, "pruned": False, "quant": "",
            "version": [int(x) for x in m.group(1).split(".")] if m else None,
            "need": int(size * 1.2 + 1.5e9)}
    title, notes = [], []
    if meta["version"]:
        title.append("v" + m.group(1))
    if kind == "gguf":
        if "mmproj" in low:
            meta.update(role="addon", need=0)
            return meta | {"title": "Vision add-on", "note": "Lets the chat model see images. Download it together with one main file."}
        q = QUANT_RE.search(base)
        if q:
            quant = q.group(1).upper()
            meta["quant"] = quant
            d = re.search(r"\d+", quant.split("-")[-1])
            tier = int(d.group()) if d else None
            if quant.startswith(("F", "BF")):
                tier = 32 if "32" in quant else 16
            meta["tier"] = tier
            title = [quant]
            notes.append(GGUF_NOTES.get(tier, "A compressed version."))
            if quant.startswith("IQ"):
                notes.append("Uses a newer compression method that keeps a bit more quality for its size.")
    else:
        if "lora" in toks or "lora" in path_low:
            meta.update(role="addon", need=0)
            return meta | {"title": "LoRA add-on", "note": "A small add-on, not a full model. It needs the base model too."}
        if "vae" in toks or "/vae" in "/" + path_low:
            meta.update(role="helper", need=0)
            return meta | {"title": "VAE helper", "note": "A helper file the main model uses to turn its output into pictures."}
        if any(t in path_low for t in ("text_encoder", "clip", "umt5", "t5xxl", "t5_xxl")):
            meta.update(role="helper", need=0)
            return meta | {"title": "Text encoder", "note": "A helper file the main model uses to read your prompt."}
        if toks & {"w4a8", "int4", "4bit", "nf4", "q4", "fp4"}:
            meta["tier"] = 4
            title.append("4-bit")
            notes.append("4-bit: the smallest, with some loss in quality.")
        elif toks & {"int8", "fp8", "8bit", "q8"} or "fp8" in low:
            meta["tier"] = 8
            title.append("8-bit")
            notes.append("8-bit: about half the size, with quality very close to full.")
        elif toks & {"bf16", "fp16", "f16", "half"}:
            meta["tier"] = 16
            title.append("Full quality")
            notes.append("Full quality (16-bit). The best this model can look, and the biggest.")
        elif toks & {"fp32", "f32"}:
            meta["tier"] = 32
            title.append("32-bit")
            notes.append("Full 32-bit precision. Twice the size with no visible gain.")
        if "pruned" in toks:
            meta["pruned"] = True
            title.append("pruned")
            notes.append("Pruned: slimmed down to save space, so slightly lower quality.")
        if toks & {"distill", "distilled", "turbo", "lightning", "lcm", "schnell"}:
            title.append("fast")
            notes.append("Fast version: quicker results in fewer steps.")
    meta["title"] = " · ".join(title) if title else Path(base).stem
    meta["note"] = " ".join(notes)
    return meta


def build_view(fmt, files, whole):
    """Group a repo's files for one format tab: choice / required / helper / extra."""
    has_st = any(file_kind(n) == "safetensors" for n, _ in files)
    groups = {}
    for name, size in files:
        kind = file_kind(name)
        low = name.lower()
        if fmt == "GGUF":
            if kind == "gguf":
                group = "choice"
            elif low in ("readme.md", "license", "license.md", "license.txt"):
                group = "helper"
            else:
                continue
        else:
            if kind == "gguf":
                continue
            if low.endswith(MEDIA_EXT) or low.endswith(".gitkeep") or "tfevents" in low or low.startswith("runs/") \
                    or (kind == "other" and has_st):
                group = "extra"
            elif kind == "safetensors":
                group = "required" if whole else "choice"
            elif kind == "other":
                group = "required"
            else:
                group = "helper"
        key = SPLIT_RE.sub("", name) if kind == "gguf" else name
        g = groups.setdefault(key, {"label": key, "kind": kind, "group": group, "files": [], "size": 0})
        g["files"].append(name)
        g["size"] += size
    items = []
    for g in groups.values():
        g["parts"] = len(g["files"])
        if g["group"] == "choice":
            g.update(weight_meta(g["label"], g["kind"], g["size"]))
        items.append(g)
    items.sort(key=lambda g: (-g["size"] if g["group"] == "choice" else g["size"]))
    mode = "choose" if any(g["group"] == "choice" for g in items) else "whole"
    need = 0
    if mode == "whole":
        need = int(sum(g["size"] for g in items if g["group"] == "required") * 1.2 + 1.5e9)
    return {"mode": mode, "items": items, "need": need}


def repo_scan(repo):
    info = hf_api().model_info(repo, files_metadata=True)
    files = [(s.rfilename, s.size or 0) for s in info.siblings or [] if s.rfilename != ".gitattributes"]
    return info, files


def gate_state(info):
    """"" for an open model, otherwise "ok" (this account may download it),
    "login" (nobody is logged in) or "locked" (terms not accepted yet)."""
    if not info.gated:
        return ""
    if not hf_token():
        return "login"
    try:
        hf_api().auth_check(info.id)
        return "ok"
    except Exception:
        return "locked"


def repo_details(repo):
    info, files = repo_scan(repo)
    names = {n.lower() for n, _ in files}
    kinds = {file_kind(n) for n, _ in files}
    st = [n for n, _ in files if file_kind(n) == "safetensors"]
    tags = list(info.tags or [])
    is_mlx = "mlx" in tags or repo.lower().startswith("mlx-community/")
    available = []
    if "gguf" in kinds:
        available.append("GGUF")
    if is_mlx:
        available.append("MLX")
    elif "safetensors" in kinds:
        available.append("SAFETENSORS")
    if not available:
        available.append("OTHER")
    detected = "MLX" if is_mlx else "SAFETENSORS" if "safetensors" in kinds else available[0]
    # One model split over several files, or several alternative versions of it?
    whole = (is_mlx or len(st) <= 1 or "config.json" in names or "model_index.json" in names
             or any(SPLIT_RE.search(n) for n in st))
    return {
        "type": "repo",
        "repo": info.id,
        "gated": gate_state(info),
        "downloads": info.downloads or 0,
        "likes": info.likes or 0,
        "pipeline": info.pipeline_tag or "",
        "license": next((t.split(":", 1)[1] for t in tags if t.startswith("license:")), ""),
        "available": available,
        "detected": detected,
        "about": about_model(info.pipeline_tag or "", tags, available),
        "views": {f: build_view(f, files, whole) for f in available},
    }


def search(q, fmt):
    kw = {"search": q, "sort": "downloads", "limit": 30}
    flt = {"GGUF": "gguf", "MLX": "mlx", "SAFETENSORS": "safetensors"}.get(fmt)
    if flt:
        kw["filter"] = flt
    out = []
    for m in hf_api().list_models(**kw):
        tags = m.tags or []
        out.append({
            "repo": m.id,
            "downloads": m.downloads or 0,
            "likes": m.likes or 0,
            "kind": PIPELINES.get(m.pipeline_tag or "", ("", "", ""))[1],
            "formats": [f for f, t in (("GGUF", "gguf"), ("MLX", "mlx"), ("SAFETENSORS", "safetensors")) if t in tags],
        })
    return {"type": "search", "results": out}


def resolve(q, fmt):
    q = (q or "").strip()
    if not q:
        raise ValueError("Type a model name, a search term or a Hugging Face link.")
    repo = parse_query(q)
    if repo:
        try:
            return repo_details(repo)
        except ValueError:
            raise
        except Exception as e:
            if "huggingface.co" in q:
                raise ValueError(f"Could not open {repo}: {type(e).__name__}")
    return search(q, fmt)


# ---------------------------------------------------------------- the download queue

def cache_env(base):
    """Keep every byte of cache next to the files being fetched, never on some other disk."""
    return {
        "HF_HUB_CACHE": str(Path(base) / ".hf-cache" / "hub"),
        "HF_XET_CACHE": str(Path(base) / ".hf-cache" / "xet"),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        # Plain HTTP downloads grow the partial file byte by byte, which is what makes the progress
        # bar and resume trustworthy. Files over 50 GB cannot be fetched any other way than xet.
        "HF_HUB_DISABLE_XET": "1",
    }


def _existing(path):
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def _free(path):
    try:
        return shutil.disk_usage(_existing(path)).free
    except OSError:
        return 0


def _dev(path):
    try:
        return os.stat(_existing(path)).st_dev
    except OSError:
        return None


def _final(job, path):
    """Where a kit file ends up: the engine's folder for it, under the file's own name."""
    return COMFY_MODELS / job["folders"][path] / Path(path).name


def job_done_bytes(job):
    if job["kind"] == "ollama":
        return min(job.get("done_bytes", 0), job["total"])
    dest = Path(job["dest"])
    done = 0
    for f, size in job["files"].items():
        if job["kind"] == "kit":
            try:
                final = _final(job, f)
                if final.exists() and final.stat().st_size >= size * 0.9:
                    done += size
                    continue
            except OSError:
                pass
        try:
            done += min((dest / f).stat().st_size, size)
        except OSError:
            pass
    inc = dest / ".cache" / "huggingface" / "download"
    if inc.is_dir():
        for p in inc.rglob("*.incomplete"):
            if p.name.startswith("._"):
                continue
            try:
                done += p.stat().st_size
            except OSError:
                pass
    return min(done, job["total"])


def job_view(job):
    done = job["total"] if job["state"] == "done" else job_done_bytes(job)
    now = time.time()
    speed = 0
    if job["state"] == "running":
        s = job["samples"]
        s.append((now, done))
        while len(s) > 2 and now - s[0][0] > 20:
            s.pop(0)
        if len(s) > 1 and s[-1][0] > s[0][0]:
            speed = max(0, (s[-1][1] - s[0][1]) / (s[-1][0] - s[0][0]))
    return {k: job[k] for k in ("id", "kind", "kit", "title", "repo", "format", "dest", "total", "state", "error")} | {
        "done": done, "speed": speed, "count": len(job["files"]), "note": job.get("note", ""),
    }


def _new_job(**kw):
    global _job_seq
    _job_seq += 1
    job = {"id": f"{int(time.time() * 1000):x}{_job_seq:02x}", "kind": "free", "kit": "", "title": "", "repo": "",
           "format": "", "dest": "", "root": "", "files": {}, "folders": {}, "total": 0, "state": "queued",
           "error": "", "cancel": False, "proc": None, "conn": None, "samples": [], "note": ""}
    job.update(kw)
    return job


def _pending_on(dev, snap):
    """Bytes that downloads already in the queue will still write to the same disk."""
    return sum(j["total"] - job_done_bytes(j) for j in snap
               if j["state"] in ("queued", "running") and j["kind"] != "ollama" and _dev(j["dest"]) == dev)


def _room_check(target, need, snap):
    free = _free(target)
    need = need + _pending_on(_dev(target), snap)
    if need > free - SAFETY_MARGIN:
        raise ValueError(f"Not enough room there: this needs {need / 1e9:.1f} GB and only "
                         f"{free / 1e9:.1f} GB is free (I always leave 2 GB spare).")


def write_model_info(job):
    path = Path(job["dest"]) / "model-info.json"
    data = {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    files = dict(data.get("files", {}))
    files.update(job["files"])
    data.update({
        "repo": job["repo"],
        "format": job["format"],
        "source": "https://huggingface.co/" + job["repo"],
        "downloaded": datetime.now().isoformat(timespec="seconds"),
        "files": files,
    })
    path.write_text(json.dumps(data, indent=2))


def _adopt(job):
    """Move every kit file that has finished downloading out of the staging area into the engine's folder."""
    dest = Path(job["dest"])
    for f, size in job["files"].items():
        staged = dest / f
        try:
            if staged.is_symlink() or not staged.is_file():
                continue
            final = _final(job, f)
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists() and final.stat().st_size >= size * 0.9:
                staged.unlink()  # a good copy is already in place: keep that one
                continue
            if final.is_symlink():
                final.unlink()   # a link to something that is gone
            os.replace(staged, final)
            log("kit", job["kit"], "file ready:", final.name)
        except OSError as e:
            log("could not move", staged, e)


def _hf_download(job, files):
    """Run `hf download` for a job. Returns (exit code, the last lines it printed)."""
    hf = hf_bin()
    if not hf:
        raise RuntimeError("The download helper (hf) is not installed. Run setup.command in the app's folder.")
    dest = Path(job["dest"])
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [hf, "download", job["repo"], *files, "--local-dir", str(dest)]
    if shutil.which("caffeinate"):  # macOS: keep the computer awake while downloading
        cmd = ["caffeinate", "-i", *cmd]
    env = os.environ | cache_env(Path(job["root"]))
    if max(job["files"].values()) > XET_ONLY_OVER:
        del env["HF_HUB_DISABLE_XET"]  # Hugging Face only serves files this big through xet
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                            start_new_session=os.name == "posix")
    job["proc"] = proc
    tail = b""
    while True:
        chunk = proc.stdout.read1(65536)
        if not chunk:
            break
        tail = (tail + chunk)[-6000:]
    code = proc.wait()
    job["proc"] = None
    return code, tail


def _plain_tail(tail, code):
    lines = [l.strip() for l in re.split(r"[\r\n]+", tail.decode("utf-8", "replace")) if l.strip()]
    lines = [l for l in lines if "%|" not in l and "new version of huggingface_hub" not in l and "hf.co/cli/install" not in l]
    return "\n".join(lines[-6:]) or f"hf exited with code {code}"


def run_free_job(job):
    code, tail = _hf_download(job, list(job["files"]))
    if job["cancel"]:
        job["state"] = "cancelled"
    elif code == 0:
        write_model_info(job)
        job["state"] = "done"
    else:
        job["error"] = _plain_tail(tail, code)
        job["state"] = "error"


def run_kit_job(job):
    """Fetch a kit's missing files into the staging area and move each into place as soon as it is whole."""
    _adopt(job)  # anything a stopped earlier try already finished
    missing = [f for f, size in job["files"].items() if not _have(job["folders"][f], Path(f).name, size)]
    code, tail = 0, b""
    if missing:
        stop = threading.Event()

        def watch():
            while not stop.wait(1.0):
                _adopt(job)
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            code, tail = _hf_download(job, missing)
        finally:
            stop.set()
            watcher.join(5)
        _adopt(job)
    if job["cancel"]:
        job["state"] = "cancelled"
        return
    still = [Path(f).name for f, size in job["files"].items() if not _have(job["folders"][f], Path(f).name, size)]
    if code == 0 and not still:
        shutil.rmtree(job["dest"], ignore_errors=True)  # only download notes are left in there
        try:  # and the staging area itself, once no other kit has anything waiting in it
            if not any(p.is_dir() and not p.name.startswith(".") for p in STAGING.iterdir()):
                shutil.rmtree(STAGING, ignore_errors=True)
        except OSError:
            pass
        job["state"] = "done"
    else:
        job["error"] = _plain_tail(tail, code) if code else "The download ended but this is still missing: " + ", ".join(still)
        job["state"] = "error"


def run_ollama_job(job):
    """Ask Ollama to fetch a writing model and follow its progress line by line."""
    u = urlparse(OLLAMA_URL)
    conn = http.client.HTTPConnection(u.hostname or "127.0.0.1", u.port or 11434, timeout=300)
    job["conn"] = conn
    try:
        conn.request("POST", "/api/pull", json.dumps({"model": job["repo"], "stream": True}),
                     {"Content-Type": "application/json"})
        resp = conn.getresponse()
        layers = {}
        while True:
            line = resp.readline()
            if not line or job["cancel"]:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("error"):
                raise RuntimeError(str(msg["error"]))
            if msg.get("digest") and msg.get("total"):
                layers[msg["digest"]] = (int(msg.get("completed") or 0), int(msg["total"]))
                job["total"] = max(sum(t for _, t in layers.values()), 1)
                job["done_bytes"] = sum(c for c, _ in layers.values())
            job["note"] = str(msg.get("status") or "")
            if msg.get("status") == "success":
                job["done_bytes"] = job["total"]
                job["state"] = "done"
                return
        if job["cancel"]:
            job["state"] = "cancelled"
        else:
            raise RuntimeError("Ollama stopped answering before the model was finished.")
    except Exception as e:
        if job["cancel"]:
            job["state"] = "cancelled"
        else:
            job["state"], job["error"] = "error", str(e) or "Ollama could not download that model."
    finally:
        job["conn"] = None
        _writer["at"] = 0.0  # look at the model list afresh
        try:
            conn.close()
        except Exception:
            pass


def worker():
    while True:
        queue_event.wait()
        with jobs_lock:
            job = next((j for j in jobs if j["state"] == "queued"), None)
            if job is None:
                queue_event.clear()
                continue
            job["state"] = "running"
        log("download started:", job["title"] or job["repo"])
        try:
            if job["kind"] == "kit":
                run_kit_job(job)
            elif job["kind"] == "ollama":
                run_ollama_job(job)
            else:
                if not Path(job["root"]).is_dir():
                    raise RuntimeError("The save location is not available. Is the drive plugged in?")
                run_free_job(job)
        except Exception as e:  # keep the queue alive whatever happens
            job["state"], job["error"] = ("cancelled", "") if job["cancel"] else ("error", str(e))
        log("download", job["state"] + ":", job["title"] or job["repo"], job["error"].replace("\n", " / ")[:300])


def _enqueue(job):
    global _worker
    jobs.append(job)  # the caller holds jobs_lock
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=worker, daemon=True)
        _worker.start()
    queue_event.set()


def dest_for(fmt, repo):
    org, name = repo.split("/", 1)
    return root() / fmt / org / name


def start_download(body):
    """A free download: the files picked on the notepad, saved into MODELS/<format>/<maker>/<model>/."""
    repo, fmt, files = body.get("repo", ""), body.get("format", ""), body.get("files") or []
    if not REPO_RE.match(str(repo)) or fmt not in FORMATS or not files:
        raise ValueError("Pick a model, a format and at least one file.")
    base = ensure_root()
    sizes = dict(repo_scan(repo)[1])
    unknown = [f for f in files if f not in sizes]
    if unknown:
        raise ValueError("Not a file in this repo: " + str(unknown[0]))
    chosen = {f: sizes[f] for f in files}
    dest = dest_for(fmt, repo)
    total = sum(chosen.values())
    probe = {"kind": "free", "dest": str(dest), "files": chosen, "total": total}
    with jobs_lock:
        if any(j["dest"] == str(dest) and j["state"] in ("queued", "running") for j in jobs):
            raise ValueError("That model is already downloading. Wait for it to finish, then add more files.")
        _room_check(dest, total - job_done_bytes(probe), jobs)
        job = _new_job(kind="free", title=repo, repo=repo, format=fmt, dest=str(dest), root=str(base),
                       files=chosen, total=total)
        _enqueue(job)
    return job_view(job)


def start_kit(kit_id):
    """Download whatever a kit is still missing."""
    kit = KIT.get(str(kit_id))
    if kit is None or kit["kind"] not in ("files", "fast"):
        raise ValueError("That is not a kit I can download.")
    if not COMFY_DIR.is_dir():
        raise ValueError("The engine is not installed yet. Run setup.command in the app's folder first.")
    if kit["kind"] == "fast":
        m = kit["merged"]
        if _have(m["folder"], m["name"], m["bytes"]):
            raise ValueError("Fast video is already set up.")
    files = {f["path"]: f["bytes"] for f in kit["files"] if not _have(f["folder"], Path(f["path"]).name, f["bytes"])}
    if not files:
        raise ValueError("Everything in that kit is already here.")
    folders = {f["path"]: f["folder"] for f in kit["files"]}
    dest = STAGING / kit["id"]
    total = sum(files.values())
    probe = {"kind": "kit", "dest": str(dest), "files": files, "folders": folders, "total": total}
    with jobs_lock:
        if any(j["kit"] == kit["id"] and j["state"] in ("queued", "running") for j in jobs):
            raise ValueError("That kit is already downloading.")
        _room_check(COMFY_MODELS, total - job_done_bytes(probe), jobs)
        job = _new_job(kind="kit", kit=kit["id"], title=kit["title"], repo=kit["repo"], format="KIT",
                       dest=str(dest), root=str(STAGING), files=files, folders=folders, total=total)
        _enqueue(job)
    return job_view(job)


def start_writer_pull(model=None):
    """Ask Ollama to download a writing model (the suggested one unless another is named)."""
    kit = KIT["writer"]
    model = str(model or kit["suggest"]["model"]).strip()
    if not re.match(r"^[A-Za-z0-9][\w.\-/]*(:[\w.\-]+)?$", model) or len(model) > 120:
        raise ValueError("That does not look like an Ollama model name.")
    if not writer_status(max_age=0)["running"]:
        raise ValueError("Ollama is not running. Start it (or open the Movies page once, which starts it), then try again.")
    with jobs_lock:
        if any(j["kind"] == "ollama" and j["state"] in ("queued", "running") for j in jobs):
            raise ValueError("A writing model is already downloading.")
        job = _new_job(kind="ollama", kit="writer", title=f"Script writer: {model}", repo=model, format="OLLAMA",
                       dest="", root="", files={model: 0}, total=kit["suggest"]["bytes"] if model == kit["suggest"]["model"] else 1)
        _enqueue(job)
    return job_view(job)


def cancel_job(job_id):
    with jobs_lock:
        job = next((j for j in jobs if j["id"] == job_id), None)
        if not job:
            return
        job["cancel"] = True
        if job["state"] == "queued":
            job["state"] = "cancelled"
        proc, conn = job["proc"], job["conn"]
    if conn is not None:  # an Ollama download: hanging up stops it, and it resumes from there next time
        try:
            conn.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
    if proc and proc.poll() is None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, sig)
                else:
                    proc.kill()
            except (ProcessLookupError, OSError):
                break
            try:
                proc.wait(timeout=3)
                break
            except subprocess.TimeoutExpired:
                continue


def shutdown():
    """Stop every download that is running (they pick up where they stopped next time)."""
    for j in list(jobs):
        if j["state"] in ("queued", "running"):
            cancel_job(j["id"])


atexit.register(shutdown)


# ---------------------------------------------------------------- what is on the drive

def _fits(name):
    """The slots a downloaded file could go in, judged by its ending only."""
    low = name.lower()
    if SPLIT_RE.search(name) or "mmproj" in low:
        return []
    if low.endswith(".gguf"):
        return ["picture_reader"]
    if low.endswith(".safetensors"):
        return [s["name"] for s in SLOTS]
    return []


def library():
    out = []
    base = root()
    if not base.is_dir():
        return out
    for fmt in FORMATS:
        fdir = base / fmt
        if not fdir.is_dir():
            continue
        for org in sorted(p for p in fdir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            for repo in sorted(p for p in org.iterdir() if p.is_dir() and not p.name.startswith(".")):
                size = n = 0
                weights = []
                for top, dirs, files in os.walk(repo):
                    dirs[:] = sorted(d for d in dirs if d != ".cache")
                    for f in sorted(files):
                        if f.startswith("._") or f in ("model-info.json", ".DS_Store"):
                            continue
                        fp = Path(top) / f
                        try:
                            fsize = fp.stat().st_size
                        except OSError:
                            continue
                        size += fsize
                        n += 1
                        if f.lower().endswith((".gguf", ".safetensors")) and len(weights) < 60:
                            weights.append({"name": str(fp.relative_to(repo)), "path": str(fp), "size": fsize,
                                            "slots": _fits(f), "parts": bool(SPLIT_RE.search(f))})
                out.append({"format": fmt, "repo": f"{org.name}/{repo.name}", "path": str(repo),
                            "size": size, "files": n, "weights": weights})
    return out


def reveal(path):
    p = Path(str(path or "")).expanduser().resolve()
    roots = {Path(j["root"]).resolve() for j in jobs if j["root"]} | {root().resolve(), COMFY_MODELS.resolve()}
    if not p.exists() or not any(r in (p, *p.parents) for r in roots):
        raise ValueError("Not a folder inside the models folders.")
    if SYSTEM == "Windows":
        os.startfile(str(p))  # noqa: only exists on Windows
    else:
        subprocess.Popen(["open" if SYSTEM == "Darwin" else "xdg-open", str(p)])


# ---------------------------------------------------------------- the page and its requests

def status():
    with jobs_lock:
        snap = list(jobs)
    ok = ready()
    r = root()
    free, total = _usage(_existing(r)) if ok else (0, 0)
    return {
        "drive": ok, "path": str(r), "free": free, "total": total,
        "jobs": [job_view(j) for j in snap],
        "kits": [kit_view(k, snap) for k in KITS],
        "slots": slots_view(),
        "vision_reader": vision_reader(),
        "engine_free": _free(COMFY_MODELS) if COMFY_DIR.is_dir() else 0,
        "gguf_addon": (COMFY_DIR / "custom_nodes" / "ComfyUI-GGUF").is_dir(),
        "hooked": hooks.get("bake") is not None,
        "system": SYSTEM,
    }


def bake():
    """The last step of the Fast video kit: app.py bakes the add-on into its own copy of the video model."""
    kit = KIT["fast"]
    m, b = kit["merged"], kit["base"]
    if _have(m["folder"], m["name"], m["bytes"]):
        raise ValueError("Fast video is already set up.")
    lora = kit["files"][0]
    if not _have(lora["folder"], Path(lora["path"]).name, lora["bytes"]) or not _have(b["folder"], b["name"], b["bytes"]):
        raise ValueError("Get the Video kit and the Fast video add-on first.")
    fn = hooks.get("bake")
    if fn is None:
        raise ValueError("Open this from the Storyboard app.")
    return fn() or {"ok": True}


def handle_get(h, path, query):
    """Answer GET /models and /models/... . Returns True when it did."""
    try:
        if path in ("/models", "/models/"):
            h.send_file(HERE / "models.html")
        elif path == "/models/vendor/three.module.min.js":
            h.send_file(HERE / "vendor" / "three.module.min.js")
        elif path == "/models/api/status":
            h.send_json(status())
        elif path == "/models/api/resolve":
            h.send_json(resolve((query.get("q") or [""])[0], (query.get("format") or [""])[0]))
        elif path == "/models/api/locations":
            h.send_json(locations())
        elif path == "/models/api/library":
            h.send_json({"models": library()})
        else:
            return False
    except ValueError as e:
        h.fail(str(e) or "That did not work.")
    except Exception as e:
        h.fail(f"{type(e).__name__}: {e}"[:400], 500)
    return True


def handle_post(h, path, body):
    """Answer POST /models/api/... . Returns the JSON to send; raises ValueError with a plain message."""
    body = body if isinstance(body, dict) else {}
    if path == "/models/api/kit/download":
        return start_kit(body.get("id", ""))
    if path == "/models/api/download":
        return start_download(body)
    if path == "/models/api/cancel":
        cancel_job(str(body.get("id", "")))
        return {"ok": True}
    if path == "/models/api/slot":
        return set_slot(body.get("name", ""), body.get("file", ""))
    if path == "/models/api/slots/reset":
        return reset_slots()
    if path == "/models/api/use":
        return use_as(body.get("path", ""), body.get("slot", ""))
    if path == "/models/api/location":
        return {"ok": True, "path": str(set_root(body.get("path", "")))}
    if path == "/models/api/reveal":
        reveal(body.get("path", ""))
        return {"ok": True}
    if path == "/models/api/bake":
        return bake()
    if path == "/models/api/writer/pull":
        return start_writer_pull(body.get("model"))
    raise ValueError("Unknown request")
