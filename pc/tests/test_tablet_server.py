"""
The LAN audio/session server the tablet app talks to (token, session.json, WAV with Range):

    python tests/test_tablet_server.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from muteharam_pc import server as srv  # noqa: E402


class FakeSession:
    total_frames = 44100 * 4
    duration = 4.0

    def log(self, *a):
        pass

    def read_frames(self, f0, f1):
        n = np.arange(f0, f1)
        return np.stack([(n % 1000).astype("<i2"), (-(n % 1000)).astype("<i2")], axis=1)


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, b"", {}


def main():
    doc = {"id": 1, "video": "https://example.com/v.m3u8", "kind": "hls"}
    s = srv.make_server(FakeSession(), host="127.0.0.1", port=0, token="tok",
                        routes={"/session.json": lambda: doc})
    base = f"http://127.0.0.1:{s.server_address[1]}"
    assert get(base + "/session.json")[0] == 401
    assert get(base + "/audio.wav?t=wrong")[0] == 401
    code, body, _ = get(base + "/session.json?t=tok")
    assert code == 200 and json.loads(body) == doc
    code, body, _ = get(base + "/session.json", {"X-MuteHaram-Token": "tok"})
    assert code == 200
    code, body, h = get(base + "/audio.wav?t=tok", {"Range": "bytes=0-43"})
    assert code == 206 and body[:4] == b"RIFF" and len(body) == 44, (code, len(body))
    # a range in the middle: frame 1000 onwards, two frames
    off = 44 + 1000 * 4
    code, body, _ = get(base + "/audio.wav?t=tok", {"Range": f"bytes={off}-{off + 7}"})
    got = np.frombuffer(body, dtype="<i2")
    assert code == 206 and list(got) == [0, 0, 1, -1], list(got)
    s.shutdown()
    print("TABLET SERVER TEST OK")


if __name__ == "__main__":
    main()
