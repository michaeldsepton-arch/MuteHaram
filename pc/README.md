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

**Badge `?` (no video stream seen).** Press play first and let the video run a few seconds, then click again.
The extension now also catches streams fetched by the page's background workers and streams with unusual
file names, and it tries the page address with yt-dlp as a last resort. When it still finds nothing it saves
`pc\logs\extension-debug-<time>.txt`: the page address and what the page loaded. Open that file to see what
the site does (it may contain addresses with login tokens; delete those parts before sharing it).
The extension keeps the stream address in the browser's session storage, so it is still known when you click
minutes after the video started (browsers stop and restart extension scripts, which used to make it forget).
After updating, reload the extension on the browser's extensions page.

**Sites that scramble their playlists are not supported.** Some sites encrypt the playlists of their videos so that
only their own web page can play them; no ordinary player (mpv, ffmpeg, VLC) can read those. MuteHaram detects this
(the playlist starts with random-looking text instead of `#EXTM3U`) and says so in a message box. YouTube and sites
with ordinary streams (like CineHD) work. The player log (`pc\logs\play-*.log`) shows a `candidate ...` line for every
stream address the page used and what each one really returns.

**Settings (optional).** Copy `settings.example.json` to `settings.json` in the `pc` folder and edit it; every
run (including the browser button) uses it. The useful ones:
- `"gain": 1.4` removes the music harder (more music gone, a little more risk to voices); `1.0` is the default,
  `0.5` keeps half of it.
- `"mode": "speech"` keeps ONLY voices and drops sound effects and music (also drops singing).
- `"sub_langs"`: which YouTube caption languages to load (`"all"` for everything).
- `"pip": true` always opens the small corner window.

**Captions.** YouTube captions (uploaded and automatic) are loaded and the first language in
`en, ar` is shown automatically; use the player's subtitle button to switch or turn them off. On other sites,
captions inside the stream are picked up by the player, and caption files the browser extension sees
(`.vtt/.srt`) are loaded too.

**Player controls.** The player uses the free *uosc* add-on (installed by `setup.bat`): move the mouse
anywhere over the video and the controls appear (with a tooltip when you hover a button); click the
picture to pause/resume; double-click for fullscreen; right-click for the menu (audio tracks, speed ...);
mouse wheel for volume. If it says "uosc: MISSING" in `python setup_pc.py --check`, run `setup.bat` again.

**If you hear music or nothing:** the player's audio track button switches between *our* cleaned track and the
video's own sound. When MuteHaram cannot read a video's audio it now says why on screen and plays the original
sound; the same reason is in the newest file in `pc\logs` (search for `PLAYING ORIGINAL AUDIO`).

**Small corner window (picture-in-picture style):** right-click the MuteHaram toolbar icon and choose
*Open in a small always-on-top window*, or press **P** in the player to toggle it at any time (small,
no border, always on top, bottom-right). Drag the picture to move it; press **P** again for a normal,
resizable window. (`T` toggles always-on-top by itself.) Command line: add `--pip`. New in this version
and not yet checked on a screen: tell me if the position or size looks wrong.

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

## Live mode: clean the sound inside the page you are watching

For sites the player cannot open (for example sites that scramble their video
addresses), the extension can clean the page's own sound while it plays.

1. Keep the helper running (`start_helper.bat`).
2. On the video page, **right-click the MuteHaram toolbar icon** and choose
   **"Clean the sound inside this page (live, about 3 s late)"**. The badge shows LIVE.
   The first time, the helper starts the cleaner on your graphics card (a few seconds).
3. The sound is cleaned about 3 seconds late, and the picture is held back by the same
   amount so lips and voice still match. Seeking or skipping makes it restart.
4. Click the menu item again to turn it off.

Needs the graphics card (about real-time speed). If it fails, see `pc/logs/live-server.log`.
Picture-in-picture windows show the original picture, not the delayed one.
Test: `python tests/test_live_server.py`.

If voice and picture are not lined up in live mode, click the video once, then press
**Alt + .** (picture later) or **Alt + ,** (picture earlier) in 40 ms steps; hold **Shift** as well
for 200 ms steps. The page shows the current offset and remembers it.
