"""
The library queue (films cleaned in advance): order, start/stop, finished and stopped jobs. No graphics card needed.

    python tests/test_library.py
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from muteharam_pc.library import Library, Runner  # noqa: E402

root = tempfile.mkdtemp(prefix="mh_lib_")
lib = Library(root)
a = lib.add({"url": "https://example.com/a.m3u8", "title": "Film A"})
b = lib.add({"url": "https://example.com/b.m3u8", "title": "Film B"})
c = lib.add({"url": "https://example.com/c.m3u8", "title": "Film C"})
assert [x["title"] for x in lib.items()] == ["Film A", "Film B", "Film C"]
lib.move(c, -1)
lib.move(c, -1)
assert [x["title"] for x in lib.items()] == ["Film C", "Film A", "Film B"]
lib.remove(a)
assert [x["title"] for x in lib.items()] == ["Film C", "Film B"]

# a fake cleaning program: "quick" finishes at once, "slow" runs until it is stopped
FAKE = ("import json,os,sys,time;d=sys.argv[1];slow=sys.argv[2]=='slow';"
        "json.dump({'progress':0.5,'eta_s':9},open(os.path.join(d,'progress.json'),'w'));"
        "time.sleep(600 if slow else 0.5);"
        "open(os.path.join(d,'audio.opus'),'wb').write(b'x');"
        "json.dump({'done':True,'progress':1.0},open(os.path.join(d,'progress.json'),'w'))")


def command_for(item_id, meta):
    return [sys.executable, "-c", FAKE, lib.item_dir(item_id), "slow" if meta["title"] == "Film B" else "quick"]


run = Runner(lib, command_for, log=lambda *m: None)
time.sleep(2.5)
assert all(x["status"] == "queued" for x in lib.items()), "nothing may run before Start"

run.start()
t0 = time.time()
while time.time() - t0 < 30:
    st = {x["title"]: x["status"] for x in lib.items()}
    if st["Film C"] == "ready" and st["Film B"] == "processing":
        break
    time.sleep(0.5)
st = {x["title"]: x for x in lib.items()}
print("after start:", {k: v["status"] for k, v in st.items()})
assert st["Film C"]["status"] == "ready" and st["Film B"]["status"] == "processing"
assert abs(st["Film B"]["progress"] - 0.5) < 1e-6

run.stop()                                  # Stop: the job ends at once, it carries on later
time.sleep(2.5)
st = {x["title"]: x["status"] for x in lib.items()}
print("after stop:", st, run.state())
assert st["Film B"] == "paused" and not run.state()["running"] and run.proc is None
print("LIBRARY TEST OK")
