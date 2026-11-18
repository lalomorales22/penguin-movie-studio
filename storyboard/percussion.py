#!/usr/bin/env python3
"""Recorded drums and percussion for the Music section.

The drums the sound engine makes by itself are built from noise and sine waves. This adds real ones, from two
free sets of recordings:

  - the drum kits of FluidR3 Mono GM, the General MIDI sound set MuseScore 3 came with (MIT licence), and
  - the orchestral and hand percussion of VSCO 2 Community Edition by Versilian Studios (CC0, public domain).

start() downloads them once into data/music/percussion/src (136 MB) and build() cuts them into one small file
per sound that a browser can play, with an index that says which drum each one is. The browser's sound engine
(music/audio.js) reads the index and plays the recordings in place of its own drums; without them everything
still plays, as before.

Standard library only; ffmpeg does the audio work.
"""
import array
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("STORYBOARD_DATA", HERE / "data")).resolve()
ROOT = DATA / "music" / "percussion"
SRC = ROOT / "src"            # what was downloaded, kept so the sounds can be made again without the internet
OUT = ROOT / "sounds"         # one small file per sound, made by build()
INDEX = ROOT / "index.json"   # which drum each of them is
LIST = json.loads((HERE / "music" / "percussion.json").read_text())
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
BUILD = 1                     # raise it when build() makes something different: the sounds are then made again
NO_FETCH = os.environ.get("MUSIC_NO_FETCH") == "1"
TOTAL = LIST["fluid"]["bytes"] + sum(LIST["vsco"]["files"].values())
RATE = 44100
FILM_SOUNDS = ("gong", "cymbal", "bigdrum", "chimes", "scrape", "bow")   # what the film layer gains from the recordings

hooks = {"log": print}        # app.py points this at its own log


def log(*a):
    try:
        hooks["log"]("percussion:", *a)
    except Exception:
        pass


# ---------------------------------------------------------------- which recording is which drum

P, P1 = "Percussion/", "VSCO 1 Percussion/"


def _rr(pattern, layers, takes=(1, 2)):
    """Layers from soft to loud, each with its takes (the same stroke recorded more than once)."""
    return [[pattern.format(v=v, r=r) for r in takes] for v in layers]


