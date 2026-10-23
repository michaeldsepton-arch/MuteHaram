"""
A stream with a missing piece (its clock jumps ahead) must keep the sound in step with the picture:
the gap becomes silence instead of being left out (which put the voices ~15 s off after half an hour).

    python tests/test_source_gap.py         (no graphics card needed)
"""
import os
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from muteharam_pc import config  # noqa: E402
from muteharam_pc.engine import MediaSource, SAMPLE_RATE, CHANNELS  # noqa: E402

cfg = config.load()
tmp = tempfile.mkdtemp(prefix="mh_gap_")


def tone(freq, dur, offset, out):
    subprocess.run([cfg["ffmpeg"], "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}",
                    "-ac", "2", "-ar", "44100", "-c:a", "aac", "-output_ts_offset", str(offset), "-f", "mpegts", out], check=True)


a, b, gap = (os.path.join(tmp, n) for n in ("a.ts", "b.ts", "gap.ts"))
tone(440, 5, 0, a)          # 0 - 5 s
tone(880, 5, 8, b)          # 8 - 13 s: 3 s are missing in between
with open(gap, "wb") as f:
    f.write(open(a, "rb").read() + open(b, "rb").read())


def decode(src, start):
    p = src.decoder(start)
    raw = p.stdout.read()
    p.wait()
    return np.frombuffer(raw[: len(raw) // (4 * CHANNELS) * 4 * CHANNELS], np.float32).reshape(-1, CHANNELS)


def level(x, t0, t1):
    seg = x[int(t0 * SAMPLE_RATE):int(t1 * SAMPLE_RATE), 0]
    return float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0


src = MediaSource(gap, ffmpeg=cfg["ffmpeg"], ffprobe=cfg["ffprobe"])
x = decode(src, 0.0)
print(f"decoded {len(x) / SAMPLE_RATE:.2f} s; level 1-4 s {level(x, 1, 4):.3f}, gap 5.5-7.5 s {level(x, 5.5, 7.5):.4f}, "
      f"9-12 s {level(x, 9, 12):.3f}")
assert abs(len(x) / SAMPLE_RATE - 13.0) < 0.3, "the missing 3 s must be kept as time (silence), not dropped"
assert level(x, 5.5, 7.5) < 0.01 and level(x, 1, 4) > 0.03 and level(x, 9, 12) > 0.03

# starting in the middle (a skip) lands on the right spot as well
y = decode(src, 9.0)
print(f"from 9 s: decoded {len(y) / SAMPLE_RATE:.2f} s")
assert abs(len(y) / SAMPLE_RATE - 4.0) < 0.3
print("SOURCE GAP TEST OK")
