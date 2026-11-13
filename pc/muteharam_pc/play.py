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
        from .library import mpv_header_args
        return dict(media_url=url, headers=headers, duration=None, mpv_url=url, mpv_args=mpv_header_args(headers))
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
        self.files = {}                  # extra files the tablet may fetch (a library film's compressed sound)
        self.audio_path = "/audio.wav"   # where the tablet gets the cleaned sound
        self.resume_key = None           # which film, for remembering where it was left (see resume_key)
        self.scenes = None               # -> {"covers": [[s, e]...], "scanned_to", "done"} (Lowering the gaze)
        self.scanner = None              # the live scene check (it follows the player when it skips)
        self.captions = None             # MuteHaram's own subtitles (captions.Captioner, or a finished list)
        self.steps = list(TABLET_STEPS)
        self.progress = None             # 0..1 within the current step, if known
        self.eta_s = None                # seconds this step still takes, if known
        self.duration = 0.0
        self._pos_saved = 0.0

    # ---- what the audio server reads (nothing until the session is ready)
    @property
    def total_frames(self):
        return self.hub.total_frames if self.hub else 0

    def read_frames(self, f0, f1):
        return self.hub.read_frames(f0, f1)

    # ---- progress
    def stage(self, text):
        self.text = text
        self.progress = self.eta_s = None
        self.log(f"[tablet] {text}")

    def step_progress(self, progress, eta_s=None):
        self.progress = None if progress is None else max(0.0, min(1.0, progress))
        self.eta_s = None if eta_s is None else max(0, int(eta_s))

    def fail(self, text):
        self.failed = True
        self.stage("Could not prepare the film: " + text)

    def ready(self, hub, doc):
        self.hub, self.doc = hub, doc

    def level_route(self, q):
        """/level?l=auto|normal|strong|strongest&pos=<ms> : clean again from there, as strict as asked; answers
        once the sound at that point is ready (the tablet then reloads its sound)."""
        return set_strength(self.hub, q, self.log)

    def subs_route(self, q):
        """/subs?m=site|own|off : which subtitles; own = MuteHaram makes them for the whole film (remembered)."""
        return set_subtitles(self.captions, q, self.log)

    def session_json(self, q):
        # where the tablet is in the film (saved now and then: the next "Send to the tablet" carries on there)
        pos = (q.get("pos") or [""])[0]
        if pos.isdigit() and self.resume_key and time.time() - self._pos_saved > 10:
            self._pos_saved = time.time()
            save_position(self.resume_key, int(pos) / 1000.0, self.duration)
        # the tablet reports its loading problems (stalls, slow or failing downloads of the picture / the PC's sound)
        # (written when a stall or a failure is new, else once a minute)
        diag = (q.get("diag") or [""])[0]
        key = re.sub(r"\d+ pieces at [\d.]+ Mbit/s", "", diag)
        if diag and (key != getattr(self, "_diag_key", "") or time.time() - getattr(self, "_diag_t", 0) > 60):
            self._diag_key, self._diag_t = key, time.time()
            self.log(f"[tablet] {time.strftime('%H:%M:%S')} {diag}")
        if pos.isdigit():
            for who in (self.scanner, self.captions):
                if who is not None and hasattr(who, "follow"):
                    who.follow(int(pos) / 1000.0)
        if self.hub is None:
            step = next((i + 1 for i, s in enumerate(self.steps) if self.text.startswith(s)), 0)
            return {"preparing": True, "failed": self.failed, "status": self.text,
                    "elapsed_s": int(time.time() - self.t0), "step": step, "steps": len(self.steps),
                    "progress": self.progress, "eta_s": self.eta_s, "step_names": self.steps}
        doc = dict(self.doc, mode=self.hub.mode, age_s=int(time.time() - self.doc["id"] / 1000),
                   level=getattr(self.hub, "level", "normal"))
        if self.scenes is not None:
            try:
                doc["scenes"] = self.scenes()
            except Exception:
                pass
        # MuteHaram's own subtitles: the ones the tablet does not have yet (it says how many it has)
        doc["subtitles"] = S_SETTINGS().get("subtitles", "site")
        if self.captions is not None:
            k = (q.get("subs_from") or ["0"])[0]
            n, lines = self.captions.lines_from(int(k) if k.isdigit() else 0)
            doc["made"] = {"from": int(k) if k.isdigit() else 0, "n": n, "lines": lines[:400]}
        return doc


