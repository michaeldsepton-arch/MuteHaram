"""
The engine: removes music from a video's audio AHEAD of playback.

How it fits together
  * A media URL (or file) is decoded by ffmpeg to 44.1 kHz stereo float.
  * A worker thread runs the TIGER network on the GPU over sliding windows, keeping
    only the centre of each window (so every kept sample has `margin` seconds of
    context on both sides), and subtracts the music estimate from both channels
    ("music" mode) or keeps only the dialogue estimate ("speech" mode).
  * The cleaned audio is stored block by block (a block = one hop).
  * server.py exposes it as a normal seekable WAV file over local HTTP; mpv plays the
    original video with that WAV as its audio track. Because this is on-demand and
    runs several times faster than real time, the audio is always ready before the
    player needs it: no audio/video delay tricks.
  * If the player seeks, the worker jumps to the block that was asked for.
  * Trouble never turns into silence: a hiccup while reading the source is retried;
    a part that still can't be cleaned plays as the ORIGINAL audio.
"""
import os
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
            cmd = [self.ffprobe, "-v", "error", *self._http_args(), "-show_entries", "format=duration",
                   "-of", "default=nw=1:nk=1", self.url]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            out = res.stdout.strip()
            try:
                d = float(out)
                if d > 0:
                    return d
            except ValueError:
                pass
            if attempt == 0 and self._maybe_enable_picky(res.stderr):
                continue
            why = " | ".join(res.stderr.strip().splitlines()[-3:]) or (
                "the stream reports no length (a live stream?)" if out in ("", "N/A") else f"unexpected output {out!r}")
            raise RuntimeError("ffprobe could not read the stream: " + why)

    def read_s16(self, start_seconds, seconds):
        """Original audio [start, start+seconds) as int16 stereo (used as a fallback)."""
        for attempt in range(2):
            cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *self._http_args(),
                   "-ss", f"{max(0.0, start_seconds):.3f}", "-t", f"{seconds:.3f}", "-i", self.url, "-vn",
                   "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"]
            res = subprocess.run(cmd, capture_output=True, timeout=120)
            out = res.stdout
            err = res.stderr.decode("utf-8", "replace")
            if out:
                n = len(out) // (2 * CHANNELS) * 2 * CHANNELS
                return np.frombuffer(out[:n], dtype=np.int16).reshape(-1, CHANNELS)
            if attempt == 0 and self._maybe_enable_picky(err):
                continue
            why = err.strip().splitlines()
            raise RuntimeError("ffmpeg returned no audio: " + (" | ".join(why[-3:]) or f"exit code {res.returncode}"))

    def probe(self, start_seconds=0.0):
        """Can we really decode audio from this source? -> (ok, reason)"""
        try:
            a = self.read_s16(start_seconds, 1.0)
            return (len(a) > 1000, "" if len(a) > 1000 else "ffmpeg returned almost no audio")
        except Exception as ex:
            return False, str(ex)

    def decoder(self, start_seconds):
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *self._http_args(),
               "-ss", f"{max(0.0, start_seconds):.3f}", "-i", self.url, "-vn",
               "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        proc.err_tail = []

        def drain():
            for line in proc.stderr:
                proc.err_tail = (proc.err_tail + [line.decode("utf-8", "replace").strip()])[-5:]

        threading.Thread(target=drain, daemon=True).start()
        return proc


