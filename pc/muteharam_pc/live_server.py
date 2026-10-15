"""
Live audio cleaning for the browser extension ("remove music inside this page").

    python -m muteharam_pc.live_server        (the helper starts it automatically when needed)

The extension sends the sound a page is playing, as it plays, over a WebSocket on this
computer. This server cuts it into overlapping windows, removes the music on the GPU and
sends back the cleaned centre of every window ("block"). The browser plays the cleaned
audio a fixed time later and holds the picture back by the same amount.

Protocol (one WebSocket per video; integers little-endian):
  text  client -> server  {"type":"hello","token":"...","window_ms":3000,"margin_ms":500}
  text  server -> client  {"type":"hello_ok","rate":44100,"hop":N,"margin":N}   (frames)
  text  client -> server  {"type":"reset","gen":G}          start over (seek / new video)
  bin   client -> server  0x01 | u32 gen | float32 L R L R ...      44.1 kHz stereo
  bin   server -> client  0x02 | u32 gen | u32 block | u32 frames | int16 L R L R ...
Block k holds input frames [k*hop, (k+1)*hop) of the current generation.
"""
import asyncio
import json
import os
import secrets
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import torch

from . import config
from .model import SAMPLE_RATE, load_network
from .stream_server import load_or_create_token

PORT = 47802
CH = 2


class Cleaner:
    """The AI part, shared by all connections (the GPU works on one window at a time)."""

    def __init__(self, mode="music", gain=1.0, device=None, models_dir=None, bypass=False):
        self.mode, self.gain, self.bypass = mode, gain, bypass
        self.net, self.device, self.target = load_network("music" if mode == "music" else "dialog",
                                                          models_dir, device)
        self.stereo = self.device.type == "cuda"
        import threading
        self.lock = threading.Lock()

    @torch.no_grad()
    def clean(self, x, margin, hop):
        """x: [win, 2] float32 -> cleaned centre [hop, 2] int16."""
        c = slice(margin, margin + hop)
        xc = x[c]
        if self.bypass:
            y = xc
        else:
            with self.lock:
                if self.stereo:
                    t = torch.from_numpy(np.ascontiguousarray(x.T)).to(self.device)[:, None, :]
                    est = self.net(t)[:, self.target, :].float().cpu().numpy().T[c]
                else:
                    mono = torch.from_numpy(x.mean(axis=1)).to(self.device)[None, None]
                    est = self.net(mono)[0, self.target].float().cpu().numpy()[c, None]
            removal = est if self.mode == "music" else xc - est
            y = xc - self.gain * removal
        return (np.clip(y, -1.0, 1.0) * 32767.0).astype(np.int16)


class LiveSession:
    """Turns a continuous stereo stream into cleaned blocks (one hop each)."""

    def __init__(self, cleaner, window_ms, margin_ms):
        self.cleaner = cleaner
        self.M = int(round(margin_ms * SAMPLE_RATE / 1000))
        self.W = int(round(window_ms * SAMPLE_RATE / 1000))
        self.H = self.W - 2 * self.M
        if self.H < SAMPLE_RATE // 4:
            raise ValueError("window too short for this margin")
        self.reset()

    def reset(self):
        self.buf = np.zeros((0, CH), np.float32)
        self.base = 0            # absolute frame index of buf[0]
        self.count = 0           # frames received
        self.k = 0               # next block to produce

    def push(self, samples):
        self.buf = np.concatenate([self.buf, samples])
        self.count += len(samples)

    def ready(self):
        return self.count >= (self.k + 1) * self.H + self.M

    def process_next(self):
        k, s = self.k, self.k * self.H - self.M
        x = np.zeros((self.W, CH), np.float32)
        lo, hi = max(s, self.base), min(s + self.W, self.base + len(self.buf))
        if hi > lo:
            x[lo - s:hi - s] = self.buf[lo - self.base:hi - self.base]
        y = self.cleaner.clean(x, self.M, self.H)
        self.k += 1
        keep_from = self.k * self.H - self.M          # the next window starts here
        if keep_from > self.base:
            cut = min(keep_from - self.base, len(self.buf))
            self.buf = self.buf[cut:]
            self.base += cut
        return k, y


