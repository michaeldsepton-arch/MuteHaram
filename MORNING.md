# Good morning — what was built overnight and what to test

Everything below is pushed to GitHub and builds green (all APKs + tests). Nothing was
installed or changed on your phone/tablets while you slept.

## Quick summary

1. **PC player** (the mpv one): finished and tested end to end on this PC with a real YouTube
   link (yt-dlp → ffmpeg → AI on the GTX 1070 → mpv playing the cleaned audio). Needs your ears.
2. **Tablet → PC over Wi-Fi**: PC server written and tested (including against the real Kotlin
   client code on the build server). Android side wired in, **not yet run on a tablet**.
3. **On-device Android** (regular APKs): "MuteHaram Engine" app + hook `local` mode written and
   built. **Not yet run on a device.** Only worth trying on the newer tablet.
4. `pc\setup.bat` + `requirements.txt` set up any new computer (tested from an empty folder).

## Listen first (no setup): what the Android pipeline sounds like

`hookesearch\outndroid_demo\` has a 20 s movie clip run through the exact Android pipeline design
(48 kHz stream → fixed delay → AI via the network protocol → subtract): `1_original_delayed.wav` vs
`2_android_pipeline_cleaned.wav`. Measured: 17.5 dB closer to the best-case result than the untouched
original (3.2 dB), 1.3 s delay, no late audio. (The same files are attached to every build on GitHub:
Actions → latest run → "android-pipeline-demo-audio".)

## Test 1 — PC player (10 min, on this PC, nothing else needed)

1. Double-click `pc\start_helper.bat` and leave it open.
2. Chrome/Edge → `chrome://extensions` → Developer mode on → **Load unpacked** → pick `pc\extension`.
3. Open a YouTube video with music, press play, click the MuteHaram toolbar button
   (badge says OK, the page pauses, an mpv window opens after a few seconds).
4. Listen: is the music gone / speech intact? Try seeking (a few seconds' wait is normal).
5. Try CineHD in the browser: play the video for a few seconds first, then click the button.
   Badge `?` = no stream address seen; tell me what you see.

Tell me: quality (music left? speech damaged?), any errors in `pc\logs\`.

## Test 2 — Tablet over Wi-Fi (20 min)

Follow **ANDROID.md → "Mode remote"**. You need the new `hook-debug.apk` and to re-patch the apps
you want to test (or only YouTube first).

## Test 3 — On-device (newer tablet)

1. Install `bench-debug.apk` and `engine-debug.apk` from the latest release; run the **Speed test**
   in the Engine app and send me the result text. Then ANDROID.md → "Mode local".

## Things I could not decide for you

* Which browser: the extension loads in Chrome/Edge/Brave and (temporarily) Firefox; Firefox is untested.
* Default AI window / delay trade-off (quality vs how long the picture waits at the start):
  PC player uses 10 s windows (no wait); Android defaults are 3 s windows ≈ 3.2 s wait on the PC route,
  2 s windows ≈ 2.5 s wait on-device. Change with `muteharam_chunk_ms`.

## If something breaks

* Android: `adb shell settings put global muteharam_mode test` puts the hook back to the behaviour you
  already verified.
* PC: `python setup_pc.py --check` shows what is missing; logs are in `pc\logs`.
