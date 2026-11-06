"""
Lowering the gaze: finds the parts of a film to cover (nudity, partial nudity, something sexual, kissing).

The film's picture is read at one frame per second, in memory only: no frame is ever written to disk or shown.
  * NudeNet (small nudity detector, on the processor) looks at every second;
  * a vision model in Ollama (on this PC) answers a fixed yes/no question every 2 seconds, and every second
    once something was found (and for the few seconds before it, to find where it began);
  * a scene ends after 5 clear seconds in a row; it is covered with 5 extra seconds before and after.
The result is a list of time ranges ([start, end] seconds); the players cover the picture and mute the sound
there (the cover says "Lowering the gaze"). It is written to a small JSON file next to the film:
    {"covers": [[s, e], ...], "scanned_to": seconds, "done": bool, "error": ""}
"""
import base64
import collections
import json
import os
import subprocess
import threading
import time
import urllib.request

import numpy as np

OLLAMA = "http://127.0.0.1:11434"
MODEL = "gemma4:e4b"
QUESTION = (
    "You check frames of a film for a family that avoids immodest scenes. Look at this frame and answer with JSON "
    'only, exactly these keys: {"nudity": true/false, "partial_nudity": true/false, "sexual": true/false, '
    '"kissing": true/false}. nudity = exposed private parts or bare breasts or bare buttocks. partial_nudity = '
    "underwear, lingerie, swimwear or a person mostly undressed. sexual = sexual activity or a clearly sexual "
    "pose or touching. kissing = two people kissing (on the lips or passionately). If unsure, answer false."
)
NUDE_LABELS = {"FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "BUTTOCKS_EXPOSED",
               "ANUS_EXPOSED"}
HINT_LABELS = {"FEMALE_BREAST_COVERED", "BUTTOCKS_COVERED", "FEMALE_GENITALIA_COVERED"}
W, H = 448, 252                       # frame size the detectors see (letterboxed)
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def ollama_exe():
    p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
    return p if os.path.exists(p) else "ollama"


