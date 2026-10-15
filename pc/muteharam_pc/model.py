"""Load the TIGER-DnR *music* network (the part that estimates the music)."""
import json
import os

import torch
from safetensors.torch import load_file

from .tiger import TIGERDNR

SAMPLE_RATE = 44100  # the model's native rate

HERE = os.path.dirname(os.path.abspath(__file__))
PC_DIR = os.path.dirname(HERE)
DEFAULT_MODELS_DIR = os.path.join(PC_DIR, "models", "tiger-dnr")


def pick_device(prefer=None):
    if prefer:
        return torch.device(prefer)
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


# TIGER-DnR is three separate networks (dialogue / effects / music), each with 3 outputs; the
# output that is the network's own target: music net -> 0, effects net -> 1, dialogue net -> 2.
TARGET_INDEX = {"music": 0, "effect": 1, "dialog": 2}


def load_network(kind="music", models_dir=None, device=None):
    """Returns (network, device, target_index). network(x[B,1,T] float) -> [B,3,T]."""
    net, device = _load(kind, models_dir, device)
    return net, device, TARGET_INDEX[kind]


def load_music_model(models_dir=None, device=None):
    """Returns (network, device). network(x[B,1,T] float) -> [B,3,T]; source 0 is music."""
    return _load("music", models_dir, device)


def _load(kind, models_dir=None, device=None):
    models_dir = models_dir or DEFAULT_MODELS_DIR
    cfg_path = os.path.join(models_dir, "config.json")
    weights = os.path.join(models_dir, "model.safetensors")
    if not (os.path.exists(cfg_path) and os.path.exists(weights)):
        raise FileNotFoundError(
            f"Model files not found in {models_dir}. Run  python setup_pc.py  to download them.")
    full = TIGERDNR(**json.load(open(cfg_path)))
    full.load_state_dict(load_file(weights))
    net = getattr(full, kind).eval()  # only the one network we need goes to the GPU
    device = pick_device(device)
    net.to(device)
    for p in net.parameters():
        p.requires_grad_(False)
    return net, device
