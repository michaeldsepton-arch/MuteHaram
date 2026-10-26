"""MuteHaram Library: films and videos queued from the browser extension, cleaned IN ADVANCE (when you press Start),
and watched later on the PC or the tablet without any waiting and without using the graphics card.

Only the cleaned SOUND is kept (about 70 MB per hour of film); the picture is streamed from the website when
you watch, exactly as before.

pc/library/<id>/meta.json       what was queued (written by the helper)
pc/library/<id>/progress.json   how far the cleaning is (written by the cleaning program, play.py --preclean)
pc/library/<id>/cleaned.pcm     the cleaned sound so far (while it is being made; lets a stopped job carry on)
pc/library/<id>/audio.opus      the finished cleaned sound
"""
import json
import os
import shutil
import subprocess
import threading
import time

import numpy as np

from . import config

LIB_DIR = os.path.join(config.PC_DIR, "library")
SAMPLE_RATE = 44100
CHANNELS = 2


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)          # never a half-written file, even if the program is stopped right then


class Library:
    def __init__(self, root=LIB_DIR):
        self.root = root
        self.lock = threading.RLock()
        os.makedirs(root, exist_ok=True)

    def item_dir(self, item_id):
        return os.path.join(self.root, str(item_id))

    def meta(self, item_id):
        return _read_json(os.path.join(self.item_dir(item_id), "meta.json"))

    def progress(self, item_id):
        return _read_json(os.path.join(self.item_dir(item_id), "progress.json"), {}) or {}

    def ids(self):
        out = []
        for name in os.listdir(self.root):
            if name.isdigit() and os.path.exists(os.path.join(self.root, name, "meta.json")):
                out.append(name)
        return out

    def items(self):
        """Everything, in queue order, with what the cleaning program reported."""
        with self.lock:
            res = []
            for i in self.ids():
                m = self.meta(i)
                if not m:
                    continue
                p = self.progress(i)
                ready = bool(p.get("done")) and os.path.exists(os.path.join(self.item_dir(i), "audio.opus"))
                status = "ready" if ready else m.get("status", "queued")
                res.append(dict(id=i, title=m.get("title") or m.get("url", "")[:80], page=m.get("page", ""),
                                added=m.get("added", 0), order=m.get("order", 0), status=status,
                                progress=1.0 if ready else float(p.get("progress", 0.0)),
                                eta_s=p.get("eta_s"), duration=p.get("duration") or m.get("duration"),
                                error=m.get("error") or p.get("error") or "", stage=p.get("stage", ""),
                                unclean_s=p.get("unclean_s", 0), mode=p.get("mode", ""),
                                watched_at=m.get("watched_at"),
                                size_mb=round(os.path.getsize(os.path.join(self.item_dir(i), "audio.opus")) / 1e6, 1)
                                if ready else None))
            res.sort(key=lambda x: (x["order"], x["added"]))
            return res

    def add(self, req, title=None):
        with self.lock:
            item_id = str(int(time.time() * 1000))
            while os.path.exists(self.item_dir(item_id)):
                item_id = str(int(item_id) + 1)
            os.makedirs(self.item_dir(item_id))
            orders = [x["order"] for x in self.items()]
            meta = dict(id=item_id, title=title or req.get("title") or "", url=req.get("url", ""),
                        page=req.get("page") or req.get("url", ""), req=req, added=time.time(),
                        order=(max(orders) + 1) if orders else 1, status="queued")
            write_json(os.path.join(self.item_dir(item_id), "meta.json"), meta)
            return item_id

    def space(self):
        """Bytes of cleaned sound kept on this PC: (all, of films already watched)."""
        total = watched = 0
        for x in self.items():
            if x["status"] == "ready" and x["size_mb"]:
                total += x["size_mb"]
                if x["watched_at"]:
                    watched += x["size_mb"]
        return round(total, 1), round(watched, 1)

    def delete_watched(self):
        n = 0
        for x in self.items():
            if x["status"] == "ready" and x["watched_at"]:
                self.remove(x["id"])
                n += 1
        return n

    def update(self, item_id, **fields):
        with self.lock:
            m = self.meta(item_id)
            if m is None:
                return None
            m.update(fields)
            write_json(os.path.join(self.item_dir(item_id), "meta.json"), m)
            return m

    def move(self, item_id, delta):
        """Up (-1) or down (+1) in the queue."""
        with self.lock:
            items = [x for x in self.items()]
            idx = next((k for k, x in enumerate(items) if x["id"] == str(item_id)), None)
            if idx is None:
                return
            j = max(0, min(len(items) - 1, idx + delta))
            items.insert(j, items.pop(idx))
            for k, x in enumerate(items):
                self.update(x["id"], order=k + 1)

    def remove(self, item_id):
        with self.lock:
            d = self.item_dir(item_id)
            if os.path.isdir(d) and os.path.basename(d).isdigit():
                shutil.rmtree(d, ignore_errors=True)


