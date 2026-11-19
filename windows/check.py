#!/usr/bin/env python3
"""Start the app on throw-away data and try what can be tried without a graphics card or any model files.

Written for the Windows version (GitHub's Windows machines run it after every change), but it runs the same way
on a Mac. Run it with the engine's own Python from the app's folder:

    ComfyUI\\.venv\\Scripts\\python.exe windows\\check.py --engine      (Windows)
    ComfyUI/.venv/bin/python windows/check.py                          (Mac)

What it tries: the app starts and answers; the three pages and their script files are served as what they are;
LilyPond engraves a score and writes down its dynamics; a hidden browser turns that score into an audio file;
ffmpeg joins two test clips into a movie through the app's own queue; a picture of your own becomes a character
picture; the Models page reads this computer's drives; a second copy of the app steps aside; the keep-awake
helper runs. With --engine the app also starts the picture and video engine (on the processor, which is enough to
see that it starts) and the engine is asked for the app's own add-on.

What it cannot try: drawing a picture or filming a clip. That needs the models and a graphics card.
Nothing is left behind (--keep keeps the data folder, to look at what was made).
"""
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "storyboard"

if os.name == "nt" and not sys.flags.utf8_mode:  # the app's files are UTF-8 (see the top of storyboard/app.py)
    os.environ["PYTHONUTF8"] = "1"
    raise SystemExit(subprocess.call([sys.executable, *sys.argv]))

ENGINE = "--engine" in sys.argv
KEEP = "--keep" in sys.argv
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
RESULTS = []
SCORE = r'''\version "2.24.0"
\header { title = "Check" }
\score {
  \new Staff \with { instrumentName = "Piano" midiInstrument = "acoustic grand" } {
    \tempo 4 = 120
    c'4\p\< d' e' f' | g'1\f
  }
  \layout { }
  \midi { }
}
'''


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def say(ok, what, detail=""):
    RESULTS.append((ok, what))
    print(("  pass  " if ok else "  FAIL  ") + what + (("   " + str(detail)) if detail else ""), flush=True)


def check(what):
    """Run one check: the function returns a short detail to print, or raises with the reason it failed."""
    def wrap(fn):
        try:
            say(True, what, fn() or "")
        except Exception as e:
            say(False, what, f"{type(e).__name__}: {e}"[:600])
        return fn
    return wrap


