"""The narrator: a movie's lines spoken on this computer, by a voice model that runs here.

One small interface, so another voice engine can be added later without touching the rest of the app:

    state()                   what is here: {"ready", "why", "voices", "model"}; never waits long
    speak(text, voice, wav)   says `text` in `voice` into the file `wav`; returns its length in seconds
    track(lines, total, wav)  lays spoken lines on one sound track as long as the movie

Every engine has to hand over the same thing: one channel, 24,000 samples a second, 16 bit. speak() then cuts the
silence off both ends and brings every line to the same loudness, so no line jumps out of the movie.

The engine today is Ollama's speech support with the model "phonon" by Gradium: about 110 MB, English, Apache 2.0,
some forty voices. Ollama can speak from version 0.40.9 on, and so far only on Apple silicon Macs.
"""
import array
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.environ.get("STORYBOARD_VOICE_MODEL", "phonon")
RATE = 24000
LEVEL_DB = -18.0          # every line: the loudness (RMS) of the parts where something is said, in dB below full scale
LEAD = 0.45               # in a movie, a line starts this long after its shot begins
GAP = 0.25                # and at least this long after the line before it has ended
NOT_HERE = "The narrator's voice is not on this computer yet. Open Models and download Narrator voices."
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_seen = {"at": 0.0, "busy": False,
         "value": {"ready": False, "why": "Looking for the narrator's voice…", "voices": [], "model": MODEL}}


def _look(timeout=1.5):
    """Ask Ollama about the voice model."""
    out = {"ready": False, "why": NOT_HERE, "voices": [], "model": MODEL}
    try:
        req = urllib.request.Request(OLLAMA_URL + "/api/show", data=json.dumps({"model": MODEL}).encode(),
                                     headers={"Content-Type": "application/json"})
        with OPENER.open(req, timeout=timeout) as r:
            data = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError:
        return out                                    # Ollama is there, the model is not
    except Exception:
        out["why"] = "Ollama is not running, and it does the speaking. Open the Movies page once and it starts by itself."
        return out
    if "speech" not in (data.get("capabilities") or []):
        out["why"] = ("This Ollama cannot speak yet. Speech needs Ollama 0.40.9 or newer, "
                      "and so far it only works on Apple silicon Macs.")
        return out
    names = [str(v) for v in data.get("voices") or [] if str(v).strip()]
    plain = sorted((v for v in names if v != "default"), key=str.lower)
    out.update(ready=True, why="", voices=plain + (["default"] if "default" in names or not plain else []))
    return out


def state(block=False, max_age=6.0):
    """What the page needs to know. block=False never waits: it gives what was seen last and looks again behind the scenes."""
    if time.time() - _seen["at"] <= max_age:
        return _seen["value"]
    if block or not _seen["at"]:
        _seen["value"], _seen["at"] = _look(1.5 if block else 0.6), time.time()
        return _seen["value"]
    if not _seen["busy"]:
        _seen["busy"] = True

        def again():
            try:
                _seen["value"], _seen["at"] = _look(), time.time()
            finally:
                _seen["busy"] = False
        threading.Thread(target=again, daemon=True).start()
    return _seen["value"]


def forget():
    """Look afresh next time (a voice model was just downloaded or removed)."""
    _seen["at"] = 0.0


def ready():
    return bool(state(block=True)["ready"])


def pick(voice):
    """The voice to use when `voice` is asked for: itself if it is here, else a calm default."""
    names = state(block=True)["voices"]
    if voice in names:
        return voice
    return next((v for v in ("Hugh", "default") if v in names), names[0] if names else "default")


