"""Locate the tools and models (pc/config.json is written by setup_pc.py)."""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
PC_DIR = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(PC_DIR, "config.json")
TOOLS_DIR = os.path.join(PC_DIR, "tools")


def _first(*cands):
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def load():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        cfg = json.load(open(CONFIG_PATH))
    exe = ".exe" if os.name == "nt" else ""
    out = {
        "models_dir": cfg.get("models_dir") or os.path.join(PC_DIR, "models", "tiger-dnr"),
        "ffmpeg": _first(cfg.get("ffmpeg"), os.path.join(TOOLS_DIR, "ffmpeg" + exe)) or shutil.which("ffmpeg"),
        "ffprobe": _first(cfg.get("ffprobe"), os.path.join(TOOLS_DIR, "ffprobe" + exe)) or shutil.which("ffprobe"),
        "mpv": _first(cfg.get("mpv"), os.path.join(TOOLS_DIR, "mpv", "mpv" + exe)) or shutil.which("mpv"),
        "yt_dlp": _first(cfg.get("yt_dlp"), os.path.join(TOOLS_DIR, "yt-dlp" + exe)) or shutil.which("yt-dlp"),
    }
    missing = [k for k in ("ffmpeg", "ffprobe", "mpv") if not out[k]]
    if missing:
        raise SystemExit("Missing: " + ", ".join(missing) + "\nRun  python setup_pc.py  (or setup.bat) first.")
    return out
