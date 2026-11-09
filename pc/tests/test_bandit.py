"""
The Bandit v2 film model inside the engine (skipped when its weights are not installed):

    python tests/test_bandit.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from muteharam_pc import bandit_model as bm  # noqa: E402


def main():
    if not bm.is_available():
        print("BANDIT TEST SKIPPED (weights not installed: python setup_pc.py)")
        return
    model, dev = bm.load_bandit()
    rng = np.random.default_rng(1)
    n = 44100 * 8
    t = np.arange(n) / 44100
    x = np.stack([0.1 * np.sin(2 * np.pi * 220 * t) + 0.02 * rng.standard_normal(n),
                  0.1 * np.sin(2 * np.pi * 330 * t) + 0.02 * rng.standard_normal(n)], axis=1).astype(np.float32)
    est = bm.separate(model, dev, x, ("music", "speech", "sfx"))
    for k, v in est.items():
        assert v.shape == x.shape and np.isfinite(v).all(), k
    # the three stems add up to (about) the mix
    resid = np.sqrt(np.mean((x - sum(est.values())) ** 2)) / np.sqrt(np.mean(x ** 2))
    print("stems add up to the mix, residual", round(float(resid), 4))
    assert resid < 0.1
    print("BANDIT TEST OK on", dev)


if __name__ == "__main__":
    main()
