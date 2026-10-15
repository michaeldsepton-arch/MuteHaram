"""
The engine: removes music from a video's audio AHEAD of playback.

How it fits together
  * A media URL (or file) is decoded by ffmpeg to 44.1 kHz stereo float.
  * A worker thread runs the TIGER music network on the GPU over sliding
    windows, keeping only the centre of each window (so every kept sample has
    `margin` seconds of context on both sides) and subtracts the music estimate
    from both channels.
  * The cleaned audio is stored block by block (a block = one hop).
  * server.py exposes it as a normal seekable WAV file over local HTTP; mpv
    plays the original video with that WAV as its audio track. Because this
    is on-demand and runs several times faster than real time, the audio is
    always ready before the player needs it: no audio/video delay tricks.
  * If the player seeks, the worker jumps to the block that was asked for.
"""
import os
import subprocess
import tempfile
import threading
import time

import numpy as np
import torch

from .model import SAMPLE_RATE, load_music_model

CHANNELS = 2


class MediaSource:
    """Something ffmpeg can read: a file path or URL (+ optional HTTP headers)."""

    def __init__(self, url, headers=None, ffmpeg="ffmpeg", ffprobe="ffprobe"):
        self.url = url
        self.headers = dict(headers or {})
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def _http_args(self):
        args = []
        if self.url.startswith(("http://", "https://")):
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

    def duration(self):
        cmd = [self.ffprobe, "-v", "error", *self._http_args(), "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", self.url]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout.strip()
        return float(out)

    def read_s16(self, start_seconds, seconds):
        """Original audio [start, start+seconds) as int16 stereo (used as a fallback)."""
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *self._http_args(),
               "-ss", f"{max(0.0, start_seconds):.3f}", "-t", f"{seconds:.3f}", "-i", self.url, "-vn",
               "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"]
        out = subprocess.run(cmd, capture_output=True, timeout=120).stdout
        n = len(out) // (2 * CHANNELS) * 2 * CHANNELS
        return np.frombuffer(out[:n], dtype=np.int16).reshape(-1, CHANNELS)

    def decoder(self, start_seconds):
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *self._http_args(),
               "-ss", f"{max(0.0, start_seconds):.3f}", "-i", self.url, "-vn",
               "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"]
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)


