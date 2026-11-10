"""
Subtitles made by MuteHaram itself.

  * Cohere Transcribe (the model the translation project uses: CohereLabs/cohere-transcribe-arabic-07-2026, which
    does English and Arabic), on the graphics card in 4-bit: 1.5 GB, so it fits beside the music removal and the
    scene check (in 16-bit it takes 4 GB and everything slows down). Measured: a minute of film in ~10 s, the same
    text as 16-bit. Without a graphics card (or bitsandbytes): Whisper "small" on the processor, as before.
  * Cohere gives text only, no times: a speech detector (Silero VAD, from faster-whisper) cuts the sound into
    spoken pieces of at most 7 s, each piece is transcribed on its own (its times are the subtitle's), and a long
    piece is split into subtitle-sized lines timed by their share of its letters.
  * Made from the CLEANED sound where it is ready (no music in the way), else from the original.

    lines = transcribe(audio_16k_mono, offset_s)  -> [[start_s, end_s, "text"], ...]   ("\\n" = second line)

Captioner makes them while a film plays: the covered parts (Lowering the gaze: the picture is hidden and the sound
muted there, but people may still be talking), and, when asked for, the whole film just ahead of the player.
"""
import os
import re
import threading
import time

import numpy as np

COHERE = "CohereLabs/cohere-transcribe-arabic-07-2026"
WHISPER = "small"               # the fallback (Systran/faster-whisper-small, on this PC already)
RATE = 16000
LINE = 42                       # letters on one subtitle line (the usual for films); two lines at most
MAX_PIECE_S = 7.0
_model = None
_lock = threading.Lock()
_which = None                   # "cohere" / "whisper"


def _load(log=print):
    global _model, _which
    if _model is not None:
        return _model
    try:
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("no graphics card")
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        from transformers import AutoProcessor, BitsAndBytesConfig, CohereAsrForConditionalGeneration
        q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16)
        proc = AutoProcessor.from_pretrained(COHERE)
        model = CohereAsrForConditionalGeneration.from_pretrained(COHERE, dtype=torch.float16, device_map="cuda",
                                                                  quantization_config=q).eval()
        _model, _which = (proc, model), "cohere"
        log("captions: Cohere Transcribe ready (graphics card, 4-bit)")
    except Exception as ex:
        from faster_whisper import WhisperModel
        log(f"captions: Cohere not available ({ex!r}); using Whisper on the processor")
        _model, _which = WhisperModel(WHISPER, device="cpu", compute_type="int8", cpu_threads=4), "whisper"
    return _model


