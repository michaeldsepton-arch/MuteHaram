"""
Open a video in mpv with the music removed.

    python -m muteharam_pc.play "https://www.youtube.com/watch?v=..."
    python -m muteharam_pc.play "https://site/video/master.m3u8" --referer "https://site/"
    python -m muteharam_pc.play movie.mp4 --start 90

For direct links that need cookies / headers use  --header "Name: value"  (repeatable).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

from . import config
from .engine import MediaSource, Session
from .server import make_server

HERE = os.path.dirname(os.path.abspath(__file__))
DIRECT = re.compile(r"\.(m3u8|mpd|mp4|m4v|webm|mkv|mov|ts|mp3|m4a|flac|ogg|opus)(\?|#|$)", re.I)


def ui_error(message):
    """Tell the user about a problem even when we were started without a console
    (by the browser extension's helper): a message box on Windows."""
    print("ERROR:", message, flush=True)
    if os.name == "nt" and not (sys.stdout and sys.stdout.isatty()):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, "MuteHaram", 0x10)
        except Exception:
            pass


def is_direct(url):
    return os.path.exists(url) or bool(DIRECT.search(url))


def mpv_list_value(v):
    """mpv separates key=value pairs with commas; quote a value that contains commas as %LEN%value."""
    return f"%{len(v)}%{v}"