# name: layers from soft to loud; each layer lists the takes that are played in turn. "slow" marks sounds that
# swell in (they are not trimmed tight), "accent" those whose beat is their loudest moment, not their first.
INSTRUMENTS = {
    "bassdrum": {"layers": _rr(P + "BDrumNewhit_v{v}_rr{r}_Sum", range(1, 8))},
    "snare": {"layers": _rr(P + "Snare2-HitSN_v{v}_rr{r}_Sum", (1, 3, 5, 7, 9))},
    "piatti": {"layers": _rr(P + "cymbal-crash1_{v}_rr{r}", ("pp", "mp", "mf", "ff")), "max": 12},
    "choke": {"layers": [[P + "cymbal-crashshort_v1"]]},
    "suscym": {"layers": _rr(P + "susCymb1-hit_{v}_rr{r}", ("pp", "mp", "f", "fff")), "max": 12},
    "gong": {"layers": [[P + "gongHit_p"], [P + "gongHit_mf"], [P + "gongHit_f"], [P + "gongHit_fff"]], "max": 18},
    "tamb": {"layers": _rr(P + "Tamb1-Hit_v{v}_rr{r}_Sum", (1, 2))},
    "tambshake": {"layers": [[P + "Tamb1-Shake_v1_rr1_Sum", P + "Tamb1-Shake_v1_rr2_Sum"]], "slow": True},
    "trimute": {"layers": _rr(P + "Triangle3-HitM_v{v}_rr{r}_Sum", (1, 2))},
    "triangle": {"layers": _rr(P + "Triangle3-Hit_v{v}_rr{r}_Sum", (1, 2)), "max": 8},
    "cowbell": {"layers": _rr(P + "Cowbell1_Hit_v{v}_rr{r}_Sum", (1, 2, 3, 4))},
    "claves": {"layers": _rr(P + "Claves1_Hit_v{v}_rr{r}_Sum", (1, 2, 3))},
    "conga": {"layers": _rr(P + "Conga-HitN_v{v}_rr{r}_Sum", (1, 2, 3))},
    "quinto": {"layers": _rr(P + "Quinto-HitN_v{v}_rr{r}_Sum", (1, 2, 3))},
    "tumba": {"layers": _rr(P + "Tumba-HitN_v{v}_rr{r}_Sum", (1, 2, 3))},
    "belltree": {"layers": [[P + "BellTree_Stroke1_v1_Sum"], [P + "BellTree_Stroke3_v1_Sum"]], "slow": True},
    "chimes_up": {"layers": [[P1 + "varMetal/various/windchimes_asc1"]], "slow": True},
    "chimes_down": {"layers": [[P1 + "varMetal/various/windchimes_desc1"]], "slow": True},
    "agogo_low": {"layers": [[P1 + "varMetal/various/agogoBell2_mp_1"], [P1 + "varMetal/various/agogoBell2_mf_1"]]},
    "agogo_high": {"layers": [[P1 + "varMetal/various/agogoBell3_f_1"], [P1 + "varMetal/various/agogoBell3_ff_1"]]},
    "cabasa": {"layers": [[P1 + "varMetal/various/cabasa_%d" % i for i in range(1, 7)]], "slow": True, "accent": True},
    "guiro": {"layers": [[P1 + "varWood/guiro_short", P1 + "varWood/guiro_short2", P1 + "varWood/guiro_short3"]], "slow": True, "accent": True},
    "maracas": {"layers": [[P1 + "varWood/maraca%d" % i for i in range(1, 6)]], "slow": True, "accent": True},
    "vibraslap": {"layers": [[P1 + "varWood/vibraslap1", P1 + "varWood/vibraslap2"]]},
    "woodblock": {"layers": [[P1 + "varWood/wood_click_pp_1"], [P1 + "varWood/wood_click_mp"], [P1 + "varWood/wood_click_f"], [P1 + "varWood/wood_click_ff_2"]]},
    "shaker": {"layers": [[P1 + "varWood/Camo's Shaker/shake%d" % i for i in range(1, 9)]]},
    "bongo_high": {"layers": [[P1 + "drums/other/Bongos/HighBongo1", P1 + "drums/other/Bongos/HighBongo2"]]},
    "bongo_low": {"layers": [[P1 + "drums/other/Bongos/LowBongo1", P1 + "drums/other/Bongos/LowBongo2"]]},
    "bigdrum": {"layers": [[P1 + "drums/other/ethnic/giant/mallet/EthnicLargeMallet_hit_%s" % v] for v in ("pp_1", "mf_1", "f_1")]
                + [[P1 + "drums/other/ethnic/giant/mallet/EthnicLargeMallet_hit_ff_1", P1 + "drums/other/ethnic/giant/mallet/EthnicLargeMallet_hit_ff_2"]]},
    "tenor_high": {"layers": [[P1 + "drums/tenor/tenor_higher/tenorH_mf_%d" % i for i in (1, 2, 3)], [P1 + "drums/tenor/tenor_higher/tenorH_f_%d" % i for i in (1, 2, 3)]]},
    "tenor_low": {"layers": [[P1 + "drums/tenor/tenor_lower/tenor_mf_%d" % i for i in (1, 2, 3, 4)], [P1 + "drums/tenor/tenor_lower/tenor_f_%d" % i for i in (1, 2)]]},
    "cymroll_short": {"layers": [[P + "susCymb1-cresc-Short_v1"]], "slow": True, "accent": True, "lead": 9},
    "cymroll_mid": {"layers": [[P + "susCymb1-cresc-Median_v1"]], "slow": True, "accent": True, "lead": 9},
    "cymroll_long": {"layers": [[P + "susCymb1-cresc-Long_v1"]], "slow": True, "accent": True, "lead": 9, "max": 16},
    "cymbow": {"layers": [[P + "susCymb1-bow-1"]], "slow": True, "max": 16},
    "cymbow_short": {"layers": [[P + "susCymb1-bow-2"]], "slow": True},
    "scrape": {"layers": [[P + "gongscrape_mf"]], "slow": True},
}

