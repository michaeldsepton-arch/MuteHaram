"""
Subtitles made by MuteHaram itself, for the parts that are covered ("Lowering the gaze"): the picture is hidden
and the sound muted there, but people may still be talking, so the players show what is said in a panel.
Websites don't always have subtitles, so the speech is turned into text here (Whisper, on the processor: the
graphics card is busy removing the music). Only the covered parts are transcribed.

    lines = transcribe(audio_16k_mono, offset_s)  -> [[start_s, end_s, "text"], ...]
"""
import threading

import numpy as np

MODEL = "small"                 # Systran/faster-whisper-small (already on this PC); "large-v3" is better but slower
RATE = 16000
_model = None
_lock = threading.Lock()


def _load():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=4)
    return _model


def transcribe(audio, offset_s=0.0):
    """audio: float32 mono at 16 kHz. -> [[start, end, text], ...] in film time (seconds)."""
    if audio is None or len(audio) < RATE // 2:
        return []
    with _lock:                                  # one at a time (the model is shared)
        segs, _ = _load().transcribe(np.asarray(audio, np.float32), beam_size=1, vad_filter=True,
                                     condition_on_previous_text=False)
        return [[round(offset_s + s.start, 2), round(offset_s + s.end, 2), s.text.strip()]
                for s in segs if s.text.strip()]


def to_16k_mono(x, rate=44100):
    """[n, 2] (or [n]) float/int16 audio -> float32 mono at 16 kHz."""
    from scipy.signal import resample_poly
    x = np.asarray(x)
    if x.dtype == np.int16:
        x = x.astype(np.float32) / 32768.0
    if x.ndim == 2:
        x = x.mean(axis=1)
    return resample_poly(x.astype(np.float32), 160, 441).astype(np.float32) if rate == 44100 else x.astype(np.float32)


class CoverCaptioner:
    """Transcribes each covered part once its sound can be read; the lines join the scene list (key "said").
    read_audio(start_s, seconds) -> audio at 44.1 kHz ([n, 2]) for that stretch of the film."""

    def __init__(self, scanner, read_audio, log=print):
        self.scan = scanner
        self.read_audio = read_audio
        self.log = log
        self.done = []                           # covered parts already transcribed: [start, end]
        self._stop = False
        threading.Thread(target=self._run, name="cover-captions", daemon=True).start()

    def stop(self):
        self._stop = True

    def _run(self):
        import time
        while not self._stop:
            try:
                st = self.scan.state()
                for s, e in st["covers"]:
                    # only once the part is complete (the scanner moved on past it), and not done yet
                    if e > st["scanned_to"] and not st["done"]:
                        continue
                    if any(abs(s - a) < 0.5 and abs(e - b) < 0.5 for a, b in self.done):
                        continue
                    lines = transcribe(to_16k_mono(self.read_audio(s, e - s)), s)
                    self.done.append([s, e])
                    self.scan.add_said(lines)
                    self.log(f"captions: {len(lines)} line(s) for the covered part at {int(s // 60)}:{int(s % 60):02d}")
                if st["done"] and len(self.done) >= len(st["covers"]):
                    return
            except Exception as ex:
                self.log(f"captions: {ex!r}")
                time.sleep(10)
            time.sleep(2)
