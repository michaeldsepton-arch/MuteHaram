"""
Stage-1 listening test: remove music from a cinematic clip the way the phone
would do it -- short sliding windows, no future context beyond the window --
and write WAV files to compare by ear.

Output = mix - (music estimate from TIGER-DnR's music network).

Files written to --out:
  00_original.wav          the input
  10_full_context.wav      best case: whole clip in one pass (what the model
                           can do with lots of context)
  20_win2.0_hop1.0.wav     2 s window, keep centre 1 s  (latency ~1.5 s)
  21_win3.0_hop1.5.wav     3 s window, keep centre 1.5 s
  22_win1.0_hop0.5.wav     1 s window, keep centre 0.5 s (latency ~0.75 s)
  30_music_only_win2.0.wav what the 2 s version thinks the music is

Usage:
  python music_removal_demo.py --tiger-src <TIGER clone> --weights <dir> \
      --audio <wav> --out <dir> [--seconds 30] [--start 0]
"""
import argparse, os, sys, time
import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, os.path.dirname(__file__))
from tiger_window_eval import load_model, streaming, run_music  # noqa: E402


def write(path, x, sr):
    x = np.clip(x, -1.0, 1.0)
    sf.write(path, x, sr, subtype="PCM_16")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiger-src", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    mix, sr = sf.read(a.audio, dtype="float32")
    if mix.ndim > 1:
        mix = mix.mean(1)
    mix = mix[int(a.start * sr): int((a.start + a.seconds) * sr)]
    mixt = torch.from_numpy(mix)
    model = load_model(a.tiger_src, a.weights, a.device)

    write(f"{a.out}/00_original.wav", mix, sr)

    t0 = time.time()
    full = run_music(model, mixt.to(a.device)).cpu().numpy()
    print(f"full-context pass: {time.time()-t0:.1f}s")
    write(f"{a.out}/10_full_context.wav", mix - full, sr)

    for tag, w, h in [("20_win2.0_hop1.0", 2000, 1000),
                      ("21_win3.0_hop1.5", 3000, 1500),
                      ("22_win1.0_hop0.5", 1000, 500)]:
        t0 = time.time()
        est, t = streaming(model, mixt, sr, w, h, a.device)
        n = min(len(est), len(mix))
        print(f"{tag}: {time.time()-t0:.1f}s total, median {np.median(t)*1e3:.0f} ms/window "
              f"(hop budget {h} ms)")
        write(f"{a.out}/{tag}.wav", mix[:n] - est[:n], sr)
        if tag.startswith("20"):
            write(f"{a.out}/30_music_only_win2.0.wav", est[:n], sr)
    print("done ->", a.out)


if __name__ == "__main__":
    main()