async def handle(ws, cleaner, token, log=print):
    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(max_workers=1)
    sess = None
    gen = 0
    try:
        log("live: a page connected, waiting for its hello")
        hello = json.loads(await asyncio.wait_for(ws.recv(), 15))
        if hello.get("type") != "hello":
            log(f"live: first message was not a hello: {str(hello)[:80]}")
            await ws.close(4002, "no hello")
            return
        if token and not secrets.compare_digest(str(hello.get("token", "")), token):
            log("live: refused, the page's token does not match pc/token.txt (reload the extension and try again)")
            await ws.close(4003, "bad token")
            return
        sess = LiveSession(cleaner, float(hello.get("window_ms", 3000)), float(hello.get("margin_ms", 500)))
        await ws.send(json.dumps({"type": "hello_ok", "rate": SAMPLE_RATE, "hop": sess.H, "margin": sess.M}))
        log(f"live: client connected, window {sess.W / SAMPLE_RATE:.1f}s hop {sess.H / SAMPLE_RATE:.1f}s")
        async for msg in ws:
            if isinstance(msg, str):
                m = json.loads(msg)
                if m.get("type") == "reset":
                    gen = int(m.get("gen", gen + 1))
                    sess.reset()
                continue
            if len(msg) < 5 or msg[0] != 1:
                continue
            (g,) = struct.unpack_from("<I", msg, 1)
            if g != gen:
                continue                      # stale audio from before a reset
            sess.push(np.frombuffer(msg, dtype="<f4", offset=5).reshape(-1, CH))
            while sess.ready():
                my_gen = gen
                t0 = time.time()
                k, y = await loop.run_in_executor(pool, sess.process_next)
                if my_gen != gen:             # a reset happened while we were busy
                    break
                await ws.send(b"\x02" + struct.pack("<III", gen, k, len(y)) + y.astype("<i2").tobytes())
                if k % 15 == 0:
                    log(f"live: block {k} took {(time.time() - t0) * 1000:.0f} ms for {len(y) / SAMPLE_RATE:.1f}s")
    except Exception as ex:
        if type(ex).__name__ not in ("ConnectionClosedOK", "ConnectionClosedError", "ConnectionClosed"):
            log(f"live: connection ended: {ex!r}")
    finally:
        pool.shutdown(wait=False)
        log("live: client disconnected")


async def serve_forever(cleaner, token, port=PORT, host="127.0.0.1", log=print):
    try:
        from websockets.asyncio.server import serve
    except ImportError:                       # websockets < 14
        from websockets import serve
    async with serve(lambda ws: handle(ws, cleaner, token, log), host, port, max_size=1 << 23):
        log(f"live server ready on ws://{host}:{port}")
        await asyncio.Future()


def main():
    import argparse
    S = config.settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--mode", default=S["mode"], choices=["music", "speech"])
    ap.add_argument("--gain", type=float, default=S["gain"])
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--no-token", action="store_true")
    ap.add_argument("--bypass", action="store_true", help="test only: pass the audio through unchanged")
    a = ap.parse_args()
    try:
        models_dir = json.load(open(config.CONFIG_PATH)).get("models_dir")
    except Exception:
        models_dir = None
    cleaner = Cleaner(a.mode, a.gain, "cpu" if a.cpu else None, models_dir,
                      bypass=a.bypass or os.environ.get("MUTEHARAM_LIVE_BYPASS") == "1")
    token = "" if a.no_token else load_or_create_token()
    print(f"live cleaner on {cleaner.device} (mode {a.mode})", flush=True)
    try:
        asyncio.run(serve_forever(cleaner, token, a.port, log=lambda m: print(m, flush=True)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
