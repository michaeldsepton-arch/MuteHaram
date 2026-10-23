"""
The engine must not turn passing trouble into silence, and must never let the music through.

    python tests/test_engine_robustness.py

1. A source that stops early once in the middle (a network hiccup) is retried and the
   audio of that part is still produced.
2. A part of the source that can NEVER be decoded is silent by default (never the music), or the original
   audio (read separately) with fallback="original".
3. Normal and "speech" modes both run.
"""
import os
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from muteharam_pc import config  # noqa: E402
from muteharam_pc.engine import MediaSource, Session, SAMPLE_RATE  # noqa: E402

cfg = config.load()
tmp = tempfile.mkdtemp(prefix="mh_test_")
wav = os.path.join(tmp, "tone.wav")
# 45 s of audio that is never silent: a low tone with an amplitude wobble
subprocess.run([cfg["ffmpeg"], "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                "sine=frequency=220:duration=45", "-af", "volume='0.2+0.1*sin(2*PI*t)':eval=frame,aresample=44100",
                "-ac", "2", wav], check=True)


class Flaky(MediaSource):
    """Decoder that stops after 2 s the first `fails` times it is started beyond 18 s."""

    def __init__(self, *a, fails=1, region_never=None, **k):
        super().__init__(*a, **k)
        self.fails = fails
        self.region_never = region_never    # (from_s, to_s) where decoding always breaks
        self.starts = []

    def decoder(self, start_seconds, log=None):
        self.starts.append(start_seconds)
        if self.region_never:
            broken = self.region_never[0] <= start_seconds < self.region_never[1]
        else:
            broken = start_seconds > 18 and self.fails > 0
            if broken:
                self.fails -= 1
        if broken:
            cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{start_seconds:.3f}",
                   "-t", "2", "-i", self.url, "-vn", "-ac", "2", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"]
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            p.err_tail = []
            return p
        return super().decoder(start_seconds, log)


def rms(a):
    a = a.astype(np.float32) / 32768
    return float(np.sqrt((a ** 2).mean()))


def make(src, **kw):
    return Session(src, models_dir=cfg["models_dir"], log=lambda *a: None, patience_s=60, **kw)


# reference: a normal run (a pure tone counts as "music", so the AI may remove most of it;
# what matters is that trouble doesn't change the result into silence or something else)
src = MediaSource(wav, ffmpeg=cfg["ffmpeg"], ffprobe=cfg["ffprobe"])
s = make(src)
ref = s.read_frames(15 * SAMPLE_RATE, 30 * SAMPLE_RATE)
s.close()

# 1. one hiccup in the middle -> retried, same result as a normal run
src = Flaky(wav, ffmpeg=cfg["ffmpeg"], ffprobe=cfg["ffprobe"], fails=1)
s = make(src)
audio = s.read_frames(15 * SAMPLE_RATE, 30 * SAMPLE_RATE)
diff = int(np.abs(audio.astype(np.int32) - ref.astype(np.int32)).max())
print("hiccup test: max difference from a normal run =", diff, "| decoder (re)starts:", len(src.starts),
      "| blocks marked bad:", int(s.bad.sum()))
assert diff < 400, "after a hiccup the audio must match a normal run (not silence)"
assert int(s.bad.sum()) == 0, "a single hiccup should be recovered by the retry"
s.close()

# 2a. a region that can never be read -> silence by default (the music must never come through)
src = Flaky(wav, ffmpeg=cfg["ffmpeg"], ffprobe=cfg["ffprobe"], fails=0, region_never=(10.0, 40.0))
s = make(src)
audio = s.read_frames(18 * SAMPLE_RATE, 24 * SAMPLE_RATE)
print("unreadable-part test (default): rms =", round(rms(audio), 5), "| fallbacks:", s.fallbacks)
assert rms(audio) < 1e-4 and s.fallbacks >= 1
s.close()

# 2b. the same with fallback="original" -> the ORIGINAL audio, not silence
src = Flaky(wav, ffmpeg=cfg["ffmpeg"], ffprobe=cfg["ffprobe"], fails=0, region_never=(10.0, 40.0))
s = make(src, fallback="original")
audio = s.read_frames(18 * SAMPLE_RATE, 24 * SAMPLE_RATE)
orig = MediaSource(wav, ffmpeg=cfg["ffmpeg"], ffprobe=cfg["ffprobe"]).read_s16(18.0, 6.0)[: len(audio)]
diff = int(np.abs(audio[: len(orig)].astype(np.int32) - orig.astype(np.int32)).max())
print("unreadable-part test: rms =", round(rms(audio), 4), "| max difference from the original =", diff,
      "| blocks marked bad:", int(s.bad.sum()), "| fallbacks:", s.fallbacks)
assert rms(audio) > 0.005, "an unreadable part must not be silent"
assert diff < 400, "an unreadable part must play as the original audio"
assert s.fallbacks >= 1
s.close()

# 3. both modes run
for mode in ("music", "speech"):
    src = MediaSource(wav, ffmpeg=cfg["ffmpeg"], ffprobe=cfg["ffprobe"])
    s = make(src, mode=mode)
    a = s.read_frames(0, 8 * SAMPLE_RATE)
    print(f"mode {mode}: rms {rms(a):.4f}")
    assert len(a) == 8 * SAMPLE_RATE and np.isfinite(a).all()
    s.close()
print("ENGINE ROBUSTNESS TEST OK")
