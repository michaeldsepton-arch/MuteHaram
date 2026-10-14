"""
Step-A experiment from HANDOFF.md: how badly does a pretrained
music/dialogue/effects separator degrade when it only sees a SHORT
window of audio (the latency budget), versus the long context it was
trained/run with?

Model: TIGER-DnR (JusperLee/TIGER-DnR, Apache-2.0, 3 x 1.41M-param
band-split models for dialogue / effects / music, 44.1 kHz mono).

Method: run the *music* model with the same overlap-discard streaming
scheme as StreamingWindowBuffer (window W, hop H, keep the centre H
samples) and score the result against the model's own full-context
(12 s) output on the same clip. Reference = long-context output, so the
numbers say "how much quality is lost by shrinking context", not
absolute separation quality (the repo ships no clean ground-truth stems).

Usage:
  python tiger_window_eval.py --tiger-src <path to clone of
      github.com/JusperLee/TIGER> --weights <dir with config.json +
      model.safetensors> --audio <mix.wav> [--seconds 20] [--device cuda]
"""
import argparse, json, sys, time
import numpy as np
import soundfile as sf
import torch


def si_sdr(est, ref, eps=1e-8):
    est = est - est.mean()
    ref = ref - ref.mean()
    proj = (est @ ref) / (ref @ ref + eps) * ref
    noise = est - proj
    return 10 * np.log10((proj @ proj + eps) / (noise @ noise + eps))


def load_model(tiger_src, weights, device):
    sys.path.insert(0, tiger_src)
    import look2hear.models as m
    from safetensors.torch import load_file
    cfg = json.load(open(f"{weights}/config.json"))
    model = m.TIGERDNR(**cfg)
    model.load_state_dict(load_file(f"{weights}/model.safetensors"))
    return model.to(device).eval()


@torch.no_grad()
def run_music(model, x):  # x: [T] float tensor -> music estimate [T]
    out = model.music(x[None, None])  # [1, 3, T]
    return out[0, 0]


@torch.no_grad()
def streaming(model, mix, sr, window_ms, hop_ms, device):
    W, H = sr * window_ms // 1000, sr * hop_ms // 1000
    margin = (W - H) // 2
    padded = torch.cat([torch.zeros(margin), mix, torch.zeros(W)]).to(device)
    out, times = [], []
    pos = 0
    while pos + W <= len(padded) and sum(o.numel() for o in out) < len(mix):
        t0 = time.perf_counter()
        est = run_music(model, padded[pos:pos + W])
        if device == "cuda":
            torch.cuda.synchronize()
        times.append(time.perf_counter() - t0)
        out.append(est[margin:margin + H].cpu())
        pos += H
    return torch.cat(out)[:len(mix)].numpy(), np.array(times)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiger-src", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    a = ap.parse_args()

    mix, sr = sf.read(a.audio, dtype="float32")
    if mix.ndim > 1:
        mix = mix.mean(1)
    mix = torch.from_numpy(mix[: int(sr * a.seconds)])
    model = load_model(a.tiger_src, a.weights, a.device)

    # Long-context reference: whole clip in one pass (>= 12 s context).
    ref_music = run_music(model, mix.to(a.device)).cpu().numpy()
    mixn = mix.numpy()
    ref_clean = mixn - ref_music  # "everything except music"
    print(f"clip {len(mixn)/sr:.1f}s on {a.device}; reference = full-context output")
    print(f"{'window':>7} {'hop':>5} | {'music SI-SDR':>12} {'non-music SI-SDR':>16} | "
          f"{'p50 ms':>7} {'p95 ms':>7} {'hop budget':>10}")
    for window_ms, hop_ms in [(120, 60), (250, 125), (500, 250), (1000, 500), (2000, 1000), (4000, 2000)]:
        est, t = streaming(model, mix, sr, window_ms, hop_ms, a.device)
        n = min(len(est), len(ref_music))
        s_m = si_sdr(est[:n], ref_music[:n])
        s_c = si_sdr((mixn - est)[:n], ref_clean[:n])
        print(f"{window_ms:>5}ms {hop_ms:>3}ms | {s_m:>10.1f}dB {s_c:>14.1f}dB | "
              f"{np.median(t)*1e3:>7.1f} {np.percentile(t,95)*1e3:>7.1f} {hop_ms:>8}ms")


if __name__ == "__main__":
    main()
