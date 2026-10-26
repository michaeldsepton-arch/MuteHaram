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
    if st["Film C"] == "ready" and st["Film B"] == "processing" and lib.progress(b).get("progress"):
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
# pause ONE film while it is being cleaned: it is held (skipped), the rest of the queue would carry on
run.start()
t0 = time.time()
while time.time() - t0 < 20 and {x["title"]: x["status"] for x in lib.items()}["Film B"] != "processing":
    time.sleep(0.5)
run.hold(next(x["id"] for x in lib.items() if x["title"] == "Film B"))
time.sleep(3)
st = {x["title"]: x["status"] for x in lib.items()}
print("after pausing Film B:", st)
assert st["Film B"] == "held" and run.proc is None
run.stop()

# watched films: space shown, "delete all watched" frees it and keeps the rest
import os as _os
cid = next(x["id"] for x in lib.items() if x["title"] == "Film C")
open(_os.path.join(lib.item_dir(cid), "audio.opus"), "wb").write(b"x" * 2_000_000)
assert lib.space() == (2.0, 0.0), lib.space()
lib.update(cid, watched_at=time.time())
assert lib.space() == (2.0, 2.0)
assert lib.delete_watched() == 1 and lib.space() == (0, 0)
assert [x["title"] for x in lib.items()] == ["Film B"]
# giving way to other programs on the graphics card (fake card readings, no waiting)
from muteharam_pc.library import GpuGovernor
clock = [0.0]
slept = []


def fake_sleep(s):
    slept.append(s)
    clock[0] += s


readings = {"now": (5.0, 1000.0)}            # (utilization %, memory MB) the card reports
g = GpuGovernor(log=lambda *m: None, sample=lambda: readings["now"], sleep=fake_sleep, clock=lambda: clock[0])
g.before_load()                              # desktop: 1000 MB
readings["now"] = (5.0, 7400.0)
g.after_first_block()                        # our model: 6400 MB
assert g.ours == 6400.0
stages = []
released = []
g.gate(5.0, lambda: released.append(1), stages.append)
assert g.state == "full" and sum(slept) < 2 and not released, "a quiet card: full speed"
slept.clear()
readings["now"] = (100.0, 7900.0)            # a video playing in the browser: busy card, little memory
g.gate(5.0, lambda: released.append(1), stages.append)
assert g.state == "light" and 5.0 in slept and not released, "a busy card alone never makes it wait, only slower"
# a heavy program starts (4 GB of its own), and stops again after a minute
script = iter([(90.0, 11400.0)] * 3 + [(95.0, 5000.0)] * 9 + [(3.0, 1000.0)] * 30)
g.sample = lambda: next(script)
g.gate(5.0, lambda: released.append(1) or True, stages.append)
print("gpu governor:", stages, "released", len(released))
assert released == [1] and g.state == "full" and g.changed
assert any(s.startswith("waiting") for s in stages) and stages[-1] == "cleaning"
# the standard model can't be freed: while waiting it must still count its own memory as its own (never wait forever)
script = iter([(90.0, 11400.0)] * 3 + [(95.0, 11400.0)] * 9 + [(3.0, 7400.0)] * 30)
g.sample = lambda: next(script)
stages.clear()
clock[0] = 0.0
g.gate(5.0, lambda: False, stages.append)
assert g.state == "full" and stages[-1] == "cleaning" and clock[0] < 200, (stages, clock[0])
print("LIBRARY TEST OK")
