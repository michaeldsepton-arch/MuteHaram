"""Set up SAM Audio (Meta) as the best music-removal mode. Run once:  python setup_sam.py

SAM Audio needs its own Python environment (many extra packages), so it is installed apart from the player.
Before running this:
  1. Make a free account at https://huggingface.co and request access on https://huggingface.co/facebook/sam-audio-small
     (Meta approves it, usually within minutes to a day).
  2. Create a "read" token at https://huggingface.co/settings/tokens and run  hf auth login  (or: python -m huggingface_hub.commands.huggingface_cli login).
Needs Python 3.11 or newer and a graphics card with 8 GB or more.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SAM_DIR = os.path.join(HERE, "models", "sam-audio")
VENV = os.path.join(SAM_DIR, "venv")
REPO = os.path.join(SAM_DIR, "sam-audio-src")
GIT_DEPS = [
    "dacvae@git+https://github.com/facebookresearch/dacvae.git",
    "imagebind@git+https://github.com/facebookresearch/ImageBind.git",
    "laion-clap@git+https://github.com/lematt1991/CLAP.git",
    "perception-models@git+https://github.com/facebookresearch/perception_models@unpin-deps",
]
# found one by one: what SAM Audio imports, installed WITHOUT their own dependency lists so the
# graphics-card build of torch that is already there is never replaced
PLAIN_DEPS = ["descript-audiotools", "audiobox_aesthetics", "torchdiffeq", "einops", "pydub", "argbind", "julius",
              "pyloudnorm", "randomname", "flatten-dict", "ffmpy", "markdown2", "librosa", "soxr", "timm",
              "absl-py", "tensorboard-data-server", "grpcio", "markdown", "werkzeug", "protobuf", "scipy",
              "safetensors", "huggingface_hub", "transformers>=4.54"]


def run(args, **kw):
    print("   $", " ".join(map(str, args))[:150])
    return subprocess.run(args, **kw)


def main():
    if sys.version_info < (3, 11):
        sys.exit("SAM Audio needs Python 3.11 or newer.")
    os.makedirs(SAM_DIR, exist_ok=True)
    if not os.path.exists(VENV):
        run([sys.executable, "-m", "venv", "--system-site-packages", VENV], check=True)
    py = os.path.join(VENV, "Scripts" if os.name == "nt" else "bin", "python.exe" if os.name == "nt" else "python")
    pip = [py, "-m", "pip", "install", "-q"]
    run(pip + ["wheel", "setuptools"], check=True)
    import torch
    print(f"   using torch {torch.__version__} (kept as it is)")
    tv = torch.__version__.split("+")[0]
    cu = torch.__version__.split("+")[1] if "+" in torch.__version__ else "cpu"
    # torchvision and xformers must match torch exactly
    tvv = {"2.5.1": ("0.20.1", "0.0.27.post2")}.get(tv)
    if tvv:
        idx = f"https://download.pytorch.org/whl/{cu}"
        run(pip + ["--no-deps", f"torchvision=={tvv[0]}", "--index-url", idx], check=True)
        run(pip + ["--no-deps", "xformers==0.0.27.post2", "--index-url", idx])
    else:
        run(pip + ["--no-deps", "torchvision", "xformers"])
    # xformers only has to be importable: its compiled parts do not match this torch and make Windows show an
    # "entry point not found" box, so they are removed (it then runs without them)
    site0 = subprocess.check_output([py, "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"], text=True).strip()
    import glob
    for p in glob.glob(os.path.join(site0, "xformers", "*.pyd")):
        os.remove(p)
    if not os.path.exists(REPO):
        run(["git", "clone", "--depth", "1", "https://github.com/facebookresearch/sam-audio.git", REPO], check=True)
    base = os.path.join(REPO, "sam_audio", "model", "base.py")
    with open(base, encoding="utf-8") as f:
        t = f.read()
    t = t.replace("        proxies: Optional[Dict],", "        proxies: Optional[Dict] = None,")
    t = t.replace("        resume_download: bool,", "        resume_download: bool = False,")      # newer huggingface_hub does not pass these
    with open(base, "w", encoding="utf-8") as f:
        f.write(t)
    run(pip + ["--no-deps", "--no-build-isolation", REPO], check=True)
    run(pip + ["--no-deps", "--no-build-isolation"] + GIT_DEPS, check=True)
    run(pip + ["--no-deps"] + PLAIN_DEPS)
    # video-reading part of the package: not needed (we only handle sound) and has no build for this torch
    site = subprocess.check_output([py, "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"], text=True).strip()
    stub = os.path.join(site, "torchcodec")
    if not os.path.exists(os.path.join(stub, "decoders.py")):
        os.makedirs(stub, exist_ok=True)
        with open(os.path.join(stub, "__init__.py"), "w") as f:
            f.write("")
        with open(os.path.join(stub, "decoders.py"), "w") as f:
            f.write('class AudioDecoder:\n    def __init__(self, *a, **k):\n        raise RuntimeError("not available")\n'
                    'class VideoDecoder(AudioDecoder):\n    pass\n')
        meta = os.path.join(site, "torchcodec-0.7.0.dist-info")
        os.makedirs(meta, exist_ok=True)
        with open(os.path.join(meta, "METADATA"), "w") as f:
            f.write("Metadata-Version: 2.1\nName: torchcodec\nVersion: 0.7.0\n")
    r = run([py, "-c", "from sam_audio import SAMAudio, SAMAudioProcessor; print('import ok')"])
    if r.returncode != 0:
        sys.exit("SAM Audio could not be imported; see the messages above.")
    print("   downloading the model (about 5 GB, needs the Hugging Face login) ...")
    r = run([py, "-c", "from huggingface_hub import snapshot_download as d; d('facebook/sam-audio-small')"],
            env=dict(os.environ, HF_HUB_DISABLE_XET="1"))
    if r.returncode != 0:
        sys.exit("The model download failed. Check that Meta approved your access and that you are logged in "
                 "(hf auth login), then run this again.")
    with open(os.path.join(SAM_DIR, "python.txt"), "w", encoding="utf-8") as f:
        f.write(py)
    print("\nSAM Audio is ready. Choose \"SAM Audio\" in the extension menu, or press C in the player.")


if __name__ == "__main__":
    main()
