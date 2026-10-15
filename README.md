# MuteHaram

Remove **music** (not speech) from the audio of videos while you watch, using an AI
model (TIGER-DnR, trained to split film sound into dialogue / effects / music).
Personal use, three ways to run it:

| | What you do | Where the AI runs | Guide |
|---|---|---|---|
| **1. PC player** | click a browser button on YouTube / CineHD; the video opens in the mpv player with clean audio | this PC's graphics card | [pc/README.md](pc/README.md) |
| **2. Tablet → PC over Wi-Fi** | patched YouTube / CineHD on the tablet send their audio to the PC and play back the cleaned audio | the PC's graphics card | [ANDROID.md](ANDROID.md) |
| **3. On the device** | same patched apps + the "MuteHaram Engine" app | the tablet/phone's own GPU | [ANDROID.md](ANDROID.md) |

**Start here: [MORNING.md](MORNING.md)** (what to test first, step by step).
Project history, findings and design decisions: [HANDOFF.md](HANDOFF.md).

## How it works (short)

* **PC:** the AI runs ahead of playback (it is ~4x faster than real time on a GTX 1070), so
  nothing needs delaying; mpv plays the original picture with the cleaned audio attached.
* **Android:** apps can't be read ahead, so the hook delays the audio by a fixed ~2-4 s while the
  AI works, and makes the player's audio clock run that far behind so the **picture is delayed by the
  same amount** (verified on YouTube and CineHD). No root is needed: the hook is embedded into the
  apps with LSPatch (see [NOROOT.md](NOROOT.md)).

## Repository map

```
pc/            PC player, Wi-Fi server, browser extension, setup script (python setup_pc.py)
hook/          Android hook module (embedded in YouTube/CineHD with LSPatch): AudioTrack (Java) + AAudio (C++) hooks
engine/        "MuteHaram Engine" Android app: runs the model on the device GPU (LiteRT), serves the hook on localhost
separation/    pure-Kotlin signal processing + real-time pipeline, unit-tested in CI (FFT, STFT, resampler, Wi-Fi client)
bench/         speed-test app for the model on a device's GPU
app/           early audio-capture test app (no longer used)
hook/research/ experiments (model windows, ONNX, the .tflite re-timing tool)
```

Builds: every push to `main` builds all APKs (see [Releases](https://github.com/michaeldsepton-arch/MuteHaram/releases/latest))
and runs the tests, including a Kotlin-vs-real-Python-server integration test.

## Credits / licences

AI model: [TIGER](https://github.com/JusperLee/TIGER) (code MIT, weights Apache-2.0); GPU conversion
by [litert-community](https://huggingface.co/litert-community/TIGER-DnR-LiteRT); ShadowHook (inline hooks),
LSPatch/NPatch (rootless Xposed), mpv, ffmpeg, yt-dlp.
