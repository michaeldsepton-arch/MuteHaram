"""
The engine: removes music from a video's audio AHEAD of playback.

How it fits together
  * A media URL (or file) is decoded by ffmpeg to 44.1 kHz stereo float.
  * A worker thread runs the TIGER network on the GPU over sliding windows, keeping
    only the centre of each window (so every kept sample has `margin` seconds of
    context on both sides), and subtracts the music estimate.
  * The cleaned audio is stored block by block (a block = one hop).
  * server.py exposes it as a normal seekable WAV file over local HTTP; mpv plays the
    original video with that WAV as its audio track. Because this is on-demand and
    runs several times faster than real time, the audio is always ready before the
    player needs it: no audio/video delay tricks.
  * If the player seeks, the worker jumps to the block that was asked for.
  * A hiccup while reading the source is retried; a part that still can't be cleaned is silent (never the music).
"""
import os
import json
import subprocess
import tempfile
import threading
import time

import numpy as np
import torch

from .model import SAMPLE_RATE, load_network

CHANNELS = 2


class MediaSource:
    """Something ffmpeg can read: a file path or URL (+ optional HTTP headers)."""

    def __init__(self, url, headers=None, ffmpeg="ffmpeg", ffprobe="ffprobe"):
        self.url = url
        self.headers = dict(headers or {})
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self._picky_on = False   # allow "disguised" HLS segments (.jpg etc.), see _http_args
        self.start_time = None   # the stream's own clock at the film's start (from duration()), see _timing

    _picky_supported = {}

    def _has_extension_picky(self):
        """ffmpeg 6+ refuses HLS segments whose file extension looks wrong (sites disguise
        them as .jpg etc.); `-extension_picky 0` allows them. Older builds don't know the
        option, and it is only valid for HLS inputs."""
        if self.ffmpeg not in MediaSource._picky_supported:
            try:
                out = subprocess.run([self.ffmpeg, "-hide_banner", "-h", "demuxer=hls"],
                                     capture_output=True, text=True, timeout=30)
                MediaSource._picky_supported[self.ffmpeg] = "extension_picky" in (out.stdout + out.stderr)
            except Exception:
                MediaSource._picky_supported[self.ffmpeg] = False
        return MediaSource._picky_supported[self.ffmpeg]

    def _http_args(self):
        args = []
        if self.url.startswith(("http://", "https://")):
            args += ["-rw_timeout", "20000000"]          # give up on a stalled connection after 20 s
            if (self._picky_on or ".m3u8" in self.url.lower()) and self._has_extension_picky():
                args += ["-extension_picky", "0"]
            h = dict(self.headers)
            ua = None
            for k in list(h):
                if k.lower() == "user-agent":
                    ua = h.pop(k)
            if ua:
                args += ["-user_agent", ua]
            if h:
                args += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in h.items())]
        return args

    def _maybe_enable_picky(self, stderr_text):
        """If ffmpeg complained about segment extensions, switch the workaround on (once)."""
        if (not self._picky_on and ("allowed_segment_extensions" in stderr_text or "extension" in stderr_text
                                    and "hls" in stderr_text) and self._has_extension_picky()):
            self._picky_on = True
            return True
        return False

    def duration(self):
        for attempt in range(2):
            cmd = [self.ffprobe, "-v", "error", *self._http_args(), "-show_entries", "format=duration,start_time",
                   "-of", "json", self.url]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            out = res.stdout.strip()
            try:
                fmt = json.loads(out or "{}").get("format") or {}
                d = float(fmt.get("duration", "nan"))
                if d > 0:
                    try:
                        self.start_time = float(fmt["start_time"])
                    except (KeyError, ValueError):
                        self.start_time = None
                    return d
            except ValueError:
                pass
            if attempt == 0 and self._maybe_enable_picky(res.stderr):
                continue
            why = " | ".join(res.stderr.strip().splitlines()[-3:]) or (
                "the stream reports no length (a live stream?)" if out in ("", "N/A") else f"unexpected output {out!r}")
            raise RuntimeError("ffprobe could not read the stream: " + why)

    def _timing(self, start_seconds):
        """-> (options before the input, sound filter) to start at start_seconds of the film.

        aresample async: the sound follows the stream's own clock. Without it a missing or broken piece of the
        stream (it happens on long films) is simply left out, and from then on the sound runs AHEAD of the
        picture by that much. (Keeping the stream's clock with -copyts instead was tried: on some sites it lands
        16 s off or gives silence. A start inside pieces the website refuses is caught in decoder() instead.)"""
        # dts_delta_threshold: ffmpeg treats a jump in the stream's clock of more than 10 s as a glitch and closes
        # it up. When the website refuses minutes of pieces that moved everything after 50 s+ too early.
        return (["-dts_delta_threshold", "100000", "-ss", f"{max(0.0, start_seconds):.3f}"],
                "aresample=async=1:first_pts=0")

    def read_s16(self, start_seconds, seconds):
        """Original audio [start, start+seconds) as int16 stereo (used as a fallback)."""
        for attempt in range(2):
            pre, af = self._timing(start_seconds)
            # (no "-t": with the stream's own clock kept it would count from the film's start, not from here;
            # the wanted amount is read and ffmpeg stopped instead)
            cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *self._http_args(),
                   *pre, "-i", self.url, "-vn", "-af", af,
                   "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"]
            out, err = self._read_bytes(cmd, int(round(seconds * SAMPLE_RATE)) * 2 * CHANNELS, timeout=120)
            if out:
                n = len(out) // (2 * CHANNELS) * 2 * CHANNELS
                return np.frombuffer(out[:n], dtype=np.int16).reshape(-1, CHANNELS)
            if attempt == 0 and self._maybe_enable_picky(err):
                continue
            why = err.strip().splitlines()
            raise RuntimeError("ffmpeg returned no audio: " + (" | ".join(why[-3:]) or "no message"))

    @staticmethod
    def _read_bytes(cmd, nbytes, timeout):
        """Run ffmpeg until it has written nbytes (or ended / timed out) -> (bytes, its error text)."""
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        err = []
        threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True).start()
        timer = threading.Timer(timeout, proc.kill)
        timer.start()
        try:
            buf = bytearray()
            while len(buf) < nbytes:
                chunk = proc.stdout.read(min(1 << 16, nbytes - len(buf)))
                if not chunk:
                    break
                buf += chunk
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        time.sleep(0.05)
        return bytes(buf), (err[0] if err else b"").decode("utf-8", "replace")

    def probe(self, start_seconds=0.0):
        """Can we really decode audio from this source? -> (ok, reason)"""
        try:
            a = self.read_s16(start_seconds, 1.0)
            return (len(a) > 1000, "" if len(a) > 1000 else "ffmpeg returned almost no audio")
        except Exception as ex:
            return False, str(ex)

    def decoder(self, start_seconds, log=None):
        pre, af = self._timing(start_seconds)      # (why: see _timing)
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin", *self._http_args(),
               *pre, "-i", self.url, "-vn", "-af", af,
               "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        proc.err_tail = []
        shown = [0]

        # pieces the website refuses BEFORE any sound came: ffmpeg skips them and calls the first sound it gets
        # "the start", although it is from later in the film (everything after would play too early: Black Bird
        # episode 5 after 29 min). Such a start is not trusted (see Session._read_until_inner).
        proc.got_audio = False
        proc.skipped_early = False

        def drain():
            for line in proc.stderr:
                text = line.decode("utf-8", "replace").strip()
                proc.err_tail = (proc.err_tail + [text])[-5:]
                if "skipping" in text.lower() and not proc.got_audio:
                    proc.skipped_early = True
                if log and shown[0] < 20 and any(k in text.lower() for k in ("segment", "skip", "discontinu", "non-monoton", "failed")):
                    shown[0] += 1
                    log("source: " + text[:200])

        threading.Thread(target=drain, daemon=True).start()
        return proc