class Runner:
    """Cleans the queue one item at a time, ONLY while switched on (the Start button). Stop ends the current job
    at once (the graphics card is free again); it carries on from where it was the next time."""

    def __init__(self, library, command_for, log=print):
        self.lib = library
        self.command_for = command_for          # command_for(item_id, meta) -> the command line to clean it
        self.log = log
        self.running = False
        self.proc = None
        self.current = None
        self._stopped_by_us = False
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, name="library-runner", daemon=True).start()

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        self._kill()

    def cancel(self, item_id):
        """Stop working on this item (it is being removed)."""
        if self.current == str(item_id):
            self._kill()

    def _kill(self):
        with self._lock:
            p = self.proc
            self._stopped_by_us = True
        if p is not None and p.poll() is None:
            try:
                p.terminate()
                p.wait(15)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
            if os.name == "nt":                  # anything it started must go too
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)

    def state(self):
        return dict(running=self.running, current=self.current)

    def _next(self):
        for x in self.lib.items():                    # "held" = paused for this one film only: skipped
            if x["status"] in ("queued", "processing", "paused"):
                return x
        return None

    def hold(self, item_id):
        """Pause one film: it keeps what is done and is skipped until resumed (the rest of the queue carries on)."""
        self.lib.update(str(item_id), status="held")
        if self.current == str(item_id):
            self._kill()

    def _loop(self):
        while True:
            time.sleep(1.0)
            try:
                if self.proc is not None:
                    if self.proc.poll() is None:
                        continue
                    self._finished()
                if not self.running:
                    continue
                item = self._next()
                if item is None:
                    self.running = False          # everything is cleaned
                    self.log("library: everything in the queue is cleaned")
                    continue
                meta = self.lib.meta(item["id"])
                cmd = self.command_for(item["id"], meta)
                os.makedirs(os.path.join(config.PC_DIR, "logs"), exist_ok=True)
                logf = open(os.path.join(config.PC_DIR, "logs", time.strftime("preclean-%Y%m%d-%H%M%S.log")), "w")
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                with self._lock:
                    self._stopped_by_us = False
                    self.current = item["id"]
                    self.lib.update(item["id"], status="processing", error="")
                    self.proc = subprocess.Popen(cmd, cwd=config.PC_DIR, stdout=logf, stderr=subprocess.STDOUT,
                                                 creationflags=flags)
                self.log(f"library: cleaning {item['title'][:60]!r}")
            except Exception as ex:
                self.log(f"library: {ex!r}")
                time.sleep(5)

    def _finished(self):
        item_id, p = self.current, self.proc
        self.proc, self.current = None, None
        if item_id is None or self.lib.meta(item_id) is None:
            return                                      # removed meanwhile
        prog = self.lib.progress(item_id)
        if prog.get("done"):
            self.lib.update(item_id, status="ready", error="")
            self.log(f"library: {item_id} is ready")
        elif self._stopped_by_us:
            if (self.lib.meta(item_id) or {}).get("status") != "held":
                self.lib.update(item_id, status="paused")
        else:
            self.lib.update(item_id, status="error",
                            error=prog.get("error") or f"the cleaning program stopped (code {p.returncode}); see pc/logs")
            self.log(f"library: {item_id} failed")


class FileSession:
    """A finished, pre-cleaned sound file served like a live cleaning session (same player code, no GPU)."""
    mode = "precleaned"

    def __init__(self, path, total_frames, ffmpeg, log=print):
        import tempfile
        self.path = path
        self.log = log
        self.total_frames = int(total_frames)
        self.duration = self.total_frames / SAMPLE_RATE
        self.hop = SAMPLE_RATE * 12
        self.want = 0
        self._tmp = tempfile.NamedTemporaryFile(prefix="muteharam_lib_", suffix=".pcm", delete=False)
        self._tmp.close()
        self.store = np.memmap(self._tmp.name, dtype=np.int16, mode="w+", shape=(max(1, self.total_frames), CHANNELS))
        self.decoded = 0
        self.eof = False
        self.cv = threading.Condition()
        self._closed = False
        self._readers = 0
        self.proc = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", path,
                                      "-f", "s16le", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "pipe:1"],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        threading.Thread(target=self._decode, name="library-decode", daemon=True).start()

    def _decode(self):
        pos = 0
        rest = b""
        try:
            while not self._closed:
                raw = self.proc.stdout.read(SAMPLE_RATE * 4 * 10)
                if not raw:
                    break
                raw = rest + raw
                n = len(raw) // 4
                rest = raw[n * 4:]
                a = np.frombuffer(raw[:n * 4], dtype=np.int16).reshape(-1, CHANNELS)
                a = a[: max(0, self.total_frames - pos)]
                if len(a):
                    self.store[pos:pos + len(a)] = a
                    pos += len(a)
                with self.cv:
                    self.decoded = pos
                    self.cv.notify_all()
        finally:
            with self.cv:
                self.eof = True
                self.cv.notify_all()

    def read_frames(self, f0, f1):
        f1 = min(f1, self.total_frames)
        with self.cv:
            self._readers += 1
            try:
                t0 = time.time()
                while self.decoded < f1 and not self.eof and not self._closed and time.time() - t0 < 120:
                    self.want = f0 // self.hop
                    self.cv.wait(0.5)
                out = np.zeros((max(0, f1 - f0), CHANNELS), np.int16)
                hi = min(f1, self.decoded)
                if hi > f0 and not self._closed:
                    out[: hi - f0] = self.store[f0:hi]
                return out
            finally:
                self._readers -= 1
                self.cv.notify_all()

    def prepare(self, timeout=150.0, at_frame=None):
        return True

    def want_position(self, mode):
        return 0

    def remake(self, mode, start_block=0):
        raise RuntimeError("this film was cleaned in advance; its sound cannot be switched to another mode")

    def stop_work(self):
        pass

    def close(self):
        with self.cv:
            self._closed = True
            self.cv.notify_all()
            t0 = time.time()
            while self._readers and time.time() - t0 < 30:
                self.cv.wait(0.5)
        try:
            self.proc.kill()
        except Exception:
            pass
        try:
            self.store._mmap.close()
            os.unlink(self._tmp.name)
        except Exception:
            pass


