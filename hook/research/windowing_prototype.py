"""
Prototype + correctness check for the streaming chunk/overlap-add buffer
the real AudioProcessor will need.

Problem: AudioTrack.write() hands us tiny buffers (often a few hundred
samples -- a handful of milliseconds). A real separation model needs a
much bigger window of context (on the order of ~1 second) to separate
well. So we need a layer that:
  1. accumulates incoming small writes into a rolling buffer
  2. once enough samples are available, runs inference on a
     fixed-size WINDOW of audio
  3. only the middle portion of each window's output is kept (the
     edges of any windowed model's output tend to be less reliable --
     this is standard practice, sometimes called "overlap-discard" or
     processing with a context margin), with windows overlapping by
     HOP so there's no gap
  4. hands back processed samples to be written out, same length and
     order as what came in, with no audible clicks/discontinuities at
     window boundaries

This script verifies the buffering/indexing math is correct using a
trivial stand-in "model" (identity, and separately a known filter) so
any bug would show up as either lost/duplicated samples, timing drift,
or audible-would-be discontinuities -- before translating this into
the real Kotlin implementation where bugs are much more annoying to
find.
"""

import numpy as np

SAMPLE_RATE = 44100
WINDOW = SAMPLE_RATE * 1          # 1 second of context per inference call
HOP = SAMPLE_RATE // 2            # advance by 0.5s each time (50% overlap)
MARGIN = (WINDOW - HOP) // 2      # samples trimmed from each edge of a window's output


class StreamingWindowBuffer:
    """
    Feed it small chunks via push(); it calls `model_fn(window) -> window`
    on fixed-size WINDOW buffers as they become available, and yields
    back correctly-ordered, correctly-trimmed output samples via pull().

    This mirrors exactly what the Kotlin version needs to do inside
    AudioTrackHook's per-write() callback, just without the Android
    plumbing.
    """

    def __init__(self, model_fn, window=WINDOW, hop=HOP, margin=MARGIN):
        self.model_fn = model_fn
        self.window = window
        self.hop = hop
        self.margin = margin
        self._in_buf = np.zeros(0, dtype=np.float64)
        self._out_queue = np.zeros(0, dtype=np.float64)
        self._warmed_up = False
        # how many samples of true silence we pad at the very start so
        # the first real window has a left margin to trim -- otherwise
        # the first MARGIN samples of real audio would be lost
        self._pad = margin

    def push(self, chunk: np.ndarray):
        self._in_buf = np.concatenate([self._in_buf, chunk])
        self._maybe_process()

    def _maybe_process(self):
        if not self._warmed_up:
            self._in_buf = np.concatenate([np.zeros(self._pad), self._in_buf])
            self._warmed_up = True

        while len(self._in_buf) >= self.window:
            win = self._in_buf[: self.window]
            processed = self.model_fn(win)
            # keep only the centre HOP-sized slice, discarding MARGIN
            # samples from each edge where the model had less context
            centre = processed[self.margin : self.margin + self.hop]
            self._out_queue = np.concatenate([self._out_queue, centre])
            # advance by HOP, not WINDOW -- this is what creates the overlap
            self._in_buf = self._in_buf[self.hop :]

    def pull(self, n: int) -> np.ndarray:
        """Return up to n processed samples, in order, FIFO."""
        avail = min(n, len(self._out_queue))
        out, self._out_queue = self._out_queue[:avail], self._out_queue[avail:]
        return out


def identity_model(window: np.ndarray) -> np.ndarray:
    return window


def gain_halving_model(window: np.ndarray) -> np.ndarray:
    # stand-in for "a model that changed the signal" -- halves amplitude.
    # Used to confirm processed output (not raw passthrough) is really
    # what comes out the other end, and that boundaries between windows
    # don't show seams even when every window is independently processed.
    return window * 0.5