# How strict the music removal is ("strength"):
#   normal     the standard model (TIGER) takes the music out; voices and sound effects stay
#   strong     Bandit v2 (a film model) takes the music out instead: it catches music TIGER leaves behind
#   strongest  Bandit v2 keeps the speech only (effects go too)
#   auto       normal, and for each block a music detector (AST, AudioSet) listens to the result: where music
#              is still heard the block is cleaned again at strong, and if needed at strongest. Blocks are
#              cleaned ahead of playback, so this never pauses anything.
LEVELS = ("auto", "normal", "strong", "strongest")
# the detector's "Music" score above which music is still heard (clean dialogue scores 0.01-0.03; measured)
AUTO_MUSIC = 0.15
VOICE_KEEP = 0.15         # a voice this clear after the normal cleaning must still be there after a stricter one
_EXTRA = {}


def _bandit():
    if "bandit" not in _EXTRA:
        from . import bandit_model
        _EXTRA["bandit"] = bandit_model.load_bandit()
    return _EXTRA["bandit"]


_DETECTOR_LOCK = threading.Lock()
# what counts as a voice for the detector (AudioSet): talking, and shouting, screaming, crying
VOICE_LABELS = ("Speech", "Male speech, man speaking", "Female speech, woman speaking", "Child speech, kid speaking",
                "Conversation", "Narration, monologue", "Babbling", "Shout", "Bellow", "Yell", "Battle cry",
                "Children shouting", "Screaming", "Whispering", "Crying, sobbing", "Wail, moan")


