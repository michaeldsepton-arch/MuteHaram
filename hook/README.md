# MuteHaram Hook — LSPosed module

This is the piece that solves the "mute the original, stay in sync"
problem discussed in chat: instead of capturing audio *after* it's
been mixed to the speaker (which gave no way to silence the original),
this hooks `AudioTrack.write(...)` directly **inside the target app's
own process**, before the audio is written at all. Whatever this hook
returns from `AudioProcessor.process(...)` is literally what plays —
there's no original to compete with, and no extra delay to desync
from video, because it runs synchronously in the app's own audio
callback.

**Requires root + Magisk + LSPosed/Zygisk.** This cannot work without
them — Android has no non-root API for one app to hook another's
method calls. See the chat history / main README for the rooting
walkthrough.

## What's here right now

- `AudioTrackHook.kt` — hooks three common `AudioTrack.write()`
  overloads (`short[]`, `byte[]`, `ByteBuffer`) in whichever apps are
  listed in `HookConfig.TARGET_PACKAGES`, and routes the PCM buffer
  through `HookConfig.processor` before it's actually written.
- `AudioProcessor.kt` — the interface the real separation model will
  implement. Currently wired to `PassthroughProcessor`, which does
  nothing, on purpose: the first goal is proving the hook itself fires
  and doesn't break playback, before any processing cost is added.

## Setup once you have root

1. Install Magisk, then install **LSPosed** through it (as a Magisk/
   Zygisk module — see LSPosed's own install instructions, it changes
   over time).
2. Reboot, open the **LSPosed Manager** app.
3. Install this module's APK (from the same GitHub Release as the
   main app) like any normal APK.
4. In LSPosed Manager, find "MuteHaram Hook" in the modules list,
   enable it, and tap into it to **scope it to YouTube and CineHD
   specifically** — LSPosed modules do nothing until you explicitly
   check which target apps they're allowed to hook.
5. Reboot (LSPosed usually needs this after scoping changes, at least
   for the targeted apps to pick it up — force-stopping just the
   target app is sometimes enough, but reboot first to rule out stale
   state if nothing happens).
6. Before testing: open `hook/src/main/java/com/muteharam/hook/HookConfig.kt`
   and replace the CineHD placeholder package name
   (`com.cinehd.app`) with its real one. Find it via:
   `adb shell pm list packages | grep -i cine`

## How to check it's actually hooking

```
adb logcat | grep MuteHaramHook
```

Open YouTube or CineHD and play something. You should see lines like:

```
Hooking AudioTrack in com.google.android.youtube
Hooked write(ByteBuffer, int, int)
```

If you see "Hooking AudioTrack in ..." but none of the "Hooked write(...)"
lines, LSPosed found the app but the specific overload(s) that app
actually calls weren't matched — that's useful signal for which
overload to focus on, not a dead end.

If you see nothing at all, start with the basics: is the module
enabled *and* scoped to that exact package in LSPosed Manager, did you
reboot after scoping, and does `adb shell pm list packages` confirm
the package name in `HookConfig` is exactly right.

## Next step once hooking is confirmed working

Swap `PassthroughProcessor` for a real implementation backed by a
benchmarked separation model (see the main project README/chat for
the model research — likely a small ONNX export run via ONNX Runtime
Mobile). Benchmark its per-chunk latency in isolation first; only wire
it into this hook once you know it's fast enough not to stutter audio.