GOVERNOR = None

POSITIONS = os.path.join(config.PC_DIR, "positions.json")


def resume_key(a, duration):
    """Which film this is, to remember where it was left: a library film by its library entry, anything else by
    its page and length (one page address can show several episodes, which differ in length)."""
    if getattr(a, "library", None):
        return "library:" + os.path.basename(os.path.normpath(a.library))
    return f"{a.page or a.url}|{round(duration or 0)}"


def load_positions():
    try:
        return json.load(open(POSITIONS, encoding="utf-8"))
    except Exception:
        return {}


def save_position(key, seconds, duration):
    """Remember where a film was left; forget it once the film is (nearly) finished."""
    from .library import write_json
    pos = load_positions()
    if duration and seconds > duration - 120:
        pos.pop(key, None)
    elif seconds > 30:
        pos[key] = round(seconds, 1)
    try:
        write_json(POSITIONS, pos)
    except Exception:
        pass


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
    scan = start_scene_scan(r.get("mpv_url") or a.url, r.get("headers") or {}, session.duration,
                            os.path.join(out_dir, "scenes.json"), cfg, log, alts=list(a.alt or []))

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
            save(progress=(b + 1) / nblocks, done_blocks=b + 1, eta_s=eta,
                 scenes_progress=scene_progress(scan, session.duration))
    sess.stop_work()
    sess.close()
    # the picture check may still be going (it reads the film at its own pace): the film is ready once both are done
    while scan is not None and scan.thread.is_alive():
        save(stage=f"checking scenes {scene_progress(scan, session.duration) * 100:.0f} %",
             scenes_progress=scene_progress(scan, session.duration))
        scan.thread.join(5)
    caption_library(os.path.join(out_dir, "subs.json"), pcm_path, total, log, lambda text: save(stage=text))
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


def start_scene_scan(url, headers, duration, path, cfg, log, start=0.0, alts=()):
    """Lowering the gaze: check the picture for scenes to cover (carries on from an earlier, stopped check).
    -> the running SceneScanner, or None (switched off, or already checked)."""
    if not S_SETTINGS().get("scenes", True):
        return None
    from . import scenes
    old = scenes.load(path) if path else {}
    if old.get("done"):
        return None
    try:
        S = S_SETTINGS()
        first = start if old.get("scanned") else max(start, float(old.get("scanned_to") or 0))
        return scenes.SceneScanner(url, headers, cfg["ffmpeg"], duration, out_path=path, start=first,
                                   covers=old.get("covers"), scanned=old.get("scanned"), log=log,
                                   model=S.get("scenes_model", scenes.MODEL), alts=alts,
                                   gpu_layers=S.get("scenes_gpu_layers", scenes.GPU_LAYERS)).begin()
    except Exception as ex:
        log(f"scenes: could not start the picture check: {ex!r}")
        return None


def caption_library(subs_path, pcm_path, total_frames, log, stage=lambda t: None):
    """Library film: MuteHaram's own subtitles for the whole film, from the cleaned sound (pcm: int16 stereo,
    44.1 kHz) -> subs.json {"lines": [[start, end, text], ...], "done": true} (shown when chosen, and while covered)."""
    from .library import write_json
    old = {}
    try:
        old = json.load(open(subs_path, encoding="utf-8"))
    except Exception:
        pass
    if old.get("done"):
        return
    try:
        from .captions import caption_file
        pcm = np.memmap(pcm_path, dtype=np.int16, mode="r").reshape(-1, 2)
        n = min(len(pcm), total_frames)
        total_s = n / SAMPLE_RATE

        def read(s, d):
            return np.array(pcm[int(s * SAMPLE_RATE):min(n, int((s + d) * SAMPLE_RATE))])
        lines = caption_file(read, total_s, S_SETTINGS().get("subtitles_lang", "en"), log,
                             lambda done, tot: stage(f"writing subtitles {done / tot * 100:.0f} %"))
        write_json(subs_path, {"lines": lines, "done": True})
        log(f"captions: {len(lines)} subtitle line(s) for the whole film")
    except Exception as ex:
        log(f"captions: could not write them ({ex!r})")


