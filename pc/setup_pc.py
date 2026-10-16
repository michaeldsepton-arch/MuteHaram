#!/usr/bin/env python3
"""
One-shot setup for MuteHaram PC on a new computer.

    python setup_pc.py              # everything
    python setup_pc.py --cpu        # force the CPU build of PyTorch
    python setup_pc.py --no-venv    # install into the Python you're running
    python setup_pc.py --skip-tools # don't download ffmpeg / mpv / yt-dlp
    python setup_pc.py --check      # only report what is missing

It is safe to run again: anything already present is skipped.

What it does
  1. creates pc/.venv and installs PyTorch (CUDA or CPU build, chosen from your
     graphics card) plus requirements.txt
  2. downloads the TIGER-DnR model weights (17 MB, Apache-2.0) into pc/models/
  3. downloads ffmpeg, mpv and yt-dlp into pc/tools/ unless already on PATH
  4. writes pc/config.json and runs a quick self-test
"""
import argparse, json, os, platform, shutil, subprocess, sys, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(HERE, ".venv")
TOOLS = os.path.join(HERE, "tools")
MODELS = os.path.join(HERE, "models", "tiger-dnr")
CONFIG = os.path.join(HERE, "config.json")
IS_WIN = platform.system() == "Windows"
EXE = ".exe" if IS_WIN else ""

HF = "https://huggingface.co/JusperLee/TIGER-DnR/resolve/main/"
WEIGHTS = {"config.json": 250, "model.safetensors": 17130568}  # expected sizes


def say(msg):
    print(f"\n==> {msg}", flush=True)


def _curl():
    return shutil.which("curl.exe") or shutil.which("curl")


def download(url, dest, what=None):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    try:
        _download_py(url, dest, tmp, what)
    except Exception as ex:
        # Some Python installs cannot check website certificates; Windows' own curl can.
        c = _curl()
        if not c:
            raise
        print(f"   Python could not download this ({type(ex).__name__}); using curl instead", flush=True)
        subprocess.run([c, "-L", "--fail", "-sS", "-A", "muteharam-setup", "-o", tmp, url], check=True)
        os.replace(tmp, dest)


def _download_py(url, dest, tmp, what=None):
    req = urllib.request.Request(url, headers={"User-Agent": "muteharam-setup"})
    with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                print(f"\r   {what or os.path.basename(dest)}: {got/1e6:6.1f} / {total/1e6:.1f} MB", end="", flush=True)
    print()
    os.replace(tmp, dest)


def run(cmd, **kw):
    print("   $", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def nvidia_compute_cap():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=compute_cap,name", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=20).stdout.strip().splitlines()[0]
        cap, name = [x.strip() for x in out.split(",", 1)]
        return float(cap), name
    except Exception:
        return None


def find_tool(name):
    local = os.path.join(TOOLS, name + EXE)
    if os.path.exists(local):
        return local
    for sub in ("ffmpeg", "mpv"):
        p = os.path.join(TOOLS, sub, name + EXE)
        if os.path.exists(p):
            return p
    return shutil.which(name)


# ----------------------------------------------------------------- python env
def setup_python(args):
    py = sys.executable
    if sys.version_info < (3, 10):
        sys.exit("Python 3.10 or newer is required (python.org). Found " + platform.python_version())
    if not args.no_venv:
        say("Creating virtual environment (pc/.venv)")
        if not os.path.exists(VENV):
            run([sys.executable, "-m", "venv", VENV])
        py = os.path.join(VENV, "Scripts" if IS_WIN else "bin", "python" + EXE)
    gpu = None if args.cpu else nvidia_compute_cap()
    if gpu:
        cap, name = gpu
        index = "cu128" if cap >= 12.0 else "cu121"
        say(f"NVIDIA GPU found: {name} (compute {cap}) -> PyTorch CUDA build ({index})")
    else:
        index = "cpu"
        say("No NVIDIA GPU (or --cpu) -> PyTorch CPU build. Works, but slower than real time.")
    have = subprocess.run([py, "-c", "import torch;print(torch.__version__)"], capture_output=True, text=True)
    if have.returncode == 0 and (index == "cpu" or "+cu" in have.stdout or ("cpu" not in have.stdout)):
        say(f"PyTorch already installed ({have.stdout.strip()}) -- skipping")
    else:
        say("Installing PyTorch (large download)")
        run([py, "-m", "pip", "install", "--upgrade", "pip"])
        run([py, "-m", "pip", "install", "torch", "--index-url", f"https://download.pytorch.org/whl/{index}"])
    say("Installing the other Python packages")
    run([py, "-m", "pip", "install", "-r", os.path.join(HERE, "requirements.txt")])
    return py


# -------------------------------------------------------------------- weights
def setup_weights():
    say("Model weights (TIGER-DnR)")
    for fn, size in WEIGHTS.items():
        dest = os.path.join(MODELS, fn)
        if os.path.exists(dest) and os.path.getsize(dest) == size:
            print(f"   {fn}: present")
            continue
        download(HF + fn, dest, fn)
        if os.path.getsize(dest) != size:
            sys.exit(f"{fn}: unexpected size {os.path.getsize(dest)} (expected {size}); delete it and retry")


# ---------------------------------------------------------------------- tools
def gh_latest(repo):
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"User-Agent": "muteharam-setup"})
    try:
        return json.load(urllib.request.urlopen(req))
    except Exception:
        c = _curl()
        if not c:
            raise
        out = subprocess.run([c, "-L", "--fail", "-sS", "-A", "muteharam-setup", req.full_url],
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)


