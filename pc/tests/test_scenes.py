"""
Lowering the gaze: the scanner's rules, with fake frames (no pictures: each fake frame just says cover / clear).
  every second NudeNet + CLIP; the model confirms what they flag (and looks every 8 s anyway); every
  second once something is found, look back to where it began,
  a scene ends after 5 clear seconds, 5 extra seconds covered on each side.

    python tests/test_scenes.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from muteharam_pc.scenes import SceneScanner, merge  # noqa: E402

SCENE = set(range(100, 108)) | set(range(400, 402))   # seconds that show something to cover
MISSED_BY_CLIP = set(range(300, 310))                  # a scene CLIP does not notice: the 8-second check finds it
NUDE_ALONE = {200, 500, 502}                           # NudeNet finds: 200 alone (a false alarm), 500 + 502 a pair


class FakeDetector:
    def __init__(self):
        self.asked = []

    def nudenet(self, frame):
        t = int(frame[0, 0, 0]) * 256 + int(frame[0, 0, 1])
        return "nudity" if t in NUDE_ALONE else "clear"

    def clip(self, frame):
        t = int(frame[0, 0, 0]) * 256 + int(frame[0, 0, 1])
        vec = np.zeros(1024, np.float32)
        vec[t % 1024] = 1.0                      # every second a different shot (no answer used again)
        return (0.9 if t in SCENE else 0.05), vec

    def ask(self, frame, t=None, vec=None):
        t = int(frame[0, 0, 0]) * 256 + int(frame[0, 0, 1])
        self.asked.append(t)
        return t in SCENE or t in MISSED_BY_CLIP


class FakeScanner(SceneScanner):
    def _frames(self, at):
        for t in range(int(at), int(self.duration)):
            f = np.zeros((2, 2, 3), np.uint8)
            f[0, 0, 0], f[0, 0, 1] = t // 256, t % 256
            yield float(t), f


s = FakeScanner("x", {}, "ffmpeg", duration=600, log=lambda *a: None)
s.det = FakeDetector()
s._scan_from(0.0)
covers = merge(s.covers)
print("covers:", covers, "| model checks:", len(s.det.asked))
assert covers == [[95.0, 112.0], [295.0, 314.0], [395.0, 406.0], [497.0, 507.0]], covers   # (300: found at 304, looked back)
assert not any(a <= 200 <= b for a, b in covers), "a single NudeNet find (no other sign) is not covered"
assert len(s.det.asked) < 130, "between scenes the model only looks every 8 s (and at what the fast checks flag)"
assert all(t in s.det.asked for t in range(108, 113)), "after a scene the model checks every second until 5 clear"
assert not any(t in s.det.asked for t in range(102, 108)), "CLIP sure for 3 s starts it; inside, a sure fast check is enough"
assert merge([[1, 5], [5.5, 9], [20, 30]]) == [[1, 9], [20, 30]]

# the real frame reader (one frame per second, in memory), on a synthetic test pattern video
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from muteharam_pc import config  # noqa: E402
from muteharam_pc.scenes import W, H  # noqa: E402
cfg = config.load()
clip = os.path.join(tempfile.mkdtemp(prefix="mh_scenes_"), "pattern.mp4")
subprocess.run([cfg["ffmpeg"], "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=6:r=25", clip], check=True)
real = SceneScanner(clip, {}, cfg["ffmpeg"], duration=6, log=lambda *a: None)
got = [(t, f.shape) for t, f in real._frames(0.0)]
assert len(got) in (6, 7) and got[0][1] == (H, W, 3), got[:2]
os.remove(clip)

# the same shot: the answer is used again (for at most 4 s), a new shot is asked
import muteharam_pc.scenes as SC  # noqa: E402
d = object.__new__(SC.Detector)
d.vlm_ok, d.asked, d.reused, d._last, calls = True, 0, 0, None, []
d._ask = lambda f: calls.append(1) or True
a, b = np.zeros(4, np.float32), np.zeros(4, np.float32)
a[0], b[1] = 1.0, 1.0
assert d.ask(None, 10, a) and d.ask(None, 11, a) and d.ask(None, 14, a) and len(calls) == 1, "same shot: asked once"
d.ask(None, 19, a)
d.ask(None, 20, b)
assert len(calls) == 3 and d.reused == 2, "too old, or another shot: asked again"

# the player skips ahead: checking moves there, then fills the part it skipped; the head start it needs
import threading  # noqa: E402
import time  # noqa: E402
order = []


class SlowFake(FakeScanner):
    def _frames(self, at):
        for t in range(int(at), int(self.duration)):
            if self._stop or self._jump is not None:
                return
            time.sleep(0.003)
            order.append(t)
            f = np.zeros((2, 2, 3), np.uint8)
            f[0, 0, 0], f[0, 0, 1] = t // 256, t % 256
            yield float(t), f


class FakeDet(FakeDetector):
    vlm_ok = True
    asked_n = reused = 0

    def __init__(self, *a):
        super().__init__()

    def warm_up(self):
        pass


SC.Detector = FakeDet
sk = SlowFake("x", {}, "ffmpeg", duration=600, log=lambda *a: None).begin()
while sk.scanned_to < 50:
    time.sleep(0.01)
sk.follow(400)
sk.thread.join(30)
st = sk.state()
assert st["done"] and st["scanned"] == [[0.0, 599.0]], st["scanned"]
k = order.index(397)
assert order[k - 1] < 120 and max(order[k:k + 200]) >= 590 and min(order[k + 205:]) > 50, "skipped part checked next, gap after"
assert sk.ahead_of(450) == 599.0
assert len(set(order)) == 600 and len(order) <= 604, ("every second once (a frame at each boundary is read, not checked)", len(order))
sk._speed.clear()
now = time.time()
sk._speed.extend([(now - 60, 100.0), (now, 130.0)])          # 0.5x film speed: needs a big head start
assert 340 < sk.need_ahead(0) < 360, sk.need_ahead(0)   # 600 s left at 0.45x: 600*0.55 + 18
sk._speed.extend([(now + 30, 200.0)])
sk._speed.popleft()
assert sk.need_ahead(0) == 45.0, "faster than playback: a short head start is enough"
print("SCENES TEST OK")
