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


SETTINGS_PATH = os.path.join(PC_DIR, "settings.json")
DEFAULT_SETTINGS = {
    "mode": "music",          # "music": remove the music;  "speech": keep ONLY the voices (also removes sound effects)
    "gain": 1.0,              # 1.0 = remove fully, 0.5 = halve it, 1.3 = push a little harder
    "window": 10.0,           # AI window in seconds (bigger = better, slower)
    "hop": 6.0,
    "tablet_mode": "",        # "" = same as "mode"; "speech" = keep only voices for the tablet (drops music AND effects)
    "channels": "auto",       # auto | mono | stereo
    "sub_langs": "en.*,ar.*", # YouTube caption languages to load (yt-dlp style, "all" for everything)
    "pip": False,             # always start in the small corner window
}


def settings():
    """pc/settings.json overrides the defaults above (unknown keys are ignored). Command-line options win."""
    out = dict(DEFAULT_SETTINGS)
    try:
        if os.path.exists(SETTINGS_PATH):
            user = json.load(open(SETTINGS_PATH, encoding="utf-8"))
            for k in out:
                if k in user:
                    out[k] = user[k]
    except Exception as ex:
        print(f"settings.json could not be read ({ex}); using defaults", flush=True)
    return out
