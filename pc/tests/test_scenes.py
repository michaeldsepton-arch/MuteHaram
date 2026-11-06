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


class FakeDetector:
    def __init__(self):
        self.asked = []

    def nudenet(self, frame):
        return "clear"                                   # only the model sees these scenes (like kissing)

    def clip(self, frame):
        t = int(frame[0, 0, 0]) * 256 + int(frame[0, 0, 1])
        return 0.9 if t in SCENE else 0.05

    def ask(self, frame):
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
assert covers == [[95.0, 112.0], [295.0, 314.0], [395.0, 406.0]], covers   # (300 s found at 304, looked back)
assert len(s.det.asked) < 130, "between scenes the model only looks every 8 s (and at what the fast checks flag)"
assert all(t in s.det.asked for t in range(99, 113)), "around a find it looks every second"
assert merge([[1, 5], [5.5, 9], [20, 30]]) == [[1, 9], [20, 30]]
print("SCENES TEST OK")
