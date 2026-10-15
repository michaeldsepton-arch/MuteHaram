"""
Serves the cleaned audio of a Session as a virtual 16-bit stereo 44.1 kHz WAV
file over HTTP (with Range support so players can seek). Nothing is stored as a
WAV: bytes are produced on demand from the engine's blocks.
"""
import re
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .engine import CHANNELS
from .model import SAMPLE_RATE

BYTES_PER_FRAME = CHANNELS * 2
HEADER_LEN = 44


def wav_header(frames):
    data = frames * BYTES_PER_FRAME
    return (b"RIFF" + struct.pack("<I", 36 + data) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, CHANNELS, SAMPLE_RATE, SAMPLE_RATE * BYTES_PER_FRAME,
                        BYTES_PER_FRAME, 16) + b"data" + struct.pack("<I", data))


def make_server(session, host="127.0.0.1", port=0):
    total = HEADER_LEN + session.total_frames * BYTES_PER_FRAME
    header = wav_header(session.total_frames)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _range(self):
            m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if not m:
                return 0, total - 1, False
            a, b = m.groups()
            if a == "":
                start, end = max(0, total - int(b)), total - 1
            else:
                start = int(a)
                end = int(b) if b else total - 1
            return start, min(end, total - 1), True

        def _head(self, start, end, partial):
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            self.end_headers()

        def do_HEAD(self):
            self._head(0, total - 1, False)

        def do_GET(self):
            if self.path.split("?")[0] != "/audio.wav":
                self.send_error(404)
                return
            start, end, partial = self._range()
            if start >= total:
                self.send_error(416)
                return
            self._head(start, end, partial)
            try:
                pos = start
                if pos < HEADER_LEN:
                    n = min(HEADER_LEN, end + 1) - pos
                    self.wfile.write(header[pos:pos + n])
                    pos += n
                while pos <= end:
                    f0 = (pos - HEADER_LEN) // BYTES_PER_FRAME
                    f1 = min(f0 + SAMPLE_RATE * 2, (end - HEADER_LEN) // BYTES_PER_FRAME + 1)
                    frames = session.read_frames(f0, f1)
                    raw = frames.tobytes()
                    skip = (pos - HEADER_LEN) - f0 * BYTES_PER_FRAME
                    chunk = raw[skip:skip + (end - pos + 1)]
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    pos += len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as ex:  # engine failure / timeout: drop the connection
                session.log(f"server: stopping a request: {ex!r}")
                self.close_connection = True

    class Server(ThreadingHTTPServer):
        daemon_threads = True

    srv = Server((host, port), Handler)
    threading.Thread(target=srv.serve_forever, name="audio-http", daemon=True).start()
    return srv