def _detector(device):
    """-> score(x44 [n, 2] float) -> (how much the detector hears music, how much it hears a voice), each 0..1.
    (Loaded under a lock: two threads importing from transformers at once failed with an ImportError.)"""
    with _DETECTOR_LOCK:
        if "ast" not in _EXTRA:
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            from transformers import ASTFeatureExtractor, ASTForAudioClassification
            from scipy.signal import resample_poly
            name = "MIT/ast-finetuned-audioset-10-10-0.4593"
            model = ASTForAudioClassification.from_pretrained(name, use_safetensors=True).eval().to(device)
            fe = ASTFeatureExtractor.from_pretrained(name)
            labels = model.config.id2label
            music = [i for i, l in labels.items() if l == "Music"][0]
            voice = [i for i, l in labels.items() if l in VOICE_LABELS]

            @torch.no_grad()
            def score(x):
                m = resample_poly(np.asarray(x, np.float32).mean(axis=1), 160, 441).astype(np.float32)
                inp = fe(m, sampling_rate=16000, return_tensors="pt").to(device)
                p = torch.sigmoid(model(**inp).logits)[0]
                return float(p[music]), float(p[voice].max())
            _EXTRA["ast"] = score
        return _EXTRA["ast"]


class Session:
    def __init__(self, source, duration=None, window_s=10.0, hop_s=6.0, lookahead_s=150.0,
                 music_gain=1.0, device=None, models_dir=None, log=print,
                 channels_mode="auto", patience_s=45.0, mode="music", start_block=0,
                 fallback="silence", level="normal"):
        assert mode == "music"
        assert window_s > hop_s > 0
        self.src = source
        self.log = log
        self.gain = music_gain
        self.mode = mode
        self.duration = duration or source.duration()
        self.total_frames = int(round(self.duration * SAMPLE_RATE))
        self.hop = int(round(hop_s * SAMPLE_RATE))
        self.win = int(round(window_s * SAMPLE_RATE))
        self.margin = (self.win - self.hop) // 2
        self.win = self.hop + 2 * self.margin
        self.nblocks = -(-self.total_frames // self.hop)
        self.lookahead_blocks = max(2, int(lookahead_s / hop_s))

        self.net, self.device, self.target = load_network("music", models_dir, device)
        # "stereo": run the network on left and right separately (better stereo, 2x work);
        # "mono": run it once on the mix. auto = stereo when a GPU is available.
        self.stereo_sep = channels_mode == "stereo" or (channels_mode == "auto" and self.device.type == "cuda")
        self.patience = patience_s
        self.fallback = fallback
        self.level = level if level in LEVELS else "normal"
        self.stepped_up = 0                    # auto: blocks cleaned again at a stricter level
        self.picked = {}                       # block -> what cleaned it (for "music here" reports)
        self.fallbacks = 0
        self.log(f"engine: {self.duration:.0f}s of audio, window {window_s:g}s / hop {hop_s:g}s on {self.device}, "
                 f"{'separate L/R' if self.stereo_sep else 'mono mix'} processing, mode {mode}")

        self._clean_stale_files()
        self._tmp = tempfile.NamedTemporaryFile(prefix="muteharam_", suffix=".pcm", delete=False)
        self._tmp.close()
        self.store = np.memmap(self._tmp.name, dtype=np.int16, mode="w+",
                               shape=(self.nblocks * self.hop, CHANNELS))
        self.ready = np.zeros(self.nblocks, dtype=bool)
        self.bad = np.zeros(self.nblocks, dtype=bool)     # parts that could not be cleaned -> original audio
        self.cv = threading.Condition()
        self.want = max(0, min(start_block, self.nblocks - 1))   # block the player most recently asked for
        self.error = None
        self._stop = False
        self._readers = 0                      # player requests reading from the cache right now
        self._reading = False                  # the worker is waiting for the stream
        self._last_bytes = time.time()
        self._last_read = time.time()          # when the player last asked for sound
        self._computing = False
        threading.Thread(target=self._watchdog, name="engine-watchdog", daemon=True).start()
        self.thread = threading.Thread(target=self._work, name="engine", daemon=True)
        self.thread.start()

    @staticmethod
    def _clean_stale_files():
        """Remove cache files left behind by a run that was killed (older than an hour)."""
        import glob
        for p in glob.glob(os.path.join(tempfile.gettempdir(), "muteharam_*.pcm")):
            try:
                if time.time() - os.path.getmtime(p) > 3600:
                    os.unlink(p)
            except OSError:
                pass

    # ---------------------------------------------------------------- public
    def prepare(self, timeout=150.0, at_frame=None):
        """Wait until the block the player is at (or the one holding `at_frame`) is cleaned, so playback only
        starts / a mode switch only answers when the sound is ready."""
        b = self.want if at_frame is None else max(0, min(at_frame // self.hop, self.nblocks - 1))
        try:
            self.wait_block(b, timeout=timeout)
            return True
        except Exception:
            return False

    def wait_block(self, b, timeout=120.0):
        """Block until cleaned block `b` is available (priority-boosting it)."""
        with self.cv:
            self.want = b
            self._last_read = time.time()          # someone needs this sound (the player, or the library cleaning)
            self.cv.notify_all()
            t0 = time.time()
            while not self.ready[b]:
                if self.error:
                    raise RuntimeError(self.error)
                if self._stop or time.time() - t0 > timeout:
                    raise TimeoutError(f"block {b} not ready")
                self.cv.wait(0.5)
        return self.store[b * self.hop:(b + 1) * self.hop]

    def peek_frames(self, f0, f1):
        """Cleaned frames [f0, f1) if they are all cleaned already, else None. Never waits, never changes which
        part is cleaned next (the subtitles use it)."""
        f1 = min(f1, self.total_frames)
        if f1 <= f0:
            return None
        b0, b1 = f0 // self.hop, (f1 - 1) // self.hop
        with self.cv:
            if not (self.ready[b0:b1 + 1].all() and not self.bad[b0:b1 + 1].any()):
                return None
            return np.array(self.store[f0:f1])

    def read_frames(self, f0, f1):
        """Cleaned int16 stereo frames [f0, f1). If the AI can't deliver in time (slow computer, network trouble,
        engine error) that stretch is SILENT (fallback="silence", the default: never let the music through) or the
        ORIGINAL audio (fallback="original"), so playback never freezes."""
        f1 = min(f1, self.total_frames)
        with self.cv:
            self._readers += 1
            self._last_read = time.time()
        try:
            return self._read_frames(f0, f1)
        finally:
            with self.cv:
                self._readers -= 1
                self.cv.notify_all()

    def _read_frames(self, f0, f1):
        out = []
        f = f0
        while f < f1:
            b = f // self.hop
            try:
                blk = self.wait_block(b, timeout=self.patience)
                if self.bad[b]:
                    blk = self._raw_block(b, "this part could not be cleaned")
            except (TimeoutError, RuntimeError) as ex:
                blk = self._raw_block(b, ex)
            lo = f - b * self.hop
            hi = min(f1 - b * self.hop, self.hop)
            out.append(np.array(blk[lo:hi]))     # a COPY: the cache file may be closed right after
            f = b * self.hop + hi
        return np.concatenate(out) if out else np.zeros((0, CHANNELS), np.int16)

    def _raw_block(self, b, why):
        self.fallbacks += 1
        if self.fallback != "original":
            self.log(f"block {b + 1}: SILENT, it is not cleaned yet ({why})")
            return np.zeros((self.hop, CHANNELS), np.int16)
        self.log(f"block {b + 1}: using the ORIGINAL audio ({why})")
        raw = None
        for attempt in range(3):
            try:
                raw = self.src.read_s16(b * self.hop / SAMPLE_RATE, self.hop / SAMPLE_RATE + 0.05)
                break
            except Exception as ex:
                if attempt == 2:
                    self.log(f"block {b + 1}: original audio unavailable too ({ex!r}); silence")
                else:
                    time.sleep(1.0 + attempt)
        out = np.zeros((self.hop, CHANNELS), np.int16)
        if raw is not None:
            out[: min(len(raw), self.hop)] = raw[: self.hop]
        return out

    def stop_work(self):
        """Stop computing (and reading the source) at once, but keep the finished audio readable."""
        self._stop = True
        with self.cv:
            self.cv.notify_all()
        self._kill_decoder()

    def close(self):
        self._stop = True
        with self.cv:
            self.cv.notify_all()
        self._kill_decoder()
        # Let the worker finish the block it is on BEFORE the cache file is unmapped:
        # writing into an unmapped file would crash the whole program.
        self.thread.join(timeout=30)
        # ... and only once no player request is still reading from it (that crashed the program after a mode switch)
        with self.cv:
            t0 = time.time()
            while self._readers and time.time() - t0 < 60:
                self.cv.wait(0.5)
        if self.thread.is_alive() or self._readers:
            self.log("engine still busy at shutdown; leaving the cache file for later cleanup")
            return
        try:
            self.store._mmap.close()
        except Exception:
            pass
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    # ---------------------------------------------------------------- worker
    def _next_block(self, last):
        """Which block to compute next, or None to sleep."""
        w = self.want
        if not self.ready[w]:
            return w                       # the player is waiting for this one
        b = last + 1 if (last is not None and last + 1 >= w) else w
        hi = min(self.nblocks, w + self.lookahead_blocks)
        while b < hi and self.ready[b]:
            b += 1
        return b if b < hi else None

    # decoder state (only touched by the worker thread)
    _dec = None
    _buf = np.zeros((0, CHANNELS), np.float32)
    _buf_start = 0           # absolute frame index of _buf[0]
    _dec_end = 0             # absolute frame index after the last decoded frame
    _eof = False

    def _kill_decoder(self):
        dec, self._dec = self._dec, None
        if dec is not None:
            try:
                dec.kill()
            except Exception:
                pass

    def _restart(self, need_s):
        self._kill_decoder()
        self._dec = self.src.decoder(need_s / SAMPLE_RATE, self.log)
        self._buf = np.zeros((0, CHANNELS), np.float32)
        self._buf_start = self._dec_end = need_s
        self._eof = False

    STALL_S = 20.0

    def _watchdog(self):
        """A stream that stops delivering without breaking (a stalled download) would leave the player waiting
        forever: after STALL_S without any sound, drop the connection; the reader then reopens the stream."""
        while not self._stop:
            time.sleep(2.0)
            if self._reading and self._dec is not None and time.time() - self._last_bytes > self.STALL_S:
                self.log(f"source: no sound from the stream for {self.STALL_S:.0f}s, reconnecting")
                self._last_bytes = time.time()
                self._kill_decoder()

    def _read_until(self, e):
        self._reading = True
        self._last_bytes = time.time()
        try:
            self._read_until_inner(e)
        finally:
            self._reading = False

    def _read_until_inner(self, e):
        limit = min(e, self.total_frames + SAMPLE_RATE)
        while self._dec_end < limit and not self._eof:
            dec = self._dec
            if dec is None:                      # closed from another thread (shutting down)
                self._eof = True
                break
            raw = dec.stdout.read(min(limit - self._dec_end, SAMPLE_RATE * 4) * CHANNELS * 4)
            if not raw:
                self._eof = True
                break
            if not getattr(dec, "got_audio", True):
                time.sleep(0.3)                  # (ffmpeg's messages come a moment later than its sound)
                if getattr(dec, "skipped_early", False):
                    self.log("source: the website refused the pieces where this starts; not trusting this start")
                    self._kill_decoder()
                    self._eof = True
                    break
                dec.got_audio = True
            raw = raw[: len(raw) // (CHANNELS * 4) * CHANNELS * 4]
            a = np.frombuffer(raw, dtype=np.float32).reshape(-1, CHANNELS)
            self._buf = np.concatenate([self._buf, a])
            self._dec_end += len(a)
            self._last_bytes = time.time()

    def _fill(self, s, e, need_s):
        """Make sure the decoded audio covers [need_s, e). A stream that stops early (a
        network hiccup) is retried a few times instead of being treated as silence."""
        need_end = min(e, self.total_frames)
        for attempt in range(3):
            if self._stop:
                return False                 # told to stop: never reopen the source
            if self._dec is None or need_s < self._buf_start or need_s > self._dec_end + SAMPLE_RATE:
                self._restart(need_s)
            self._read_until(e)
            if self._stop:
                return False
            missing = need_end - self._dec_end if need_end > need_s else 0
            if missing <= 2 * SAMPLE_RATE:       # (a small shortfall at the very end is normal)
                return True
            tail = " | ".join(getattr(self._dec, "err_tail", [])[-2:])
            self.log(f"source stopped early (missing {missing / SAMPLE_RATE:.1f}s); retry {attempt + 1}/3"
                     + (f": {tail}" if tail else ""))
            self._kill_decoder()
            time.sleep(1.0 + attempt)
        return False

    def _window(self, s, e):
        x = np.zeros((e - s, CHANNELS), np.float32)
        lo = max(s, self._buf_start)
        hi = min(e, self._buf_start + len(self._buf))
        if hi > lo:
            x[lo - s:hi - s] = self._buf[lo - self._buf_start:hi - self._buf_start]
        return x

    def _trim(self, b):
        keep_from = (b + 1) * self.hop - self.margin
        if keep_from > self._buf_start:
            cut = min(keep_from - self._buf_start, len(self._buf))
            self._buf = self._buf[cut:]
            self._buf_start += cut

    def _work(self):
        last = None
        while not self._stop:
            b = None
            try:
                with self.cv:
                    b = self._next_block(last)
                    while b is None and not self._stop:
                        self.cv.wait(0.5)
                        b = self._next_block(last)
                    if self._stop:
                        return
                s = b * self.hop - self.margin     # window start (may be < 0)
                e = s + self.win
                bad = True
                dt = 0.0
                if self._fill(s, e, max(0, s)):
                    t0 = time.time()
                    self._computing = True
                    try:
                        cleaned = self._clean(self._window(s, e))
                    finally:
                        self._computing = False
                    if self._stop:
                        return
                    self.store[b * self.hop:(b + 1) * self.hop] = cleaned
                    self.picked[b] = getattr(self, "_last_pick", None)
                    dt = time.time() - t0
                    bad = False
                with self.cv:
                    self.bad[b] = bad
                    self.ready[b] = True
                    self.cv.notify_all()
                last = b
                if bad:
                    self.log(f"block {b + 1}/{self.nblocks}: could not be read/cleaned -> will play the original")
                else:
                    self.log(f"block {b + 1}/{self.nblocks}: {dt:.2f}s for {self.hop / SAMPLE_RATE:.0f}s of audio")
                    self._trim(b)
            except Exception as ex:
                # One bad block must never stop everything: mark it, reset the reader, carry on.
                if self._stop:
                    return                         # shut down in the middle of a block: nothing to report
                import traceback
                traceback.print_exc()
                self._kill_decoder()
                if b is not None:
                    with self.cv:
                        self.bad[b] = True
                        self.ready[b] = True
                        self.cv.notify_all()
                    last = b
                time.sleep(0.5)

    def set_level(self, level, at_frame=None):
        """Change how strict the removal is: the blocks from the playing one on are cleaned again."""
        if level not in LEVELS:
            raise ValueError(f"unknown strength {level!r}")
        with self.cv:
            self.level = level
            b = self.want if at_frame is None else max(0, min(at_frame // self.hop, self.nblocks - 1))
            self.want = b
            self.ready[b:] = False
            self.cv.notify_all()
        self.log(f"engine: strength {level}, cleaning again from {b * self.hop / SAMPLE_RATE:.0f} s")

    @torch.no_grad()
    def _clean(self, x):
        """x: [win, 2] float32 -> cleaned centre [hop, 2] int16, as strict as self.level says."""
        c = slice(self.margin, self.margin + self.hop)
        level = self.level
        self._last_pick = {"level": level, "used": level}
        try:
            if level in ("strong", "strongest"):
                st = self._bandit_safe(x)
                if st is not None:
                    y = st["speech"][c] if level == "strongest" else (st["speech"] + st["sfx"])[c]
                    return (np.clip(y, -1.0, 1.0) * 32767.0).astype(np.int16)
                return (np.clip(self._tiger(x, c), -1.0, 1.0) * 32767.0).astype(np.int16)
            y = self._tiger(x, c)
            if level == "auto":
                score = _detector(self.device)
                heard, voice = score(y)
                st = self._bandit_safe(x) if heard >= AUTO_MUSIC else None
                if st is not None:
                    # (how clear the voice is: after the normal cleaning, or in the original if clearer there)
                    voice = max(voice, score(x[c])[1])
                    # the stricter result is used only if it keeps the voice: Bandit can take a voice that shouts
                    # or screams over loud music for part of the music, and leave silence (measured: Young
                    # Sherlock's opening, a woman shouting for help: voice 0.4-0.7 after the normal cleaning,
                    # 0.02 after Bandit). Then the normal cleaning is kept: the voice matters more.
                    pick, what = y, "normal kept (the stricter cleaning would remove a voice)"
                    for name, cand in (("strong", (st["speech"] + st["sfx"])[c]), ("strongest (speech only)", st["speech"][c])):
                        m2, v2 = score(cand)
                        if voice >= VOICE_KEEP and v2 < voice * 0.6:
                            continue                     # the voice would be lost
                        pick, what = cand, name
                        if m2 < AUTO_MUSIC:
                            break
                    y = pick
                    self._last_pick = {"level": level, "used": what, "music_after_normal": round(heard, 3),
                                       "voice": round(voice, 3)}
                    self.stepped_up += 1
                    if self.stepped_up <= 30 or self.stepped_up % 50 == 0:
                        self.log(f"auto: music still heard ({heard:.2f}, voice {voice:.2f}) -> {what}"
                                 f" ({self.stepped_up} block(s) so far)")
            self._level_fail = 0
            return (np.clip(y, -1.0, 1.0) * 32767.0).astype(np.int16)
        except (FileNotFoundError, ImportError, OSError) as ex:
            # Bandit or the detector is not available: the standard cleaning for this block; after 3 failures
            # in a row for the rest of the film (one failure can be passing: the models were still loading)
            self._level_fail = getattr(self, "_level_fail", 0) + 1
            if self._level_fail >= 3:
                self.log(f"engine: strength {level} not available ({ex}); using normal")
                self.level = "normal"
            else:
                self.log(f"engine: strength {level} not ready for this block ({ex}); normal cleaning for it")
            y = self._tiger(x, c)
            return (np.clip(y, -1.0, 1.0) * 32767.0).astype(np.int16)

    def _bandit_safe(self, x):
        """Bandit's speech and effects, or None if the graphics card has no room for it right now (it is shared with
        the subtitles and the scene check): then the normal cleaning is kept. Never silence because of this."""
        for attempt in range(2):
            try:
                return self._bandit_stems(x)
            except torch.OutOfMemoryError:
                torch.cuda.empty_cache()
                if attempt == 0:
                    time.sleep(0.5)
        self.short_of_memory = getattr(self, "short_of_memory", 0) + 1
        if self.short_of_memory <= 5 or self.short_of_memory % 50 == 0:
            self.log(f"engine: no graphics memory free for the stronger cleaning here; the normal cleaning is kept "
                     f"({self.short_of_memory} block(s) so far)")
        return None

    def _bandit_stems(self, x):
        from . import bandit_model
        model, dev = _bandit()
        return bandit_model.separate(model, dev, x, stems=("speech", "sfx"))

    def _bandit_level(self, x, c, speech_only):
        st = self._bandit_stems(x)
        y = st["speech"][c] if speech_only else (st["speech"] + st["sfx"])[c]
        return (np.clip(y, -1.0, 1.0) * 32767.0).astype(np.int16)

    def _tiger(self, x, c):
        """The standard model: the music taken out of the centre of window x -> float [hop, 2]."""
        if self.stereo_sep:
            t = torch.from_numpy(np.ascontiguousarray(x.T)).to(self.device)[:, None, :]   # [2,1,win]
            est = self.net(t)[:, self.target, :].float().cpu().numpy().T[c]               # [hop,2]
        else:
            mono = torch.from_numpy(x.mean(axis=1)).to(self.device)[None, None]
            est = self.net(mono)[0, self.target].float().cpu().numpy()[c, None]           # [hop,1]
        return x[c] - self.gain * est                      # est is the music


class SessionHub:
    """What the audio server serves: the current cleaning Session (or a finished file from the library)."""

    def set_level(self, level, at_frame=None):
        if not hasattr(self.cur, "set_level"):
            raise ValueError("this film was cleaned in advance (library): its strength is fixed")
        self.cur.set_level(level, at_frame)
        if at_frame is not None:
            self.cur.prepare(timeout=120, at_frame=at_frame)

    @property
    def level(self):
        return getattr(self.cur, "level", "precleaned")

    def __init__(self, session, remake):
        self.cur = session
        self.remake = remake                 # remake(mode) -> a new Session on the same source
        self.lock = threading.Lock()
        self.total_frames = session.total_frames
        self.duration = session.duration
        self.log = session.log

    @property
    def mode(self):
        return self.cur.mode

    def peek_frames(self, f0, f1):
        return self.cur.peek_frames(f0, f1) if hasattr(self.cur, "peek_frames") else None

    def read_frames(self, f0, f1):
        for _ in range(4):
            cur = self.cur
            try:
                out = cur.read_frames(f0, f1)
            except Exception:
                if cur is self.cur:
                    raise
                continue
            if cur is self.cur:
                return out
            # the mode was switched while we were reading: what we got may be from the old mode; ask the new one
        return self.cur.read_frames(f0, f1)

    def close(self):
        self.cur.close()