class Session:
    def __init__(self, source, duration=None, window_s=10.0, hop_s=6.0, lookahead_s=150.0,
                 music_gain=1.0, device=None, models_dir=None, log=print,
                 channels_mode="auto", patience_s=15.0):
        assert window_s > hop_s > 0
        self.src = source
        self.log = log
        self.gain = music_gain
        self.duration = duration or source.duration()
        self.total_frames = int(round(self.duration * SAMPLE_RATE))
        self.hop = int(round(hop_s * SAMPLE_RATE))
        self.win = int(round(window_s * SAMPLE_RATE))
        self.margin = (self.win - self.hop) // 2
        self.win = self.hop + 2 * self.margin
        self.nblocks = -(-self.total_frames // self.hop)
        self.lookahead_blocks = max(2, int(lookahead_s / hop_s))

        self.net, self.device = load_music_model(models_dir, device)
        # "stereo": run the network on left and right separately (better stereo, 2x work);
        # "mono": run it once on the mix. auto = stereo when a GPU is available.
        self.stereo_sep = channels_mode == "stereo" or (channels_mode == "auto" and self.device.type == "cuda")
        self.patience = patience_s
        self.fallbacks = 0
        self.log(f"engine: {self.duration:.0f}s of audio, window {window_s:g}s / hop {hop_s:g}s on {self.device}, "
                 f"{'separate L/R' if self.stereo_sep else 'mono mix'} processing")

        self._tmp = tempfile.NamedTemporaryFile(prefix="muteharam_", suffix=".pcm", delete=False)
        self._tmp.close()
        self.store = np.memmap(self._tmp.name, dtype=np.int16, mode="w+",
                               shape=(self.nblocks * self.hop, CHANNELS))
        self.ready = np.zeros(self.nblocks, dtype=bool)
        self.cv = threading.Condition()
        self.want = 0           # block the player most recently asked for
        self.error = None
        self._stop = False
        self.thread = threading.Thread(target=self._work, name="engine", daemon=True)
        self.thread.start()

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
        stretch is returned instead, so playback never freezes."""
        f1 = min(f1, self.total_frames)
        out = []
        f = f0
        while f < f1:
            b = f // self.hop
            try:
                blk = self.wait_block(b, timeout=self.patience)
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
        try:
            raw = self.src.read_s16(b * self.hop / SAMPLE_RATE, self.hop / SAMPLE_RATE + 0.05)
        except Exception as ex:
            self.log(f"block {b + 1}: original audio unavailable too ({ex!r}); silence")
            raw = np.zeros((0, CHANNELS), np.int16)
        out = np.zeros((self.hop, CHANNELS), np.int16)
        out[: min(len(raw), self.hop)] = raw[: self.hop]
        return out

    def close(self):
        self._stop = True
        with self.cv:
            self.cv.notify_all()
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

    def _work(self):
        dec = None
        buf = np.zeros((0, CHANNELS), np.float32)
        buf_start = 0                       # absolute frame index of buf[0]
        dec_end = 0                         # absolute frame index after the last decoded frame
        eof = False
        last = None
        try:
            while not self._stop:
                with self.cv:
                    b = self._next_block(last)
                    while b is None and not self._stop:
                        self.cv.wait(0.5)
                        b = self._next_block(last)
                    if self._stop:
                        return
                s = b * self.hop - self.margin     # window start (may be < 0)
                e = s + self.win
                need_s = max(0, s)
                # (re)start the decoder if this window isn't continuous with what we have
                if dec is None or need_s < buf_start or need_s > dec_end + SAMPLE_RATE:
                    if dec is not None:
                        dec.kill()
                    dec = self.src.decoder(need_s / SAMPLE_RATE)
                    buf = np.zeros((0, CHANNELS), np.float32)
                    buf_start = dec_end = need_s
                    eof = False
                # decode until we have frames up to e (or end of stream)
                while dec_end < min(e, self.total_frames + SAMPLE_RATE) and not eof:
                    want_frames = min(e, self.total_frames + SAMPLE_RATE) - dec_end
                    raw = dec.stdout.read(min(want_frames, SAMPLE_RATE * 4) * CHANNELS * 4)
                    if not raw:
                        eof = True
                        break
                    raw = raw[: len(raw) // (CHANNELS * 4) * CHANNELS * 4]
                    a = np.frombuffer(raw, dtype=np.float32).reshape(-1, CHANNELS)
                    buf = np.concatenate([buf, a])
                    dec_end += len(a)
                # assemble the window [s, e), zero-padding outside the available audio
                x = np.zeros((self.win, CHANNELS), np.float32)
                lo = max(s, buf_start)
                hi = min(e, buf_start + len(buf))
                if hi > lo:
                    x[lo - s:hi - s] = buf[lo - buf_start:hi - buf_start]
                t0 = time.time()
                y = self._clean(x)
                dt = time.time() - t0
                self.store[b * self.hop:(b + 1) * self.hop] = y
                with self.cv:
                    self.ready[b] = True
                    self.cv.notify_all()
                last = b
                self.log(f"block {b + 1}/{self.nblocks}: {dt:.2f}s for {self.hop / SAMPLE_RATE:.0f}s of audio")
                # drop audio we won't need again
                keep_from = (b + 1) * self.hop - self.margin
                if keep_from > buf_start:
                    cut = min(keep_from - buf_start, len(buf))
                    buf = buf[cut:]
                    buf_start += cut
        except Exception as ex:  # surface to waiting readers
            import traceback
            traceback.print_exc()
            with self.cv:
                self.error = f"engine failed: {ex!r}"
                self.cv.notify_all()

    @torch.no_grad()
    def _clean(self, x):
        """x: [win, 2] float32 -> cleaned centre [hop, 2] int16."""
        c = slice(self.margin, self.margin + self.hop)
        if self.stereo_sep:
            t = torch.from_numpy(np.ascontiguousarray(x.T)).to(self.device)[:, None, :]   # [2,1,win]
            music = self.net(t)[:, 0, :].float().cpu().numpy().T                          # [win,2]
            y = x[c] - self.gain * music[c]
        else:
            mono = torch.from_numpy(x.mean(axis=1)).to(self.device)[None, None]
            music = self.net(mono)[0, 0].float().cpu().numpy()
            y = x[c] - self.gain * music[c, None]
        return (np.clip(y, -1.0, 1.0) * 32767.0).astype(np.int16)