# How loud each instrument plays at mezzo forte. Every recording is brought to the same peak when it is cut, so
# these numbers are the whole balance of the percussion section. (Set by measurement against the engine's own drums.)
GAIN = {
    "bassdrum": 1.3, "snare": 1.0, "piatti": 0.58, "choke": 0.4, "suscym": 0.35, "gong": 0.45, "tamb": 0.19, "tambshake": 0.19,
    "trimute": 0.18, "triangle": 0.18, "cowbell": 0.4, "claves": 0.42, "conga": 0.35, "quinto": 0.35, "tumba": 0.35,
    "belltree": 0.25, "agogo_low": 0.45, "agogo_high": 0.45, "cabasa": 0.32, "guiro": 0.32, "maracas": 0.36, "vibraslap": 0.45,
    "woodblock": 0.53, "shaker": 0.3, "bongo_high": 0.35, "bongo_low": 0.44, "bigdrum": 0.72, "tenor_high": 0.72, "tenor_low": 0.72,
}
FLUID_GAIN = 1.0   # the same for the drum kits, whose own balance (the sound set's) is kept
# ... except for a few drums that stand far in front when every drum is struck equally hard, as a score does
# (General MIDI key: decibels).
FLUID_TRIM = {35: -2, 36: -2, 37: -2, 39: -3, 42: -8, 44: -9, 46: -9, 51: -3, 53: -2, 59: -3, 65: -4, 66: -3, 74: -6}
KIT_TRIM = {"power": -3}   # and one whole kit that is recorded hotter than the others

# The drum kits of the sound set: (name here, General MIDI kit number, what the Kit menu calls it).
FLUID_KITS = [("studio", 0, "Studio kit"), ("room", 8, "Room kit"), ("power", 16, "Power kit"), ("jazz", 32, "Jazz kit"),
              ("brush", 40, "Brush kit"), ("electronic", 24, "Electronic kit"), ("tr808", 25, "TR-808 kit")]
# Hand percussion with several takes and several strengths replaces the kits' single recordings (General MIDI
# key: instrument, and what is different about it), except in the two electronic kits.
HAND = {54: ("tamb", {}), 56: ("cowbell", {}), 58: ("vibraslap", {}), 60: ("bongo_high", {}), 61: ("bongo_low", {}),
        62: ("quinto", {"cut": 0.12}), 63: ("conga", {}), 64: ("tumba", {}), 67: ("agogo_high", {}), 68: ("agogo_low", {}),
        69: ("cabasa", {}), 70: ("maracas", {}), 73: ("guiro", {}), 75: ("claves", {}), 76: ("woodblock", {"rate": 1.26}),
        77: ("woodblock", {}), 80: ("trimute", {}), 81: ("triangle", {}), 82: ("shaker", {}), 84: ("belltree", {})}
HAND_KITS = {"studio", "room", "power", "jazz", "brush"}
# Orchestral percussion: the concert bass drum, snare, cymbals and tam-tam sit on the keys a score writes for
# kick, snare, crash and Chinese cymbal; the toms become large drums.
ORCH = {**HAND, 35: ("bassdrum", {"rate": 0.94}), 36: ("bassdrum", {}), 38: ("snare", {}), 40: ("snare", {"rate": 1.06}),
        41: ("bigdrum", {"rate": 0.84}), 43: ("bigdrum", {}), 45: ("tenor_low", {"rate": 0.9}), 47: ("tenor_low", {}),
        48: ("tenor_high", {"rate": 0.92}), 50: ("tenor_high", {}), 49: ("piatti", {}), 57: ("suscym", {}), 55: ("choke", {}),
        51: ("suscym", {"g": 0.8}), 59: ("suscym", {"g": 0.8}), 52: ("gong", {})}
# Where each drum sits, seen from the hall (-1 left, 1 right).
PAN = {37: 0.1, 38: 0.05, 40: 0.05, 41: -0.4, 43: -0.3, 45: -0.15, 47: 0.05, 48: 0.2, 50: 0.3, 42: 0.3, 44: 0.3, 46: 0.3,
       49: 0.35, 51: -0.3, 52: -0.45, 53: -0.3, 55: 0.15, 57: -0.35, 59: -0.25, 54: 0.4, 56: -0.1, 58: 0.2, 60: -0.3, 61: -0.2,
       62: 0.25, 63: 0.3, 64: 0.35, 65: -0.25, 66: -0.15, 67: 0.4, 68: 0.35, 69: -0.4, 70: 0.45, 73: -0.35, 74: -0.35, 75: 0.2,
       76: -0.2, 77: -0.15, 80: 0.35, 81: 0.35, 82: -0.45, 84: 0.3}
