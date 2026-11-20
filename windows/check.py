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

More options are there for GitHub's machines, which are thrown away after each run:
  --claude     Claude Code has to be found (the workflow installs it first)
  --ollama     Ollama has to be found and running (setup installs it)
  --gguf       the engine has to load the GGUF add-on (setup -Gguf installs it)
  --voice      the narrator has to read two lines aloud and the movie has to carry them (needs the Narrator voices
               kit, which only works on Apple silicon Macs so far)
  --download   really download from Hugging Face through the Models page: the Fast video add-on (332 MB) into the
               engine's models folder, and then the start of the Video kit, which is stopped after a few megabytes
               to see that stopping works. Do not use it on a computer you keep: it leaves those files there.

What it cannot try: drawing a picture or filming a clip. That needs the models and a graphics card.
Without --download nothing is left behind (--keep keeps the data folder, to look at what was made).
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
CLAUDE = "--claude" in sys.argv
OLLAMA = "--ollama" in sys.argv
GGUF = "--gguf" in sys.argv
VOICE = "--voice" in sys.argv
DOWNLOAD = "--download" in sys.argv
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
        began = time.time()
        took = lambda: f"   [{time.time() - began:.0f} s]"
        try:
            detail = fn() or ""
            say(True, what, detail + took())
        except Exception as e:
            say(False, what, f"{type(e).__name__}: {e}"[:900] + took())
        return fn
    return wrap


