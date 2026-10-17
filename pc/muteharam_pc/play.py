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


def clean_headers(headers):
    """Only the headers a browser would send for a media request."""
    keep = {"user-agent", "referer", "origin", "cookie", "authorization", "accept", "accept-language"}
    return {k: v for k, v in (headers or {}).items() if k.lower() in keep}


def fetch_head(url, headers, nbytes=4096, timeout=12):
    """(status, content_type, first bytes, response headers) of a URL, fetched the way the player would.
    Never raises."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={**{"User-Agent": "Mozilla/5.0"}, **clean_headers(headers)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read(nbytes), dict(resp.headers.items())
    except urllib.error.HTTPError as e:
        try:
            body = e.read(300)
        except Exception:
            body = b""
        return e.code, (e.headers.get("Content-Type", "") if e.headers else ""), body, dict(e.headers.items() if e.headers else [])
    except Exception as e:
        return None, "", str(e).encode(), {}


def classify(url, headers):
    """Look at what the server really returns for a stream address.
    -> dict(kind, note): kind is master / media / video / http_error / web_page / unreadable / other."""
    import base64
    import gzip
    import re
    from urllib.parse import urljoin
    status, ctype, data, rh = fetch_head(url, headers)
    if status is None:
        return dict(kind="unreadable", note="could not connect (" + data.decode("utf-8", "replace")[:80] + ")")
    if status >= 400:
        return dict(kind="http_error", note=f"the server answered {status}")
    body = data
    extra = ""
    if body[:2] == b"\x1f\x8b":                       # gzip-compressed body
        try:
            body = gzip.GzipFile(fileobj=__import__("io").BytesIO(body)).read(4096)
            extra = " (the playlist arrives gzip-compressed)"
        except Exception:
            pass
    stripped = body.lstrip(b"\xef\xbb\xbf \t\r\n")    # ignore a byte-order mark / blank lines
    if stripped != body.lstrip() or body[:3] == b"\xef\xbb\xbf":
        extra += " (it starts with an invisible marker, which strict readers reject)"
    if not stripped.startswith(b"#EXTM3U") and re.fullmatch(rb"[A-Za-z0-9+/=\r\n]{16,}", stripped[:400] or b"-"):
        try:
            dec = base64.b64decode(stripped[: len(stripped) // 4 * 4])
            if dec.lstrip().startswith(b"#EXTM3U"):
                return dict(kind="other", note="the playlist is BASE64-ENCODED (hidden from ordinary players)")
        except Exception:
            pass
    if stripped.startswith(b"#EXTM3U"):
        text = stripped.decode("utf-8", "replace")
        if "#EXT-X-STREAM-INF" in text:
            return dict(kind="master", note="main playlist" + extra)
        # a playlist of video/audio pieces: check what the first piece looks like
        first = next((ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")), None)
        note = "playlist" + extra
        if first:
            st, ct, seg, _ = fetch_head(urljoin(url, first), headers, nbytes=16)
            if st is None or st >= 400:
                note = f"playlist, but its first piece is not available ({'error ' + str(st) if st else 'no connection'})"
            elif seg[:1] == b"G" or seg[4:8] in (b"ftyp", b"styp", b"moof") or seg[:3] == b"ID3" or seg[:2] == b"\xff\xf1":
                note = "playlist with normal pieces" + extra
            elif seg[:4] == b"\x89PNG" or seg[:2] == b"\xff\xd8" or seg[:3] == b"GIF":
                note = ("playlist whose video pieces are DISGUISED AS PICTURES (the site hides its video "
                        "from ordinary players)")
            elif seg[:1] == b"<":
                note = "playlist whose first piece is a web page, not video (blocked or login needed?)"
        return dict(kind="media", note=note)
    if stripped[:1] == b"<":
        return dict(kind="web_page", note="the address returns a web page, not video")
    if "video" in ctype.lower() or body[4:8] in (b"ftyp",) or body[:4] == b"\x1aE\xdf\xa3":
        return dict(kind="video", note="video file")
    enc = rh.get("Content-Encoding") or rh.get("content-encoding") or "none"
    head = stripped[:120]
    readable_text = len(head) >= 8 and b" " in head and all(32 <= c < 127 or c in (9, 10, 13) for c in head)
    if stripped[:1] in (b"{", b"[") or readable_text:
        # readable text / JSON: an error message or similar, not scrambled data
        return dict(kind="other", note=f"the server returned text, not a playlist: {stripped[:90]!r}")
    if any(k in ctype.lower() for k in ("mpegurl", "m3u")) or url.split("?")[0].lower().endswith(".m3u8"):
        # claims to be a playlist but is not readable as one: encrypted / scrambled by the site
        return dict(kind="scrambled",
                    note=f"the site SCRAMBLES this playlist (it starts with {body[:24]!r}); only that site's own "
                         f"web page can read it")
    return dict(kind="other", note=f"unrecognised content (type {ctype or 'none'}, encoding {enc}); "
                                   f"it starts with {body[:48]!r}{extra}")


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


AUDIO_ONLY_RE = re.compile(r"([?&]type=audio\b|/audio[/_.-]|[_-]audio[_.-]|audio[_-]only)", re.I)


def looks_audio_only(url):
    return bool(AUDIO_ONLY_RE.search(url))


FORCE_DIRECT = set()      # addresses the extension saw being played: streams, whatever their name looks like


def is_direct(url):
    return url in FORCE_DIRECT or os.path.exists(url) or bool(DIRECT.search(url))


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
    log(f"yt-dlp chose: site {info.get('extractor_key')}, format {info.get('format_id')} "
        f"(audio codec {info.get('acodec')}, video codec {info.get('vcodec')}, protocol {info.get('protocol')})")
    if info.get("acodec") == "none":
        # the chosen format has a picture but no sound: ask for one that has sound
        out2 = subprocess.run([cfg["yt_dlp"], "-J", "--no-playlist", "-f", "ba*[acodec!=none]/b*[acodec!=none]/ba*", url],
                              capture_output=True, text=True, timeout=180)
        if out2.returncode == 0:
            info2 = json.loads(out2.stdout)
            if info2.get("url") and info2.get("acodec") != "none":
                info = info2
                log(f"no sound in that one, using format {info.get('format_id')} ({info.get('acodec')}) instead")
    return dict(
        media_url=info["url"], headers=info.get("http_headers", {}), duration=info.get("duration"),
        mpv_url=url,
        mpv_args=[f"--script-opts=ytdl_hook-ytdl_path={cfg['yt_dlp']}",
                  # picture only: the cleaned audio is attached separately
                  "--ytdl-format=bestvideo[height<=?1080]/bestvideo/best",
                  # keep the video's captions (uploaded ones and YouTube's automatic ones)
                  "--ytdl-raw-options=write-subs=,write-auto-subs=,sub-langs=" + mpv_list_value(sub_langs)],
    )


def resolve_video(url, cfg, log, sub_langs):
    """A page address (YouTube ...) -> a direct picture address the tablet can play: (url, headers, subs)."""
    log("finding the picture address with yt-dlp ...")
    out = subprocess.run([cfg["yt_dlp"], "-J", "--no-playlist", "-f",
                          "bv*[height<=?1080][vcodec^=avc1]/bv*[height<=?1080]/b[height<=?1080]/b", url],
                         capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        raise SystemExit("yt-dlp could not open that link:" + chr(10) + out.stderr[-800:])
    info = json.loads(out.stdout)
    subs = []
    pats = [p for p in str(sub_langs or "").split(",") if p]
    for table in (info.get("subtitles") or {}, ):
        for lang, entries in table.items():
            if any(re.fullmatch(pt, lang) for pt in pats) or "all" in pats:
                vtt = next((e for e in entries if e.get("ext") == "vtt"), None)
                if vtt and vtt.get("url"):
                    subs.append({"url": vtt["url"], "lang": lang})
    return info["url"], info.get("http_headers", {}), subs, info.get("title")


def tablet_kind(url, note_kind):
    u = url.lower().split("?")[0]
    if u.endswith(".m3u8") or note_kind in ("master", "media"):
        return "hls"
    if u.endswith(".mpd"):
        return "dash"
    return "progressive"


def run_tablet(a, cfg, r, notes, session, problem, headers, log):
    """Offer the picture address + the cleaned audio to the tablet app (instead of opening mpv)."""
    import socket as _socket
    from .stream_server import load_or_create_token, lan_addresses
    if session is None:
        raise SystemExit("The music could not be removed, so nothing is sent to the tablet: " + str(problem))
    token = load_or_create_token()
    title = None
    if is_direct(a.url):
        video, vheaders, subs = r["mpv_url"], dict(r["headers"] or {}), [{"url": u, "lang": ""} for u in a.sub]
        kind = tablet_kind(video, (notes.get(video) or {}).get("kind"))
    else:
        video, vheaders, subs, title = resolve_video(a.url, cfg, log, a.sub_langs)
        subs += [{"url": u, "lang": ""} for u in a.sub]
        kind = tablet_kind(video, None)
    doc = {"id": int(time.time() * 1000), "title": title or "", "video": video, "kind": kind,
           "headers": clean_headers(vheaders),
           "subs": subs, "start": max(0.0, a.start), "duration": session.duration}
    srv = make_server(session, host="0.0.0.0", port=a.tablet_port, token=token,
                      routes={"/session.json": lambda: doc})
    log(f"tablet session ready: {kind} picture, cleaned audio on port {a.tablet_port}")
    log("In the MuteHaram tablet app use one of these addresses:", ", ".join(lan_addresses()) or "(this computer's address)")
    log("Waiting for the tablet. (This window closes after 30 minutes without the tablet asking.)")
    try:
        while time.time() - srv.last_request < 1800 or time.time() - doc["id"] / 1000 < 120:
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        session.close()


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
    ap.add_argument("--direct", action="store_true",
                    help="the addresses given are video streams (not web pages), whatever they look like")
    ap.add_argument("--pip", action="store_true", default=bool(S["pip"]),
                    help="start in a small always-on-top corner window (press P in the player to toggle)")
    ap.add_argument("--sub-langs", default=S["sub_langs"],
                    help='caption languages to fetch for YouTube, yt-dlp style, e.g. "en.*,ar.*" or "all"')
    ap.add_argument("--audio-src", metavar="URL",
                    help="audio-only stream the viewer chose (e.g. English); the music is removed from this, "
                         "the picture comes from the main address")
    ap.add_argument("--alt", action="append", default=[], metavar="URL",
                    help="other stream addresses the page used (the best one is chosen)")
    ap.add_argument("--page", help="address of the web page (fallback: yt-dlp tries to find the stream itself)")
    ap.add_argument("--sub", action="append", default=[], metavar="URL",
                    help="caption file (.vtt/.srt/...) to load (repeatable)")
    ap.add_argument("--cpu", action="store_true", help="do not use the GPU")
    ap.add_argument("--channels", choices=["auto", "mono", "stereo"], default=S["channels"],
                    help="stereo = clean left and right separately (needs a GPU); mono = clean the mix once")
    ap.add_argument("--tablet", action="store_true",
                    help="do not open mpv: offer the video and the cleaned audio to the MuteHaram tablet app")
    ap.add_argument("--tablet-port", type=int, default=47804)
    ap.add_argument("--no-player", action="store_true", help="only serve the audio (prints its address)")
    ap.add_argument("--mpv-arg", action="append", default=[], help="extra argument for mpv (repeatable)")
    a = ap.parse_args(argv)
    if a.direct:
        FORCE_DIRECT.update([a.url, a.audio_src, *a.alt])
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

    # Look at every candidate address and try the most promising first: a main ("master") playlist
    # beats a media playlist (e.g. audio-only) beats anything else.
    notes = {}
    order = [a.url]
    if is_direct(a.url) and a.url.startswith(("http://", "https://")):
        order = [a.url] + [u for u in a.alt if u != a.url][:5]
        rank = {"master": 0, "video": 1, "media": 2}
        for u in order:
            notes[u] = classify(u, headers)
            log(f"candidate {u[:110]}: {notes[u]['kind']} - {notes[u]['note']}")
        order.sort(key=lambda u: rank.get(notes[u]["kind"], 9))
    if notes and all(n["kind"] == "scrambled" for n in notes.values()):
        raise SystemExit(
            "This website scrambles (encrypts) its video playlists, so that only its own web page can play "
            "them. MuteHaram's player cannot read them, and this site is not supported." + chr(10) + chr(10) +
            "Sites that use ordinary streams (like YouTube and CineHD) do work.")
    attempts = []
    if a.audio_src and a.audio_src.startswith(("http://", "https://")):
        notes[a.audio_src] = classify(a.audio_src, headers)
        log(f"chosen audio {a.audio_src[:110]}: {notes[a.audio_src]['kind']} - {notes[a.audio_src]['note']}")
        attempts.append(("the audio you selected",
                         lambda: resolve(a.audio_src, headers, cfg, log, a.sub_langs)))
    for u in order:
        if u == a.url:
            attempts.append(("the stream address", lambda: r))
        else:
            attempts.append((f"another stream address ({u[-40:]})",
                             lambda u=u: resolve(u, headers, cfg, log, a.sub_langs)))
    best_master = next((u for u in order if notes.get(u, {}).get("kind") == "master"), None)
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
            for u, n in notes.items():
                if n["kind"] not in ("master", "video") and n["note"] not in " ".join(errors):
                    errors.append(f"[{u[-45:]} -> {n['note']}]")
            problem = RuntimeError(" ; ".join(errors))
    if best_master and r["mpv_url"] != best_master:
        r["mpv_url"] = best_master
    elif a.audio_src and session is not None and r["mpv_url"] == a.audio_src:
        r["mpv_url"] = a.url             # never show an audio-only playlist as "the video"
    if looks_audio_only(r["mpv_url"]):
        # never show an audio-only playlist as "the video": prefer a main playlist, then a video one
        pool = [u for u in order if not looks_audio_only(u)]
        pick = next((u for u in pool if "type=" not in u), None) or (pool[0] if pool else None)
        if pick:
            log("the address picked for the picture looked audio-only; showing", pick[:100], "instead")
            r["mpv_url"] = pick
    if a.tablet:
        return run_tablet(a, cfg, r, notes, session, problem, headers, log)
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
