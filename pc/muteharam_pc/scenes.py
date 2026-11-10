"""
Lowering the gaze: finds the parts of a film to cover (nudity, partial nudity, something sexual, kissing).

The film's picture is read at one frame per second, in memory only: no frame is ever written to disk or shown.
  * every second, two fast checks on the processor: NudeNet (nudity) and CLIP (how much the frame looks like
    kissing, a sexual scene, underwear ...);
  * the vision model in Ollama (on this PC, ~3 s a frame) answers a short yes/no question for every frame those
    flag, every second while a scene lasts (and for the few seconds before it, to find where it began), and
    every 8 seconds anyway, in case both fast checks missed something;
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
# Measured on a film with many such scenes: the plain question ("nudity, underwear, sexual, kissing?") made the
# small model answer "no" to almost everything a larger model flagged. This wording asks about modesty instead.
# Then (watching Young Sherlock) it covered a chase, a man shaving and a kiss on the cheek: those are named as
# not counting.
QUESTION = ("This frame is from a film. A family that keeps strict modesty wants to skip any shot that shows: "
            "a woman's cleavage, bare shoulders, bare belly or bare thighs; anyone in underwear, lingerie or "
            "swimwear; nudity; people in bed together intimately; sexual activity or sexual touching; or a "
            "romantic kiss on the lips. A man's bare chest, fighting, running, hugging, or a kiss on the cheek "
            "or forehead do not count. Does this shot show any of these? Answer only yes or no.")
# Within one shot the frames hardly change: the answer for a frame this alike (CLIP, cosine) and at most this many
# seconds older is used again instead of asking again (most questions were about the same shot, second after second)
SAME_SHOT = 0.96
SAME_SHOT_S = 4
GPU_LAYERS = 4            # Ollama: this much of the vision model on the graphics card (~1 GB: room is left for the
                          # music removal and the subtitles; the whole model would take 5 GB and slow them down)
CLIP_MODEL = "openai/clip-vit-base-patch32"
# what CLIP compares each frame with: the first group is what to cover, the rest is ordinary film
CLIP_COVER = ["two people kissing on the lips", "a couple kissing passionately", "a sexual scene in bed",
              "a naked person", "a woman in underwear or lingerie", "a person in a bikini or swimwear",
              "a shirtless couple embracing in bed"]
CLIP_OTHER = ["people talking to each other", "a man and a woman talking face to face", "a person's face in close up",
              "people walking in a street", "a room in a house", "a landscape", "a car or a vehicle",
              "a group of people", "text on a screen", "a dark scene", "people fighting", "people at a table eating",
              "a person hugging another person", "people in an office"]
CLIP_FLAG = 0.30          # this much of CLIP's choice on the "cover" group: ask the vision model
CLIP_SURE = 0.60          # inside a scene already found: this much counts as "still going" without asking
# Measured: on a film with many such scenes CLIP was this sure on >10 % of its seconds (a larger model agreed on
# ~60 % of them); on a clean 1940 film on none at all. So this sure for CLIP_RUN seconds in a row starts a scene.
CLIP_STRONG = 0.90
CLIP_RUN = 3
NUDE_PAIR_S = 3           # NudeNet alone needs two finds this close (a clean film had 2 single false alarms)


NUDE_LABELS = {"FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "BUTTOCKS_EXPOSED",
               "ANUS_EXPOSED"}
HINT_LABELS = {"FEMALE_BREAST_COVERED", "BUTTOCKS_COVERED", "FEMALE_GENITALIA_COVERED"}
W, H = 448, 252                       # frame size the detectors see (letterboxed)
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def small_variant(url, headers, alts=(), duration=None, ffprobe=None, log=print):
    """For checking, a small copy of the same film's picture (at least 600 wide): far less to download and decode
    than full quality (the checks only look at 448 x 252), which leaves the processor to the vision model.
    Lists of qualities among the other addresses the page loaded are used only when their length matches the
    film's to the second (pages also load trailers and adverts)."""
    import re
    from urllib.parse import urljoin
    if not str(url).startswith(("http://", "https://")):
        return url
    hdrs = {k: v for k, v in (headers or {}).items() if k.lower() != "cookie"}

    def get(u):
        with urllib.request.urlopen(urllib.request.Request(u, headers=hdrs), timeout=20) as r:
            return r.read(1 << 20).decode("utf-8", "replace")

    def options(u):
        try:
            text = get(u)
        except Exception:
            return []
        lines = text.splitlines()
        out = []
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                ref = next((x.strip() for x in lines[i + 1:] if x.strip() and not x.startswith("#")), None)
                res = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
                bw = re.search(r"BANDWIDTH=(\d+)", line)
                if ref:
                    out.append((int(res.group(1)) if res else 0, int(bw.group(1)) if bw else 0, urljoin(u, ref)))
        return out

    same_folder = lambda u: u.split("?")[0].rsplit("/", 1)[0] == url.split("?")[0].rsplit("/", 1)[0]
    cands = [url] + ([url.replace("/index.m3u8", "/master.m3u8")] if "/index.m3u8" in url else [])
    cands += [u for u in (alts or []) if u not in cands]
    for u in cands:
        opts = options(u)
        if len(opts) < 2:
            continue
        small = min([o for o in opts if o[0] >= 600] or opts, key=lambda o: (o[0] or 1e9, o[1]))[2]
        if u != url and not same_folder(u):
            if not (duration and ffprobe):
                continue
            from .library import probe_duration
            d = probe_duration(ffprobe, small, headers)
            if d is None or abs(d - duration) > 1.5:
                continue                                  # a trailer, an advert, another film
        return small
    return url


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

    def __init__(self, model=MODEL, log=print, gpu_layers=GPU_LAYERS):
        from nudenet import NudeDetector
        self.nude = NudeDetector()
        self._clip = None
        self.model = model
        self.log = log
        self.gpu_layers = gpu_layers
        self.vlm_ok = ensure_ollama(log)
        self.vlm_errors = 0
        self.asked = 0                         # questions to the vision model
        self.reused = 0                        # answers used again for the same shot
        self._last = None                      # (time, picture vector, answer) of the last question

    def nudenet(self, frame):
        """-> 'nudity' / 'hint' (covered private parts: worth asking the model) / 'clear'"""
        labels = {d["class"] for d in self.nude.detect(frame[:, :, ::-1].copy()) if d["score"] >= 0.45}
        return "nudity" if labels & NUDE_LABELS else "hint" if labels & HINT_LABELS else "clear"

    def clip(self, frame):
        """-> (how much the frame looks like something to cover (0..1), its picture vector), from CLIP (~0.1 s)."""
        import torch
        if self._clip is None:
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            from transformers import CLIPModel, CLIPProcessor
            torch.set_num_threads(2)            # (the vision model needs the processor more)
            m = CLIPModel.from_pretrained(CLIP_MODEL, use_safetensors=True).eval()
            proc = CLIPProcessor.from_pretrained(CLIP_MODEL)
            with torch.no_grad():
                t = _features(m.get_text_features(**proc(text=CLIP_COVER + CLIP_OTHER, return_tensors="pt", padding=True)))
            self._clip = (m, proc, t / t.norm(dim=-1, keepdim=True))
        m, proc, text = self._clip
        with torch.no_grad():
            im = _features(m.get_image_features(**proc(images=frame, return_tensors="pt")))
            im = im / im.norm(dim=-1, keepdim=True)
            probs = (100.0 * im @ text.T).softmax(dim=-1)[0]
        return float(probs[:len(CLIP_COVER)].sum()), im[0].numpy()

    def ask(self, frame, t=None, vec=None):
        """The vision model's answer: True = cover. Unreachable model: False (NudeNet still works).
        t, vec (film time, CLIP vector): the same shot as the last question, seconds ago -> that answer again."""
        if not self.vlm_ok:
            return False
        last = self._last
        if (t is not None and vec is not None and last is not None and 0 <= t - last[0] <= SAME_SHOT_S
                and float(np.dot(vec, last[1])) >= SAME_SHOT):
            self.reused += 1
            return last[2]
        ans = self._ask(frame)
        self.asked += 1
        if t is not None and vec is not None:
            self._last = (t, vec, ans)
        return ans

    def _ask(self, frame):
        import cv2
        ok, jpg = cv2.imencode(".jpg", frame[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 85])
        body = {"model": self.model, "prompt": QUESTION, "images": [base64.b64encode(jpg.tobytes()).decode()],
                "stream": False, "think": False, "keep_alive": "30m", "options": {"temperature": 0, "num_predict": 3}}
        if self.gpu_layers is not None:
            body["options"]["num_gpu"] = int(self.gpu_layers)
        try:
            req = urllib.request.Request(OLLAMA + "/api/generate", json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                ans = (json.loads(r.read()).get("response") or "").strip().lower()
            self.vlm_errors = 0
            return ans.startswith("yes")
        except Exception as ex:
            self.vlm_errors += 1
            if self.vlm_errors in (1, 10, 100):
                self.log(f"scenes: the vision model did not answer ({ex!r}); NudeNet keeps checking")
            if self.vlm_errors >= 3:
                self.vlm_ok = ensure_ollama(self.log, wait_s=5)
            return False

    def warm_up(self):
        """Load the model now (the first answer takes ~45 s), so live checking starts on time."""
        self._ask(np.zeros((H, W, 3), np.uint8))


class SceneScanner:
    BACKSTOP = 8      # the vision model also looks every this many seconds (in case the fast checks miss something)
    LOOKBACK = 4      # seconds looked at again before a find, to see where it began
    CLEAR_RUN = 5     # clear seconds in a row that end a scene
    PAD = 5           # extra seconds covered before and after

    def __init__(self, url, headers, ffmpeg, duration, out_path=None, start=0.0, covers=None, log=print,
                 model=MODEL, alts=(), gpu_layers=GPU_LAYERS, scanned=None):
        self.url, self.headers, self.ffmpeg = url, dict(headers or {}), ffmpeg
        self.duration = float(duration or 0)
        self.out_path = out_path
        self.start = max(0.0, float(start))
        self.covers = [list(c) for c in (covers or [])]
        self.said = []                         # subtitles of the covered parts: [start, end, text] (captions.py)
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
        self.ready = False                     # the models are loaded (the first minute is loading them)
        self.scan_url = None                   # the (small) picture actually read for checking
        self.alts = list(alts or [])
        self.gpu_layers = gpu_layers
        # the parts already checked: closed ranges, and the one being checked now [cur_from, scanned_to]
        self.ranges = [list(r) for r in (scanned or [])]
        self.cur_from = self.start
        self.pos = self.start                  # where the player is (it reports it)
        self._jump = None                      # the player skipped: check from here instead
        self._speed = collections.deque(maxlen=400)   # (wall time, scanned_to) of the current range

    # ---- what the players read
    def scanned(self):
        """The checked parts of the film, merged: [[from, to], ...]."""
        return merge(self.ranges + [[self.cur_from, self.scanned_to]])

    def ahead_of(self, t):
        """How far the checked part reaches from t on (the end of the checked range t is in), or None."""
        for a, b in self.scanned():
            if a - 0.5 <= t <= b:
                return b
        return None

    def rate(self):
        """How fast the check goes, in seconds of film per second (measured over the last minute), or None."""
        sp = list(self._speed)
        if len(sp) < 2:
            return None
        now = sp[-1][0]
        old = next((x for x in sp if now - x[0] <= 60), sp[0])
        if now - old[0] < 20:
            return None
        return (sp[-1][1] - old[1]) / (now - old[0])

    def need_ahead(self, t):
        """How far ahead of playback (seconds) the check must be so that playback never has to wait for it."""
        r = self.rate()
        left = max(0.0, (self.duration or t + 3600) - t)
        if r is None:
            return min(left, 60.0)
        r *= 0.9                               # a little margin: busy scenes are checked more slowly
        if r >= 1.0:
            return min(left, 45.0)
        return min(left, left * (1.0 - r) + 40.0 * r)

    def follow(self, t):
        """The player is at t (seconds). If it skipped to a part not checked, checking moves there."""
        self.pos = t
        if self.done:
            return
        with self._lock:
            if self.cur_from - 1 <= t <= self.scanned_to + 30 or self._jump is not None:
                return
            end = self.ahead_of(t)
            if end is not None:
                # checked already (earlier): carry on where that part ends, once playback gets near it
                if end - t > 150 or self.cur_from <= end <= self.scanned_to + 1:
                    return
                self._jump = end
            else:
                self._jump = max(0.0, t - 3)
        self.log(f"scenes: the player is at {fmt(t)}: checking from {fmt(self._jump)}")

    def state(self):
        with self._lock:
            cov = [list(c) for c in self.covers] + ([list(self.live)] if self.live else [])
            end = self.ahead_of(self.pos)
            rate = self.rate()
            return {"covers": merge(cov), "scanned_to": round(end if end is not None else self.scanned_to, 1),
                    "scanned": self.scanned(), "done": self.done, "error": self.error,
                    "said": list(self.said), "rate": round(rate, 2) if rate is not None else None,
                    "need": round(self.need_ahead(self.pos), 1)}

    def add_said(self, lines):
        with self._lock:
            self.said = sorted(self.said + [list(x) for x in lines])
        self.save()

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
    def _next_start(self):
        """Where to check next: the first part not checked from the player on, else from the beginning."""
        gaps, prev = [], 0.0
        for a, b in self.scanned():
            if a - prev > 2:
                gaps.append((prev, a))
            prev = max(prev, b)
        if not self.duration or self.duration - prev > 2:
            gaps.append((prev, self.duration or prev + 1e9))
        for a, b in gaps:
            if b > self.pos:
                return max(a, min(self.pos, b - 2))
        return gaps[0][0] if gaps else None

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
        if self.scan_url is None:
            probe = os.path.join(os.path.dirname(self.ffmpeg), "ffprobe" + (".exe" if self.ffmpeg.lower().endswith(".exe") else ""))
            self.scan_url = small_variant(self.url, self.headers, self.alts, self.duration,
                                          probe if os.path.exists(probe) else "ffprobe", self.log)
            if self.scan_url != self.url:
                self.log("scenes: checking a smaller copy of the picture (same film, less to download)")
        cmd += ["-threads", "2", "-ss", f"{at:.2f}", "-i", self.scan_url, "-an", "-sn",
                "-vf", f"fps=1,scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        n = W * H * 3
        k = 0
        try:
            while not self._stop and self._jump is None:
                raw = proc.stdout.read(n)
                if len(raw) < n:
                    break
                yield at + k, np.frombuffer(raw, np.uint8).reshape(H, W, 3)
                k += 1
        finally:
            proc.kill()

    def _cover(self, frame, t=None):
        if self.det.nudenet(frame) == "nudity":
            return True
        c, vec = self.det.clip(frame)
        return self.det.ask(frame, t, vec)

    def _run(self):
        try:
            self.det = Detector(self.model, self.log, self.gpu_layers)
            self.det.warm_up()
            self.ready = True
            if not self.det.vlm_ok:
                self.log("scenes: Ollama is not running; only NudeNet checks (nudity only, no kissing)")
            tries = 0
            nxt = self.start
            while not self._stop and nxt is not None and tries < 4:
                before = self.scanned_to
                self._scan_from(nxt)
                with self._lock:
                    jump, self._jump = self._jump, None
                    if jump is not None or (self.duration and self.scanned_to >= self.duration - 2):
                        # this part is finished (or the player skipped): keep it, start the next one
                        self.ranges = merge(self.ranges + [[self.cur_from, self.scanned_to]])
                        self.cur_from = self.scanned_to = jump if jump is not None else self.scanned_to
                        self._speed.clear()
                        tries = 0
                    else:
                        tries = tries + 1 if self.scanned_to - before < 5 else 0
                if self._stop:
                    break
                nxt = jump if jump is not None else self._next_start()
                if jump is None and nxt is not None and abs(nxt - self.scanned_to) > 2:
                    with self._lock:
                        self.ranges = merge(self.ranges + [[self.cur_from, self.scanned_to]])
                        self.cur_from = self.scanned_to = nxt
                        self._speed.clear()
            with self._lock:
                if self.live:
                    self.covers.append([self.live[0], self.live[1]])
                    self.live = None
                self.covers = merge(self.covers)
                self.ranges = merge(self.ranges + [[self.cur_from, self.scanned_to]])
                self.done = not self._stop and self._next_start() is None
                if not self.done and not self._stop:
                    self.error = "the picture stopped coming from the website"
            self.log(f"scenes: checked {self._summary()}; {len(self.covers)} part(s) to cover")
        except Exception as ex:
            self.error = repr(ex)[:200]
            self.log(f"scenes: stopped: {ex!r}")
        self.save()

    def _summary(self):
        d = self.det
        r = self.rate()
        return (" + ".join(f"{fmt(a)}-{fmt(b)}" for a, b in self.scanned())
                + (f" | {r:.2f}x film speed" if r else "")
                + (f" | vision model asked {d.asked} times, same-shot answer used {d.reused} times" if d else ""))

    def _scan_from(self, at):
        recent = collections.deque(maxlen=self.LOOKBACK)
        next_report = at + 600
        why = ""
        # stop where an earlier checked part begins (or at the end of the film): never check anything twice
        stop_at = min([r[0] for r in self.ranges if r[0] > at + 0.5] + [self.duration or 1e12])
        in_scene, clear_run, scene_start, last_pos = False, 0, 0.0, 0.0
        strong_run, strong_start, last_nude = 0, 0.0, None
        last_save = time.time()
        for t, frame in self._frames(at):
            if t >= stop_at:
                break
            nn = self.det.nudenet(frame)
            c, vec = self.det.clip(frame)
            strong_run = strong_run + 1 if c >= CLIP_STRONG else 0
            if strong_run == 1:
                strong_start = t
            nude_sure = nn == "nudity" and (c >= CLIP_FLAG or (last_nude is not None and t - last_nude <= NUDE_PAIR_S))
            if nn == "nudity":
                last_nude = t
            if in_scene:
                # still going? a clear sign from the fast checks is enough; otherwise the vision model decides
                pos = nn == "nudity" or c >= CLIP_SURE or self.det.ask(frame, t, vec)
            elif nude_sure or strong_run >= CLIP_RUN:
                pos = True                             # the fast checks are sure enough on their own
                why = "NudeNet: nudity" if nude_sure else "CLIP very sure for 3 s"
            else:
                ask = nn != "clear" or c >= CLIP_FLAG or int(t) % self.BACKSTOP == 0
                pos = ask and self.det.ask(frame, t, vec)
                why = f"the vision model said yes (CLIP {c:.2f}, NudeNet {nn})"
            if not in_scene and pos:
                first = strong_start if strong_run >= CLIP_RUN else t
                for tb, fb in reversed(recent):        # where did it begin? (the seconds just before)
                    if tb >= first:
                        continue
                    if self._cover(fb, tb):
                        first = tb
                    else:
                        break
                in_scene, scene_start, last_pos, clear_run = True, first, t, 0
                self.log(f"scenes: something to cover at {fmt(first)} ({why})")
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
                self._speed.append((time.time(), t))
            if t >= next_report:
                next_report = t + 600
                self.log(f"scenes: {self._summary()}")
            recent.append((t, frame))
            if time.time() - last_save > 5:
                last_save = time.time()
                self.save()
        if in_scene:
            with self._lock:
                self.covers.append([max(0.0, scene_start - self.PAD), last_pos + self.PAD])
                self.covers = merge(self.covers)
                self.live = None


def _features(out):
    """CLIP's feature vectors (newer transformers return them inside an output object)."""
    return out if hasattr(out, "norm") else out.pooler_output


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