class Session:
    def __init__(self, source, duration=None, window_s=10.0, hop_s=6.0, lookahead_s=150.0,
                 music_gain=1.0, device=None, models_dir=None, log=print,
                 channels_mode="auto", patience_s=15.0, mode="music"):
        assert window_s > hop_s > 0
        assert mode in ("music", "speech")
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

        # "music": subtract the estimated music; "speech": keep only the estimated dialogue
        self.net, self.device, self.target = load_network("music" if mode == "music" else "dialog",
                                                          models_dir, device)
        # "stereo": run the network on left and right separately (better stereo, 2x work);
        # "mono": run it once on the mix. auto = stereo when a GPU is available.
        self.stereo_sep = channels_mode == "stereo" or (channels_mode == "auto" and self.device.type == "cuda")
        self.patience = patience_s
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
        self.want = 0           # block the player most recently asked for
        self.error = None
        self._stop = False
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
    def wait_block(self, b, timeout=120.0):
        """Block until cleaned block `b` is available (priority-boosting it)."""
        with self.cv:
            self.want = b
            self.cv.notify_all()
            t0 = time.time()
            while not self.ready[b]:
                if self.error:
                    raise RuntimeError(self.error)
                if self._stop or time.time() - t0 > timeout:
                    raise TimeoutError(f"block {b} not ready")
                self.cv.wait(0.5)
        return self.store[b * self.hop:(b + 1) * self.hop]

    def read_frames(self, f0, f1):
        """Cleaned int16 stereo frames [f0, f1). If the AI can't deliver in time
        (slow computer, network trouble, engine error) the ORIGINAL audio for that
        stretch is returned instead, so playback never freezes or goes silent."""
        f1 = min(f1, self.total_frames)
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
            out.append(np.asarray(blk[lo:hi]))
            f = b * self.hop + hi
        return np.concatenate(out) if out else np.zeros((0, CHANNELS), np.int16)

    def _raw_block(self, b, why):
        self.fallbacks += 1
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

    def close(self):
        self._stop = True
        with self.cv:
            self.cv.notify_all()
        self._kill_decoder()
        # Let the worker finish the block it is on BEFORE the cache file is unmapped:
        # writing into an unmapped file would crash the whole program.
        self.thread.join(timeout=30)
        if self.thread.is_alive():
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
        self._dec = self.src.decoder(need_s / SAMPLE_RATE)
        self._buf = np.zeros((0, CHANNELS), np.float32)
        self._buf_start = self._dec_end = need_s
        self._eof = False

    def _read_until(self, e):
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
            raw = raw[: len(raw) // (CHANNELS * 4) * CHANNELS * 4]
            a = np.frombuffer(raw, dtype=np.float32).reshape(-1, CHANNELS)
            self._buf = np.concatenate([self._buf, a])
            self._dec_end += len(a)

    def _fill(self, s, e, need_s):
        """Make sure the decoded audio covers [need_s, e). A stream that stops early (a
        network hiccup) is retried a few times instead of being treated as silence."""
        need_end = min(e, self.total_frames)
        for attempt in range(3):
            if self._dec is None or need_s < self._buf_start or need_s > self._dec_end + SAMPLE_RATE:
                self._restart(need_s)
            self._read_until(e)
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
        x = np.zeros((self.win, CHANNELS), np.float32)
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
                    cleaned = self._clean(self._window(s, e))
                    if self._stop:
                        return
                    self.store[b * self.hop:(b + 1) * self.hop] = cleaned
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

    @torch.no_grad()
    def _clean(self, x):
        """x: [win, 2] float32 -> cleaned centre [hop, 2] int16."""
        c = slice(self.margin, self.margin + self.hop)
        if self.stereo_sep:
            t = torch.from_numpy(np.ascontiguousarray(x.T)).to(self.device)[:, None, :]   # [2,1,win]
            est = self.net(t)[:, self.target, :].float().cpu().numpy().T[c]               # [hop,2]
        else:
            mono = torch.from_numpy(x.mean(axis=1)).to(self.device)[None, None]
            est = self.net(mono)[0, self.target].float().cpu().numpy()[c, None]           # [hop,1]
        xc = x[c]
        # music mode: est is the music; speech mode: est is the dialogue, everything else goes
        removal = est if self.mode == "music" else xc - est
        y = xc - self.gain * removal
        return (np.clip(y, -1.0, 1.0) * 32767.0).astype(np.int16)
