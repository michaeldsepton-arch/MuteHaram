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
import threading
import sys
import time

import numpy as np

from . import config
from .engine import MediaSource, Session, SAMPLE_RATE
from .server import make_server
from .engine import SessionHub

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


def stop_other_tablet_sessions(log):
    """A new "send to the tablet" replaces the old one: stop any older tablet session still running."""
    if os.name != "nt" or os.environ.get("MUTEHARAM_KEEP_OTHER_SESSIONS") == "1":   # (the variable is for tests)
        return
    me = os.getpid()
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne %d -and $_.CommandLine -match 'muteharam_pc.play' "
          "-and $_.CommandLine -match '--tablet' -and $_.CommandLine -notmatch 'Get-CimInstance' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" % me)
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(1.0)
    except Exception as ex:
        log("could not stop the older tablet session:", ex)


class TabletFront:
    """What the tablet app talks to from the moment "Send to the tablet" is pressed: until the film is open and its
    first seconds are cleaned it reports progress ({"preparing": true, "status": ...}); then it serves the session."""

    def __init__(self, log):
        self.log = log
        self.t0 = time.time()
        self.text = "Starting"
        self.failed = False
        self.hub = None
        self.doc = None

    # ---- what the audio server reads (nothing until the session is ready)
    @property
    def total_frames(self):
        return self.hub.total_frames if self.hub else 0

    def read_frames(self, f0, f1):
        return self.hub.read_frames(f0, f1)

    # ---- progress
    def stage(self, text):
        self.text = text
        self.log(f"[tablet] {text}")

    def fail(self, text):
        self.failed = True
        self.stage("Could not prepare the film: " + text)

    def ready(self, hub, doc):
        self.hub, self.doc = hub, doc

    def session_json(self, q):
        if self.hub is None:
            return {"preparing": True, "failed": self.failed, "status": self.text,
                    "elapsed_s": int(time.time() - self.t0)}
        return dict(self.doc, mode=self.hub.mode, age_s=int(time.time() - self.doc["id"] / 1000))


GOVERNOR = None


