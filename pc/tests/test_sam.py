"""
SAM Audio cleaning engine (skipped when it is not set up: python setup_sam.py):

    python tests/test_sam.py
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from muteharam_pc import sam_model as sm  # noqa: E402


def main():
    if not sm.is_available():
        print("SAM TEST SKIPPED (not set up: python setup_sam.py)")
        return
    client = sm.get_client()
    rng = np.random.default_rng(1)
    n = 44100 * 8
    t = np.arange(n) / 44100
    x = np.stack([0.1 * np.sin(2 * np.pi * 220 * t) + 0.02 * rng.standard_normal(n),
                  0.1 * np.sin(2 * np.pi * 330 * t) + 0.02 * rng.standard_normal(n)], axis=1).astype(np.float32)
    t0 = time.time()
    y = client.remove_music(x)
    print("8 s window took", round(time.time() - t0, 1), "s")
    assert y.shape == x.shape and y.dtype == np.float32 and np.isfinite(y).all()
    # a second call on the same worker works too (the protocol stays in step)
    y2 = client.remove_music(x[: 44100 * 3])
    assert y2.shape == (44100 * 3, 2)
    sm.shutdown()
    assert client.proc is None
    # a worker that was shut down is replaced by a new one on demand
    c2 = sm.get_client()
    assert c2 is not client
    sm.shutdown()
    print("SAM TEST OK")


if __name__ == "__main__":
    main()