def pieces(audio):
    """Where people speak: [(start_s, end_s), ...] (at most 7 s each)."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    ts = get_speech_timestamps(np.asarray(audio, np.float32),
                               VadOptions(min_silence_duration_ms=300, max_speech_duration_s=MAX_PIECE_S,
                                          speech_pad_ms=150))
    return [(t["start"] / RATE, t["end"] / RATE) for t in ts]


JUNK = {"you", "oh", "uh", "um", "hmm", "mm", "ah", "thank you.", "thanks for watching!"}


def _cohere_texts(clips, lang):
    """Several spoken pieces at once (measured: 3x faster than one by one on the graphics card, the same text)."""
    import torch
    proc, model = _model
    out = []
    for k in range(0, len(clips), 16):
        inp = proc(clips[k:k + 16], sampling_rate=RATE, return_tensors="pt", language=lang).to("cuda", dtype=torch.float16)
        with torch.no_grad():
            ids = model.generate(**inp, max_new_tokens=160)
        texts = proc.decode(ids, skip_special_tokens=True)
        out += [t.strip() for t in (texts if isinstance(texts, (list, tuple)) else [texts])]
    return out


def split_lines(text, start, end):
    """One spoken piece -> subtitles of at most two lines of ~42 letters, timed by their share of the letters."""
    text = " ".join(text.split())
    # the model sometimes runs two sentences together ("pocket.the problem"): a space and a capital after the stop
    text = re.sub(r"([a-z]{2}[.!?])([a-zA-Z])", lambda m: m.group(1) + " " + m.group(2).upper(), text)
    if not text:
        return []
    if len(text) <= 2 * LINE:
        return [[round(start, 2), round(end, 2), two_lines(text)]]
    # cut at sentence ends, then commas, then spaces, into parts of at most two lines
    parts, rest = [], text
    while len(rest) > 2 * LINE:
        cut = -1
        for pat in (r"[.!?]\s", r"[,;:]\s", r"\s"):
            hits = [m.end() for m in re.finditer(pat, rest[:2 * LINE + 1]) if m.end() >= LINE // 2]
            if hits:
                cut = hits[-1]
                break
        if cut <= 0:
            cut = 2 * LINE
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    total = sum(len(p) for p in parts)
    out, t = [], start
    for p in parts:
        d = (end - start) * len(p) / total
        out.append([round(t, 2), round(t + d, 2), two_lines(p)])
        t += d
    return out


def two_lines(text):
    """Break a subtitle into two balanced lines when it is longer than one."""
    if len(text) <= LINE:
        return text
    mid = len(text) // 2
    spaces = [i for i, c in enumerate(text) if c == " "]
    if not spaces:
        return text
    i = min(spaces, key=lambda k: abs(k - mid))
    return text[:i] + "\n" + text[i + 1:]


def transcribe(audio, offset_s=0.0, lang="en", log=print):
    """audio: float32 mono at 16 kHz. -> [[start, end, text], ...] in film time (seconds)."""
    if audio is None or len(audio) < RATE // 2:
        return []
    audio = np.asarray(audio, np.float32)
    with _lock:                                  # one at a time (the model is shared)
        _load(log)
        if _which == "whisper":
            segs, _ = _model.transcribe(audio, beam_size=1, vad_filter=True, condition_on_previous_text=False,
                                        language=lang)
            out = []
            for s in segs:
                out += split_lines(s.text, offset_s + s.start, offset_s + s.end)
            return out
        out = []
        ps = pieces(audio)
        texts = _cohere_texts([audio[int(a * RATE):int(b * RATE)] for a, b in ps], lang) if ps else []
        for (a, b), text in zip(ps, texts):
            if not text or (b - a < 1.0 and text.lower().strip(" .!?,") in JUNK) or text.lower() in JUNK:
                continue                         # what speech models "hear" in a breath or a noise
            out += split_lines(text, offset_s + a, offset_s + b)
        return out


def to_16k_mono(x, rate=44100):
    """[n, 2] (or [n]) float/int16 audio -> float32 mono at 16 kHz."""
    from scipy.signal import resample_poly
    x = np.asarray(x)
    if x.dtype == np.int16:
        x = x.astype(np.float32) / 32768.0
    if x.ndim == 2:
        x = x.mean(axis=1)
    return resample_poly(x.astype(np.float32), 160, 441).astype(np.float32) if rate == 44100 else x.astype(np.float32)


def merge_ranges(rs):
    out = []
    for a, b in sorted(rs):
        if out and a <= out[-1][1] + 0.05:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


class Captioner:
    """Makes subtitles while a film plays.
      * the covered parts (scanner: Lowering the gaze), once each is found;
      * full=True: the whole film, from where the player is, up to AHEAD seconds ahead of it.
    read_clean(start_s, seconds) -> [n, 2] audio at 44.1 kHz, or None while that part is not cleaned yet;
    read_src(start_s, seconds)   -> the original sound there (used when the cleaned one is far off).
    lines: [[start, end, text], ...] sorted; a player asks for the ones it does not have yet (lines_from)."""
    CHUNK = 30.0
    AHEAD = 150.0

    def __init__(self, duration, read_clean, read_src=None, scanner=None, full=False, lang="en", log=print,
                 out_path=None):
        self.duration = float(duration or 0)
        self.read_clean, self.read_src = read_clean, read_src
        self.is_bad = None                       # is_bad(a, b) -> True if the engine gave up on part of it
        self.scan = scanner
        self.full = full
        self.lang = lang
        self.log = log
        self.out_path = out_path                 # (mpv) the lines are written here for the player script
        self.lines = []
        self.done = []                           # stretches transcribed: [[a, b], ...]
        self.pos = 0.0
        self._lock = threading.Lock()
        self._stop = False
        self._n_saved = -1
        threading.Thread(target=self._run, name="captions", daemon=True).start()

    # ---- what the players use
    def follow(self, t):
        self.pos = float(t)

    def set_full(self, on):
        self.full = bool(on)

    def lines_from(self, k):
        with self._lock:
            return len(self.lines), [list(x) for x in self.lines[k:]]

    def state(self):
        with self._lock:
            return {"lines": [list(x) for x in self.lines], "full": self.full, "done": merge_ranges(self.done)}

    def stop(self):
        self._stop = True

    # ---- work
    def _covered(self, t):
        return any(a - 0.05 <= t < b for a, b in self.done)

    def _next(self):
        """-> (a, b, is_cover) to transcribe next, or None. Covers first (they are on screen soon)."""
        if self.scan is not None:
            st = self.scan.state()
            for s, e in st["covers"]:
                if e > st["scanned_to"] and not st["done"]:
                    continue                     # still being found
                a = s
                while a < e and self._covered(a):
                    a = next((b for x, b in merge_ranges(self.done) if x - 0.05 <= a < b), e)
                if a < e - 0.5:
                    return a, min(e, a + self.CHUNK), True
        if self.full:
            a = max(0.0, self.pos - 2)
            while self._covered(a):
                a = next((b for x, b in merge_ranges(self.done) if x - 0.05 <= a < b), a + 1)
            if a < min(self.duration or 1e12, self.pos + self.AHEAD):
                return a, min(self.duration or a + self.CHUNK, a + self.CHUNK), False
        return None

    def _unclean(self, a, b):
        """The stretch includes a part the engine marked as not cleanable (it will never be ready)."""
        try:
            return bool(self.is_bad(a, b)) if self.is_bad else False
        except Exception:
            return False

    def _run(self):
        with _lock:
            _load(self.log)                      # ready before the film starts (loading takes up to a minute)
        while not self._stop:
            try:
                job = self._next()
                if job is None:
                    time.sleep(1)
                    continue
                a, b, cover = job
                audio = self.read_clean(a, b - a) if self.read_clean else None
                if audio is None:
                    # only ever from the CLEANED sound (the original has the music: its songs would become
                    # subtitles); a part that could not be cleaned gets none
                    if self.read_clean is not None and self._unclean(a, b):
                        with self._lock:
                            self.done = merge_ranges(self.done + [[a, b]])
                    time.sleep(1)
                    continue
                x = to_16k_mono(audio)
                got = transcribe(x, a, self.lang, self.log)
                # a piece cut off by the end of this stretch is done with the next one (unless the film ends here)
                end = a + len(x) / RATE
                last = b >= (self.duration or 1e12) - 0.5
                if not last and got and got[-1][1] > end - 0.4:
                    cut = got[-1][0]
                    while got and got[-1][0] >= cut - 0.01:
                        got.pop()
                    end = max(a + 1.0, cut)
                with self._lock:
                    self.lines = sorted([x for x in self.lines if not (a <= x[0] < end)] +
                                        [x for x in got if x[0] < end])
                    self.done = merge_ranges(self.done + [[a, end]])
                if cover:
                    self.log(f"captions: {len(got)} line(s) at {int(a // 60)}:{int(a % 60):02d} (covered part)")
                self._save()
            except Exception as ex:
                self.log(f"captions: {ex!r}")
                time.sleep(5)

    def _save(self):
        if not self.out_path or len(self.lines) == self._n_saved:
            return
        from .library import write_json
        try:
            write_json(self.out_path, self.state())
            self._n_saved = len(self.lines)
        except Exception:
            pass


def caption_file(read, total_s, lang="en", log=print, stage=lambda done, total: None):
    """A whole film (library: the cleaned sound) -> lines. read(start_s, seconds) -> [n, 2] 44.1 kHz audio."""
    out, a = [], 0.0
    while a < total_s - 0.5:
        b = min(total_s, a + Captioner.CHUNK)
        x = to_16k_mono(read(a, b - a))
        got = transcribe(x, a, lang, log)
        end = b
        if b < total_s - 0.5 and got and got[-1][1] > b - 0.4:
            cut = got[-1][0]
            while got and got[-1][0] >= cut - 0.01:
                got.pop()
            end = max(a + 1.0, cut)
        out += [x for x in got if x[0] < end]
        a = end
        stage(a, total_s)
    return out