class SavedCaptions:
    """A library film's finished subtitles (subs.json), served like a live Captioner's."""

    def __init__(self, path):
        self.lines = []
        try:
            self.lines = json.load(open(path, encoding="utf-8")).get("lines") or []
        except Exception:
            pass

    def lines_from(self, k):
        return len(self.lines), self.lines[k:]


def set_subtitles(captions, q, log):
    """Which subtitles: site / own / off. own: MuteHaram makes them for the whole film (live: from here on)."""
    m = (q.get("m") or [""])[0]
    if m not in ("site", "own", "off"):
        return {"ok": False, "error": "unknown choice"}
    try:
        config.save_setting("subtitles", m)
    except Exception:
        pass
    if captions is not None and hasattr(captions, "set_full"):
        # ("Website" when the site has no full subtitles for this film: MuteHaram's own are shown instead)
        captions.set_full(m == "own" or (m == "site" and getattr(captions, "site_missing", False)))
    log(f"subtitles: {m}")
    return {"ok": True, "subtitles": m}


def site_subs_full(url, cues, duration):
    """Is a website's subtitle file the whole dialogue, or a 'forced' track (only signs and a few lines)?
    Measured: Young Sherlock's forced track had 18 lines for 53 min; full ones have ~8-15 lines a minute."""
    if "forced" in url.lower():
        return False
    minutes = (duration or 0) / 60.0
    return cues >= max(30, 2 * minutes)


def start_captions(session_ref, scan, duration, log, out_path=None):
    """Live: MuteHaram's own subtitles (the covered parts; the whole film when chosen). session_ref() -> the
    current session / hub (cleaned sound), its .src the original."""
    from .captions import Captioner

    def clean(s, d):
        return session_ref().peek_frames(int(s * SAMPLE_RATE), int((s + d) * SAMPLE_RATE))

    def bad(s, d):
        cur = session_ref()
        cur = getattr(cur, "cur", cur)
        b0, b1 = int(s * SAMPLE_RATE) // cur.hop, int((s + d) * SAMPLE_RATE - 1) // cur.hop
        return bool((cur.ready[b0:b1 + 1] & cur.bad[b0:b1 + 1]).any())
    S = S_SETTINGS()
    cap = Captioner(duration, clean, None, scanner=scan, full=S.get("subtitles") == "own",
                    lang=S.get("subtitles_lang", "en"), log=log, out_path=out_path)
    cap.is_bad = lambda a, b: bad(a, b - a)
    return cap


def set_strength(hub, q, log):
    """Change how strict the music removal is while watching (tablet button, L in mpv); remembered for next time."""
    from .engine import LEVELS
    level = (q.get("l") or [""])[0]
    if level not in LEVELS:
        return {"ok": False, "error": "unknown strength", "level": getattr(hub, "level", "")}
    pos = (q.get("pos") or ["0"])[0]
    at = int(float(pos) / 1000.0 * SAMPLE_RATE) if pos.replace(".", "", 1).isdigit() else None
    try:
        hub.set_level(level, at)
    except Exception as ex:
        return {"ok": False, "error": str(ex), "level": getattr(hub, "level", "")}
    try:
        config.save_setting("strength", level)
    except Exception:
        pass
    log(f"strength changed to {level}")
    return {"ok": True, "level": level}


def subs_args(path, port):
    """mpv: MuteHaram's own subtitles (mpv_subs.lua draws them, film-style; C switches site / own / off)."""
    out = [f"--script={os.path.join(HERE, 'mpv_subs.lua')}",
           f"--script-opts-append=muteharam-subs={mpv_list_value(path)}",
           f"--script-opts-append=muteharam-subs-mode={S_SETTINGS().get('subtitles', 'site')}"]
    if port:
        out.append(f"--script-opts-append=muteharam-subs-port={port}")
    return out


def cover_logo():
    """The logo as small raw pictures for the PC player's cover (mpv shows raw BGRA, premultiplied):
    <temp>/muteharam-logo-<size>.bgra; -> the common start of their names, or "" if it can't be made."""
    import tempfile
    base = os.path.join(tempfile.gettempdir(), "muteharam-logo")
    try:
        from PIL import Image
        src = Image.open(os.path.join(config.PC_DIR, "assets", "logo.png")).convert("RGBA")
        for n in (48, 64, 80, 100, 128, 160):
            out = f"{base}-{n}.bgra"
            if os.path.exists(out):
                continue
            px = np.asarray(src.resize((n, n), Image.LANCZOS), np.float32)
            a = px[:, :, 3:4] / 255.0
            bgra = np.concatenate([px[:, :, 2::-1] * a, px[:, :, 3:4]], axis=2)
            np.round(bgra).astype(np.uint8).tofile(out)
        return base
    except Exception:
        return ""