def _tidy(a):
    """Cut the silence off both ends of a spoken line, bring it to LEVEL_DB, and soften its first and last moment."""
    piece = RATE // 50                                              # 20 ms
    full = 32768.0
    quiet = (full * 10 ** (-45 / 20)) ** 2 * piece                   # a piece counts as sound above -45 dB
    sums = [sum(x * x for x in a[i:i + piece]) for i in range(0, len(a) - piece + 1, piece)]
    said = [i for i, s in enumerate(sums) if s > quiet]
    if not said:
        return a
    start = max(0, said[0] * piece - RATE * 30 // 1000)              # keep 30 ms before the first sound
    end = min(len(a), (said[-1] + 1) * piece + RATE * 120 // 1000)   # and 120 ms after the last
    rms = math.sqrt(sum(sums[i] for i in said) / (len(said) * piece))
    seg = a[start:end]
    gain = full * 10 ** (LEVEL_DB / 20) / max(rms, 1.0)
    peak = max(max(seg), -min(seg), 1)
    gain = min(gain, 0.9 * 32767 / peak)
    ramp = RATE // 100                                               # 10 ms in and out
    n = len(seg)
    out = array.array("h", bytes(2 * n))
    for i, x in enumerate(seg):
        g = gain * (i / ramp if i < ramp else (n - 1 - i) / ramp if i >= n - ramp else 1.0)
        out[i] = max(-32767, min(32767, int(x * g)))
    return out


def speak(text, voice, wav):
    """Say `text` in `voice` into the file `wav` (24 kHz, one channel, 16 bit). Returns its length in seconds."""
    text = " ".join(str(text or "").split())[:4000]
    if not text:
        raise ValueError("There is nothing to say.")
    body = {"model": MODEL, "input": text, "voice": voice or "default", "response_format": "pcm"}
    req = urllib.request.Request(OLLAMA_URL + "/v1/audio/speech", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=300) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        said = e.read().decode("utf-8", "replace")[:300]
        try:
            said = json.loads(said).get("error", {}).get("message") or said
        except (ValueError, AttributeError):
            pass
        raise ValueError("The voice could not say that: " + str(said))
    except Exception as e:
        raise ValueError(f"Ollama did not answer, and it does the speaking ({e}).")
    a = array.array("h")
    a.frombytes(raw[:len(raw) // 2 * 2])
    if sys.byteorder == "big":
        a.byteswap()
    if len(a) < RATE // 10:
        raise ValueError("The voice said nothing. Try other words.")
    a = _tidy(a)
    save(a, wav)
    return len(a) / RATE


def save(a, wav):
    wav = Path(wav)
    wav.parent.mkdir(parents=True, exist_ok=True)
    if sys.byteorder == "big":
        a = array.array("h", a)
        a.byteswap()
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(a.tobytes())


def load(wav):
    """The samples of a line that speak() made (None if the file is not that kind of file)."""
    try:
        with wave.open(str(wav), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != RATE:
                return None
            a = array.array("h")
            a.frombytes(w.readframes(w.getnframes()))
    except (OSError, wave.Error, EOFError):
        return None
    if sys.byteorder == "big":
        a.byteswap()
    return a


def seconds(wav):
    a = load(wav)
    return len(a) / RATE if a is not None else 0.0


def track(lines, total, wav):
    """One sound track of `total` seconds with the spoken lines on it. `lines` is a list of (when the shot begins,
    the line's file or None). A line starts LEAD after its shot begins, or GAP after the line before it if that one is
    still being said: a long line runs on into the next shot rather than being cut. Returns [(start, end)] of the
    lines that are on the track."""
    n = int(total * RATE)
    buf = array.array("h", bytes(2 * n))
    free_from, spans = 0.0, []
    for begins, f in lines:
        a = load(f) if f else None
        if a is None or not len(a):
            continue
        start = max(begins + LEAD, free_from)
        k = int(start * RATE)
        if k >= n:
            break
        a = a[:n - k]
        buf[k:k + len(a)] = a
        end = start + len(a) / RATE
        spans.append((start, end))
        free_from = end + GAP
    save(buf, wav)
    return spans


def dip(spans, down_db):
    """An ffmpeg volume expression that turns a track down by `down_db` (a negative number) while a line is spoken:
    down a quarter of a second before the voice, back up in about half a second after it. Lines less than 1.2 s
    apart share one dip, so the music does not bob up between them."""
    joined = []
    for start, end in spans:
        if joined and start - joined[-1][1] < 1.2:
            joined[-1][1] = end
        else:
            joined.append([start, end])
    if not joined:
        return "1"
    depth = 1 - 10 ** (down_db / 20)
    parts = "+".join(f"clip((t-{max(0.0, a - 0.30):.3f})/0.25,0,1)*clip(({b + 0.60:.3f}-t)/0.55,0,1)" for a, b in joined)
    return f"1-{depth:.4f}*min(1,{parts})"
