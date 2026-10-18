"""SAM Audio worker. Runs in its OWN Python environment (see setup_sam.py), never imported by the player.

Protocol on stdin/stdout (binary, little endian):
  worker -> b"READY\\n" once the model is loaded
  player -> uint32 n, then n*2 float32 (stereo at 44.1 kHz)
  worker -> n*2 float32: the same audio with the music taken out
Everything the libraries print goes to stderr.
"""
import importlib
import os
import struct
import sys

CH = 2
MODEL = os.environ.get("MUTEHARAM_SAM_MODEL", "facebook/sam-audio-small")
STEP = float(os.environ.get("MUTEHARAM_SAM_STEP", "0.25"))      # 4 solver steps: fastest setting that sounded the same


def _protocol_streams():
    out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)                                  # stray prints can no longer corrupt the audio stream
    sys.stdout = sys.stderr
    if os.name == "nt":
        import msvcrt
        msvcrt.setmode(0, os.O_BINARY)
    return sys.stdin.buffer, out


def _read_exact(f, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def main():
    inp, out = _protocol_streams()
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")      # the faster downloader drops big files on some connections
    import numpy as np
    import torch
    from scipy.signal import resample_poly

    # parts of SAM Audio that this use does not need (span prediction, candidate re-ranking) are switched off,
    # otherwise they load several extra GB and need packages we do not install
    m = importlib.import_module("sam_audio.model.model")
    m.create_ranker = lambda c: None

    class _Off(torch.nn.Module):
        @classmethod
        def from_config(cls, *a, **k):
            return cls()
    m.PEAudioFrame = _Off
    m.PEAudioFrameTransform = _Off
    load = torch.nn.Module.load_state_dict
    torch.nn.Module.load_state_dict = lambda self, sd, strict=True, **k: load(self, sd, strict=False, **k)
    from sam_audio import SAMAudio, SAMAudioProcessor

    model = SAMAudio.from_pretrained(MODEL).half().eval().cuda()
    for p in model.parameters():
        p.requires_grad_(False)
    proc = SAMAudioProcessor.from_pretrained(MODEL)
    sr = proc.audio_sampling_rate
    ode = {"method": "euler", "options": {"step_size": STEP}}
    out.write(b"READY\n")

    while True:
        head = _read_exact(inp, 4)
        if head is None:
            return
        n = struct.unpack("<I", head)[0]
        raw = _read_exact(inp, n * CH * 4)
        if raw is None:
            return
        x = np.frombuffer(raw, dtype=np.float32).reshape(n, CH)
        mono = x.mean(axis=1)
        w48 = resample_poly(mono, sr // 300, 44100 // 300).astype(np.float32)
        wav = torch.from_numpy(w48)[None]
        batch = proc(audios=[wav], descriptions=["music"]).to("cuda")
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16):
            r = model.separate(batch, predict_spans=False, reranking_candidates=1, ode_opt=ode)
        res = r.residual[0].float().cpu().numpy().reshape(-1)
        res = resample_poly(res, 44100 // 300, sr // 300).astype(np.float32)
        if len(res) < n:
            res = np.pad(res, (0, n - len(res)))
        res = res[:n]
        y = res[:, None] + (x - mono[:, None])           # SAM's mono result + the original stereo difference
        out.write(np.ascontiguousarray(y, dtype=np.float32).tobytes())


if __name__ == "__main__":
    main()