def scene_args(path):
    """mpv options for Lowering the gaze (mpv_scenes.lua covers the listed parts; G turns it on / off)."""
    if not path or not S_SETTINGS().get("scenes", True):
        return []
    on = "yes" if S_SETTINGS().get("scenes_cover", True) else "no"
    return [f"--script={os.path.join(HERE, 'mpv_scenes.lua')}",
            f"--script-opts-append=muteharam-scenes={mpv_list_value(path)}",
            f"--script-opts-append=muteharam-logo={mpv_list_value(cover_logo())}",
            f"--script-opts-append=muteharam-on={on}"]


def scene_progress(scan, duration):
    if scan is None or not duration:
        return 1.0
    st = scan.state()
    return 1.0 if st["done"] else min(1.0, st["scanned_to"] / duration)


def S_SETTINGS():
    try:
        return config.settings()
    except Exception:
        return dict(config.DEFAULT_SETTINGS)


def adaptive_picture(url, headers, alts):
    """The website's list of qualities (master playlist) for the same film as this one-quality playlist, or None."""
    if not str(url).startswith(("http://", "https://")) or classify(url, headers)["kind"] == "master":
        return None
    folder = lambda u: u.split("?")[0].rsplit("/", 1)[0]
    cands = [url.replace("/index.m3u8", "/master.m3u8")] if "/index.m3u8" in url else []
    cands += [u for u in alts if "master" in u.lower() and u not in cands]
    for u in cands:
        if folder(u) == folder(url) and classify(u, headers)["kind"] == "master":   # same film, same server
            return u
    return None


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
        front.steps = ["Opening the cleaned sound", "Finding the picture and the captions",
                       "Cleaning the first seconds of sound"]
        front.stage("Opening the cleaned sound")
        # the website's list of picture qualities for this film, so the tablet picks one its Wi-Fi can carry
        # (one fixed quality, often 1080p, kept running dry: a pause every 20-45 s)
        master = adaptive_picture(r["mpv_url"], r["headers"], req.get("alts") or [])
        if master:
            FORCE_DIRECT.add(master)
            r["mpv_url"] = master
            log("picture: the website's list of qualities (the tablet adapts to its Wi-Fi)")
        if is_direct(r["mpv_url"]) and r["mpv_url"].startswith(("http://", "https://")):
            notes[r["mpv_url"]] = classify(r["mpv_url"], r["headers"])      # tells the tablet it is a playlist (HLS)
            log(f"picture {r['mpv_url'][:90]}: {notes[r['mpv_url']]['kind']} - {notes[r['mpv_url']]['note']}")
        sess = FileSession(audio, prog["total_frames"], cfg["ffmpeg"], log)
        # the finished sound file as it is (160 kb/s instead of 1.4 Mbit/s raw), for tablet apps that can play it
        front.files["/audio.opus"] = audio
        front.audio_path = "/audio.opus"
        scenes_file = os.path.join(a.library, "scenes.json")
        if os.path.exists(scenes_file):
            from . import scenes
            front.scenes = lambda: scenes.load(scenes_file)
        if os.path.exists(os.path.join(a.library, "subs.json")):
            front.captions = SavedCaptions(os.path.join(a.library, "subs.json"))
        return run_tablet(a, cfg, r, notes, sess, None, r["headers"], log, front, srv)
    cmd = [cfg["mpv"], r["mpv_url"], f"--audio-file={audio}", f"--script={os.path.join(HERE, 'mpv_select_audio.lua')}",
           f"--script={os.path.join(HERE, 'mpv_pip.lua')}"] + scene_args(os.path.join(a.library, "scenes.json")) \
        + subs_args(os.path.join(a.library, "subs.json"), None)
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
    global ACTIVE_FRONT
    front = ACTIVE_FRONT = TabletFront(log)
    srv = None
    for attempt in range(10):                   # the port of a session that was just stopped may need a moment
        try:
            srv = make_server(front, host="0.0.0.0", port=a.tablet_port, token=load_or_create_token(),
                              routes={"/session.json": front.session_json, "/level": front.level_route,
                                      "/subs": front.subs_route})
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
        st, ct, data, _ = fetch_head(u, vheaders, nbytes=4 * 1024 * 1024)
        text = data.lstrip(bytes([0xEF, 0xBB, 0xBF, 32, 13, 10]))
        # (subtitle services send SRT as plain text from addresses without .srt: known by its "-->" timings)
        is_caption = (text[:6] == b"WEBVTT" or b"-->" in text
                      or u.lower().split("?")[0].endswith((".srt", ".ass", ".ssa", ".ttml")))
        if st is not None and st < 400 and is_caption:
            sub["full"] = site_subs_full(u, text.count(b"-->"), session.duration)
            if not sub["full"]:
                log(f"subtitles: the site's file has only {text.count(b'-->')} lines (a 'forced' track for a few "
                    f"moments, not the whole dialogue): {u[:100]}")
            good.append(sub)
        else:
            log(f"skipping a caption file that does not load ({st}): {u[:100]}")
    subs = good
    # no full subtitles from the site: with "Website" chosen, MuteHaram's own are shown for this film instead
    front.site_full = any(x.get("full") for x in subs)
    if not front.site_full and front.captions is not None:
        front.captions.site_missing = True
        if S_SETTINGS().get("subtitles", "site") == "site":
            front.captions.set_full(True)
            log("subtitles: the site has no full subtitles for this film; MuteHaram's own are made instead")
    hub = SessionHub(session, session.remake)
    # carry on where this film was left last time (the tablet reports where it is while it plays)
    front.resume_key = resume_key(a, session.duration)
    front.duration = session.duration
    if a.start <= 0:
        at = load_positions().get(front.resume_key)
        if at and 30 < at < session.duration - 120:
            a.start = max(0.0, at - 5)
            log(f"[tablet] carrying on from {int(a.start // 60)}:{int(a.start % 60):02d}, where it was left last time")
    # live: the picture check gets a head start (it then keeps ahead; the tablet waits if playback catches up)
    if front.scanner is not None:
        front.scanner.follow(max(0.0, a.start))
    if front.scanner is not None and not front.scanner.done:
        front.stage("Checking the scenes ahead")
        wait_for_scenes(front.scanner, max(0.0, a.start), front, log)
    # the film only starts on the tablet once its first seconds are cleaned: no original sound (music) at the start
    front.stage("Cleaning the first seconds of sound")
    if not session.prepare(at_frame=int(max(0.0, a.start) * SAMPLE_RATE), timeout=240):
        log("[tablet] the first seconds are taking long; starting anyway")
    doc = {"id": int(time.time() * 1000), "title": title or "", "video": video, "kind": kind,
           "headers": clean_headers(vheaders),
           "subs": subs, "site_subs_full": front.site_full, "start": max(0.0, a.start), "duration": session.duration, "audio": front.audio_path}
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


