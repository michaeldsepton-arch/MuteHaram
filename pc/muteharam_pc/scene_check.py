"""
Scene check ("Lowering the gaze"): which frames of a film show nudity, something sexual or kissing.

Two detectors, both on this PC (nothing leaves it):
  * NudeNet: a small nudity detector (fast, on the processor);
  * a vision model in Ollama, asked a fixed yes/no question about the frame (also catches kissing).

This first part is the accuracy test: frames at chosen moments of a film, each detector's answer and how long
it took, so the detectors can be compared on real films before they are trusted.

    python -m muteharam_pc.scene_check --item <library id> --at 1:02:10,1:05:00 --every 300
    python -m muteharam_pc.scene_check --url <stream address> --at ...
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

from . import config

OLLAMA = "http://127.0.0.1:11434"
QUESTION = (
    "You check frames of a film for a family that avoids immodest scenes. Look at this frame and answer with JSON "
    'only, exactly these keys: {"nudity": true/false, "partial_nudity": true/false, "sexual": true/false, '
    '"kissing": true/false}. nudity = exposed private parts or bare breasts or bare buttocks. partial_nudity = '
    "underwear, lingerie, swimwear or a person mostly undressed. sexual = sexual activity or a clearly sexual "
    "pose or touching. kissing = two people kissing (on the lips or passionately). If unsure, answer false."
)
NUDE_LABELS = {"FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "BUTTOCKS_EXPOSED",
               "ANUS_EXPOSED"}
PARTIAL_LABELS = {"FEMALE_BREAST_COVERED", "BUTTOCKS_COVERED", "FEMALE_GENITALIA_COVERED", "BELLY_EXPOSED"}


def seconds(t):
    """'1:02:10' / '62:10' / '3730' -> seconds."""
    parts = [float(x) for x in str(t).split(":")]
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s


def clock(s):
    s = int(s)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def frame_at(url, headers, at, ffmpeg, width=640):
    """One frame of the film at `at` seconds, as JPEG bytes (b'' if it can't be read)."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if url.startswith(("http://", "https://")):
        cmd += ["-rw_timeout", "20000000"]
        if ".m3u8" in url.lower() or "/pl/" in url or "/vs/" in url:
            cmd += ["-extension_picky", "0"]
        h = dict(headers or {})
        ua = next((h.pop(k) for k in list(h) if k.lower() == "user-agent"), None)
        if ua:
            cmd += ["-user_agent", ua]
        if h:
            cmd += ["-headers", "".join(f"{k}: {v}" + chr(13) + chr(10) for k, v in h.items())]
    cmd += ["-ss", f"{at:.2f}", "-i", url, "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3",
            "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=90).stdout
    except Exception:
        return b""


_nude = None


def nudenet(jpeg, tmp):
    """-> (verdict, detail): 'nudity' / 'partial' / 'clear' and the parts NudeNet saw."""
    global _nude
    if _nude is None:
        from nudenet import NudeDetector
        _nude = NudeDetector()
    with open(tmp, "wb") as f:
        f.write(jpeg)
    found = [(d["class"], round(d["score"], 2)) for d in _nude.detect(tmp) if d["score"] >= 0.45]
    labels = {c for c, _ in found}
    verdict = "nudity" if labels & NUDE_LABELS else "partial" if labels & PARTIAL_LABELS else "clear"
    return verdict, found


def ask_model(model, jpeg):
    """-> (answers dict, seconds taken). The model answers the fixed question about the frame."""
    body = {"model": model, "prompt": QUESTION, "images": [base64.b64encode(jpeg).decode()], "stream": False,
            "format": "json", "think": False, "keep_alive": "10m", "options": {"temperature": 0, "num_predict": 80}}
    t = time.time()
    req = urllib.request.Request(OLLAMA + "/api/generate", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.loads(r.read())
    try:
        ans = json.loads(out.get("response") or "{}")
    except ValueError:
        ans = {"unreadable": out.get("response", "")[:80]}
    return ans, time.time() - t


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--item", help="library id of the film (uses its saved stream address)")
    ap.add_argument("--url", help="stream address (instead of --item)")
    ap.add_argument("--at", default="", help="moments to check, comma-separated (h:mm:ss), e.g. where a scene is known")
    ap.add_argument("--every", type=float, default=0, help="also check a frame every N seconds (normal scenes)")
    ap.add_argument("--models", default="gemma4:e4b,qwen2.5vl:3b", help="Ollama vision models to compare")
    ap.add_argument("--out", default="", help="folder for the frames and the report (default: pc/logs/scene-test-...)")
    a = ap.parse_args(argv)
    cfg = config.load()
    headers = {}
    url = a.url
    duration = None
    if a.item:
        from .library import Library
        lib = Library()
        url, headers = lib.picture(a.item)
        duration = (lib.progress(a.item) or {}).get("duration")
    if not url:
        raise SystemExit("give --item or --url")
    moments = [seconds(x) for x in a.at.split(",") if x.strip()]
    if a.every and duration:
        moments += [t for t in range(int(a.every), int(duration), int(a.every))]
    moments = sorted(set(moments))
    out = a.out or os.path.join(config.PC_DIR, "logs", time.strftime("scene-test-%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    models = [m for m in a.models.split(",") if m]
    report = []
    print(f"{len(moments)} frames, models: {', '.join(models)}; frames and report in {out}", flush=True)
    for at in moments:
        t = time.time()
        jpeg = frame_at(url, headers, at, cfg["ffmpeg"])
        grab = time.time() - t
        if not jpeg:
            print(f"{clock(at)}  could not read a frame", flush=True)
            continue
        name = os.path.join(out, clock(at).replace(":", "-") + ".jpg")
        with open(name, "wb") as f:
            f.write(jpeg)
        t = time.time()
        nv, nd = nudenet(jpeg, os.path.join(out, "_tmp.jpg"))
        row = {"at": clock(at), "frame": os.path.basename(name), "grab_s": round(grab, 1),
               "nudenet": nv, "nudenet_parts": nd, "nudenet_s": round(time.time() - t, 2)}
        for m in models:
            try:
                ans, took = ask_model(m, jpeg)
            except Exception as ex:
                ans, took = {"error": str(ex)[:80]}, 0.0
            row[m] = ans
            row[m + "_s"] = round(took, 2)
        report.append(row)
        flags = "  ".join(f"{m.split(':')[0]}: " + (",".join(k for k, v in row[m].items() if v is True) or "-")
                          + f" ({row[m + '_s']}s)" for m in models)
        print(f"{clock(at)}  nudenet: {nv} ({row['nudenet_s']}s)  {flags}", flush=True)
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    print("report:", os.path.join(out, "report.json"))


if __name__ == "__main__":
    sys.exit(main())
