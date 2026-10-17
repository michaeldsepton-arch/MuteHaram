"""
Wi-Fi server: lets the Android hook on a tablet/phone use THIS computer's graphics
card to remove the music from its audio in real time.

    python -m muteharam_pc.stream_server            (or start_wifi_server.bat)

The tablet sends short windows of mono 44.1 kHz audio; this program returns the
estimated music for each window (the tablet subtracts it). Everything stays on your
home network.

Protocol (all integers little-endian), one TCP connection per audio stream:
  client -> server   b"MHR1" | u8 tokenLength | token bytes
  server -> client   b"MHR1" | u8 status          (0 = ok, 1 = wrong token)
  then repeated:
  client -> server   u32 seq | u32 n | n * float32      mono audio, 44100 Hz
  server -> client   u32 seq | u32 n | n * float32      music estimate, same length
                     (n = 0xFFFFFFFF means "failed", no payload)
"""
import json
import os
import secrets
import socket
import socketserver
import struct
import sys
import threading
import time

import numpy as np
import torch

from . import config
from .model import load_music_model

MAGIC = b"MHR1"
PORT = 47801
MAX_SAMPLES = 44100 * 30
ERROR_N = 0xFFFFFFFF
TOKEN_FILE = os.path.join(config.PC_DIR, "wifi_token.txt")


def read_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(1 << 20, n - len(buf)))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return bytes(buf)


def lan_addresses():
    addrs = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        addrs.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            addrs.add(ip)
    except Exception:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


def load_or_create_token():
    if os.path.exists(TOKEN_FILE):
        t = open(TOKEN_FILE).read().strip()
        if t:
            return t
    t = secrets.token_hex(4)
    open(TOKEN_FILE, "w").write(t + "\n")
    return t


class Engine:
    def __init__(self, models_dir=None, device=None):
        self.net, self.device = load_music_model(models_dir, device)
        self.lock = threading.Lock()
        self.windows = 0
        self.busy = 0.0

    @torch.no_grad()
    def music(self, x):
        t0 = time.time()
        with self.lock:
            t = torch.from_numpy(x)[None, None].to(self.device)
            y = self.net(t)[0, 0].float().cpu().numpy()
        self.windows += 1
        self.busy += time.time() - t0
        return y


def make_handler(engine, token):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            sock = self.request
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(60)
            peer = self.client_address[0]
            try:
                if read_exact(sock, 4) != MAGIC:
                    return
                tlen = read_exact(sock, 1)[0]
                got = read_exact(sock, tlen).decode("utf-8", "replace")
                ok = (not token) or secrets.compare_digest(got, token)
                sock.sendall(MAGIC + bytes([0 if ok else 1]))
                if not ok:
                    print(f"{peer}: wrong token, closed", flush=True)
                    return
                print(f"{peer}: connected", flush=True)
                served = 0
                dump = []
                while True:
                    seq, n = struct.unpack("<II", read_exact(sock, 8))
                    if n == 0 or n > MAX_SAMPLES:
                        sock.sendall(struct.pack("<II", seq, ERROR_N))
                        continue
                    x = np.frombuffer(read_exact(sock, n * 4), dtype="<f4").copy()
                    t0 = time.time()
                    try:
                        y = engine.music(x)
                        sock.sendall(struct.pack("<II", seq, n) + y.astype("<f4").tobytes())
                        served += 1
                        if served <= 12:      # keep the first windows so problems can be examined afterwards
                            dump.append((x.copy(), y.copy()))
                            if served == 12:
                                try:
                                    os.makedirs(os.path.join(config.PC_DIR, "logs"), exist_ok=True)
                                    np.savez(os.path.join(config.PC_DIR, "logs", "wifi-dump.npz"),
                                             x=np.stack([d[0] for d in dump]), y=np.stack([d[1] for d in dump]))
                                    print(f"{peer}: saved the first 12 windows to pc/logs/wifi-dump.npz", flush=True)
                                except Exception as ex:
                                    print(f"could not save the dump: {ex!r}", flush=True)
                        if served % 10 == 1:
                            rin = float(np.sqrt(np.mean(x * x)) + 1e-9)
                            rmu = float(np.sqrt(np.mean(y * y)) + 1e-9)
                            rres = float(np.sqrt(np.mean((x - y) ** 2)) + 1e-9)
                            print(f"{peer}: window {served} ({n/44100:.1f}s of audio) took "
                                  f"{(time.time()-t0)*1000:.0f} ms | sound in {20*np.log10(rin):.0f} dB, "
                                  f"music found {20*np.log10(rmu):.0f} dB, left after removal {20*np.log10(rres):.0f} dB",
                                  flush=True)
                    except Exception as ex:
                        print(f"{peer}: processing failed: {ex!r}", flush=True)
                        sock.sendall(struct.pack("<II", seq, ERROR_N))
            except (ConnectionError, socket.timeout, OSError):
                pass
            finally:
                print(f"{peer}: disconnected", flush=True)

    return Handler


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-token", action="store_true", help="accept any device on the network (not recommended)")
    ap.add_argument("--cpu", action="store_true")
    a = ap.parse_args()
    try:
        models_dir = json.load(open(config.CONFIG_PATH)).get("models_dir")
    except Exception:
        models_dir = None
    engine = Engine(models_dir, "cpu" if a.cpu else None)
    token = "" if a.no_token else load_or_create_token()
    # warm-up so the first real window isn't slow
    engine.music(np.zeros(44100 * 3, dtype=np.float32))
    srv = Server(("0.0.0.0", a.port), make_handler(engine, token))
    print(f"\nMuteHaram Wi-Fi server running on {engine.device}.", flush=True)
    print("On the tablet (with adb), run:", flush=True)
    for ip in lan_addresses() or ["<this computer's IP>"]:
        print(f"  adb shell settings put global muteharam_server {ip}:{a.port}", flush=True)
    if token:
        print(f"  adb shell settings put global muteharam_token {token}", flush=True)
    print("  adb shell settings put global muteharam_mode remote", flush=True)
    print("\nIf Windows asks about the firewall, allow PRIVATE networks. Ctrl+C to stop.\n", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