TABLET_STEPS = ("Opening the film on the PC", "Loading the cleaning model and opening the sound",
                "Finding the picture and the captions", "Checking the scenes ahead", "Cleaning the first seconds of sound")


def wait_for_scenes(scan, start, front, log, limit_s=15 * 60):
    """Before playing: let the scene check get far enough ahead that playback never has to wait for it later.
    How far depends on how fast it goes (measured): faster than the film -> 45 s; slower -> enough for the rest."""
    t0 = time.time()
    progress = front.step_progress
    if not scan.ready:
        front.stage("Checking the scenes ahead: loading the picture models (about a minute)")
        while not scan.ready and not scan.error and scan.thread.is_alive() and time.time() - t0 < 300:
            progress(min(0.95, (time.time() - t0) / 60.0), max(1, 60 - (time.time() - t0)))
            time.sleep(1)
        front.stage("Checking the scenes ahead")
    while not scan.done and not scan.error:
        st = scan.state()
        ahead = st["scanned_to"] - start
        need, rate = st["need"], st["rate"]
        if rate is not None and ahead >= need:
            break
        eta = (need - ahead) / rate if rate else None
        progress(ahead / need if need > 0 else 1.0, eta)
        if time.time() - t0 > limit_s:
            log(f"[tablet] the scene check is slow ({rate}x film speed): starting anyway (it may pause later)")
            break
        time.sleep(1)
    log(f"[tablet] scene check ahead by {scan.state()['scanned_to'] - start:.0f} s "
        f"(needs {scan.state()['need']:.0f} s at {scan.state()['rate']}x film speed) after {time.time() - t0:.0f} s")