ORCH_PAN = {35: -0.15, 36: -0.15, 38: 0.1, 40: 0.1, 41: -0.3, 43: -0.25, 45: -0.15, 47: -0.1, 48: 0.0, 50: 0.05, 49: 0.2, 57: 0.25,
            55: 0.2, 51: 0.25, 59: 0.25, 52: -0.4}
CREDITS = [LIST["fluid"]["credit"] + " MIT licence.", LIST["vsco"]["credit"] + " " + LIST["vsco"]["licence"] + "."]


# ---------------------------------------------------------------- fetching

_lock = threading.Lock()
_job = {"state": "", "error": ""}   # "" (nothing going on), "fetching", "building" or "error"


def sources():
    """Every file to download: (address, where it goes, its size)."""
    f, v = LIST["fluid"], LIST["vsco"]
    return [(f["url"], SRC / f["file"], f["bytes"])] + [
        (v["base"] + urllib.parse.quote(p), SRC / "vsco" / p, size) for p, size in v["files"].items()]


def _size(path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


def missing():
    return [s for s in sources() if _size(s[1]) != s[2]]


def _download(job):
    url, out, size = job
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    last = None
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 16)
            if _size(tmp) != size:
                raise OSError("it came out %d bytes long, not %d" % (_size(tmp), size))
            os.replace(tmp, out)
            return
        except OSError as e:
            last = e
    raise RuntimeError("Could not download %s: %s" % (out.name, last))


_index = {"mtime": None, "ok": False}


def ready():
    """Are the sounds made, by this version of build()?"""
    try:
        m = INDEX.stat().st_mtime
    except OSError:
        return False
    if _index["mtime"] != m:
        try:
            ok = json.loads(INDEX.read_text()).get("build") == BUILD
        except (OSError, ValueError):
            ok = False
        _index.update(mtime=m, ok=ok)
    return _index["ok"]


def status():
    """For the Models page: ready, missing, partial (some of it is here), fetching, building or error."""
    have = sum(size if _size(p) == size else min(size, _size(p.with_name(p.name + ".part"))) for _, p, size in sources())
    state = _job["state"] or ("ready" if ready() else "partial" if have else "missing")
    return {"state": state, "done": have, "total": TOTAL, "error": _job["error"], "ready": ready()}


def start():
    """Fetch whatever is missing, then make the sounds. Runs in the background; status() says how far it is."""
    with _lock:
        if _job["state"] in ("fetching", "building"):
            return
        _job.update(state="fetching", error="")
    threading.Thread(target=_work, daemon=True).start()


def ensure():
    """At start-up: if the recordings are here but the sounds are not (a newer build), make them."""
    if not ready() and SRC.is_dir() and not missing():
        start()


def _work():
    try:
        todo = missing()
        if todo:
            if NO_FETCH:
                raise RuntimeError("Downloading is switched off.")
            log("fetching", len(todo), "files")
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(_download, todo))
        _job["state"] = "building"
        t0 = time.time()
        n = build()
        log("made %d sounds in %.0f s" % (n, time.time() - t0))
        _job["state"] = ""
    except Exception as e:  # noqa: whatever went wrong, the page should say so
        _job.update(state="error", error=str(e)[:300])
        log("failed:", e)


# ---------------------------------------------------------------- reading the sound set (SoundFont 2 / 3)

