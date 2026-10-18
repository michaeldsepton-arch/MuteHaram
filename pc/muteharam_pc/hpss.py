"""Harmonic / percussive split (sustained tones vs short sharp sounds), on the graphics card.

The same method as librosa.effects.hpss (median filtering of the spectrogram along time and along frequency,
soft masks), written with torch so it runs in milliseconds on a GPU instead of seconds on the processor.
"""
import torch
import torch.nn.functional as F

N_FFT = 2048
HOP = 512
KERNEL = 31


def _median(s, k, dim):
    """Median over a window of k along `dim` of s [C, F, T] with reflect padding (like scipy's median_filter)."""
    pad = k // 2
    if dim == -1:
        p = F.pad(s, (pad, pad), mode="reflect")
        return p.unfold(-1, k, 1).median(dim=-1).values
    p = F.pad(s.transpose(1, 2), (pad, pad), mode="reflect")             # [C, T, F+2pad]
    return p.unfold(-1, k, 1).median(dim=-1).values.transpose(1, 2)


@torch.no_grad()
def harmonic(sig, margin=1.0, power=2.0):
    """sig: [C, n] float tensor -> the sustained-tone (harmonic) part, same shape."""
    n = sig.shape[-1]
    win = torch.hann_window(N_FFT, device=sig.device)
    X = torch.stft(sig, N_FFT, hop_length=HOP, window=win, center=True, pad_mode="constant", return_complex=True)
    S = X.abs()
    Hh = _median(S, KERNEL, -1)           # harmonic: smooth along time
    Pp = _median(S, KERNEL, 1) * margin   # percussive: smooth along frequency
    z = torch.maximum(Hh, Pp).clamp_min(1e-10)
    a = (Hh / z) ** power
    b = (Pp / z) ** power
    mask = a / (a + b)
    return torch.istft(X * mask, N_FFT, hop_length=HOP, window=win, center=True, length=n)
