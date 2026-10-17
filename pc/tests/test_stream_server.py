"""
End-to-end test of the Wi-Fi server on this computer (needs the model + a GPU or CPU):

    python tests/test_stream_server.py

Starts the server on a spare port, connects like the tablet does, checks the framing,
the token check, that the returned music matches running the model directly, and
reports the round-trip time for a 3 s window.
"""
import os
import socket
import struct
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from muteharam_pc import stream_server as ss  # noqa: E402

PORT = 47999
TOKEN = "t0k3n"


def connect(token, magic_out=None):
    s = socket.create_connection(("127.0.0.1", PORT), timeout=30)
    s.sendall((magic_out or ss.MAGIC) + bytes([len(token)]) + token.encode())
    magic = ss.read_exact(s, 4)
    status = ss.read_exact(s, 1)[0]
    return s, magic, status


def main():
    engine = ss.Engine()
    srv = ss.Server(("127.0.0.1", PORT), ss.make_handler(engine, TOKEN))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    engine.music(np.zeros(44100, dtype=np.float32))

    # wrong token is refused
    s, magic, status = connect("nope")
    assert magic == ss.MAGIC and status == 1, (magic, status)
    s.close()

    # right token, real window
    s, magic, status = connect(TOKEN)
    assert magic == ss.MAGIC and status == 0
    rng = np.random.default_rng(0)
    n = 44100 * 3
    t = np.arange(n) / 44100
    x = (0.1 * np.sin(2 * np.pi * 440 * t) + 0.05 * rng.standard_normal(n)).astype("<f4")
    for seq in (7, 8):
        t0 = time.time()
        s.sendall(struct.pack("<II", seq, n) + x.tobytes())
        rseq, rn = struct.unpack("<II", ss.read_exact(s, 8))
        y = np.frombuffer(ss.read_exact(s, rn * 4), dtype="<f4")
        dt = time.time() - t0
        assert rseq == seq and rn == n, (rseq, rn)
        print(f"seq {seq}: {n/44100:.0f} s window round trip {dt*1000:.0f} ms")
    ref = engine.music(x.copy())
    err = float(np.max(np.abs(y - ref)))
    print("max difference from running the model directly:", err)
    assert err < 1e-4

    # absurd length is answered with the error marker, connection stays usable
    s.sendall(struct.pack("<II", 9, 0))
    rseq, rn = struct.unpack("<II", ss.read_exact(s, 8))
    assert rseq == 9 and rn == ss.ERROR_N
    s.close()

    # ---- version 2: stereo, int16
    s, magic, status = connect("nope", ss.MAGIC2)
    assert magic == ss.MAGIC2 and status == 1
    s.close()
    s, magic, status = connect(TOKEN, ss.MAGIC2)
    assert magic == ss.MAGIC2 and status == 0
    n2 = 44100 * 3
    t2 = np.arange(n2) / 44100
    left = (0.1 * np.sin(2 * np.pi * 440 * t2) + 0.05 * rng.standard_normal(n2)).astype("float32")
    right = (0.08 * np.sin(2 * np.pi * 660 * t2) + 0.05 * rng.standard_normal(n2)).astype("float32")
    pcm = np.stack([left, right])
    q = np.round(np.clip(pcm, -1, 1) * 32767).astype("<i2")
    for seq in (3, 4):
        t0 = time.time()
        s.sendall(struct.pack("<III", seq, n2, 2) + q.tobytes())
        rseq, rn, rch = struct.unpack("<III", ss.read_exact(s, 12))
        assert (rseq, rn, rch) == (seq, n2, 2), (rseq, rn, rch)
        est = np.frombuffer(ss.read_exact(s, rn * rch * 2), dtype="<i2").reshape(rch, rn)
        print(f"v2 seq {seq}: stereo {n2/44100:.0f} s window round trip {(time.time()-t0)*1000:.0f} ms")
    ref2 = np.stack([engine.music(q[0].astype("float32") / 32768.0), engine.music(q[1].astype("float32") / 32768.0)])
    err2 = float(np.max(np.abs(est.astype("float32") / 32767.0 - ref2)))
    print("v2: max difference from running each channel through the model directly:", err2)
    assert err2 < 5e-3, err2
    # bad channel count -> error marker, connection stays usable
    s.sendall(struct.pack("<III", 5, 100, 3) + bytes(100 * 3 * 2))
    rseq, rn, rch = struct.unpack("<III", ss.read_exact(s, 12))
    assert rseq == 5 and rn == ss.ERROR_N
    s.sendall(struct.pack("<III", 6, n2, 1) + q[0].tobytes())
    rseq, rn, rch = struct.unpack("<III", ss.read_exact(s, 12))
    assert (rseq, rn, rch) == (6, n2, 1)
    ss.read_exact(s, rn * 2)
    s.close()
    srv.shutdown()
    print("STREAM SERVER TEST OK")


if __name__ == "__main__":
    main()
