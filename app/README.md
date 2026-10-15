# MuteHaram Capture Test (the `app/` module)

A minimal Android app to empirically test whether `AudioPlaybackCapture`
(Android 10+) can actually get audio signal from specific apps on your
device — e.g. YouTube / ReVanced Extended, CineHD — before investing in
building the real music-filtering pipeline on top of it.

**This app does not filter or separate anything yet.** It just captures
whatever's currently playing on the device and shows a live dB level, so
you can see per-app/per-content whether capture is blocked (DRM, or the
app opted out) or working (you'll see the level move with the audio).

## How it works

- `MainActivity` requests `RECORD_AUDIO` permission, then launches the
  system screen-capture consent dialog (`MediaProjectionManager`) — this
  is required by Android for any audio playback capture, even though
  we're not actually capturing video.
- On consent, `AudioCaptureService` (a foreground service, required by
  Android for `MediaProjection` use) builds an `AudioRecord` configured
  with `AudioPlaybackCaptureConfiguration`, matching `USAGE_MEDIA`,
  `USAGE_GAME`, and `USAGE_UNKNOWN` audio streams — i.e. "whatever's
  playing on the device right now," not a specific app.
- It computes a simple RMS/dB level from the captured PCM buffer and
  reports it back to the UI live.

## How to test

1. Open this project in Android Studio (`File > Open`, pick this folder).
   Let it sync — it'll use Android Studio's bundled Gradle, no wrapper
   jar needed.
2. Build & run on your phone (USB debugging on, or build an APK and
   sideload it).
3. Tap **Start Capture**, grant the mic permission, then accept the
   screen-capture consent dialog (Android requires this for playback
   capture — you'll see a persistent notification while it's running).
4. Switch to the app you want to test (YouTube/ReVanced, CineHD, etc.)
   and start playing something.
5. **If the app you're testing has no picture-in-picture mode** (CineHD,
   for example), you won't be able to see MuteHaram's screen while it
   plays — pull down the notification shade instead. The persistent
   notification updates live with the current level and also tracks
   whether it *ever* saw signal this session, so a quick swipe-down and
   swipe-back-up is enough to check without interrupting playback:
   - `Now: SIGNAL (-22 dB)` / `Now: silence`
   - `Ever detected signal this session: yes, peak -18 dB` / `not yet`
6. Reading it:
   - **Any "SIGNAL" reading, or "yes" on the peak summary** → capture is
     working for that content. You're clear to build the real pipeline
     against it.
   - **Stays on "silence" / "not yet" for the whole time audio is
     clearly playing** → that stream is blocked, either DRM-protected
     output (not overridable without root) or the app explicitly set
     `allowAudioPlaybackCapture="false"`.

Test both apps separately — results can differ by app, and even within
an app by content type (e.g. YouTube ad-supported video vs. a YouTube
Movies/TV rental, which may be DRM-wrapped differently).

## Known limitations of this harness

- Capture session requires that system consent dialog every time you
  start it — this is enforced by Android (same mechanism as screen
  recording), not something this app can bypass.
- No filtering/separation yet — this only answers "can I get the bytes."
- `minSdk` is 29 (Android 10) since `AudioPlaybackCapture` doesn't exist
  below that.

## Next steps (once capture is confirmed working)

If a target app passes this test, the next piece is swapping the RMS
probe in `AudioCaptureService` for an actual source-separation model
(e.g. a Demucs/BS-Roformer export run via ONNX Runtime for Android) fed
by a few seconds of lookahead buffer, with the music stem dropped and
the rest played back through the app's own `AudioTrack`.
