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

# the quick check before cleaning reads a few seconds from anywhere in the film: always the full amount
for at in (0.0, 9.0):
    got = len(src.read_s16(at, 2.0)) / SAMPLE_RATE
    assert abs(got - 2.0) < 0.05, f"read_s16 from {at} s gave {got:.2f} s"

# a website that refuses minutes of pieces (Black Bird episode 5 after 29 min): the refused part must stay as time
# (silence), and a start INSIDE it must not be trusted (ffmpeg calls the first sound it gets "the start")
import functools  # noqa: E402
import http.server  # noqa: E402
import re  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

hls = os.path.join(tmp, "hls")
os.makedirs(hls)
subprocess.run([cfg["ffmpeg"], "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anoisesrc=d=60:c=pink:r=44100:a=0.3",
                "-c:a", "aac", "-ac", "2", "-hls_time", "6", "-hls_list_size", "0", "-f", "hls",
                os.path.join(hls, "s.m3u8")], check=True)
pieces = [float(x) for x in re.findall(r"#EXTINF:([0-9.]+)", open(os.path.join(hls, "s.m3u8")).read())]
starts = np.cumsum([0.0] + pieces)
gap0, gap1 = starts[3], starts[6]                      # pieces 3-5 refused


class Refusing(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        m = re.search(r"s(\d+)\.ts", self.path)
        if m and 3 <= int(m.group(1)) <= 5:
            return self.send_error(403)
        return super().do_GET()

    def log_message(self, *a):
        pass


web_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Refusing, directory=hls))
threading.Thread(target=web_srv.serve_forever, daemon=True).start()
web = MediaSource(f"http://127.0.0.1:{web_srv.server_address[1]}/s.m3u8", ffmpeg=cfg["ffmpeg"], ffprobe=cfg["ffprobe"])
full = decode(MediaSource(os.path.join(hls, "s.m3u8"), ffmpeg=cfg["ffmpeg"], ffprobe=cfg["ffprobe"]), 0.0)[:, 0]


def first_seconds(src, start):
    p = src.decoder(start)
    first = p.stdout.read(SAMPLE_RATE * 4 * CHANNELS)
    time.sleep(0.5)
    caught = p.skipped_early
    p.kill()
    return first, caught


v = decode(web, 0.0)[:, 0]
print(f"website refusing {gap0:.0f}-{gap1:.0f} s: decoded {len(v) / SAMPLE_RATE:.1f} s")
assert abs(len(v) / SAMPLE_RATE - 60.0) < 0.5, "the refused part must be kept as time, not closed up"
assert np.abs(v[int((gap0 + 1) * SAMPLE_RATE):int((gap1 - 1) * SAMPLE_RATE)]).max() < 1e-3
after = int((gap1 + 2) * SAMPLE_RATE)
assert np.allclose(v[after:after + 2000], full[after:after + 2000], atol=1e-3), \
    "the film after the refused part must stay at its own time"
assert first_seconds(web, (gap0 + gap1) / 2)[1], "a start inside the refused part must be caught"
assert not first_seconds(web, 1.0)[1]
web_srv.shutdown()
print("SOURCE GAP TEST OK")
