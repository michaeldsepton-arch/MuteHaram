"""Bandit v2 (speech / music / effects separation for films) as a cleaning engine.

Weights: pc/models/bandit-v2/model.safetensors (made by `python setup_pc.py` from the Zenodo checkpoint).
The model works on 48 kHz mono signals in chunks of about 8 s; the player's windows are resampled to it.
"""
import os

import numpy as np
import torch

from .model import PC_DIR, pick_device

BANDIT_DIR = os.path.join(PC_DIR, "models", "bandit-v2")
WEIGHTS = os.path.join(BANDIT_DIR, "model.pt")
ZENODO_URL = "https://zenodo.org/records/12701995/files/checkpoint-multi.ckpt?download=1"
FS = 48000
STEMS = ["speech", "music", "sfx"]
KWARGS = dict(in_channels=1, band_type="musical", n_bands=64, normalize_channel_independently=False,
              treat_channel_as_feature=True, n_sqm_modules=8, emb_dim=128, rnn_dim=256, bidirectional=True,
              rnn_type="GRU", mlp_dim=512, hidden_activation="Tanh", hidden_activation_kwargs=None,
              complex_mask=True, use_freq_weights=True, n_fft=2048, win_length=2048, hop_length=512,
              window_fn="hann_window", wkwargs=None, power=None, center=True, normalized=True,
              pad_mode="reflect", onesided=True, fs=FS)

_CACHE = {}


def is_available(models_dir=None):
    return os.path.exists(os.path.join(models_dir or BANDIT_DIR, "model.pt"))


def convert_checkpoint(ckpt_path, out_path=WEIGHTS):
    """The downloaded Lightning checkpoint (447 MB, includes training state) -> just the model weights."""
    ck = torch.load(ckpt_path, map_location="cpu", weights_only=True)["state_dict"]
    sd = {k[len("model."):]: v for k, v in ck.items() if k.startswith("model.")}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    torch.save(sd, out_path)             # plain tensors only: loads with weights_only=True
    return out_path


def load_bandit(models_dir=None, device=None):
    """-> (model, device); cached."""
    key = (str(models_dir), str(device))
    if key in _CACHE:
        return _CACHE[key]
    path = os.path.join(models_dir or BANDIT_DIR, "model.pt")
    if not os.path.exists(path):
        raise FileNotFoundError("Bandit v2 weights not found. Run  python setup_pc.py  to download them (about 450 MB).")
    from .bandit.bandit import Bandit
    model = Bandit(stems=STEMS, **KWARGS)
    missing, unexpected = model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True), strict=False)
    real_missing = [k for k in missing if "window" not in k]
    if real_missing:
        raise RuntimeError(f"Bandit weights do not match the model ({len(real_missing)} missing, e.g. {real_missing[:2]})")
    device = pick_device(device)
    model.to(device).eval()
    for p in model.parameters():
        p.requires_grad_(False)
    _CACHE[key] = (model, device)
    return _CACHE[key]


def _resample(x, up, down):
    from scipy.signal import resample_poly
    return resample_poly(x, up, down, axis=0).astype(np.float32)


@torch.no_grad()
def separate(model, device, x44, stems=("music",)):
    """x44: [n, 2] float32 at 44.1 kHz -> {stem: [n, 2] float32 at 44.1 kHz}. Left and right are separated on their own."""
    n = len(x44)
    x48 = _resample(x44, 160, 147)                                       # 44.1 -> 48 kHz
    t = torch.from_numpy(np.ascontiguousarray(x48.T)).to(device)[:, None, :]    # [2, 1, T]
    out = model({"mixture": {"audio": t}})["estimates"]
    res = {}
    for s in stems:
        e = out[s]["audio"].float().cpu().numpy()[:, 0, :].T            # [T, 2]
        e = _resample(e, 147, 160)                                       # 48 -> 44.1 kHz
        if len(e) < n:
            e = np.pad(e, ((0, n - len(e)), (0, 0)))
        res[s] = e[:n]
    return res