class SoundFont:
    """Just enough of the SoundFont format to take the drum kits out: which recording each key plays, how loud,
    at what pitch, and how it fades. In an .sf3 file the recordings are Ogg Vorbis streams."""
    INDEXES = {41, 53, 54, 57, 58, 46, 47}   # generators whose amount is not a signed number

    def __init__(self, path):
        self.data = data = Path(path).read_bytes()
        if data[:4] != b"RIFF" or data[8:12] != b"sfbk":
            raise RuntimeError("%s is not a SoundFont" % Path(path).name)
        self.chunks = {}
        self._walk(12, len(data))
        rec = self._records
        self.phdr, self.pbag, self.pgen = rec("phdr", "<20sHHHIII"), rec("pbag", "<HH"), rec("pgen", "<HH")
        self.inst, self.ibag, self.igen = rec("inst", "<20sH"), rec("ibag", "<HH"), rec("igen", "<HH")
        self.shdr = rec("shdr", "<20sIIIIIBbHH")

    def _walk(self, lo, hi):
        p, data = lo, self.data
        while p + 8 <= hi:
            tag, size = data[p:p + 4], struct.unpack_from("<I", data, p + 4)[0]
            if tag == b"LIST":
                self._walk(p + 12, p + 8 + size)
            else:
                self.chunks[tag.decode("latin1")] = (p + 8, size)
            p += 8 + size + (size & 1)

    def _records(self, tag, fmt):
        off, size = self.chunks[tag]
        n = struct.calcsize(fmt)
        return [struct.unpack_from(fmt, self.data, off + i * n) for i in range(size // n)]

    def _zones(self, bags, gens, lo, hi):
        out = []
        for b in range(lo, hi):
            z = {}
            for op, amt in gens[bags[b][0]:bags[b + 1][0]]:
                if op in (43, 44):
                    z[op] = (amt & 255, amt >> 8)
                else:
                    z[op] = amt - 65536 if amt > 32767 and op not in self.INDEXES else amt
            out.append(z)
        return out

    def kit(self, preset, bank=128):
        """{key: facts} for every drum of one percussion preset (keys 27 to 87)."""
        at = next((i for i, p in enumerate(self.phdr[:-1]) if p[2] == bank and p[1] == preset), None)
        if at is None:
            return {}
        tc = lambda v: 2.0 ** (v / 1200.0)   # "timecents" to seconds
        pzones = self._zones(self.pbag, self.pgen, self.phdr[at][3], self.phdr[at + 1][3])
        pglobal = pzones[0] if pzones and 41 not in pzones[0] else {}
        out = {}
        for pz in pzones:
            if 41 not in pz:
                continue
            p = {**pglobal, **pz}
            plo, phi = p.get(43, (0, 127))
            izones = self._zones(self.ibag, self.igen, self.inst[p[41]][1], self.inst[p[41] + 1][1])
            iglobal = izones[0] if izones and 53 not in izones[0] else {}
            for iz in izones:
                if 53 not in iz:
                    continue
                g = {**iglobal, **iz}
                klo, khi = g.get(43, (0, 127))
                loud = g.get(44, (0, 127))[1]
                s = self.shdr[g[53]]
                if s[9] & 0x8000:   # a recording that lives in a synthesizer's own memory, not in this file
                    continue
                for key in range(max(klo, plo, 27), min(khi, phi, 87) + 1):
                    if key in out and out[key]["loud"] >= loud:
                        continue    # of several layers keep the loudest; of a left and right pair, the first
                    root = g.get(58, 255)
                    root = s[6] if root > 127 else root
                    cents = (key - root) * g.get(56, 100) + 100 * (g.get(51, 0) + p.get(51, 0)) + g.get(52, 0) + p.get(52, 0) + s[7]
                    out[key] = {"sample": g[53], "loud": loud, "atten": g.get(48, 0) + p.get(48, 0), "rate": 2.0 ** (cents / 1200.0),
                                "choke": g.get(57, 0), "hold": tc(g.get(35, -12000)), "decay": tc(g.get(36, -12000)),
                                "sustain": g.get(37, 0) / 10.0, "loop": bool(g.get(54, 0) & 1)}
        return out

    def sample(self, i):
        """One recording, decoded: (its samples, its sample rate, where its loop begins and ends)."""
        _, start, end, loop0, loop1, rate, _, _, _, kind = self.shdr[i]
        off = self.chunks["smpl"][0]
        if kind & 0x10:   # .sf3: an Ogg Vorbis stream; the loop is counted from its beginning
            r = subprocess.run([FFMPEG, "-v", "error", "-f", "ogg", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(rate), "pipe:1"],
                               input=self.data[off + start:off + end], capture_output=True)
            if r.returncode or not r.stdout:
                raise RuntimeError("could not read a recording of the sound set: " + r.stderr.decode("utf-8", "replace")[-200:])
            pcm = array.array("h")
            pcm.frombytes(r.stdout[:len(r.stdout) // 2 * 2])
        else:             # .sf2: plain 16-bit samples
            pcm = array.array("h")
            pcm.frombytes(self.data[off + 2 * start:off + 2 * end])
            loop0, loop1 = loop0 - start, loop1 - start
        return pcm, rate, (loop0, loop1)


def _ring(pcm, rate, z, loop):
    """One drum as it sounds when it is struck and left alone: its loop played out and its fade applied.
    (A kit's triangle, for one, is a short recording whose last moment is repeated while it dies away.)"""
    n = len(pcm)
    slope = 100.0 / z["decay"] if z["sustain"] > 0 and z["decay"] > 0 else 0.0   # decibels a second, once the hold is over
    looped = z["loop"] and 0 <= loop[0] < loop[1] <= n and loop[1] - loop[0] > 8
    if looped:
        ring = z["hold"] + (50.0 / slope if slope else 99.0)   # until it has faded by 50 dB
        total = int(min(8.0, max(loop[1] / rate + 1.5, ring)) * rate)
        body, span = pcm[:loop[1]], pcm[loop[0]:loop[1]]
        while len(body) < total:
            body.extend(span)
        pcm, n = body[:total], total
    drop = slope * max(0.0, n / rate - z["hold"])
    if slope and (looped or drop >= 1.0):
        out, h, step = array.array("h", pcm), int(z["hold"] * rate), 64
        for i in range(h, n, step):
            db = min(z["sustain"], (i - h) / rate * slope)
            if db >= 70:
                del out[i:]
                break
            k = 10.0 ** (-db / 20.0)
            for j in range(i, min(n, i + step)):
                out[j] = int(pcm[j] * k)
        pcm, n = out, len(out)
    if looped:   # it was cut off while still sounding: let it out gently
        fade = min(n, int(rate * (0.25 if drop >= 30 else 1.0)))
        for j in range(fade):
            pcm[n - 1 - j] = int(pcm[n - 1 - j] * j / fade)
    return pcm


def _flac(pcm_bytes, rate, channels, out):
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "s16le", "-ar", str(rate), "-ac", str(channels), "-i", "pipe:0",
                        "-c:a", "flac", "-compression_level", "8", str(out)], input=pcm_bytes, capture_output=True)
    if r.returncode:
        raise RuntimeError("ffmpeg could not write %s: %s" % (out.name, r.stderr.decode("utf-8", "replace")[-200:]))


# ---------------------------------------------------------------- cutting the orchestral recordings

def _name(stem):
    return re.sub(r"[^a-z0-9]+", "_", stem.rsplit("/", 1)[-1].lower()).strip("_")


def _cut(stem, spec, out_dir):
    """One recording, trimmed to where its sound begins and ends, brought to full level, as a FLAC.
    Returns its facts, or None when the take is too faint to use."""
    src = SRC / "vsco" / (stem + ".wav")
    r = subprocess.run([FFMPEG, "-v", "error", "-i", str(src), "-f", "s16le", "-ac", "2", "-ar", str(RATE), "pipe:1"], capture_output=True)
    if r.returncode or len(r.stdout) < 4000:
        raise RuntimeError("could not read %s" % src.name)
    pcm = array.array("h")
    pcm.frombytes(r.stdout[:len(r.stdout) // 4 * 4])
    n, C = len(pcm) // 2, 256
    peaks = []
    for i in range(0, n, C):
        seg = pcm[2 * i:2 * (i + C)]
        peaks.append(max(max(seg), -min(seg)))
    peak = max(peaks)
    if peak < 32768 * 10 ** (-50 / 20):
        return None
    slow = spec.get("slow")
    th = peak * (0.01 if slow else 0.03)
    first = next(k for k, v in enumerate(peaks) if v > th)
    start = first * C
    for j in range(2 * first * C, min(len(pcm), 2 * (first + 1) * C)):
        if abs(pcm[j]) > th:
            start = j // 2
            break
    start = max(0, start - int(RATE * (0.02 if slow else 0.002)))
    last = max(k for k, v in enumerate(peaks) if v > peak * 0.003)
    end = min(n, (last + 1) * C + RATE // 20)
    cap = int(spec.get("max", 14) * RATE)
    cut = end - start > cap
    if cut:
        end = start + cap
    dur = (end - start) / RATE
    fade = min(3.0, max(0.03, dur * 0.15)) if cut else min(0.2, max(0.02, dur * 0.1))
    # Left and right: a recording that is the same on both sides becomes one channel (the player seats it);
    # a wide one stays wide, and one that leans far to a side is pulled towards the middle.
    left, right = pcm[2 * start:2 * end:32], pcm[2 * start + 1:2 * end:32]
    el, er = sum(v * v for v in left) or 1, sum(v * v for v in right) or 1
    corr = sum(a * b for a, b in zip(left, right)) / math.sqrt(el * er)
    lean = 10 * math.log10(el / er)
    mono = corr > 0.9 and abs(lean) < 1.5
    accent = 0.0
    if spec.get("accent"):   # the moment the ear takes for the beat: where it is loudest
        best, at, W = 0, start, 1024
        for i in range(start, end - W, W):
            seg = pcm[2 * i:2 * (i + W):4]
            e = sum(v * v for v in seg)
            if e > best:
                best, at = e, i
        accent = min(spec.get("lead", 0.15), (at + W // 2 - start) / RATE)
    target = 32768 * 10 ** (-1 / 20)
    if mono:
        mix = max(abs(pcm[2 * i] + pcm[2 * i + 1]) for i in range(start, end)) / 2 or 1
        g = target / mix
        chain = "pan=mono|c0=%.6f*c0+%.6f*c1" % (g / 2, g / 2)
    else:
        fix = max(-4.0, min(4.0, lean)) / 2 if abs(lean) > 1.5 else 0.0   # decibels taken from the left, given to the right
        gl, gr = 10 ** (-fix / 20), 10 ** (fix / 20)
        g = target / (peak * max(gl, gr))
        chain = "pan=stereo|c0=%.6f*c0|c1=%.6f*c1" % (g * gl, g * gr)
    chain += ",afade=t=out:st=%.4f:d=%.4f" % (max(0.0, dur - fade), fade)
    name = _name(stem)
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", "%.5f" % (start / RATE), "-t", "%.5f" % dur, "-i", str(src), "-af", chain,
                        "-ar", str(RATE), "-sample_fmt", "s16", "-c:a", "flac", "-compression_level", "8", str(out_dir / (name + ".flac"))],
                       capture_output=True)
    if r.returncode:
        raise RuntimeError("ffmpeg could not cut %s: %s" % (src.name, r.stderr.decode("utf-8", "replace")[-200:]))
    return {"id": name, "len": round(dur, 3), "lead": round(accent, 3), "ch": 1 if mono else 2, "db": round(20 * math.log10(peak / 32768), 1)}


# ---------------------------------------------------------------- making the sounds

def build():
    """Turn the downloaded recordings into the sounds the browser plays and write the index. Returns how many."""
    if missing():
        raise RuntimeError("The recordings are not all here yet.")
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = ROOT / "sounds.new"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    sounds = {}

    # 1. Orchestral and hand percussion.
    jobs = {}
    for spec in INSTRUMENTS.values():
        for layer in spec["layers"]:
            for stem in layer:
                jobs[stem] = spec
    with ThreadPoolExecutor(6) as pool:
        done = dict(zip(jobs, pool.map(lambda stem: _cut(stem, jobs[stem], tmp), jobs)))
    inst = {}
    for name, spec in INSTRUMENTS.items():
        layers = [[done[s]["id"] for s in layer if done[s]] for layer in spec["layers"]]
        inst[name] = [layer for layer in layers if layer]
    for facts in done.values():
        if facts:
            sounds[facts["id"]] = {k: facts[k] for k in ("len", "lead", "ch")}

    # 2. The drum kits.
    sf = SoundFont(SRC / LIST["fluid"]["file"])
    decoded, written = {}, {}

    def drum(z):
        """The file for one drum of a kit (made the first time it is asked for)."""
        looks = z["loop"] or z["sustain"] > 0
        key = "f%d" % z["sample"] + ("_" + hashlib.sha1(repr((z["loop"], round(z["hold"], 4), round(z["decay"], 4), z["sustain"])).encode()).hexdigest()[:6]
                                     if looks else "")
        if key not in written:
            if z["sample"] not in decoded:
                decoded[z["sample"]] = sf.sample(z["sample"])
            pcm, rate, loop = decoded[z["sample"]]
            out = _ring(pcm, rate, z, loop) if looks else pcm
            if looks and len(out) == len(pcm) and out == pcm:   # the fade made no difference: it is the plain recording
                key = "f%d" % z["sample"]
            if key not in written:
                _flac(out.tobytes(), rate, 1, tmp / (key + ".flac"))
                written[key] = {"len": round(len(out) / rate, 3), "lead": 0, "ch": 1}
        sounds[key] = written[key]
        return key

    kits = {}
    for kid, preset, label in FLUID_KITS + [("orchestra", 0, "Orchestral percussion")]:
        zones = sf.kit(preset)
        if not zones:
            raise RuntimeError("The sound set has no drum kit number %d." % preset)
        keys = {}
        for key, z in sorted(zones.items()):
            keys[str(key)] = {"s": [[drum(z)]], "g": round(FLUID_GAIN * 10 ** ((FLUID_TRIM.get(key, 0) + KIT_TRIM.get(kid, 0) - 0.04 * z["atten"]) / 20.0), 4), "rate": round(z["rate"], 5),
                              "choke": z["choke"], "pan": PAN.get(key, 0)}
        over = ORCH if kid == "orchestra" else HAND if kid in HAND_KITS else {}
        for key, (name, opt) in over.items():
            if not inst.get(name):
                continue
            pan = ORCH_PAN.get(key, PAN.get(key, 0) * 0.7) if kid == "orchestra" else PAN.get(key, 0)
            keys[str(key)] = {"s": inst[name], "g": round(GAIN.get(name, 0.3) * opt.get("g", 1), 4), "rate": opt.get("rate", 1), "choke": 0, "pan": pan,
                              **({"cut": opt["cut"]} if "cut" in opt else {})}
        kits[kid] = {"label": label, "keys": keys}

    # 3. What the film layer plays from the recordings.
    rolls = [[inst[n][0][0], done[INSTRUMENTS[n]["layers"][0][0]]["lead"]] for n in ("cymroll_short", "cymroll_mid", "cymroll_long") if inst.get(n)]
    one = lambda n: inst[n][0][0] if inst.get(n) else None
    film = {"gong": inst.get("gong", []), "cymroll": rolls, "bigdrum": inst.get("bigdrum", []), "bassdrum": inst.get("bassdrum", []),
            "chimes_up": one("chimes_up"), "chimes_down": one("chimes_down"), "belltree": inst.get("belltree", []),
            "scrape": one("scrape"), "bow": one("cymbow"), "bow_short": one("cymbow_short"), "tambshake": inst.get("tambshake", [])}

    index = {"build": BUILD, "kits": kits, "film": film, "sounds": sounds, "credits": CREDITS}
    old = ROOT / "sounds.old"
    shutil.rmtree(old, ignore_errors=True)
    if OUT.exists():
        os.replace(OUT, old)
    os.replace(tmp, OUT)
    shutil.rmtree(old, ignore_errors=True)
    part = INDEX.with_name(INDEX.name + ".tmp")
    part.write_text(json.dumps(index))
    os.replace(part, INDEX)
    return len(sounds)


def sound_file(name):
    """The file of one sound, for the browser. Raises ValueError for a name that is not one."""
    if not re.match(r"^[a-z0-9_]{1,80}$", name or ""):
        raise ValueError("Unknown sound")
    f = OUT / (name + ".flac")
    if not f.is_file():
        raise ValueError("That sound is not on this computer.")
    return f


if __name__ == "__main__":   # python percussion.py: make the sounds from what is already downloaded
    t = time.time()
    print("made", build(), "sounds in %.0f s" % (time.time() - t), "->", OUT)
