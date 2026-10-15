"""
Ground truth for the phone model: run the ORIGINAL PyTorch TIGER music network
on a chunk of audio and save (a) the host-side input the .tflite expects and
(b) the complex spectrograms it should produce.

The .tflite takes the mono chunk reflect-padded by 1024 on both sides, runs the
STFT inside the graph, and returns [1, 3, 1025, T] real and imag.

Usage (global python with torch):
  python ref_pytorch.py --tiger-src <TIGER> --weights <dir> --audio <wav> \
      --frames 1040 --start 5 --out ref_1040.npz
"""
import argparse, os, sys
import numpy as np
import soundfile as sf
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from tiger_onnx import Core, stft_ri, WIN, HOP  # noqa: E402  (patches adaptive pool for export too)
from tiger_window_eval import load_model  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiger-src", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--frames", type=int, required=True, help="T: STFT frames (chunk = (T-1)*512 samples)")
    ap.add_argument("--start", type=float, default=5.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    S = (a.frames - 1) * HOP
    mix, sr = sf.read(a.audio, dtype="float32")
    mix = mix if mix.ndim == 1 else mix.mean(1)
    x = torch.from_numpy(mix[int(a.start * sr): int(a.start * sr) + S].copy())
    assert len(x) == S, "audio too short"

    model = load_model(a.tiger_src, a.weights, "cpu")
    core = Core(model.music).eval()
    spec = stft_ri(x[None])  # center=True reflect pad, [1,2,1025,T]
    assert spec.shape[-1] == a.frames, spec.shape
    with torch.no_grad():
        re, im = core(spec)  # [1,3,1025,T]
    padded = torch.nn.functional.pad(x[None, None], (WIN // 2, WIN // 2), mode="reflect")[0]
    np.savez(a.out, x_padded=padded.numpy().astype(np.float32),
             re=re.numpy().astype(np.float32), im=im.numpy().astype(np.float32),
             x=x.numpy())
    print("saved", a.out, "input", tuple(padded.shape), "out", tuple(re.shape))


if __name__ == "__main__":
    main()
