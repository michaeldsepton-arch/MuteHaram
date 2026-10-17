# Android: music removal modes

The hook inside your patched YouTube / CineHD has three live modes, switched with adb
(no re-patching): `muteharam_mode` = `test` (default) | `remote` | `local`.

```
adb shell settings put global muteharam_mode test      # default: the delay/sync experiment, as before
adb shell settings put global muteharam_mode remote    # AI on a PC over Wi-Fi
adb shell settings put global muteharam_mode local     # AI on this device (MuteHaram Engine app)
```

Always set the mode **before starting a video**, and restart the video after changing it.
What you will notice: the picture waits a few seconds at the start (and after a seek) —
that is the AI's head start, and it is the same delay applied to the picture, so sound and
picture stay together. Total delay is about 2-4 s.

## Before any of this: update the hook

The hook changed a lot. Install the newest `hook-debug.apk` from the
[latest release](https://github.com/michaeldsepton-arch/MuteHaram/releases/latest), then re-patch
YouTube and CineHD with it (steps in [NOROOT.md](NOROOT.md), Part 2-3). Uninstalling the patched
app erases its data/logins, as before. `test` mode behaves exactly like the previous build.

## Mode `remote`: the AI on your PC (works with any tablet)

1. On the PC (once): `pc\setup.bat`. Each time: double-click `pc\start_wifi_server.bat`.
   If Windows asks about the firewall, allow **private** networks. The window prints the exact
   adb commands for you (address, token).
2. Tablet and PC must be on the same Wi-Fi. Then:
   ```
   adb shell settings put global muteharam_server 192.168.x.x:47801
   adb shell settings put global muteharam_token  <the token it printed>
   adb shell settings put global muteharam_mode   remote
   ```
3. Start a video. In `adb logcat -s MuteHaramHook` you should see
   `separation stream ...: remote ... latency 3xxx ms` and, every 5 s, `blocks ok=N failed=0`.
   The log line now says `stereo` (left and right are cleaned separately, like the PC player does) or
   `mono mix` (an older PC server, or `muteharam_stereo 0`). Stereo needs the **new PC server**
   (update `pc\muteharam_pc\stream_server.py`) AND a hook built after that change (re-patch). The default
   `budget_ms` for stereo is 1400 (about 4 s total delay), because the PC cleans two channels per window.
   If the music is still audible, check that the PC window prints `(2 ch, ...)` lines.
4. If the PC is off or the Wi-Fi drops, the video keeps playing with the ORIGINAL audio (music
   included) until it reconnects. Nothing freezes.

## Mode `local`: the AI on the device itself

Needs a device with a fast GPU (a Pixel 9 Pro needs ~0.75 s of compute per 1 s window; the
Galaxy Tab S7 FE is 4x slower and cannot do it).

1. Install `engine-debug.apk` (release page), open **MuteHaram Engine**.
2. Tap **Speed test** first. It says "FAST ENOUGH", "tight" or "TOO SLOW". If too slow, choose a
   longer window (buttons 1.5 / 2.0 / 3.0 s) and test again.
3. Tap **Start engine** and wait for `ready` (the first start compiles the model; up to ~30 s).
   Leave the app running (a notification shows).
4. `adb shell settings put global muteharam_mode local`, start a video.

## Tuning (all live)

```
adb shell settings put global muteharam_chunk_ms  3000   # AI window; local must match the engine (2032 default)
adb shell settings put global muteharam_margin_ms 500    # context kept on each side of a block
adb shell settings put global muteharam_budget_ms 700    # time the AI may take per block (local default 1100)
adb shell settings put global muteharam_gain      100    # % of the music to remove (0 = none)
```
* Music still audible in bursts → the AI was late (see `late-frames` in the log): raise `budget_ms`
  (more delay, more safety) or use a longer chunk.
* Players write audio ahead of time, which shortens the time the AI really has; raise `budget_ms`
  by a few hundred ms if `late-frames` keeps growing.
* The start-of-video wait is `latency` in the log; a longer chunk = better quality but longer wait.

## Logs and troubleshooting

`adb logcat -s MuteHaramHook` (the hook) and `adb logcat -s MuteEngine` (engine app).

| You see | Meaning |
|---|---|
| `remote mode needs: ... muteharam_server` | set the server address |
| `server refused the token` | wrong `muteharam_token` |
| `failed=N` growing, `waiting before reconnecting` | PC not reachable (firewall? different Wi-Fi? server not running?) |
| engine log: `window of N samples, engine needs M` | set `muteharam_chunk_ms` to the value it prints |
| no `separation stream` line at all | mode not set to remote/local, or the app uses a path that isn't hooked |
| sound silent for the first seconds | normal: the fixed delay |

Turn everything off: `adb shell settings put global muteharam_mode test` and
`adb shell settings put global muteharam_delay_ms 0`.

## What is not verified yet (nothing here has run on a device)

Built and unit-tested on the build server (pipeline logic, network protocol against the real
Python server, JNI/native compile) but never run on a phone: the Java/AAudio paths into the pipeline,
the Engine app, and how Chromium's clock reacts when the delay is ~3 s instead of 1.5 s.
