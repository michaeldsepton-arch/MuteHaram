"""
Guards against a mistake that once made the cleaning use 15 GB of graphics memory (gradient tracking
accidentally switched on): cleaning a few seconds must stay small, and must not grow session after session.

    python tests/test_engine_memory.py        (skipped without an NVIDIA GPU)
"""
import json
import math
import os
import struct
import sys
import tempfile
import wave

import torch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from muteharam_pc import config  # noqa: E402
from muteharam_pc.engine import MediaSource, Session  # noqa: E402

if not torch.cuda.is_available():
    print("ENGINE MEMORY TEST SKIPPED (no GPU)")
    sys.exit(0)

cfg = config.load()
SR = 44100
path = os.path.join(tempfile.gettempdir(), "muteharam_mem_test.wav")
with wave.open(path, "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(b"".join(struct.pack("<hh", int(3000 * math.sin(i / 20)), int(3000 * math.sin(i / 33)))
                           for i in range(SR * 14)))

peaks = []
for i in range(2):
    torch.cuda.reset_peak_memory_stats()
    s = Session(MediaSource(path, ffmpeg=cfg["ffmpeg"], ffprobe=cfg["ffprobe"]), duration=14.0,
                models_dir=cfg["models_dir"], log=lambda *a: None)
    s.read_frames(0, 14 * SR)
    s.stop_work()
    s.close()
    peaks.append(torch.cuda.max_memory_allocated() / 1e9)
    print(f"session {i + 1}: peak graphics memory {peaks[-1]:.2f} GB, still allocated after closing {torch.cuda.memory_allocated() / 1e9:.2f} GB")
assert max(peaks) < 4.0, f"cleaning used {max(peaks):.1f} GB of graphics memory (should be about 1.6 GB)"
assert torch.cuda.memory_allocated() / 1e9 < 0.5, "memory is not released after a session"
print("ENGINE MEMORY TEST OK")
