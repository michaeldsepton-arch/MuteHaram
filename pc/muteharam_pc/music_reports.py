"""
"Music here": the viewer heard music that slipped through. Each report is one line in logs/music-reports.jsonl.

For the seconds around the tap, part by part: what cleaned it, and what the music detector hears in the original and
in what was played (music and voice, 0..1), and how loud each was. Enough to see later which model / setting lets
music through, and where: e.g. "auto kept the normal cleaning for the voice" or "the normal model barely changed
this part".
The sound of that stretch is kept too, original and played (FLAC, in logs/music-reports/), ONLY to measure and train
the cleaning on: it is never played to the viewer (the user's wish). logs/ is never uploaded (.gitignore).

    python -m muteharam_pc.music_reports        -> a summary of all reports so far
"""
import json
import os
import threading
import time

import numpy as np

from . import config

PATH = os.path.join(config.PC_DIR, "logs", "music-reports.jsonl")
BEFORE_S = 10.0         # the music was heard just before the tap
AFTER_S = 2.0
_lock = threading.Lock()


def _db(x):
    return round(float(20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-9)), 1)


def count():
    try:
        with open(PATH, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def report(hub, pos_s, film, log=print):
    """Note music heard at pos_s (seconds of film). Measures in the background; returns at once."""
    pos_s = max(0.0, float(pos_s))
    threading.Thread(target=_measure, args=(hub, pos_s, dict(film or {}), log), daemon=True).start()
    log(f"music here: reported at {int(pos_s // 60)}:{int(pos_s % 60):02d}")
    return {"ok": True, "n": count() + 1}


def _measure(hub, pos_s, film, log):
    from .engine import SAMPLE_RATE, _detector
    cur = getattr(hub, "cur", hub)
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "film": film, "at_s": round(pos_s, 1),
             "at": f"{int(pos_s // 3600)}:{int(pos_s % 3600 // 60):02d}:{int(pos_s % 60):02d}",
             "level": getattr(hub, "level", "?"), "parts": []}
    try:
        import torch
        score = None
        try:
            score = _detector(torch.device("cuda" if torch.cuda.is_available() else "cpu"))
        except Exception as ex:
            entry["detector"] = f"not available: {ex!r}"
        hop = getattr(cur, "hop", 6 * SAMPLE_RATE)
        t0 = max(0.0, pos_s - BEFORE_S)
        sound = {"original": [], "played": []}
        for b in range(int(t0 * SAMPLE_RATE) // hop, int((pos_s + AFTER_S) * SAMPLE_RATE) // hop + 1):
            f0, f1 = b * hop, (b + 1) * hop
            part = {"from_s": round(f0 / SAMPLE_RATE, 1), "to_s": round(f1 / SAMPLE_RATE, 1),
                    "cleaned_by": (getattr(cur, "picked", {}) or {}).get(b)}
            played = hub.peek_frames(f0, f1)
            orig = None
            src = getattr(cur, "src", None)
            if src is not None:
                try:
                    orig = src.read_s16(f0 / SAMPLE_RATE, hop / SAMPLE_RATE)
                except Exception as ex:
                    part["original"] = f"could not read: {ex!r}"
            for name, a in (("original", orig), ("played", played)):
                piece = np.zeros((hop, 2), np.int16)
                if a is not None and len(a):
                    piece[:min(hop, len(a))] = np.asarray(a, np.int16)[:hop]
                sound[name].append(piece)
                if a is None or len(a) == 0:
                    continue
                x = np.asarray(a, np.float32) / 32768.0
                d = {"loudness_db": _db(x)}
                if score is not None:
                    m, v = score(x)
                    d["music"], d["voice"] = round(float(m), 3), round(float(v), 3)
                part[name] = d
            entry["parts"].append(part)
        # the sound of the whole stretch (never played back: only for measuring and training the cleaning)
        try:
            import soundfile as sf
            d = os.path.join(os.path.dirname(PATH), "music-reports")
            os.makedirs(d, exist_ok=True)
            stem = time.strftime("%Y%m%d-%H%M%S") + f"-{int(pos_s)}s"
            entry["sound"] = {"from_s": entry["parts"][0]["from_s"] if entry["parts"] else None}
            for name, pieces in sound.items():
                if pieces and any(p.any() for p in pieces):
                    f = os.path.join(d, f"{stem}-{name}.flac")
                    sf.write(f, np.concatenate(pieces), SAMPLE_RATE, format="FLAC")
                    entry["sound"][name] = os.path.relpath(f, os.path.dirname(PATH))
        except Exception as ex:
            entry["sound_error"] = repr(ex)
    except Exception as ex:
        entry["error"] = repr(ex)
    with _lock:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    worst = max((p.get("played", {}).get("music", 0) for p in entry["parts"]), default=0)
    log(f"music here: noted at {entry['at']} (music the detector still hears there: {worst:.2f})")


def summary():
    try:
        rows = [json.loads(l) for l in open(PATH, encoding="utf-8") if l.strip()]
    except OSError:
        rows = []
    print(f"{len(rows)} report(s) in {PATH}")
    by = {}
    for r in rows:
        for p in r.get("parts", []):
            used = (p.get("cleaned_by") or {}).get("used", r.get("level", "?"))
            pl = p.get("played") or {}
            by.setdefault(used, []).append((pl.get("music"), (p.get("original") or {}).get("music")))
    for used, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        ms = [a for a, _ in v if a is not None]
        mo = [b for _, b in v if b is not None]
        print(f"  {used:55.55s} {len(v):4d} part(s) | music heard after: {np.mean(ms) if ms else float('nan'):.2f}"
              f" | before: {np.mean(mo) if mo else float('nan'):.2f}")
    for r in rows[-10:]:
        print(f"  {r['time']}  {r['at']}  {r.get('level')}  {(r.get('film') or {}).get('title') or ''}")


if __name__ == "__main__":
    summary()
