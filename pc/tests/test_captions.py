"""
MuteHaram's own subtitles (captions.py), without the speech model (a fake one says which second it heard):
  * long speech is split into subtitles of at most two lines of ~42 letters, timed by their share;
  * the covered parts are done first, then (when chosen) the whole film just ahead of the player;
  * a piece cut off at the end of a 30 s stretch is done again with the next stretch (no half sentences);
  * the tablet gets only the lines it does not have yet.
A real check with Cohere on a film's speech: tests/test_captions.py --real

    python tests/test_captions.py
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from muteharam_pc import captions as C  # noqa: E402

# ---- splitting into subtitles
assert C.split_lines("Hello there.", 1.0, 2.0) == [[1.0, 2.0, "Hello there."]]
long = ("I know all about reporters, Walter. A lot of daffy guys running around without a nickel in their pockets, "
        "and for what? So a million hired girls and motorman's wives will know what's going on.")
parts = C.split_lines(long, 10.0, 18.0)
assert len(parts) >= 2 and parts[0][0] == 10.0 and abs(parts[-1][1] - 18.0) < 0.05, parts
for a, b, text in parts:
    assert all(len(line) <= 2 * C.LINE for line in text.split("\n")) and text.count("\n") <= 1, text
    assert b > a
assert parts[0][2].replace("\n", " ").endswith("Walter."), "cut at a sentence end"
assert C.two_lines("short") == "short" and "\n" in C.two_lines("a" * 20 + " " + "b" * 30)

# ---- the Captioner, with a fake speech model: "heard" text says where it was spoken
calls = []


def fake_transcribe(audio, offset_s=0.0, lang="en", log=print):
    calls.append(round(offset_s, 1))
    dur = len(audio) / C.RATE
    out = []
    for k in range(int(dur // 4) + 1):      # one line every 4 s, 3 s long (the last may run past the end)
        s = offset_s + 4 * k + 0.5
        if s >= offset_s + dur:
            break
        out.append([round(s, 2), round(s + 3, 2), f"line at {s:.1f}"])
    return out


C.transcribe = fake_transcribe
real_load = C._load
C._load = lambda log=print: None            # (the Captioner loads the model as it starts: not needed here)
ready_from = [1e9]                           # the cleaned sound is ready only from here on


def clean(s, d):
    if s + d > ready_from[0]:
        return None
    return np.zeros((int(d * 44100), 2), np.int16)


class FakeScan:
    def state(self):
        return {"covers": [[500.0, 512.0]], "scanned_to": 600.0, "done": False}


ready_from[0] = 1e9
cap = C.Captioner(1200, clean, lambda s, d: np.zeros((int(d * 44100), 2), np.int16), scanner=FakeScan(),
                  full=False, log=lambda *a: None)
t0 = time.time()
while not cap.done and time.time() - t0 < 10:
    time.sleep(0.05)
time.sleep(0.5)
assert calls and calls[0] == 500.0, calls
assert all(c >= 500 for c in calls), "only the covered part (whole film not chosen)"
n, lines = cap.lines_from(0)
assert n == len(lines) == 3 and lines[0][0] == 500.5, lines

# the whole film, from the player on; the tablet gets only what it does not have
calls.clear()
cap.follow(100.0)
cap.set_full(True)
t0 = time.time()
while (not calls or max(calls) < 220) and time.time() - t0 < 10:
    time.sleep(0.05)
cap.stop()
time.sleep(0.3)
assert min(calls) == 98.0, calls
# a stretch ends at 128: the line spoken from 126.5 runs past its end -> the next stretch starts there
assert 126.5 in calls, ("the cut-off piece starts the next stretch", calls)
n2, new = cap.lines_from(n)
assert n2 > n and len(new) == n2 - n
allines = cap.lines_from(0)[1]
starts = [x[0] for x in allines]
assert starts == sorted(starts) and len(starts) == len(set(starts)), "sorted, nothing twice"
assert all(not (b > a + 3.01) for a, b, _ in allines)
print("CAPTIONS TEST OK")

if "--real" in sys.argv:
    # Cohere on a minute of a public-domain film's speech (His Girl Friday, 1940)
    import importlib
    import subprocess
    importlib.reload(C)
    from muteharam_pc import config
    url = "https://archive.org/download/his_girl_friday/his_girl_friday_512kb.mp4"
    raw = subprocess.run([config.load()["ffmpeg"], "-v", "error", "-ss", "600", "-i", url, "-t", "60", "-vn", "-f",
                          "f32le", "-ac", "1", "-ar", "16000", "pipe:1"], capture_output=True).stdout
    t = time.time()
    got = C.transcribe(np.frombuffer(raw, np.float32), 600.0)
    took = time.time() - t
    for x in got[:6]:
        print(x)
    text = " ".join(x[2] for x in got).lower()
    assert "newspaper" in text and "insurance business" in text, text
    print(f"REAL CAPTIONS OK ({C._which}): {len(got)} lines, 60 s of film in {took:.1f} s")
