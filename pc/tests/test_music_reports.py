"""'Music here' reports: the tablet route notes a moment, the PC measures it in the background and adds one line
(numbers only) to the reports file. Uses a generated tone, a real cleaning session and the real music detector."""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from muteharam_pc import config, music_reports  # noqa: E402
from muteharam_pc.engine import MediaSource, Session, SessionHub  # noqa: E402
from muteharam_pc.play import TabletFront  # noqa: E402

cfg = config.load()
tmp = tempfile.mkdtemp(prefix="mh_test_")
wav = os.path.join(tmp, "tone.wav")
subprocess.run([cfg["ffmpeg"], "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
                "-ac", "2", "-ar", "44100", wav], check=True)
music_reports.PATH = os.path.join(tmp, "music-reports.jsonl")       # never the real reports file

sess = Session(MediaSource(wav, ffmpeg=cfg["ffmpeg"], ffprobe=cfg["ffprobe"]), log=lambda *a: None, level="normal")
assert sess.prepare(at_frame=0, timeout=120)
hub = SessionHub(sess, None)
front = TabletFront(lambda *a: None)
assert front.music_route({"pos": ["5000"]})["ok"] is False          # nothing playing yet
front.ready(hub, {"id": int(time.time() * 1000), "title": "Test film"})
front.resume_key = "test|30"
assert front.music_route({"pos": ["x"]})["ok"] is False
r = front.music_route({"pos": ["10000"]})
assert r["ok"], r
for _ in range(120):
    if os.path.exists(music_reports.PATH):
        break
    time.sleep(0.5)
rows = [json.loads(l) for l in open(music_reports.PATH, encoding="utf-8")]
assert len(rows) == 1, rows
e = rows[0]
assert e["film"] == {"title": "Test film", "key": "test|30"} and e["at"] == "0:00:10" and e["level"] == "normal", e
assert len(e["parts"]) >= 2, e
p = e["parts"][0]
assert p["cleaned_by"] == {"level": "normal", "used": "normal"}, p
assert "music" in p["played"] and "music" in p["original"] and "loudness_db" in p["original"], p
snd = e["sound"]
import soundfile as sf
for k in ("original", "played"):
    f = os.path.join(tmp, snd[k])
    data, sr = sf.read(f)
    assert sr == 44100 and data.shape[1] == 2 and len(data) >= 12 * 44100, (k, data.shape)
print("report:", json.dumps(e)[:300])
sess.close()
print("MUSIC REPORTS TEST OK")