def ensure_ollama(log=print, wait_s=40):
    """Ollama answers (starts it in the background if needed). -> True when it is running."""
    def up():
        try:
            urllib.request.urlopen(OLLAMA + "/api/tags", timeout=3).read()
            return True
        except Exception:
            return False
    if up():
        return True
    try:
        subprocess.Popen([ollama_exe(), "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
    except Exception as ex:
        log(f"scenes: Ollama could not be started: {ex!r}")
        return False
    t0 = time.time()
    while time.time() - t0 < wait_s:
        time.sleep(1)
        if up():
            log("scenes: started Ollama")
            return True
    return False


class Detector:
    """Is this frame one to cover? NudeNet first (fast), the vision model when needed."""

    def __init__(self, model=MODEL, log=print):
        from nudenet import NudeDetector
        self.nude = NudeDetector()
        self.model = model
        self.log = log
        self.vlm_ok = ensure_ollama(log)
        self.vlm_errors = 0

    def nudenet(self, frame):
        """-> 'nudity' / 'hint' (covered private parts: worth asking the model) / 'clear'"""
        labels = {d["class"] for d in self.nude.detect(frame[:, :, ::-1].copy()) if d["score"] >= 0.45}
        return "nudity" if labels & NUDE_LABELS else "hint" if labels & HINT_LABELS else "clear"

    def ask(self, frame):
        """The vision model's answer: True = cover. Unreachable model: False (NudeNet still works)."""
        if not self.vlm_ok:
            return False
        import cv2
        ok, jpg = cv2.imencode(".jpg", frame[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 85])
        body = {"model": self.model, "prompt": QUESTION, "images": [base64.b64encode(jpg.tobytes()).decode()],
                "stream": False, "format": "json", "think": False, "keep_alive": "30m",
                "options": {"temperature": 0, "num_predict": 60}}
        try:
            req = urllib.request.Request(OLLAMA + "/api/generate", json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                ans = json.loads(json.loads(r.read()).get("response") or "{}")
            self.vlm_errors = 0
            return any(ans.get(k) is True for k in ("nudity", "partial_nudity", "sexual", "kissing"))
        except Exception as ex:
            self.vlm_errors += 1
            if self.vlm_errors in (1, 10, 100):
                self.log(f"scenes: the vision model did not answer ({ex!r}); NudeNet keeps checking")
            if self.vlm_errors >= 3:
                self.vlm_ok = ensure_ollama(self.log, wait_s=5)
            return False

    def warm_up(self):
        """Load the model now (the first answer takes ~45 s), so live checking starts on time."""
        self.ask(np.zeros((H, W, 3), np.uint8))


class SceneScanner:
    COARSE = 2        # seconds between the vision model's checks while nothing is found (any scene of 2 s+ is seen)
    LOOKBACK = 4      # seconds looked at again before a find, to see where it began
    CLEAR_RUN = 5     # clear seconds in a row that end a scene
    PAD = 5           # extra seconds covered before and after

    def __init__(self, url, headers, ffmpeg, duration, out_path=None, start=0.0, covers=None, log=print,
                 model=MODEL):
        self.url, self.headers, self.ffmpeg = url, dict(headers or {}), ffmpeg
        self.duration = float(duration or 0)
        self.out_path = out_path
        self.start = max(0.0, float(start))
        self.covers = [list(c) for c in (covers or [])]
        self.live = None                       # the scene being found right now: [start, end so far]
        self.scanned_to = self.start
        self.done = False
        self.error = ""
        self.log = log
        self.model = model
        self.det = None
        self._stop = False
        self._lock = threading.Lock()
        self.thread = None

    # ---- what the players read
    def state(self):
        with self._lock:
            cov = [list(c) for c in self.covers] + ([list(self.live)] if self.live else [])
            return {"covers": merge(cov), "scanned_to": round(self.scanned_to, 1), "done": self.done, "error": self.error}

    def save(self):
        if not self.out_path:
            return
        from .library import write_json
        try:
            write_json(self.out_path, self.state())
        except Exception:
            pass

    def begin(self):
        self.thread = threading.Thread(target=self._run, name="scene-scanner", daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self._stop = True

    # ---- reading the picture, one frame per second, in memory only
    def _frames(self, at):
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if self.url.startswith(("http://", "https://")):
            cmd += ["-rw_timeout", "20000000", "-extension_picky", "0"] if ".m3u8" in self.url.lower() or "/vs/" in self.url \
                else ["-rw_timeout", "20000000"]
            h = dict(self.headers)
            ua = next((h.pop(k) for k in list(h) if k.lower() == "user-agent"), None)
            if ua:
                cmd += ["-user_agent", ua]
            if h:
                cmd += ["-headers", "".join(f"{k}: {v}" + chr(13) + chr(10) for k, v in h.items())]
        cmd += ["-ss", f"{at:.2f}", "-i", self.url, "-an", "-sn",
                "-vf", f"fps=1,scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        n = W * H * 3
        k = 0
        try:
            while not self._stop:
                raw = proc.stdout.read(n)
                if len(raw) < n:
                    break
                yield at + k, np.frombuffer(raw, np.uint8).reshape(H, W, 3)
                k += 1
        finally:
            proc.kill()

    def _cover(self, frame, nn=None):
        nn = nn or self.det.nudenet(frame)
        return nn == "nudity" or self.det.ask(frame)

    def _run(self):
        try:
            self.det = Detector(self.model, self.log)
            self.det.warm_up()
            if not self.det.vlm_ok:
                self.log("scenes: Ollama is not running; only NudeNet checks (nudity only, no kissing)")
            tries = 0
            while not self._stop and (not self.duration or self.scanned_to < self.duration - 2) and tries < 4:
                before = self.scanned_to
                self._scan_from(self.scanned_to if self.scanned_to > self.start else self.start)
                tries = tries + 1 if self.scanned_to - before < 5 else 0
            with self._lock:
                if self.live:
                    self.covers.append([self.live[0], self.live[1]])
                    self.live = None
                self.covers = merge(self.covers)
                self.done = not self._stop and (not self.duration or self.scanned_to >= self.duration - 5)
                if not self.done and not self._stop:
                    self.error = "the picture stopped coming from the website"
            self.log(f"scenes: checked up to {fmt(self.scanned_to)}; {len(self.covers)} part(s) to cover")
        except Exception as ex:
            self.error = repr(ex)[:200]
            self.log(f"scenes: stopped: {ex!r}")
        self.save()

    def _scan_from(self, at):
        recent = collections.deque(maxlen=self.LOOKBACK)
        in_scene, clear_run, scene_start, last_pos = False, 0, 0.0, 0.0
        last_save = time.time()
        for t, frame in self._frames(at):
            nn = self.det.nudenet(frame)
            ask = in_scene or nn != "clear" or int(t) % self.COARSE == 0
            pos = nn == "nudity" or (ask and self.det.ask(frame))
            if not in_scene and pos:
                first = t
                for tb, fb in reversed(recent):        # where did it begin? (the seconds just before)
                    if self._cover(fb):
                        first = tb
                    else:
                        break
                in_scene, scene_start, last_pos, clear_run = True, first, t, 0
                self.log(f"scenes: something to cover at {fmt(first)}")
            elif in_scene:
                if pos:
                    last_pos, clear_run = t, 0
                else:
                    clear_run += 1
                    if clear_run >= self.CLEAR_RUN:
                        with self._lock:
                            self.covers.append([max(0.0, scene_start - self.PAD), last_pos + self.PAD])
                            self.covers = merge(self.covers)
                            self.live = None
                        in_scene = False
            with self._lock:
                if in_scene:                           # covered already while its end is still being found
                    self.live = [max(0.0, scene_start - self.PAD), t + self.PAD]
                self.scanned_to = t
            recent.append((t, frame))
            if time.time() - last_save > 5:
                last_save = time.time()
                self.save()
        if in_scene:
            with self._lock:
                self.covers.append([max(0.0, scene_start - self.PAD), last_pos + self.PAD])
                self.covers = merge(self.covers)
                self.live = None


def merge(ranges):
    out = []
    for s, e in sorted([list(r) for r in ranges]):
        if out and s <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([round(s, 1), round(e, 1)])
    return out


def fmt(s):
    s = int(s)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
