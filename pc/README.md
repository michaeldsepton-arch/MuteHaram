# MuteHaram for PC

Watch YouTube (and other sites) with the **music removed**. A click in your
browser opens the video in the mpv player; its audio is cleaned by an AI model on
your graphics card *ahead of playback*, so picture and sound stay in sync.

```
browser button ──> helper (127.0.0.1) ──> player script ──> mpv window
                                               │                ▲
                                               ▼                │ clean audio over local HTTP
                                  ffmpeg → AI model (GPU) → cleaned audio
```

## Set up a computer (do this once per computer)

Needs: **Windows 10/11**, **Python 3.10+** (python.org; tick "Add python.exe to PATH"),
an **NVIDIA graphics card** (GTX 10-series or newer; ~1.5 GB of video memory) and
internet for the first run. Without an NVIDIA card it still works but is slower than
real time, so the music won't be removed fast enough to watch.

1. Copy/clone this repository to the computer.
2. Double-click **`pc\setup.bat`** (or run `python setup_pc.py` in the `pc` folder).

It installs everything that is missing and skips what is already there, so you can
run it again any time or on a second computer:

| What | Where it goes | Size |
|---|---|---|
| Python packages (`requirements.txt`, incl. PyTorch with the right GPU support) | `pc\.venv` | ~2.5 GB with a GPU |
| AI model weights (TIGER-DnR, Apache-2.0) | `pc\models` | 17 MB |
| mpv player, ffmpeg, yt-dlp (only if not already installed) | `pc\tools` | ~250 MB |

It finishes with a self-test that prints how fast your card is. Anything under
**0.5 s of work per 1 s of audio** is fine (a GTX 1070 does 0.11).

Check what a computer is missing without installing: `python setup_pc.py --check`

## Use it

1. Start the helper: double-click **`start_helper.bat`** and leave it open.
   (Win+R → `shell:startup` and drop a shortcut there to start it automatically.)
2. One-time browser setup.
   Chrome / Edge / Brave: open `chrome://extensions`, switch on **Developer mode**, **Load unpacked**,
   choose the `pc\extension` folder.
   Firefox: open `about:debugging#/runtime/this-firefox`, **Load Temporary Add-on**, pick
   `pc\extension\manifest.json` (must be redone after each Firefox restart).
3. Open a video page. **Press play** (so the browser has seen the stream), then click the
   MuteHaram toolbar button. The page's own video pauses and the player opens.
   - YouTube: works right away.
   - Other sites (CineHD, …): the extension needs to have seen the video's stream
     address, so play the video for a few seconds first. The badge shows
     **OK** (opened), **?** (no stream seen yet) or **ERR** (helper not running).

Without the browser you can also run it directly:

```
.venv\Scripts\python -m muteharam_pc.play "https://www.youtube.com/watch?v=..."
.venv\Scripts\python -m muteharam_pc.play "https://host/path/master.m3u8" --referer "https://site/"
.venv\Scripts\python -m muteharam_pc.play movie.mp4 --start 90 --gain 0.3
```

Useful options: `--gain 0.3` (keep 30% of the music instead of removing all),
`--window 10 --hop 6` (AI context; larger window = better quality, slower),
`--cpu`, `--start SECONDS`, `--header "Cookie: ..."`.

## How it works / limits

* The AI (TIGER-DnR "music" network, trained to split film audio into dialogue, effects
  and music) runs over sliding 10 s windows and subtracts its music estimate from both
  channels. It runs about 8x faster than real time on a GTX 1070, so it stays ahead of
  the picture. After a seek it needs a few seconds to catch up.
* It needs the video's *address*. YouTube is handled through yt-dlp. For other sites the
  extension reads the stream address from the page's network traffic. It cannot work
  with copy-protected (DRM) video, or addresses that expire within seconds.
* Live streams are not supported (it needs to read ahead).
* Subtitles/menus of the website stay in the browser.
* Logs of each run are in `pc\logs`.

## Files

* `setup_pc.py`, `setup.bat`, `requirements.txt` – installer
* `muteharam_pc/engine.py` – decode + AI + cache; `server.py` – serves cleaned audio as a seekable WAV
* `muteharam_pc/play.py` – command-line player launcher; `helper.py` – local service for the extension
* `muteharam_pc/tiger/` – model code from github.com/JusperLee/TIGER (MIT licence, included)
* `extension/` – the browser extension
