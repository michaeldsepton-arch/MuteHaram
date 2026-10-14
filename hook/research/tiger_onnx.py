"""
Export TIGER-DnR's *music* network to ONNX and benchmark ONNX Runtime
(fp32 vs int8-dynamic) against the PyTorch reference.

The STFT/iSTFT are kept OUTSIDE the exported graph (torch.stft is awkward to
export and on Android we'd run our own FFT in Kotlin/C++ anyway). The graph is
the core network only:  spec_RI [1, 2, 1025, T]  ->  est real/imag [1, 3, 1025, T]
(source 0 of the music model is the music estimate).

Usage:
  python tiger_onnx.py --tiger-src <TIGER clone> --weights <dir> --audio <wav> \
      --out <dir> [--seconds 1 2]
"""
import argparse, os, sys, time, json
import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, os.path.dirname(__file__))
from tiger_window_eval import load_model, si_sdr  # noqa: E402

WIN, HOP = 2048, 512


class Core(torch.nn.Module):
    """TIGER.forward() minus torch.stft / torch.istft."""

    def __init__(self, t):
        super().__init__()
        self.t = t

    def forward(self, spec_ri):  # [1, 2, F, T]
        t = self.t
        b = spec_ri.shape[0]
        bands_ri, bands = [], []
        idx = 0
        for bw in t.band_width:
            bands_ri.append(spec_ri[:, :, idx: idx + bw])
            bands.append((spec_ri[:, 0, idx: idx + bw], spec_ri[:, 1, idx: idx + bw]))
            idx += bw
        feats = [t.BN[i](bands_ri[i].reshape(b, t.band_width[i] * 2, -1))
                 for i in range(len(t.band_width))]
        feats = torch.stack(feats, 1)
        sep = t.separator(feats.reshape(b, t.nband, t.feature_dim, -1))
        sep = sep.reshape(b, t.nband, t.feature_dim, -1)
        reals, imags = [], []
        for i in range(t.nband):
            o = t.mask[i](sep[:, i]).reshape(b, 2, 2, t.num_output, t.band_width[i], -1)
            m = o[:, 0] * torch.sigmoid(o[:, 1])
            mr, mi = m[:, 0], m[:, 1]
            mr = mr - (mr.sum(1, keepdim=True) - 1) / t.num_output
            mi = mi - mi.sum(1, keepdim=True) / t.num_output
            sr, si = bands[i]
            sr, si = sr.unsqueeze(1), si.unsqueeze(1)
            reals.append(sr * mr - si * mi)
            imags.append(sr * mi + si * mr)
        return torch.cat(reals, 2), torch.cat(imags, 2)  # [1, K, F, T] each


def _adaptive_avg_pool1d_matmul(x, output_size):
    """adaptive_avg_pool1d as a fixed averaging matrix.

    ONNX export of F.adaptive_avg_pool1d only supports output sizes that
    divide the input size; TIGER's multi-scale pooling doesn't satisfy that.
    Shapes are static for a fixed window, so the matrix is a constant.
    """
    n_in, n_out = int(x.shape[-1]), int(output_size)
    m = torch.zeros(n_in, n_out)
    for o in range(n_out):
        s = (o * n_in) // n_out
        e = -((-(o + 1) * n_in) // n_out)  # ceil
        m[s:e, o] = 1.0 / (e - s)
    return x @ m.to(x.dtype)


torch.nn.functional.adaptive_avg_pool1d = _adaptive_avg_pool1d_matmul


def stft_ri(x):
    spec = torch.stft(x, n_fft=WIN, hop_length=HOP, window=torch.hann_window(WIN),
                      return_complex=True)
    return torch.stack([spec.real, spec.imag], 1)


def istft(re, im, n):
    return torch.istft(torch.complex(re, im), n_fft=WIN, hop_length=HOP,
                       window=torch.hann_window(WIN), length=n)


def bench(fn, n=8, warm=2):
    for _ in range(warm):
        fn()
    ts = []
    for _ in range(n):
        t0 = time.perf_counter(); fn(); ts.append(time.perf_counter() - t0)
    return np.median(ts) * 1e3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiger-src", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, nargs="+", default=[1, 2])
    ap.add_argument("--threads", type=int, default=0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    import onnxruntime as ort
    from onnxruntime.quantization import quantize_dynamic, QuantType

    model = load_model(a.tiger_src, a.weights, "cpu")
    core = Core(model.music).eval()
    mix, sr = sf.read(a.audio, dtype="float32")
    mix = torch.from_numpy(mix if mix.ndim == 1 else mix.mean(1))

    rows = []
    for sec in a.seconds:
        n = int(sr * sec)
        x = mix[sr * 5: sr * 5 + n]  # arbitrary 'mid-clip' excerpt
        spec = stft_ri(x[None])
        with torch.no_grad():
            re, im = core(spec)
            ref = istft(re[:, 0], im[:, 0], n)[0].numpy()
            # sanity: matches original model.music
            orig = model.music(x[None, None])[0, 0].numpy()
        print(f"[{sec}s] core vs original model: SI-SDR {si_sdr(ref, orig):.1f} dB")

        f32 = f"{a.out}/tiger_music_{sec:g}s_fp32.onnx"
        i8 = f"{a.out}/tiger_music_{sec:g}s_int8.onnx"
        torch.onnx.export(core, (spec,), f32, input_names=["spec_ri"],
                          output_names=["re", "im"], opset_version=17, dynamo=False)
        quantize_dynamic(f32, i8, weight_type=QuantType.QInt8)

        for tag, path in [("fp32", f32), ("int8", i8)]:
            so = ort.SessionOptions()
            if a.threads:
                so.intra_op_num_threads = a.threads
            s = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
            feed = {"spec_ri": spec.numpy()}
            ms = bench(lambda: s.run(None, feed))
            r, i_ = s.run(None, feed)
            out = istft(torch.from_numpy(r[:, 0]), torch.from_numpy(i_[:, 0]), n)[0].numpy()
            q = si_sdr(out, ref)
            size = os.path.getsize(path) / 1e6
            print(f"[{sec}s] {tag}: {ms:7.1f} ms/window  (RTF {ms/1e3/sec:.2f})  "
                  f"SI-SDR vs torch {q:5.1f} dB  {size:.1f} MB")
            rows.append(dict(seconds=sec, kind=tag, ms=ms, sisdr=float(q), mb=size))
    json.dump(rows, open(f"{a.out}/bench.json", "w"), indent=1)


if __name__ == "__main__":
    main()