def resolve(url, headers, cfg, log, sub_langs="en.*,ar.*"):
    """-> dict(media_url, headers, duration, mpv_url, mpv_args)"""
    if is_direct(url):
        mpv_args = []
        hdrs = dict(headers)
        ua = next((hdrs.pop(k) for k in list(hdrs) if k.lower() == "user-agent"), None)
        ref = next((hdrs.pop(k) for k in list(hdrs) if k.lower() == "referer"), None)
        if ua:
            mpv_args.append(f"--user-agent={ua}")
        if ref:
            mpv_args.append(f"--referrer={ref}")
        if hdrs:
            mpv_args.append("--http-header-fields=" + ",".join(f"{k}: {v}".replace(",", "%2C") for k, v in hdrs.items()))
        return dict(media_url=url, headers=headers, duration=None, mpv_url=url, mpv_args=mpv_args)
    if not cfg["yt_dlp"]:
        raise SystemExit("yt-dlp is needed for page links. Run  python setup_pc.py")
    log("resolving with yt-dlp ...")
    out = subprocess.run([cfg["yt_dlp"], "-J", "--no-playlist", "-f", "bestaudio/best", url],
                         capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        raise SystemExit("yt-dlp could not open that link:\n" + out.stderr[-800:])
    info = json.loads(out.stdout)
    return dict(
        media_url=info["url"], headers=info.get("http_headers", {}), duration=info.get("duration"),
        mpv_url=url,
        mpv_args=[f"--script-opts=ytdl_hook-ytdl_path={cfg['yt_dlp']}",
                  # picture only: the cleaned audio is attached separately
                  "--ytdl-format=bestvideo[height<=?1080]/bestvideo/best",
                  # keep the video's captions (uploaded ones and YouTube's automatic ones)
                  "--ytdl-raw-options=write-subs=,write-auto-subs=,sub-langs=" + mpv_list_value(sub_langs)],
    )


def main(argv=None):
    try:
        run(argv)
    except SystemExit as e:
        if isinstance(e.code, str):
            ui_error(e.code)
            sys.exit(1)
        raise
    except KeyboardInterrupt:
        pass
    except Exception as e:  # anything unexpected: say so instead of dying silently
        import traceback
        traceback.print_exc()
        nl = chr(10)
        ui_error("MuteHaram hit an unexpected problem:" + nl + nl + repr(e) + nl + nl +
                 "Details are in the pc/logs folder.")
        sys.exit(1)


def run(argv=None):
    S = config.settings()
    ap = argparse.ArgumentParser(prog="muteharam_pc.play")
    ap.add_argument("url")
    ap.add_argument("--start", type=float, default=0.0, help="start position in seconds")
    ap.add_argument("--header", action="append", default=[], help='"Name: value" (repeatable)')
    ap.add_argument("--referer")
    ap.add_argument("--user-agent")
    ap.add_argument("--window", type=float, default=S["window"], help="AI window, seconds (more = better, slower)")
    ap.add_argument("--hop", type=float, default=S["hop"], help="seconds produced per window")
    ap.add_argument("--gain", type=float, default=S["gain"], help="1.0 = remove fully, 0.5 = halve it, 1.3 = push harder")
    ap.add_argument("--mode", choices=["music", "speech"], default=S["mode"],
                    help='"music" removes the music; "speech" keeps only voices (also drops sound effects)')
    ap.add_argument("--pip", action="store_true", default=bool(S["pip"]),
                    help="start in a small always-on-top corner window (press P in the player to toggle)")
    ap.add_argument("--sub-langs", default=S["sub_langs"],
                    help='caption languages to fetch for YouTube, yt-dlp style, e.g. "en.*,ar.*" or "all"')
    ap.add_argument("--page", help="address of the web page (fallback: yt-dlp tries to find the stream itself)")
    ap.add_argument("--sub", action="append", default=[], metavar="URL",
                    help="caption file (.vtt/.srt/...) to load (repeatable)")
    ap.add_argument("--cpu", action="store_true", help="do not use the GPU")
    ap.add_argument("--channels", choices=["auto", "mono", "stereo"], default=S["channels"],
                    help="stereo = clean left and right separately (needs a GPU); mono = clean the mix once")
    ap.add_argument("--no-player", action="store_true", help="only serve the audio (prints its address)")
    ap.add_argument("--mpv-arg", action="append", default=[], help="extra argument for mpv (repeatable)")
    a = ap.parse_args(argv)
    log = lambda *m: print(*m, flush=True)

    cfg = config.load()
    headers = {}
    for h in a.header:
        k, _, v = h.partition(":")
        headers[k.strip()] = v.strip()
    if a.referer:
        headers["Referer"] = a.referer
    if a.user_agent:
        headers["User-Agent"] = a.user_agent

    r = resolve(a.url, headers, cfg, log, a.sub_langs)
    session = srv = None
    problem = None
    audio_url = None

    def build(rr):
        """Open the media for the AI and check that audio can really be decoded."""
        src = MediaSource(rr["media_url"], rr["headers"], cfg["ffmpeg"], cfg["ffprobe"])
        sess = Session(src, duration=rr["duration"], window_s=a.window, hop_s=a.hop, music_gain=a.gain,
                       device="cpu" if a.cpu else None, models_dir=cfg["models_dir"], log=log,
                       channels_mode=a.channels, mode=a.mode)
        try:
            ok, why = src.probe(max(0.0, min(a.start, (rr["duration"] or 1e9) - 3)))
            if not ok:
                raise RuntimeError(why)
        except Exception:
            sess.close()
            raise
        return sess

    attempts = [("the stream address", lambda: r)]
    if a.page and is_direct(a.url):
        attempts.append(("the web page (yt-dlp)", lambda: resolve(a.page, headers, cfg, log, a.sub_langs)))
    errors = []
    for label, make in attempts:
        try:
            session = build(make())
            problem = None
            break
        except Exception as ex:
            import traceback
            traceback.print_exc()
            errors.append(f"{label}: {ex}")
            problem = RuntimeError(" ; ".join(errors))
    if session is not None:
        srv = make_server(session)
        audio_url = f"http://127.0.0.1:{srv.server_address[1]}/audio.wav"
        log("cleaned audio at", audio_url)
    if a.no_player:
        if problem:
            raise SystemExit(f"engine failed: {problem!r}")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return

    cmd = [cfg["mpv"], r["mpv_url"]]
    if problem is None:
        cmd += [f"--audio-file={audio_url}", f"--script={os.path.join(HERE, 'mpv_select_audio.lua')}"]
    else:
        # The AI engine could not start (no GPU driver? model missing?): still play the video.
        why = " ".join(str(problem).split())[:160]
        cmd += ["--osd-playing-msg=MuteHaram could not clean this video, so you hear the ORIGINAL audio. "
                "Reason: " + why, "--osd-duration=12000"]
        log("PLAYING ORIGINAL AUDIO because:", problem)
        if r["mpv_args"]:
            # a YouTube link was resolved as video-only for our audio track: ask for sound again
            r["mpv_args"] = [x for x in r["mpv_args"] if not x.startswith("--ytdl-format")]
    cmd += [f"--script={os.path.join(HERE, 'mpv_pip.lua')}"]
    if a.pip:
        # Opens small, frameless, always on top, bottom-right of the screen.
        cmd += ["--script-opts=muteharam-pip=yes", "--ontop", "--no-border", "--autofit=25%",
                "--geometry=98%:94%", "--keep-open=no"]
    cfg_dir = os.path.join(config.PC_DIR, "mpv_config")
    if os.path.exists(os.path.join(cfg_dir, "scripts", "uosc", "main.lua")):
        cmd += [f"--config-dir={cfg_dir}"]   # uosc controls, click-to-pause, our key bindings
    else:
        log("uosc is not installed (run setup_pc.py): using mpv's plain controls")
    for sub in a.sub:
        cmd.append(f"--sub-file={sub}")
    cmd += ["--force-window=immediate", "--network-timeout=120", "--cache=yes",
            "--title=MuteHaram - ${media-title}", *r["mpv_args"], *a.mpv_arg]
    if a.start > 0:
        cmd.append(f"--start={a.start}")
    env = dict(os.environ)
    env["PATH"] = config.TOOLS_DIR + os.pathsep + env.get("PATH", "")
    log("starting mpv ...")
    try:
        subprocess.run(cmd, env=env)
    finally:
        if session:
            session.close()
        if srv:
            srv.shutdown()


if __name__ == "__main__":
    main()
