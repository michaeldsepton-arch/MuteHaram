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


def is_direct(url):
    return os.path.exists(url) or bool(DIRECT.search(url))


def resolve(url, headers, cfg, log):
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
                  "--ytdl-format=bestvideo[height<=?1080]/bestvideo/best"],
    )


def main(argv=None):
    ap = argparse.ArgumentParser(prog="muteharam_pc.play")
    ap.add_argument("url")
    ap.add_argument("--start", type=float, default=0.0, help="start position in seconds")
    ap.add_argument("--header", action="append", default=[], help='"Name: value" (repeatable)')
    ap.add_argument("--referer")
    ap.add_argument("--user-agent")
    ap.add_argument("--window", type=float, default=10.0, help="AI window, seconds (more = better, slower)")
    ap.add_argument("--hop", type=float, default=6.0, help="seconds produced per window")
    ap.add_argument("--gain", type=float, default=1.0, help="1.0 = remove all music, 0.5 = halve it")
    ap.add_argument("--cpu", action="store_true", help="do not use the GPU")
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

    r = resolve(a.url, headers, cfg, log)
    src = MediaSource(r["media_url"], r["headers"], cfg["ffmpeg"], cfg["ffprobe"])
    session = Session(src, duration=r["duration"], window_s=a.window, hop_s=a.hop, music_gain=a.gain,
                      device="cpu" if a.cpu else None, models_dir=cfg["models_dir"], log=log)
    srv = make_server(session)
    audio_url = f"http://127.0.0.1:{srv.server_address[1]}/audio.wav"
    log("cleaned audio at", audio_url)
    if a.no_player:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return

    cmd = [cfg["mpv"], r["mpv_url"], f"--audio-file={audio_url}",
           f"--script={os.path.join(HERE, 'mpv_select_audio.lua')}",
           "--force-window=immediate", "--network-timeout=120", "--cache=yes",
           "--title=MuteHaram - ${media-title}", *r["mpv_args"], *a.mpv_arg]
    if a.start > 0:
        cmd.append(f"--start={a.start}")
    env = dict(os.environ)
    env["PATH"] = config.TOOLS_DIR + os.pathsep + env.get("PATH", "")
    log("starting mpv ...")
    try:
        subprocess.run(cmd, env=env)
    finally:
        session.close()
        srv.shutdown()


if __name__ == "__main__":
    main()
