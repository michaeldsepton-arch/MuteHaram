"""Quick check that the model loads and how fast this computer runs it."""
import sys
import time

import torch

from .model import SAMPLE_RATE, load_music_model


def main():
    t0 = time.time()
    net, device = load_music_model()
    print(f"model loaded on {device} in {time.time()-t0:.1f}s"
          + (f" ({torch.cuda.get_device_name(0)})" if device.type == "cuda" else ""))
    secs = 4.0
    x = torch.randn(1, 1, int(SAMPLE_RATE * secs), device=device) * 0.05
    with torch.no_grad():
        net(x)  # warm-up
        if device.type == "cuda":
            torch.cuda.synchronize()
        t = time.time()
        for _ in range(3):
            y = net(x)
        if device.type == "cuda":
            torch.cuda.synchronize()
        dt = (time.time() - t) / 3
    ok = tuple(y.shape) == (1, 3, x.shape[-1]) and bool(torch.isfinite(y).all())
    rtf = dt / secs
    print(f"{secs:.0f} s of audio processed in {dt*1000:.0f} ms  ->  {rtf:.2f} s of work per 1 s of audio")
    if rtf > 0.8:
        print("WARNING: this computer is too slow to keep ahead of playback with the default settings.")
    print("SELF-TEST", "OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
