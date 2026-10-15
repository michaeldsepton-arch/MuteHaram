# Project Handoff / Status

Living document — update this whenever something meaningfully changes,
so work can resume cleanly from a different session/tool (e.g. picking
this up in Claude Code on your own machine, with full internet access
this sandbox doesn't have).

**Last updated:** 2026-10-14 evening: NO ROOT NEEDED, video re-sync verified (see "ROOT NOT NEEDED" section)

## Sequencing decision: validate cheap before building expensive (important, read this first)

There are really two viable end-states, not the four I originally
listed (an earlier version of this doc had a confused "option 4" that
was just a restatement of the video-delay idea -- ignore any mention
of a 4th option elsewhere):

- **A. Short context window (~120ms), audio-only, no video hooking.**
  Keeps latency low (~150ms) without touching video at all. Quality
  ceiling unknown -- shorter context generally means a harder time
  separating music from speech well, but *how much* worse is
  unconfirmed.
- **B. Longer context window (better quality, e.g. 500ms-1s) +
  also hook video frame release timing to re-sync.** Technically
  reachable with the same hooking technique already used for audio
  (ExoPlayer releases video frames through a timed call, same class of
  interception as AudioTrack.write()) -- not categorically harder, but
  more surface area (a different class/method to hook, possibly
  player-library-dependent, one more thing that breaks on app
  updates), and real engineering on top of what exists now.

**Decision: build and validate A first, before investing in B.** The
actual unknown blocking this whole project isn't "can we hook video
too" -- it's "does a fast enough model even separate well at 120ms of
context." That's unconfirmed either way, and it's the thing that
determines whether B is worth building at all. If A's quality turns
out acceptable, B is unnecessary extra complexity. If A's quality
turns out bad, that's exactly the data needed to justify building B
next, rather than guessing now. Don't start on video-hooking (B) until
A has been tried with a real model and heard, not just reasoned about.

---

## Model research + Step-A experiment results (2026-10-14, Claude Code on PC)

**No ready-made causal/streaming music-vs-speech model exists.** HS-TasNet
(the low-latency research line) only has unofficial code repos
(lucidrains/HS-TasNet etc.), no pretrained weights, and was trained on
MUSDB stems (vocals/drums/bass/other), not cinematic dialogue/music/effects.

**Best pretrained find: TIGER-DnR** (`JusperLee/TIGER-DnR`, Apache-2.0).
Three 1.41M-param band-split models (dialogue / effects / music) trained on
the Divide-and-Remaster cinematic dataset -- the right task. An Android
LiteRT port exists (`litert-community/TIGER-DnR-LiteRT`) but it is fixed at
12 s chunks (~4.5 s/chunk/graph on a Pixel 8a GPU), i.e. not usable live as-is.

**Experiment** (`hook/research/tiger_window_eval.py`): run TIGER's music
model with the same overlap-discard windowing as `StreamingWindowBuffer`,
score vs its own full-context output (SI-SDR; higher = closer to long-context
quality). 20 s clip, GTX 1070 / 12-thread CPU:

| window / hop | music SI-SDR | CPU time per window | hop budget |
|---|---|---|---|
| 120 / 60 ms | ~0 dB (broken) | 595 ms | 60 ms |
| 250 / 125 ms | 0.6-2.6 dB (broken) | 674 ms | 125 ms |
| 500 / 250 ms | ~8 dB (poor) | 960 ms | 250 ms |
| 1 s / 500 ms | 11-13 dB (usable?) | 1885 ms | 500 ms |
| 2 s / 1 s | 17-21 dB (good) | 2856 ms | 1000 ms |

**Conclusions:**
1. **Option A (120 ms, audio-only) is dead with this pretrained model** --
   output is essentially unrelated to the long-context result. (Caveat: the
   model was never trained on short windows; fine-tuning on short crops of
   DnR could help, but that's a training project, not a download.)
2. **Quality needs >= ~1 s of context -> Option B** (video re-sync) becomes
   necessary, since ~1.5 s of audio lag is unwatchable otherwise.
3. **Compute is the harder wall:** even on a desktop CPU, PyTorch runs at
   ~4x *slower* than real time at 1 s / 500 ms. A phone will be slower.
   Needs ONNX/LiteRT + GPU/NPU + quantisation, and possibly a smaller
   model, before real-time is plausible. GTX 1070 timing was ~340 ms flat
   per call (kernel-launch bound), so GPU is real-time at >= 1 s hops only.
4. Caveat: scores are vs the model's own long-context output, not clean
   ground truth, and only one 20 s clip was tried.

**Options from here (user decision needed):** (a) fine-tune TIGER-music on
short windows (needs DnR dataset ~tens of GB + GPU time; 1070 is weak),
(b) go Option B with ~1-2 s windows and pursue ExoPlayer video-delay hook +
on-device LiteRT/ONNX speed work, (c) drop real-time and do offline
processing (separate the file/stream ahead of playback) instead.

### Option B progress (2026-10-14, later)

**Speed work (ONNX)** -- `hook/research/tiger_onnx.py` exports the music
network (STFT/iSTFT kept outside the graph) to ONNX; matches PyTorch to
~96 dB SI-SDR. Raw numbers in `hook/research/onnx_bench.json`. Desktop CPU:

| window | ORT fp32 | ORT int8 (dynamic) |
|---|---|---|
| 1 s | 1870 ms (RTF 1.87), exact | 14 s (RTF 14), 29 dB -- worse |
| 2 s | 4666 ms (RTF 2.33), exact | 28 s, 21 dB -- worse |

- ONNX Runtime fp32 is no faster than PyTorch; **dynamic int8 is both slower
  and lossy -- dead end.** Needed patches: `adaptive_avg_pool1d` with
  non-divisible sizes exported as a fixed matmul (in the script).
- **Real-time looks reachable only on GPU/NPU:** a third party measured the
  LiteRT fp16 port at ~4.5 s per 12 s chunk (RTF ~0.37) on a Pixel 8a GPU,
  full GPU residency. Only the *music* graph is needed (1 of 3). At 50%
  window overlap that implies RTF ~0.75 -- tight but plausible on Pixel 9 Pro.
  Unverified; the shipped port is fixed at 12 s, so it must be re-exported
  at 1-2 s (their converter: `litert_torch`) and benchmarked on the real phone.
  Galaxy S26 NPU run crashed per their card; Tab S7 is a much older GPU.

**Video re-sync** -- no need to hook video classes (see `hook/README.md`).
Players slave video to the AudioTrack clock, so `AudioTrackHook` now shifts
`getPlaybackHeadPosition()`/`getTimestamp()` back by the processor's
`latencyMs` (new `AudioProcessor.latencyMs`; `StreamingAudioProcessor`
reports (window+hop)/2). Written but **not compiled or tested** (CI builds it
on push; behaviour needs a rooted device). Fallback: hook
`MediaCodecVideoRenderer` frame release.

**Next steps, in order:** (1) CI build passes; (2) root Pixel + LSPosed, test
clock-shift with a fake-delay processor; (3) re-export TIGER-music to LiteRT
at 1-2 s fixed windows with in-graph STFT and time it on the Pixel GPU;
(4) integrate (STFT/iSTFT in Kotlin, LiteRT GPU) and listen to quality.

### ROOT NOT NEEDED + video re-sync VERIFIED (2026-10-14, evening)

**Big change: no root required.** The hook runs inside ReVanced YouTube
(`anddea.youtube`, ReVanced Extended) on the stock Pixel 9 Pro, patched with
**LSPatch** (JingMatrix fork v1.2, Integrated mode, hook embedded). Guides:
`NOROOT.md` (use this) and `ROOTING.md` (fallback only; never needed).
The hook no longer filters by package name (LSPatch scoping does that).

**Video re-sync works** (user-verified by eye on a talking-head video): delaying
the audio by D and shifting the reported `AudioTrack` clock back by D
(`getPlaybackHeadPosition` / `getTimestamp`) keeps lips and voice in sync;
with the shift disabled the voice is D late. YouTube's player reads
`getPlaybackHeadPosition` (~300 calls/s). Side effect: picture freezes for D
at video start/seek.

**Bugs found along the way (all fixed):**
1. Hooks were installed once per *package* loaded into the process (app +
   WebView + microG), stacking 3x: audio processed 3x (garbled "many voices"),
   clock shifted 3x (so a 500 ms shift "matched" a 1500 ms delay). Fixed with a
   once-per-process guard. LESSON: when a result looks like a weird constant
   ratio, check for duplicated hooks first.
2. Non-blocking `write(ByteBuffer)` re-presents the unaccepted tail; in-place
   processing must skip it (`RetryGuard`).
3. Delay lines are per-stream (`streamId`) and cleared on flush/stop/release.
4. Beep-and-flash sync tests with a 1 s period hide a 1 s delay. Use speech.

**YouTube's audio path:** `AudioTrack.write(ByteBuffer, int, int)`, 48 kHz,
stereo, PCM16.

**Test harness** (live, no re-patching): `DelayTestProcessor` reads
`adb shell settings put global muteharam_delay_ms|muteharam_noclock|muteharam_shift_ms`.
Logs: `adb logcat -d -s MuteHaramHook` (in/out loudness every ~250 ms, clock
calls, stream resets).

**Still open (the actual project):** the separation model. Needs (a)
re-export of TIGER-music for fixed 1-2 s windows with GPU-friendly ops,
(b) on-device speed measurement on the Pixel (LiteRT GPU), (c) STFT/iSTFT +
overlap-add in Kotlin/C++, (d) swap `DelayTestProcessor` for a real
`StreamingAudioProcessor` whose `latencyMs` = real delay incl. inference.
CineHD not tested yet (needs its package name and the same patch steps).

---

## The goal

Suppress music specifically (not all audio) in real time while
watching things on Android — originally scoped for YouTube (via
ReVanced Extended) and CineHD, for personal/non-commercial use.

## Where we landed, architecturally (read this before changing direction)

We went through several approaches before settling here — worth
knowing so we don't re-litigate them:

1. ❌ **"Destructive interference" on digital audio** — doesn't apply;
   that's physics for live sound waves in air, not mixed digital audio.
   What we actually want is *source separation* (an ML model splitting
   music from everything else), not phase cancellation.
2. ❌ **System-level audio capture only** (`AudioPlaybackCapture`) —
   confirmed working for both YouTube and CineHD (see "Capture test
   results" below), but it's **read-only**. No way to mute/replace the
   original audio, so you'd hear the original and our filtered version
   overlapping. Dead end on its own.
3. ❌ **Embedding/hosting YouTube or Chrome inside our own app** — not
   how Android works; no API lets one app host another's process and
   read its internals directly.
4. ✅ **App virtualization + method hooking (current direction)** — run
   our own code *inside* the target app's process via LSPosed, and
   hook `AudioTrack.write()` directly. We modify the buffer **in
   place** before it's actually written to the speaker — so there's no
   "original" audio to compete with, solving the muting problem
   cleanly. Requires root + Magisk + LSPosed on-device. No way around
   needing root; confirmed there's no non-root API for this.

## Key constraint discovered during build (read before picking a model)

Because the hook modifies audio **synchronously, in place**, right
before it plays, any processing delay we add is **audio-only** — there
is no equivalent delay on the video side (we don't touch video
rendering). This creates a hard trade-off between separation quality
and audio/video sync:

- A full-second context window (what offline models like Demucs use)
  → ~1.25s of audio lag → visibly/audibly out of sync.
- A ~120ms window → ~150ms total latency → close to the broadcast
  industry's general threshold (~125ms) for audio lag becoming
  noticeable.

**This means the separation model must be one built for short-context,
real-time streaming use** — not a general offline model. This is the
single most important constraint on model selection going forward.
Verified/reasoned through in `hook/README.md` and
`hook/research/windowing_prototype.py` (Python prototype, round-trip
tested, zero error / zero boundary artifacts, before porting the logic
to Kotlin).

## Devices

- **Pixel 9 Pro** — target for rooting first. Official bootloader
  unlock supported by Google, well-documented Magisk path, no
  permanent fuse/Knox-style penalty.
- **Galaxy Tab S7** — rootable too, more friction: unlocking trips the
  Knox counter permanently (0x1), disabling Samsung Pay/Knox-gated
  features for good. Plan was to prototype on the Pixel first since
  it's lower-risk, then replicate on the tablet once the process is
  proven.
- Neither device has been rooted yet as of this doc. Root-the-Pixel
  walkthrough is in the chat history (not yet copied into this repo —
  TODO if useful to have it versioned here too).

## Repo structure

```
muteharam/
├── app/           -- MuteHaram Capture Test: standalone app used to
│                     empirically confirm AudioPlaybackCapture gets
│                     real signal from a given app (no root needed).
│                     DONE, confirmed working for both targets.
├── hook/          -- The actual LSPosed module. Requires root.
│   ├── AudioTrackHook.kt         -- hooks AudioTrack.write() overloads
│   ├── AudioProcessor.kt         -- processing interface + stub +
│   │                                StreamingAudioProcessor (wraps
│   │                                the windowing buffer for real
│   │                                models)
│   ├── StreamingWindowBuffer.kt  -- chunking/overlap-add buffer that
│   │                                gives the model enough context
│   │                                per inference call
│   ├── HookConfig.kt             -- target package list + which
│   │                                processor is active (currently
│   │                                PassthroughProcessor -- no-op,
│   │                                proves the hook fires safely
│   │                                before adding real processing)
│   ├── research/windowing_prototype.py -- verified Python prototype
│   │                                        of the buffering math
│   └── README.md                 -- full setup/testing instructions
│                                     for once root + LSPosed exist
└── .github/workflows/build-apk.yml -- CI builds BOTH APKs on every
                                        push to main and publishes
                                        them as a GitHub Release
                                        (this sandbox can't build
                                        Android locally -- no network
                                        access to the Android SDK/
                                        Maven repos -- so this is the
                                        actual build path)
```

**Latest build:** https://github.com/michaeldsepton-arch/MuteHaram/releases/latest
(always has both `app-debug.apk` and `hook-debug.apk`)

## Capture test results (confirmed, 2026-10-14)

Both YouTube (via ReVanced Extended) and CineHD passed the
`AudioPlaybackCapture` signal test — neither is DRM-blocked or
opted-out at the capture-permission level. This only confirms
*capture* works, which is no longer the active approach (see above) --
kept here because it's still useful supporting evidence that these two
specific apps aren't doing anything unusual at the audio-output layer
that would also complicate the hook approach.

## What's NOT done yet / open work

1. **Root both devices.** Not started. Pixel first (lower risk).
2. **Install Magisk → LSPosed (via Zygisk) → install hook-debug.apk,
   scope it to YouTube + CineHD in LSPosed Manager.**
3. **Find CineHD's real package name** and replace the placeholder
   `com.cinehd.app` in `hook/src/main/java/com/muteharam/hook/HookConfig.kt`
   (`adb shell pm list packages | grep -i cine`).
4. **Confirm the hook actually fires** via
   `adb logcat | grep MuteHaramHook` while playing audio in each app —
   tells us which `AudioTrack.write()` overload each app actually
   uses (full details in `hook/README.md`).
5. **Find and benchmark a real low-latency separation model.** Blocked
   in this sandbox -- Hugging Face and most model-hosting sites are
   not reachable from here (network allowlist only covers npm/pypi/
   crates/GitHub/Anthropic). This needs to happen somewhere with
   normal internet access (your own machine / Claude Code locally).
   Must be a model designed for short-context streaming separation,
   not an offline model like full Demucs -- see latency constraint
   above. Candidates worth investigating first: the research line
   around "Hybrid Spectrogram-TasNet" for real-time low-latency
   source separation, and any small ONNX-exportable vocal/music
   separation model that can run via ONNX Runtime Mobile on-device.
6. **Benchmark that model's actual inference time** on real
   phone/tablet hardware against the hop-size budget (currently 60ms
   default in `StreamingAudioProcessor`) -- it must run faster than
   that or output falls back to silence (see `StreamingWindowBuffer`
   doc comment).
7. **Wire the real model into `HookConfig.processor`**, replacing
   `PassthroughProcessor()`.
8. Only after 1-7 are working: revisit whether the Tab S7 needs the
   same setup, and whether UI/config (currently everything is
   hardcoded in `HookConfig.kt`, no in-app settings) is worth building.

## Known open questions / risks, not yet resolved

- Exact sync tolerance in practice hasn't been *felt* yet -- the
  ~125ms "broadcast standard" threshold is a reasonable target, not a
  guarantee this will feel right once actually tested.
- Whether a model good enough at the required short context size
  (~120ms) exists and runs fast enough on either device's CPU is
  unconfirmed -- this is the biggest remaining unknown in the whole
  project and should be resolved before investing further in the hook
  module's polish.
- LSPosed's stability/compatibility with current Android versions on
  these specific devices hasn't been tested yet.
