"""
Live server test (what the browser extension talks to):

    python tests/test_live_server.py

1. bypass mode: the returned blocks must be the input, exactly, at the right positions
   (this checks the indexing / hop / margin logic).
2. real model: blocks arrive, are finite, and differ from the input.
3. reset: after a reset the stream starts again at block 0.
"""
import asyncio
import json
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from muteharam_pc import live_server as ls  # noqa: E402

try:
    from websockets.asyncio.client import connect
except ImportError:
    from websockets import connect

RATE = 44100
TOKEN = "livetest"


async def run_client(port, signal, gen_script):
    """Stream `signal` ([n,2] float32) in 2048-frame chunks; return {block: int16 array}."""
    got = {}
    async with connect(f"ws://127.0.0.1:{port}", max_size=1 << 23) as ws:
        await ws.send(json.dumps({"type": "hello", "token": TOKEN, "window_ms": 3000, "margin_ms": 500}))
        ok = json.loads(await ws.recv())
        assert ok["type"] == "hello_ok" and ok["rate"] == RATE
        hop = ok["hop"]
        gen = 0
        n_chunks = 0
        pos = 0
        script = list(gen_script)
        while pos < len(signal):
            if script and pos >= script[0]:       # simulate a seek: reset
                script.pop(0)
                gen += 1
                await ws.send(json.dumps({"type": "reset", "gen": gen}))
            chunk = signal[pos:pos + 2048]
            await ws.send(b"\x01" + struct.pack("<I", gen) + chunk.astype("<f4").tobytes())
            pos += len(chunk)
            n_chunks += 1
            # drain whatever the server has produced so far
            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), 0.001)
                except asyncio.TimeoutError:
                    break
                if isinstance(msg, bytes) and msg[0] == 2:
                    g, k, n = struct.unpack_from("<III", msg, 1)
                    got[(g, k)] = np.frombuffer(msg, dtype="<i2", offset=13).reshape(-1, 2)
        # wait for the stragglers
        end = asyncio.get_event_loop().time() + 20
        while asyncio.get_event_loop().time() < end:
            try:
                msg = await asyncio.wait_for(ws.recv(), 1.0)
            except asyncio.TimeoutError:
                break
            if isinstance(msg, bytes) and msg[0] == 2:
                g, k, n = struct.unpack_from("<III", msg, 1)
                got[(g, k)] = np.frombuffer(msg, dtype="<i2", offset=13).reshape(-1, 2)
    return hop, got


async def main():
    rng = np.random.default_rng(3)
    n = RATE * 14
    t = np.arange(n) / RATE
    signal = np.stack([0.2 * np.sin(2 * np.pi * 330 * t) + 0.02 * rng.standard_normal(n),
                       0.2 * np.sin(2 * np.pi * 523 * t) + 0.02 * rng.standard_normal(n)], axis=1).astype(np.float32)

    # ---- 1. bypass: exact positions
    cleaner = ls.Cleaner(bypass=True)
    task = asyncio.create_task(ls.serve_forever(cleaner, TOKEN, 47998, log=lambda m: None))
    await asyncio.sleep(1.0)
    hop, got = await run_client(47998, signal, [])
    blocks = sorted(k for (g, k) in got if g == 0)
    print(f"bypass: {len(blocks)} blocks of {hop / RATE:.2f}s, first {blocks[0]}, last {blocks[-1]}")
    assert blocks == list(range(len(blocks))) and len(blocks) >= 5
    worst = 0
    for k in blocks:
        want = np.round(np.clip(signal[k * hop:(k + 1) * hop], -1, 1) * 32767).astype(np.int16)
        have = got[(0, k)]
        assert len(have) == hop
        worst = max(worst, int(np.abs(have.astype(np.int32) - want.astype(np.int32)).max()))
    print("bypass: largest difference from the input (int16 steps):", worst)
    assert worst <= 1, "blocks are not at the right positions"
    task.cancel()
    await asyncio.sleep(0.2)

    # ---- 2. real model + 3. reset
    cleaner = ls.Cleaner()
    task = asyncio.create_task(ls.serve_forever(cleaner, TOKEN, 47997, log=lambda m: None))
    await asyncio.sleep(1.0)
    hop, got = await run_client(47997, signal, [RATE * 9])
    g0 = sorted(k for (g, k) in got if g == 0)
    g1 = sorted(k for (g, k) in got if g == 1)
    print(f"real model: generation 0 blocks {g0}, after the reset (generation 1) blocks {g1}")
    assert g0 and g1 and g1[0] == 0, "after a reset the stream must restart at block 0"
    assert all(np.isfinite(got[(0, k)]).all() for k in g0)
    diff = np.abs(got[(0, g0[1])].astype(np.int32)
                  - np.round(signal[g0[1] * hop:(g0[1] + 1) * hop] * 32767).astype(np.int32)).max()
    print("real model: the AI changed the audio by up to", int(diff), "steps")
    assert diff > 50
    task.cancel()
    print("LIVE SERVER TEST OK")


if __name__ == "__main__":
    asyncio.run(main())
