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


def connect(token):
    s = socket.create_connection(("127.0.0.1", PORT), timeout=30)
    s.sendall(ss.MAGIC + bytes([len(token)]) + token.encode())
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
    srv.shutdown()
    print("STREAM SERVER TEST OK")


if __name__ == "__main__":
    main()