class Web:
    def __init__(self, port):
        self.url = f"http://127.0.0.1:{port}"

    def get(self, path, timeout=30, raw=False):
        with OPENER.open(self.url + path, timeout=timeout) as r:
            body = r.read()
            return (body, r.headers.get("Content-Type", "")) if raw else json.loads(body)

    def post(self, path, obj=None, data=None, headers=None, timeout=60):
        body = data if data is not None else json.dumps(obj or {}).encode()
        req = urllib.request.Request(self.url + path, data=body,
                                     headers={"Content-Type": "application/json", "X-Storyboard": "1", **(headers or {})})
        try:
            with OPENER.open(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")

    def project(self, pid):
        return next(p for p in self.get("/api/state")["projects"] if p["id"] == pid)


def ffmpeg(*args):
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg is not on the PATH")
    r = subprocess.run([exe, "-y", "-v", "error", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + r.stderr[-300:])


def seconds(path):
    exe = shutil.which("ffprobe") or str(Path(shutil.which("ffmpeg")).with_name("ffprobe"))
    r = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return float(r.stdout.strip() or 0)


def prepare(data):
    """A finished little project: two shots, each with a picture and a one-second clip, and one character."""
    pid, media = "pc0ffee01", data / "projects" / "pc0ffee01" / "media"
    media.mkdir(parents=True)
    shots = []
    for n, colour in enumerate(("teal", "orange"), 1):
        sid = f"sc0ffee{n:02d}"
        ffmpeg("-f", "lavfi", "-i", f"color=c={colour}:size=832x480:rate=24", "-frames:v", "1", str(media / f"{sid}_image_1.png"))
        ffmpeg("-f", "lavfi", "-i", f"testsrc=size=832x480:rate=24", "-t", "1", "-pix_fmt", "yuv420p", "-c:v", "libx264",
               str(media / f"{sid}_video_1.mp4"))
        shots.append({"id": sid, "text": f"A {colour} test card", "motion": "", "caption": "", "who": [], "seconds": 1,
                      "start_from": "picture", "image": f"{sid}_image_1.png", "images": [f"{sid}_image_1.png"],
                      "video": f"{sid}_video_1.mp4", "videos": [f"{sid}_video_1.mp4"]})
    project = {"id": pid, "name": "Check: café “quotes”", "kind": "other", "shape": "wide", "style": "", "idea": "",
               "cast": [{"id": "cc0ffee01", "name": "Pip", "look": "a small felt penguin"}], "refs": False, "seed": 1,
               "created": time.time(), "shots": shots, "make_videos": True, "music": {"on": False, "style": "film", "note": ""}}
    (media.parent / "project.json").write_text(json.dumps(project, indent=1, ensure_ascii=False), encoding="utf-8")
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=640x480:rate=1", "-frames:v", "1", str(data / "own.jpg"))
    return pid


def main():
    print(f"Checking Penguin's Storyboard on {platform.system()} {platform.release()} with Python {platform.python_version()}")
    data = Path(tempfile.mkdtemp(prefix="storyboard-check-"))
    port = free_port()
    env = dict(os.environ, STORYBOARD_DATA=str(data), STORYBOARD_PORT=str(port), STORYBOARD_NO_BROWSER="1",
               MUSIC_NO_FETCH="1", PYTHONUTF8="1")
    if ENGINE:  # the app starts an engine of its own, on the processor: enough to see that it starts
        env.update(COMFY_URL=f"http://127.0.0.1:{free_port()}", COMFY_EXTRA_ARGS="--cpu")
    web, app, out = Web(port), None, None
    try:
        pid = prepare(data)
        out = open(data / "check-app-output.txt", "wb")
        app = subprocess.Popen([sys.executable, str(APP / "app.py")], cwd=str(ROOT), env=env, stdout=out, stderr=subprocess.STDOUT)

        @check("the app starts and answers")
        def _():
            deadline = time.time() + 90
            while time.time() < deadline:
                if app.poll() is not None:
                    raise RuntimeError(f"the app stopped at once (exit code {app.returncode})")
                try:
                    st = web.get("/api/state", timeout=3)
                    assert st["system"] == platform.system(), st.get("system")
                    assert any(p["id"] == pid for p in st["projects"]), "the prepared project was not loaded"
                    name = web.project(pid)["name"]
                    assert "café" in name, "the project's name lost its accent: " + repr(name)
                    return f"system {st['system']}, {len(st['kits'])} kits listed"
                except (urllib.error.URLError, ConnectionError, socket.timeout):
                    time.sleep(1)
            raise RuntimeError("no answer within 90 seconds")

        if not RESULTS[-1][0]:
            return

        @check("pages and script files are served as what they are")
        def _():
            kinds = []
            for path, want in (("/", "text/html"), ("/models", "text/html"), ("/music/", "text/html"),
                               ("/models/vendor/three.module.min.js", "javascript"), ("/music/audio.js", "javascript"),
                               ("/music/app.js", "javascript")):
                body, ctype = web.get(path, raw=True)
                assert want in ctype, f"{path} came as {ctype!r}"
                assert len(body) > 500, f"{path} is nearly empty"
                kinds.append(ctype.split(";")[0])
            return ", ".join(sorted(set(kinds)))

        @check("the Models page reads this computer")
        def _():
            st = web.get("/models/api/status")
            assert st["system"] == platform.system()
            places = web.get("/models/api/locations")
            assert isinstance(places["options"], list)
            engine = next((k for k in st["kits"] if k["kind"] == "engine"), None)
            return f"{len(st['kits'])} kits, engine: {engine['state'] if engine else '?'}, {len(places['options'])} places to save"

        tools = {}

        @check("the music tools are found")
        def _():
            tools.update(web.get("/music/api/status", timeout=90))
            assert tools.get("lilypond"), "LilyPond was not found"
            assert tools.get("chrome"), "no Chrome, Edge, Brave or Chromium was found"
            return f"LilyPond {tools['lilypond']}, a browser, Claude Code: {tools.get('claude') or 'not installed (only composing needs it)'}"

        @check("LilyPond engraves a score and writes down its dynamics")
        def _():
            r = web.post("/music/api/render", {"ly": SCORE}, timeout=240)
            assert r.get("ok"), "not engraved: " + str(r.get("log"))[:400]
            assert r.get("svgs") and r["svgs"][0].lstrip().startswith("<"), "no page came back"
            assert r.get("midi"), "no MIDI came back"
            perf = r.get("perf") or {}
            lines = [t.get("line") for t in (perf.get("tracks") or {}).values() if t.get("line")]
            assert lines, "the dynamics were not written down (perform.ily did not load?): " + str(r.get("log"))[:300]
            return f"{len(r['svgs'])} page, dynamics from {lines[0][0][1]} to {lines[0][-1][1]}"

        @check("a hidden browser turns the score into an audio file")
        def _():
            piece = web.post("/music/api/pieces", {"data": {"name": "Check", "ly": SCORE, "sound": "grand"}})
            body, ctype = web.get(f"/music/api/pieces/{piece['id']}/file.wav", timeout=420, raw=True)
            assert body[:4] == b"RIFF", "not a WAV file: " + body[:200].decode("utf-8", "replace")
            wav = data / "check.wav"
            wav.write_bytes(body)
            length = seconds(wav)
            assert 4.0 < length < 12.0, f"the audio is {length:.1f} seconds long (about 7.5 expected)"
            return f"{len(body) // 1000} kB, {length:.1f} seconds"

        @check("ffmpeg joins two clips into a movie through the app's queue")
        def _():
            web.post(f"/api/project/{pid}/begin")
            deadline = time.time() + 180
            while time.time() < deadline:
                p = web.project(pid)
                if p.get("movie"):
                    movie = data / "projects" / pid / "exports" / p["movie"]
                    assert movie.exists(), "the movie file is not there: " + str(movie)
                    length = seconds(movie)
                    assert 1.7 < length < 2.4, f"the movie is {length:.2f} seconds long (2 expected)"
                    return f"{p['movie']}, {length:.2f} seconds"
                failed = [j for j in web.get("/api/state")["jobs"] if j.get("project") == pid and j.get("status") == "failed"]
                if failed:
                    raise RuntimeError("the job failed: " + str(failed[-1].get("error")))
                time.sleep(1)
            raise RuntimeError("no movie within 3 minutes")

        @check("a picture of your own becomes a character picture")
        def _():
            web.post(f"/api/project/{pid}/cast/cc0ffee01/picture", data=(data / "own.jpg").read_bytes(),
                     headers={"Content-Type": "application/octet-stream", "X-Filename": "own.jpg"})
            c = web.project(pid)["cast"][0]
            assert c.get("ref") and c.get("ref_own"), "the character has no picture: " + str(c)
            head = (data / "projects" / pid / "media" / c["ref"]).read_bytes()[:8]
            assert head == b"\x89PNG\r\n\x1a\n", "the picture was not turned into a PNG"
            return c["ref"]

        @check("a second copy of the app steps aside instead of sharing the port")
        def _():
            r = subprocess.run([sys.executable, str(APP / "app.py")], cwd=str(ROOT), env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=90)
            assert "already running" in r.stdout, "it said: " + (r.stdout + r.stderr)[-300:]
            return "it said it is already running and stopped"

        @check("the keep-awake helper runs")
        def _():
            os.environ["STORYBOARD_DATA"] = str(data)
            sys.path.insert(0, str(APP))
            import models
            assert models.engine_python().exists(), "the engine's Python is not at " + str(models.engine_python())
            cmd = models.awake_command()
            assert cmd, "there is nothing to keep this computer awake with"
            helper = subprocess.Popen(cmd)
            try:
                time.sleep(2.5)
                assert helper.poll() is None, f"it stopped at once (exit code {helper.returncode})"
                held = ""
                if os.name == "nt":  # Windows can say who is holding it awake (needs an administrator window)
                    r = subprocess.run(["powercfg", "/requests"], capture_output=True, text=True, errors="replace")
                    held = ", Windows lists the request" if "python" in r.stdout.lower() else ", (Windows did not list it: " + (r.stdout.strip()[:80] or r.stderr.strip()[:80]) + ")"
                return Path(cmd[0]).name + " stayed up" + held
            finally:
                helper.terminate()

        if ENGINE:
            @check("the app starts the picture and video engine")
            def _():
                deadline = time.time() + 600
                while time.time() < deadline:
                    if web.get("/api/state")["comfy"] == "up":
                        with OPENER.open(env["COMFY_URL"] + "/object_info/StoryboardDecodeAudio", timeout=60) as r:
                            assert "StoryboardDecodeAudio" in json.loads(r.read()), "the app's own add-on is not loaded"
                        with OPENER.open(env["COMFY_URL"] + "/system_stats", timeout=60) as r:
                            stats = json.loads(r.read())
                        return f"ComfyUI {stats['system'].get('comfyui_version', '?')} is up with the app's add-on"
                    time.sleep(3)
                log = data / "comfyui.log"
                tail = log.read_text(encoding="utf-8", errors="replace")[-1500:] if log.exists() else "(no engine log)"
                raise RuntimeError("the engine did not come up within 10 minutes. Its log ends:\n" + tail)
    finally:
        if app is not None and app.poll() is None:
            app.terminate()
            try:
                app.wait(20)
            except subprocess.TimeoutExpired:
                app.kill()
        if out:
            out.close()
        bad = [what for ok, what in RESULTS if not ok]
        if bad:
            for name in ("check-app-output.txt", "app.log"):
                f = data / name
                if f.exists():
                    print(f"\n--- the end of {name} ---\n" + f.read_text(encoding="utf-8", errors="replace")[-3000:])
        if KEEP:
            print("\nThe data folder was kept:", data)
        else:
            shutil.rmtree(data, ignore_errors=True)
    print(f"\n{sum(ok for ok, _ in RESULTS)} of {len(RESULTS)} checks passed." + ("" if not bad else " Failed: " + "; ".join(bad)))
    raise SystemExit(1 if bad or not RESULTS else 0)


if __name__ == "__main__":
    main()