def run_preclean(a, r, session, problem, log):
    """Clean the whole sound into a.preclean (library): cleaned.pcm grows block by block (a stopped job carries on
    from there next time), then it becomes audio.opus. Progress goes to progress.json for the library window."""
    from .library import write_json
    out_dir = a.preclean
    prog_path = os.path.join(out_dir, "progress.json")
    prog = {}
    try:
        prog = json.load(open(prog_path, encoding="utf-8"))
    except Exception:
        pass

    def save(**k):
        prog.update(k)
        write_json(prog_path, prog)

    if session is None:
        save(error="the sound could not be opened: " + str(problem)[:300], done=False)
        raise SystemExit(1)
    cfg = config.load()
    total, hop = session.total_frames, session.hop
    nblocks = session.nblocks
    pcm_path = os.path.join(out_dir, "cleaned.pcm")
    same_job = prog.get("total_frames") == total and prog.get("hop") == hop and prog.get("mode") == session.mode
    done_blocks = 0
    if same_job and os.path.exists(pcm_path):
        done_blocks = min(os.path.getsize(pcm_path) // (hop * 4), nblocks)
        with open(pcm_path, "r+b") as f:          # drop a half-written last block
            f.truncate(done_blocks * hop * 4)
    elif os.path.exists(pcm_path):
        os.remove(pcm_path)
    # what is needed later to show the picture again
    save(direct=bool(a.direct), total_frames=total, hop=hop, mode=session.mode, duration=session.duration, nblocks=nblocks,
         url=a.url, picture=r["mpv_url"], mpv_args=r.get("mpv_args") or [], headers=r.get("headers") or {},
         subs=list(a.sub), done=False, error="", stage="cleaning", unclean_s=prog.get("unclean_s", 0) if same_job else 0)
    def remake(b):
        s = session.remake(session.mode, b)
        s.lookahead_blocks = 1           # one block at a time, so the cleaning can give way between blocks
        return s

    sess = session if done_blocks == 0 else remake(done_blocks)
    sess.lookahead_blocks = 1
    if sess is not session:
        session.stop_work()
        session.close()
    gov = GOVERNOR

    def release():
        return False                         # the standard model is small (about 1.6 GB): it stays loaded while waiting
    log(f"pre-cleaning {nblocks} blocks ({session.duration / 60:.0f} min), starting at block {done_blocks + 1}")
    recent = []                                    # when the last blocks finished: the estimate follows the current speed
    block_s = 5.0
    with open(pcm_path, "ab") as f:
        for b in range(done_blocks, nblocks):
            if gov is not None and b > done_blocks:
                gov.gate(block_s, release, lambda text: save(stage=text))
                if gov.changed:
                    recent = []                    # the speed changed: start the estimate afresh
            tb = time.time()
            blk = None
            for attempt in range(4):
                try:
                    got = np.array(sess.wait_block(b, timeout=1800))
                    if not sess.bad[b]:
                        blk = got
                        break
                except Exception as ex:
                    log(f"block {b + 1}: {ex!r}")
                # the stream broke here: reopen it from this point and try again (3 times)
                sess.stop_work()
                sess.close()
                if attempt == 3:
                    break
                log(f"block {b + 1}: could not be cleaned, reopening the stream (try {attempt + 1}/3)")
                time.sleep(3 + 5 * attempt)
                sess = remake(b)
            if blk is None:                           # give up on this stretch: silent (never the music)
                blk = np.zeros((hop, 2), np.int16)
                save(unclean_s=prog.get("unclean_s", 0) + hop / SAMPLE_RATE)
                if b + 1 < nblocks:
                    sess = remake(b + 1)
            f.write(np.ascontiguousarray(blk, dtype=np.int16).tobytes())
            f.flush()
            block_s = min(60.0, max(1.0, time.time() - tb))
            if gov is not None and b == done_blocks:
                gov.after_first_block()            # now the model is loaded: measure its graphics memory
            recent = (recent + [time.time()])[-11:]
            eta = None
            if len(recent) >= 3:                   # (the first block includes loading the model: not counted)
                per_block = (recent[-1] - recent[0]) / (len(recent) - 1)
                eta = int((nblocks - b - 1) * per_block)
            save(progress=(b + 1) / nblocks, done_blocks=b + 1, eta_s=eta)
    sess.stop_work()
    sess.close()
    save(stage="saving")
    opus = os.path.join(out_dir, "audio.opus")
    res = subprocess.run([cfg["ffmpeg"], "-y", "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(SAMPLE_RATE),
                          "-ac", "2", "-i", pcm_path, "-t", f"{total / SAMPLE_RATE:.3f}", "-c:a", "libopus", "-b:a", "160k",
                          opus], capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(opus):
        save(error="saving the cleaned sound failed: " + res.stderr[-300:])
        raise SystemExit(1)
    os.remove(pcm_path)
    save(done=True, stage="ready", progress=1.0, eta_s=0)
    log("pre-cleaning finished:", opus)


def run_library(a, S, log):
    """Watch a film from the library: the picture from the website, the sound from the finished file (no GPU)."""
    from .library import FileSession
    cfg = config.load()
    prog = json.load(open(os.path.join(a.library, "progress.json"), encoding="utf-8"))
    audio = os.path.join(a.library, "audio.opus")
    if not prog.get("done") or not os.path.exists(audio):
        raise SystemExit("This film is not cleaned yet.")
    a.url = prog["url"]
    a.sub = list(prog.get("subs") or []) + list(a.sub)
    r = dict(mpv_url=prog["picture"], mpv_args=prog.get("mpv_args") or [], headers=prog.get("headers") or {},
             media_url=prog["picture"], duration=prog.get("duration"))
    # a film queued from a stream address (CineHD ...) is a stream, not a web page: don't ask yt-dlp about it
    try:
        req = json.load(open(os.path.join(a.library, "meta.json"), encoding="utf-8")).get("req") or {}
    except Exception:
        req = {}
    notes = {}
    if req.get("direct") or prog.get("direct"):
        FORCE_DIRECT.update([a.url, prog["picture"]])
    if a.tablet:
        front, srv = start_tablet_front(a, log)
        front.stage("Opening the cleaned sound")
        if is_direct(r["mpv_url"]) and r["mpv_url"].startswith(("http://", "https://")):
            notes[r["mpv_url"]] = classify(r["mpv_url"], r["headers"])      # tells the tablet it is a playlist (HLS)
            log(f"picture {r['mpv_url'][:90]}: {notes[r['mpv_url']]['kind']} - {notes[r['mpv_url']]['note']}")
        sess = FileSession(audio, prog["total_frames"], cfg["ffmpeg"], log)
        return run_tablet(a, cfg, r, notes, sess, None, r["headers"], log, front, srv)
    cmd = [cfg["mpv"], r["mpv_url"], f"--audio-file={audio}", f"--script={os.path.join(HERE, 'mpv_select_audio.lua')}",
           f"--script={os.path.join(HERE, 'mpv_pip.lua')}"]
    if S["ontop"] and not a.pip:
        cmd += ["--ontop"]
    if a.pip:
        cmd += ["--script-opts=muteharam-pip=yes", "--ontop", "--no-border", "--autofit=25%", "--geometry=98%:94%"]
    cfg_dir = os.path.join(config.PC_DIR, "mpv_config")
    if os.path.exists(os.path.join(cfg_dir, "scripts", "uosc", "main.lua")):
        cmd += [f"--config-dir={cfg_dir}"]
    for sub in a.sub:
        cmd.append(f"--sub-file={sub}")
    cmd += ["--force-window=immediate", "--network-timeout=120", "--cache=yes",
            "--title=MuteHaram (cleaned in advance) - ${media-title}", *r["mpv_args"], *a.mpv_arg]
    if a.start > 0:
        cmd.append(f"--start={a.start}")
    env = dict(os.environ)
    env["PATH"] = config.TOOLS_DIR + os.pathsep + env.get("PATH", "")
    log("starting mpv with the sound cleaned in advance ...")
    subprocess.run(cmd, env=env)


def start_tablet_front(a, log):
    """Stop older tablet sessions and answer the tablet at once with progress messages."""
    from .stream_server import load_or_create_token, lan_addresses
    stop_other_tablet_sessions(log)
    front = TabletFront(log)
    srv = None
    for attempt in range(10):                   # the port of a session that was just stopped may need a moment
        try:
            srv = make_server(front, host="0.0.0.0", port=a.tablet_port, token=load_or_create_token(),
                              routes={"/session.json": front.session_json})
            break
        except OSError:
            if attempt == 9:
                raise SystemExit(f"Port {a.tablet_port} is in use by another program; close other MuteHaram windows.")
            time.sleep(1.0)
    log("In the MuteHaram tablet app use one of these addresses:", ", ".join(lan_addresses()) or "(this computer's address)")
    return front, srv


def run_tablet(a, cfg, r, notes, session, problem, headers, log, front, srv):
    """Offer the picture address + the cleaned audio to the tablet app (instead of opening mpv)."""
    if session is None:
        front.fail("the music could not be removed (details on the PC)")
        time.sleep(20)                          # let the tablet show the message
        srv.shutdown()
        raise SystemExit("The music could not be removed, so nothing is sent to the tablet: " + str(problem))
    front.stage("Finding the picture and the captions")
    title = None
    if is_direct(a.url):
        video, vheaders, subs = r["mpv_url"], dict(r["headers"] or {}), [{"url": u, "lang": ""} for u in a.sub]
        kind = tablet_kind(video, (notes.get(video) or {}).get("kind"))
    else:
        video, vheaders, subs, title = resolve_video(a.url, cfg, log, a.sub_langs)
        subs += [{"url": u, "lang": ""} for u in a.sub]
        kind = tablet_kind(video, None)
    # only real caption files: the seek-bar preview pictures (thumbnails.vtt) and anything that does not load are dropped
    good = []
    for sub in subs:
        u = sub["url"]
        if "thumbnail" in u.lower() or "sprite" in u.lower():
            continue
        st, ct, data, _ = fetch_head(u, vheaders, nbytes=64)
        text = data.lstrip(bytes([0xEF, 0xBB, 0xBF, 32, 13, 10]))
        is_caption = text[:6] == b"WEBVTT" or u.lower().split("?")[0].endswith((".srt", ".ass", ".ssa", ".ttml"))
        if st is not None and st < 400 and is_caption:
            good.append(sub)
        else:
            log(f"skipping a caption file that does not load ({st}): {u[:100]}")
    subs = good
    hub = SessionHub(session, session.remake)
    # the film only starts on the tablet once its first seconds are cleaned: no original sound (music) at the start
    front.stage("Cleaning the first seconds of sound")
    if not session.prepare(at_frame=int(max(0.0, a.start) * SAMPLE_RATE), timeout=240):
        log("[tablet] the first seconds are taking long; starting anyway")
    doc = {"id": int(time.time() * 1000), "title": title or "", "video": video, "kind": kind,
           "headers": clean_headers(vheaders),
           "subs": subs, "start": max(0.0, a.start), "duration": session.duration}
    front.ready(hub, doc)
    log(f"tablet session ready: {kind} picture, cleaned audio on port {a.tablet_port} "
        f"({int(time.time() - front.t0)} s after pressing Send)")
    log("Waiting for the tablet. (This window closes after 4 hours without the tablet asking.)")
    try:
        while time.time() - srv.last_request < 4 * 3600 or time.time() - doc["id"] / 1000 < 120:
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        hub.close()


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
    ap.add_argument("--mode", choices=["music"], default="music", help="(only the standard model remains)")
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
    ap.add_argument("--preclean", metavar="DIR", help="clean the WHOLE sound into DIR now (library) instead of playing")
    ap.add_argument("--library", metavar="DIR", help="play a film cleaned in advance (library) on the PC or with --tablet")
    ap.add_argument("--mpv-arg", action="append", default=[], help="extra argument for mpv (repeatable)")
    a = ap.parse_args(argv)
    if a.direct:
        FORCE_DIRECT.update([a.url, a.audio_src, *a.alt])
    log = lambda *m: print(*m, flush=True)
    if a.library:
        return run_library(a, S, log)

    cfg = config.load()
    headers = {}
    for h in a.header:
        k, _, v = h.partition(":")
        headers[k.strip()] = v.strip()
    if a.referer:
        headers["Referer"] = a.referer
    if a.user_agent:
        headers["User-Agent"] = a.user_agent

    global GOVERNOR
    if a.preclean:
        from .library import GpuGovernor
        GOVERNOR = GpuGovernor(log)
        GOVERNOR.before_load()             # graphics memory in use BEFORE our model: tells ours from others'
    front = tablet_srv = None
    if a.tablet:
        front, tablet_srv = start_tablet_front(a, log)
        front.stage("Opening the film on the PC")
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
        # switching mode later: the same source, the other network
        sess.remake = lambda m, start_block=0: Session(src, duration=sess.duration, window_s=a.window, hop_s=a.hop,
                                        music_gain=a.gain, device="cpu" if a.cpu else None,
                                        models_dir=cfg["models_dir"], log=log, channels_mode=a.channels, mode=m,
                                        start_block=start_block)
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
    if front:
        front.stage("Loading the cleaning model and opening the sound")
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
    if a.preclean:
        return run_preclean(a, r, session, problem, log)
    if a.tablet:
        return run_tablet(a, cfg, r, notes, session, problem, headers, log, front, tablet_srv)
    if session is not None:
        hub = SessionHub(session, session.remake)
        session = hub                      # closing later closes whichever session is current
        srv = make_server(hub)
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
    if S["ontop"] and not a.pip:
        cmd += ["--ontop"]                 # stays in front of other programs (press T in the player to turn it off)
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
