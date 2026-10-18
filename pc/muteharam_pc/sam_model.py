"""SAM Audio (Meta) as a cleaning engine: "remove the music" by text prompt.

The model needs its own Python environment (set up by `python setup_sam.py`), so it runs as a separate
program (sam_worker.py) that this module starts and talks to. Only one worker exists at a time and it is
shut down as soon as another cleaning mode takes over, to give the graphics card back.
"""
import atexit
import os
import struct
import subprocess
import threading

import numpy as np

from .model import PC_DIR

SAM_DIR = os.path.join(PC_DIR, "models", "sam-audio")
PYTHON_FILE = os.path.join(SAM_DIR, "python.txt")      # path of the python.exe of the SAM environment
WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sam_worker.py")
CH = 2

_lock = threading.Lock()
_client = None


def python_path():
    p = os.environ.get("MUTEHARAM_SAM_PYTHON")
    if not p and os.path.exists(PYTHON_FILE):
        with open(PYTHON_FILE, encoding="utf-8") as f:
            p = f.read().strip()
    return p if p and os.path.exists(p) else None


def is_available():
    return python_path() is not None


class SamClient:
    def __init__(self, log=print, ready_timeout=300.0):
        py = python_path()
        if not py:
            raise FileNotFoundError("SAM Audio is not set up. Run  python setup_sam.py  first.")
        env = dict(os.environ, HF_HUB_DISABLE_XET="1", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen([py, "-I", WORKER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, bufsize=0, env=env)
        self.err_tail = []
        self.dead = False
        threading.Thread(target=self._drain, daemon=True).start()
        self._ready = threading.Event()
        threading.Thread(target=self._wait_ready, daemon=True).start()
        log("SAM Audio: loading the model (about 30 s the first time, longer if it still has to download)...")
        if not self._ready.wait(ready_timeout) or self.dead:
            tail = " | ".join(self.err_tail[-3:])
            self.close()
            raise RuntimeError(f"SAM Audio did not start: {tail}")

    def _drain(self):
        for line in self.proc.stderr:
            self.err_tail = (self.err_tail + [line.decode("utf-8", "replace").strip()])[-8:]

    def _wait_ready(self):
        line = self.proc.stdout.readline()
        if line.strip() == b"READY":
            self._ready.set()
        else:
            self.dead = True
            self._ready.set()

    def _read_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                raise RuntimeError("SAM Audio stopped: " + " | ".join(self.err_tail[-3:]))
            buf += chunk
        return bytes(buf)

    def remove_music(self, x):
        """x: [n, 2] float32 at 44.1 kHz -> the same audio with the music taken out."""
        x = np.ascontiguousarray(x, dtype=np.float32)
        try:
            self.proc.stdin.write(struct.pack("<I", len(x)) + x.tobytes())
        except OSError as ex:
            raise RuntimeError("SAM Audio stopped: " + " | ".join(self.err_tail[-3:])) from ex
        return np.frombuffer(self._read_exact(len(x) * CH * 4), dtype=np.float32).reshape(-1, CH).copy()

    def close(self):
        p, self.proc = self.proc, None
        if p is None:
            return
        try:
            p.stdin.close()
        except Exception:
            pass
        try:
            p.kill()
        except Exception:
            pass


def get_client(log=print):
    global _client
    with _lock:
        if _client is None or _client.proc is None or _client.proc.poll() is not None:
            _client = SamClient(log)
        return _client


def shutdown():
    global _client
    with _lock:
        c, _client = _client, None
    if c is not None:
        c.close()


atexit.register(shutdown)