def setup_tools():
    say("Tools: ffmpeg, mpv, yt-dlp")
    if not IS_WIN:
        missing = [t for t in ("ffmpeg", "ffprobe", "mpv", "yt-dlp") if not shutil.which(t)]
        if missing:
            print("   Not on PATH:", ", ".join(missing))
            print("   Install with your package manager, e.g.  brew install ffmpeg mpv yt-dlp   or   sudo apt install ffmpeg mpv yt-dlp")
        return
    os.makedirs(TOOLS, exist_ok=True)

    if not find_tool("yt-dlp"):
        download("https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
                 os.path.join(TOOLS, "yt-dlp.exe"))
    else:
        print("   yt-dlp: found")

    if not (find_tool("ffmpeg") and find_tool("ffprobe")):
        z = os.path.join(TOOLS, "ffmpeg.zip")
        download("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip", z)
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                base = os.path.basename(n)
                if base in ("ffmpeg.exe", "ffprobe.exe") and "/bin/" in n:
                    with zf.open(n) as src, open(os.path.join(TOOLS, base), "wb") as dst:
                        shutil.copyfileobj(src, dst)
        os.remove(z)
    else:
        print("   ffmpeg/ffprobe: found")

    if not find_tool("mpv"):
        rel = gh_latest("shinchiro/mpv-winbuild-cmake")
        asset = next(a for a in rel["assets"] if a["name"].startswith("mpv-x86_64-2"))
        sevenz = os.path.join(TOOLS, "7zr.exe")
        if not os.path.exists(sevenz):
            download("https://www.7-zip.org/a/7zr.exe", sevenz)  # official 7-Zip console extractor
        arc = os.path.join(TOOLS, asset["name"])
        download(asset["browser_download_url"], arc)
        out = os.path.join(TOOLS, "mpv")
        shutil.rmtree(out, ignore_errors=True)
        run([sevenz, "x", arc, f"-o{out}", "-y"], stdout=subprocess.DEVNULL)
        os.remove(arc)
    else:
        print("   mpv: found")


def setup_uosc():
    """uosc = the nicer on-screen controls for mpv (tooltips, shows on any mouse movement)."""
    say("Player controls (uosc add-on for mpv)")
    cfg_dir = os.path.join(HERE, "mpv_config")
    main_lua = os.path.join(cfg_dir, "scripts", "uosc", "main.lua")
    if os.path.exists(main_lua):
        print("   uosc: present")
        return
    rel = gh_latest("tomasklaen/uosc")
    asset = next(a for a in rel["assets"] if a["name"] == "uosc.zip")
    z = os.path.join(TOOLS, "uosc.zip")
    download(asset["browser_download_url"], z)
    with zipfile.ZipFile(z) as zf:
        zf.extractall(cfg_dir)       # scripts/uosc/... and fonts/...
    os.remove(z)
    print(f"   uosc {rel.get('tag_name', '')}: installed")


# --------------------------------------------------------------------- finish
def write_config(py):
    cfg = {
        "python": py,
        "models_dir": MODELS,
        "ffmpeg": find_tool("ffmpeg"),
        "ffprobe": find_tool("ffprobe"),
        "mpv": find_tool("mpv"),
        "yt_dlp": find_tool("yt-dlp"),
    }
    json.dump(cfg, open(CONFIG, "w"), indent=2)
    say("Wrote " + CONFIG)
    for k, v in cfg.items():
        print(f"   {k:11s} {v}")
    return cfg


def report_missing():
    say("Check")
    ok = True
    for t in ("ffmpeg", "ffprobe", "mpv", "yt-dlp"):
        p = find_tool(t)
        print(f"   {t:8s} {p or 'MISSING'}")
        ok &= bool(p)
    for fn, size in WEIGHTS.items():
        p = os.path.join(MODELS, fn)
        good = os.path.exists(p) and os.path.getsize(p) == size
        print(f"   {fn:18s} {'ok' if good else 'MISSING'}")
        ok &= good
    uosc = os.path.exists(os.path.join(HERE, "mpv_config", "scripts", "uosc", "main.lua"))
    print(f"   uosc     {'ok' if uosc else 'MISSING (player still works, with plain controls)'}")
    venv_py = os.path.join(VENV, "Scripts" if IS_WIN else "bin", "python" + EXE)
    print(f"   venv     {'ok' if os.path.exists(venv_py) else 'MISSING'}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--no-venv", action="store_true")
    ap.add_argument("--skip-tools", action="store_true")
    ap.add_argument("--skip-weights", action="store_true")
    ap.add_argument("--skip-python", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        sys.exit(0 if report_missing() else 1)
    py = sys.executable if args.skip_python else setup_python(args)
    if not args.skip_weights:
        setup_weights()
    if not args.skip_tools:
        setup_tools()
        setup_uosc()
    write_config(py)
    if not args.skip_python:
        say("Self-test")
        r = subprocess.run([py, "-m", "muteharam_pc.selftest"], cwd=HERE)
        if r.returncode != 0:
            sys.exit("Self-test failed -- see messages above.")
    say("Done. Next:  python -m muteharam_pc.play <video link>   (see README.md)")


if __name__ == "__main__":
    main()