ACTIVE_FRONT = None        # the tablet's progress server, while "Send to the tablet" is being prepared


def tell_tablet_failed(why):
    """The PC could not prepare the film: the tablet must say so (not keep counting "preparing ...")."""
    front = ACTIVE_FRONT
    if front is None or front.hub is not None:
        return
    text = str(why or "").strip().splitlines()
    first = next((x for x in text if x.strip() and "Warning" not in x), "unknown problem")
    if "yt-dlp could not open" in str(why) or "ffprobe could not read" in str(why) or "could not be opened" in str(why):
        first = ("the website's video could not be opened on the PC (some sites lock their video to their own "
                 "player). On the website try another server, or another site")
    front.fail(first[:220])
    time.sleep(25)                        # the tablet checks every 2 s: let it see the message


def main(argv=None):
    try:
        run(argv)
    except SystemExit as e:
        if e.code not in (None, 0):
            tell_tablet_failed(e.code)
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
        tell_tablet_failed(repr(e))
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

    # the graphics card is shared by the music removal, the subtitles (Cohere) and the scene check (Ollama):
    # PyTorch would otherwise keep up to 7 GB of it for itself and slow the others down (measured)
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.set_per_process_memory_fraction(0.6)
    except Exception:
        pass

    def build(rr):
        """Open the media for the AI and check that audio can really be decoded."""
        src = MediaSource(rr["media_url"], rr["headers"], cfg["ffmpeg"], cfg["ffprobe"])
        sess = Session(src, duration=rr["duration"], window_s=a.window, hop_s=a.hop, music_gain=a.gain,
                       device="cpu" if a.cpu else None, models_dir=cfg["models_dir"], log=log,
                       channels_mode=a.channels, mode=a.mode, level=S.get("strength", "normal"))
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
                                        start_block=start_block, level=sess.level)
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
    live_scenes = None
    current = {"s": session}               # the subtitles read the cleaned sound from whatever session is current
    caps = None
    if session is not None and session.duration:
        import tempfile
        live_scenes = os.path.join(tempfile.gettempdir(), f"muteharam-scenes-{os.getpid()}.json")
        scan = start_scene_scan(r.get("mpv_url") or a.url, r.get("headers") or headers or {}, session.duration,
                                live_scenes, cfg, log, start=max(0.0, a.start), alts=list(a.alt or []))
        if scan is None:
            live_scenes = None
        elif a.tablet:
            front.scenes = scan.state
            front.scanner = scan
    if session is not None and session.duration:
        import tempfile
        live_subs = os.path.join(tempfile.gettempdir(), f"muteharam-subs-{os.getpid()}.json")
        try:
            caps = start_captions(lambda: current["s"], scan if live_scenes else None, session.duration, log,
                                  None if a.tablet else live_subs)
            if a.tablet:
                front.captions = caps
        except Exception as ex:
            caps = None
            log(f"captions: not available ({ex!r})")
    if a.tablet:
        return run_tablet(a, cfg, r, notes, session, problem, headers, log, front, tablet_srv)
    if session is not None:
        hub = SessionHub(session, session.remake)
        session = hub                      # closing later closes whichever session is current
        current["s"] = hub

        def scene_pos(q):
            t = (q.get("t") or [""])[0]
            if t.replace(".", "", 1).isdigit():
                for who in (scan if live_scenes else None, caps):
                    if who is not None:
                        who.follow(float(t))
            return {"ok": True}
        srv = make_server(hub, routes={"/level": lambda q: set_strength(hub, q, log),
                                       "/subs": lambda q: set_subtitles(caps, q, log), "/pos": scene_pos})
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
        cmd += [f"--audio-file={audio_url}", f"--script={os.path.join(HERE, 'mpv_select_audio.lua')}",
                f"--script={os.path.join(HERE, 'mpv_strength.lua')}",
                f"--script-opts-append=muteharam-level-port={srv.server_address[1]}",
                f"--script-opts-append=muteharam-level={getattr(hub, 'level', 'normal')}"]
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
    if live_scenes:
        cmd += scene_args(live_scenes)
    if caps is not None and problem is None:
        cmd += subs_args(live_subs, srv.server_address[1])
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
