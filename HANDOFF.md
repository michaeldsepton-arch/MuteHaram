# Project Handoff / Status

Living document — update this whenever something meaningfully changes,
so work can resume cleanly from a different session/tool (e.g. picking
this up in Claude Code on your own machine, with full internet access
this sandbox doesn't have).

**Last updated:** 2026-10-14, build-6

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