def gpu_sample():
    """(utilization %, memory used MB) of the graphics card, or None if it can't be read."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0).stdout
        u, m = out.strip().splitlines()[0].split(",")
        return float(u), float(m)
    except Exception:
        return None


class GpuGovernor:
    """Lets the library cleaning give way to other programs on the graphics card.

    Between blocks the cleaning is idle for a moment, so what the card does then is OTHER programs' work.
    Windows can't say which program uses how much memory, so ours is measured: the memory in use before our
    model is loaded vs. after its first block.
      another program holding a lot of graphics memory (AI work, games)  -> wait (free our memory if we can)
      the card very busy (but little memory: a video playing ...)       -> keep going, with a rest after each block
      otherwise                                                         -> full speed
    Usage alone never makes it wait: playing a video in the browser can show 100% for moments.
    """
    LIGHT_UTIL = 60.0                          # % of the card busy with others' work -> half speed
    OTHERS_MEM_MB = 2500.0                     # others holding more than this (desktop + browser use about 1 GB)
    QUIET_CHECKS = 3                           # this many quiet checks in a row (10 s apart) before carrying on
    FREE_AFTER_S = 20.0                        # still busy after this long: free our graphics memory

    def __init__(self, log=print, sample=gpu_sample, sleep=time.sleep, clock=time.time):
        self.log, self.sample, self.sleep, self.clock = log, sample, sleep, clock
        self.base = None                       # memory in use before our model was loaded
        self.ours = 0.0                        # memory our model holds
        self.state = "full"

    def before_load(self):
        s = self.sample()
        self.base = s[1] if s else None

    def after_first_block(self):
        s = self.sample()
        if s and self.base is not None:
            self.ours = max(0.0, s[1] - self.base)

    def others(self, loaded=True):
        """(others' utilization, others' memory MB), measured while we are idle."""
        vals = []
        for _ in range(3):
            s = self.sample()
            if s:
                vals.append(s)
            self.sleep(0.3)
        if not vals:
            return 0.0, 0.0
        util = sorted(v[0] for v in vals)[len(vals) // 2]
        mem = max(v[1] for v in vals) - (self.ours if loaded else 0.0)
        return util, max(0.0, mem)

    def heavy(self, util, mem):
        return mem >= self.OTHERS_MEM_MB

    def gate(self, block_seconds, release, set_stage):
        """Call between blocks. Returns when the next block may run (maybe after a rest or a long wait).
        release() frees our graphics memory (the model reloads by itself when the next block needs it)."""
        before = self.state
        try:
            self._gate(block_seconds, release, set_stage)
        finally:
            self.changed = self.state != before or self.waited

    changed = False
    waited = False

    def _gate(self, block_seconds, release, set_stage):
        self.waited = False
        util, mem = self.others(loaded=True)
        if not self.heavy(util, mem):
            if util >= self.LIGHT_UTIL:
                if self.state != "light":
                    self.log(f"gpu: another program uses the card a little ({util:.0f}%): cleaning at half speed")
                self.state = "light"
                set_stage("sharing the graphics card with another program (slower)")
                self.sleep(block_seconds)
            else:
                if self.state != "full":
                    self.log("gpu: the card is free again: full speed")
                    set_stage("cleaning")
                self.state = "full"
            return
        self.log(f"gpu: another program needs the card (use {util:.0f}%, memory {mem:.0f} MB): cleaning waits")
        self.state = "waiting"
        self.waited = True
        set_stage("waiting: another program is using the graphics card")
        t0, freed, tried, quiet = self.clock(), False, False, 0
        while True:
            self.sleep(10.0)
            util, mem = self.others(loaded=not freed)
            if not freed and not tried and self.clock() - t0 >= self.FREE_AFTER_S:
                tried = True
                # release() says whether it really freed anything (the standard model is small and stays loaded;
                # counting its memory as "another program" again would make the cleaning wait forever)
                freed = bool(release())
                if freed:
                    self.log("gpu: freed MuteHaram's graphics memory for the other program")
                    continue
            quiet = quiet + 1 if not self.heavy(util, mem) else 0
            if quiet >= self.QUIET_CHECKS:
                self.log("gpu: the other program is done: cleaning carries on")
                set_stage("cleaning")
                self.state = "full"
                return