def stop(proc):
    """Stop a program together with whatever it started. Windows stops a program without letting it tidy up, so
    the engine the app started would stay behind; there the whole family is stopped by name of its parent."""
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(20)
    except subprocess.TimeoutExpired:
        proc.kill()


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
    # The throw-away data folder sits where the real one does, inside the app's folder: if that folder's name has
    # spaces or accents, every path the app hands to ffmpeg and the others has them too, as on a real computer.
    home = APP / "data"
    made_home = not home.exists()
    home.mkdir(parents=True, exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix=".check-", dir=home))
    # The app's own models.py, loaded here for two things: on Windows, loading it puts the programs that setup
    # installed a moment ago on the PATH (this window may be older than they are), and one check asks it questions.
    os.environ["STORYBOARD_DATA"] = str(data)   # it reads its settings from here, so it must be set first
    sys.path.insert(0, str(APP))
    import models
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

        if OLLAMA:
            @check("the script writer's program (Ollama) is found and running")
            def _():
                deadline = time.time() + 90
                while True:
                    w = next(k for k in web.get("/models/api/status")["kits"] if k["id"] == "writer")["writer"]
                    if w["running"]:
                        return f"running, {len(w['models'])} writing models on this computer"
                    if time.time() > deadline:
                        raise RuntimeError("installed but not running" if w["installed"] else "Ollama was not found")
                    time.sleep(2)

        tools = {}

        @check("the music tools are found")
        def _():
            tools.update(web.get("/music/api/status", timeout=90))
            assert tools.get("lilypond"), "LilyPond was not found"
            assert tools.get("chrome"), "no Chrome, Edge, Brave or Chromium was found"
            assert tools.get("claude") or not CLAUDE, "Claude Code was not found, though setup was told to install it"
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

        temp = Path(tempfile.gettempdir())
        browser_folders = set(temp.glob("storyboard-chrome-*"))   # (left by something else, not counted below)

        @check("a hidden browser turns the score into an audio file")
        def _():
            piece = web.post("/music/api/pieces", {"data": {"name": "Check", "ly": SCORE, "sound": "grand"}})
            body, ctype = web.get(f"/music/api/pieces/{piece['id']}/file.wav", timeout=420, raw=True)
            assert body[:4] == b"RIFF", "not a WAV file: " + body[:200].decode("utf-8", "replace")
            wav = data / "check.wav"
            wav.write_bytes(body)
            length = seconds(wav)
            assert 4.0 < length < 12.0, f"the audio is {length:.1f} seconds long (about 7.5 expected)"
            # the browser worked in a folder of its own, which the app deletes (Windows may need a moment for that)
            deadline = time.time() + 20
            while time.time() < deadline and set(temp.glob("storyboard-chrome-*")) - browser_folders:
                time.sleep(0.5)
            left = set(temp.glob("storyboard-chrome-*")) - browser_folders
            assert not left, f"the hidden browser's folder was left behind: {sorted(left)[0]}"
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

        if VOICE:
            @check("the narrator reads two lines aloud and they are laid over the movie")
            def _():
                st = web.get("/api/state")
                assert st["narrator"]["ready"], "no voice here: " + st["narrator"]["why"]
                before = web.project(pid).get("movie")
                for s, line in zip(web.project(pid)["shots"], ("One.", "Two.")):
                    web.post(f"/api/project/{pid}/shot/{s['id']}", {"caption": line})
                web.post(f"/api/project/{pid}", {"narrator": {"on": True}})
                web.post(f"/api/project/{pid}/narrator/make")
                deadline = time.time() + 120
                while True:
                    p = web.project(pid)
                    jobs = [j for j in web.get("/api/state")["jobs"] if j.get("project") == pid]
                    failed = [j for j in jobs if j.get("status") == "failed"]
                    if failed:
                        raise RuntimeError("the job failed: " + str(failed[-1].get("error")))
                    if p.get("movie") != before and not any(j.get("status") in ("waiting", "running") for j in jobs):
                        break
                    if time.time() > deadline:
                        raise RuntimeError("no movie with the narrator within 2 minutes")
                    time.sleep(1)
                for s in p["shots"]:
                    head = (data / "projects" / pid / "media" / str(s.get("voice"))).read_bytes()[:4]
                    assert head == b"RIFF", "a spoken line is not a sound file: " + repr(head)
                movie = data / "projects" / pid / "exports" / p["movie"]
                exe = shutil.which("ffprobe") or str(Path(shutil.which("ffmpeg")).with_name("ffprobe"))
                r = subprocess.run([exe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name", "-of",
                                    "default=nw=1:nk=1", str(movie)], capture_output=True, text=True, encoding="utf-8", errors="replace")
                assert r.stdout.strip(), "the movie has no sound: the narrator is not in it"
                said = sum(float(s.get("voice_seconds") or 0) for s in p["shots"])
                return f"the voice {p['narrator']['voice']} (one of {len(st['narrator']['voices'])}), {said:.1f} seconds of speech, {p['movie']}"

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

        @check("the keep-awake helper runs, and stops when it is stopped")
        def _():
            assert models.engine_python().exists(), "the engine's Python is not at " + str(models.engine_python())
            cmd = models.awake_command()
            assert cmd, "there is nothing to keep this computer awake with"

            def listed():  # Windows can say who is holding it awake (it needs an administrator window for that)
                r = subprocess.run(["powercfg", "/requests"], capture_output=True, text=True, errors="replace")
                return "python" in r.stdout.lower(), (r.stdout.strip() or r.stderr.strip())[:80]

            helper = subprocess.Popen(cmd)
            try:
                time.sleep(2.5)
                assert helper.poll() is None, f"it stopped at once (exit code {helper.returncode})"
                held, said = listed() if os.name == "nt" else (False, "")
            finally:
                models.stop_process(helper)   # the way the app itself stops it
            assert helper.poll() is not None, "it is still running after being stopped"
            note = ""
            if os.name == "nt" and held:
                time.sleep(1)
                assert not listed()[0], "Windows still lists the request: the program behind python.exe was not stopped"
                note = ", Windows listed its request and dropped it afterwards"
            elif os.name == "nt":
                note = f", (Windows did not list it: {said})"
            return Path(cmd[0]).name + " stayed up" + note

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
                        gguf = ""
                        if GGUF:
                            with OPENER.open(env["COMFY_URL"] + "/object_info/CLIPLoaderGGUF", timeout=60) as r:
                                assert "CLIPLoaderGGUF" in json.loads(r.read()), "the GGUF add-on is not loaded"
                            gguf = " and the GGUF add-on"
                        return f"ComfyUI {stats['system'].get('comfyui_version', '?')} is up with the app's add-on{gguf}"
                    time.sleep(3)
                log = data / "comfyui.log"
                tail = log.read_text(encoding="utf-8", errors="replace")[-1500:] if log.exists() else "(no engine log)"
                raise RuntimeError("the engine did not come up within 10 minutes. Its log ends:\n" + tail)
        if DOWNLOAD:
            def kit(kid):
                return next(k for k in web.get("/models/api/status", timeout=60)["kits"] if k["id"] == kid)

            def staged(kid):   # bytes waiting in the staging area of one kit
                folder = models.STAGING / kid
                return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) if folder.exists() else 0

            @check("a model file is downloaded from Hugging Face and put in its place (the Fast video add-on)")
            def _():
                f = kit("fast")["files"][0]
                where = models.COMFY_MODELS / f["folder"] / f["name"]
                if f["have"]:
                    return "it is here already: nothing was downloaded"
                web.post("/models/api/kit/download", {"id": "fast"})
                deadline = time.time() + 900
                while time.time() < deadline:
                    k = kit("fast")
                    if k["files"][0]["have"] and not k["job"]:
                        assert where.stat().st_size == f["bytes"], f"{where.stat().st_size} bytes, expected {f['bytes']}"
                        assert not (models.STAGING / "fast").exists(), "its staging folder was left behind"
                        return f"{f['bytes'] / 1e6:.0f} MB, now in models/{f['folder']}"
                    if not k["job"] and k["last"]:
                        raise RuntimeError(f"the download ended as {k['last']['state']}: {k['last']['error']}")
                    time.sleep(2)
                raise RuntimeError("not finished within 15 minutes")

            @check("a download that is stopped really stops (the Video kit, stopped after a few megabytes)")
            def _():
                k = kit("video")
                if all(f["have"] for f in k["files"]):
                    return "the Video kit is here already: nothing was downloaded"
                job = web.post("/models/api/kit/download", {"id": "video"})
                deadline = time.time() + 180
                while True:
                    k = kit("video")
                    if not k["job"]:
                        last = k["last"] or {}
                        raise RuntimeError(f"it ended by itself as {last.get('state')}: {last.get('error')}")
                    if k["job"].get("done", 0) >= 8e6:
                        break
                    if time.time() > deadline:
                        raise RuntimeError("nothing arrived within 3 minutes")
                    time.sleep(1)
                web.post("/models/api/cancel", {"id": job["id"]})
                deadline = time.time() + 30
                while kit("video")["job"]:
                    if time.time() > deadline:
                        raise RuntimeError("it was still listed as downloading 30 seconds after being stopped")
                    time.sleep(1)
                last = kit("video")["last"] or {}
                assert last.get("state") == "cancelled", f"it ended as {last.get('state')}: {last.get('error')}"
                time.sleep(2)
                before = staged("video")
                time.sleep(6)
                after = staged("video")
                assert after == before, f"something is still downloading: {before} bytes, then {after} six seconds later"
                still = ""
                if os.name == "nt":
                    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq hf.exe", "/NH"], capture_output=True, text=True, errors="replace")
                    assert "hf.exe" not in r.stdout.lower(), "hf.exe is still running: " + r.stdout.strip()[:200]
                    still = ", no hf.exe left"
                return f"stopped with {before / 1e6:.0f} MB fetched, and nothing grew afterwards{still}"
    finally:
        stop(app)
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
            for _ in range(20):   # (Windows lets go of a stopped program's files a moment after it is gone)
                shutil.rmtree(data, ignore_errors=True)
                if not data.exists():
                    break
                time.sleep(0.5)
            if made_home:
                try:
                    home.rmdir()   # only if nothing else is in it
                except OSError:
                    pass
    print(f"\n{sum(ok for ok, _ in RESULTS)} of {len(RESULTS)} checks passed." + ("" if not bad else " Failed: " + "; ".join(bad)))
    raise SystemExit(1 if bad or not RESULTS else 0)


if __name__ == "__main__":
    main()