def run_round_trip_test(model_fn, expected_scale, label):
    duration_s = 5.0
    t = np.arange(int(duration_s * SAMPLE_RATE)) / SAMPLE_RATE
    # a signal with enough structure that misalignment would be obvious:
    # a few sine components at different frequencies, non-trivial phase
    signal = (
        0.6 * np.sin(2 * np.pi * 220 * t)
        + 0.3 * np.sin(2 * np.pi * 1000 * t + 0.4)
        + 0.1 * np.sin(2 * np.pi * 4000 * t + 1.1)
    )

    buf = StreamingWindowBuffer(model_fn)

    # Feed it in small, irregular chunks to mimic real AudioTrack.write()
    # call sizes rather than one convenient block -- this is the part
    # most likely to hide an off-by-one if tested lazily.
    rng = np.random.default_rng(42)
    pos = 0
    chunk_sizes = []
    while pos < len(signal):
        size = int(rng.integers(256, 1024))  # roughly realistic write() sizes
        size = min(size, len(signal) - pos)
        chunk_sizes.append(size)
        pos += size

    output_chunks = []
    pos = 0
    for size in chunk_sizes:
        buf.push(signal[pos : pos + size])
        pos += size
        # pull whatever's ready, same as the hook would after each push
        output_chunks.append(buf.pull(10_000_000))

    output = np.concatenate(output_chunks)

    # Account for the deliberate startup latency: `margin` samples of
    # padding delay before the first real output appears. That's the
    # real, expected algorithmic latency this buffering approach adds.
    aligned_output = output[: len(signal)]
    expected = signal * expected_scale

    # Compare over the steady-state region only, skipping the startup
    # transient (first window) and the very tail (last partial window
    # never gets flushed in this minimal prototype -- noted below).
    start = WINDOW
    end = min(len(aligned_output), len(expected)) - WINDOW
    diff = aligned_output[start:end] - expected[start:end]
    max_err = np.max(np.abs(diff))
    rms_err = np.sqrt(np.mean(diff ** 2))

    # Boundary-seam check: look at samples right around each HOP
    # boundary specifically -- this is where a buggy implementation
    # would show a jump/click.
    boundary_positions = range(start, end, HOP)
    boundary_jumps = []
    for p in boundary_positions:
        if p + 1 < len(aligned_output):
            local_diff = abs(aligned_output[p] - expected[p])
            boundary_jumps.append(local_diff)
    max_boundary_err = max(boundary_jumps) if boundary_jumps else 0.0

    print(f"--- {label} ---")
    print(f"  samples in: {len(signal)}, samples out (usable region): {end - start}")
    print(f"  max abs error (steady-state):   {max_err:.6f}")
    print(f"  rms error (steady-state):       {rms_err:.6f}")
    print(f"  max error AT window boundaries: {max_boundary_err:.6f}")
    print(f"  algorithmic latency introduced: {WINDOW/SAMPLE_RATE*1000:.0f} ms "
          f"(first full window) + up to {MARGIN/SAMPLE_RATE*1000:.0f} ms margin")
    ok = max_err < 1e-9 and max_boundary_err < 1e-9
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
    print()
    return ok


if __name__ == "__main__":
    print(f"WINDOW={WINDOW} samples ({WINDOW/SAMPLE_RATE*1000:.0f} ms), "
          f"HOP={HOP} samples ({HOP/SAMPLE_RATE*1000:.0f} ms), "
          f"MARGIN={MARGIN} samples ({MARGIN/SAMPLE_RATE*1000:.0f} ms) per edge\n")

    ok1 = run_round_trip_test(identity_model, 1.0, "identity model (should reconstruct input exactly)")
    ok2 = run_round_trip_test(gain_halving_model, 0.5, "gain-halving model (confirms real processing + no seams)")

    if ok1 and ok2:
        print("ALL TESTS PASSED -- windowing/overlap-discard math is correct.")
    else:
        print("FAILED -- do not port this logic to Kotlin yet.")
