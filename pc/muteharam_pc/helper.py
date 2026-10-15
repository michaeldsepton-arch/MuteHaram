"""
Small local service the browser extension talks to. It listens ONLY on
127.0.0.1, only accepts requests that come from a browser extension, and only
starts the MuteHaram player for http(s) links.

    python -m muteharam_pc.helper          (or start_helper.bat)
"""
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config

PORT = 47800
LOG_DIR = os.path.join(config.PC_DIR, "logs")
EXT_ORIGINS = ("chrome-extension://", "moz-extension://", "edge-extension://")


def python_exe():
    try:
        p = json.load(open(config.CONFIG_PATH)).get("python")
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    return sys.executable


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        print(time.strftime("%H:%M:%S"), fmt % a, flush=True)

    def _origin_ok(self):
        return (self.headers.get("Origin") or "").startswith(EXT_ORIGINS)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", ""))
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-MuteHaram")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self._origin_ok():
            return self._json(403, {"error": "not from an extension"})
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if not self._origin_ok():
            return self._json(403, {"error": "not from an extension"})
        if self.path == "/ping":
            return self._json(200, {"ok": True, "app": "muteharam"})
        self._json(404, {"error": "unknown"})

    def do_POST(self):
        if not self._origin_ok() or self.headers.get("X-MuteHaram") != "1":
            return self._json(403, {"error": "not from the MuteHaram extension"})
        if self.path == "/debug":
            # a report from the extension of what a page loaded (when no video stream was found)
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                os.makedirs(LOG_DIR, exist_ok=True)
                path = os.path.join(LOG_DIR, time.strftime("extension-debug-%Y%m%d-%H%M%S.txt"))
                nl = chr(10)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("page: " + str(req.get("page")) + nl + nl)
                    f.write("requests seen (newest last; images/scripts/styles left out):" + nl)
                    for r in req.get("requests") or []:
                        f.write("  " + str(r) + nl)
                    f.write(nl + "requests from background workers (no tab):" + nl)
                    for r in req.get("orphans") or []:
                        f.write("  " + str(r)[:230] + nl)
                return self._json(200, {"ok": True, "file": path})
            except Exception as ex:
                return self._json(500, {"error": repr(ex)})
        if self.path != "/play":
            return self._json(404, {"error": "unknown"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            url = str(req["url"])
            if not url.lower().startswith(("http://", "https://")):
                return self._json(400, {"error": "only http(s) links"})
            cmd = [python_exe(), "-m", "muteharam_pc.play", url]
            start = float(req.get("start") or 0)
            if start > 1:
                cmd += ["--start", str(start)]
            if req.get("audio") and str(req["audio"]).lower().startswith(("http://", "https://")):
                cmd += ["--audio-src", str(req["audio"])]
            for alt in (req.get("alts") or [])[:6]:
                if str(alt).lower().startswith(("http://", "https://")):
                    cmd += ["--alt", str(alt)]
            if req.get("page") and str(req["page"]).lower().startswith(("http://", "https://")):
                cmd += ["--page", str(req["page"])]
            for sub in (req.get("subs") or [])[:6]:
                if str(sub).lower().startswith(("http://", "https://")):
                    cmd += ["--sub", str(sub)]
            if req.get("pip"):
                cmd += ["--pip"]
            if req.get("referer"):
                cmd += ["--referer", str(req["referer"])]
            for k, v in (req.get("headers") or {}).items():
                if k.lower() in ("referer", "user-agent"):
                    cmd += ["--referer" if k.lower() == "referer" else "--user-agent", str(v)]
                elif k.lower() in ("cookie", "origin", "authorization"):
                    cmd += ["--header", f"{k}: {v}"]
            if os.environ.get("MUTEHARAM_ALLOW_PLAYER_ARGS") == "1":  # for tests only
                for a in req.get("mpv_args") or []:
                    cmd += [f"--mpv-arg={a}"]
            os.makedirs(LOG_DIR, exist_ok=True)
            log = open(os.path.join(LOG_DIR, time.strftime("play-%Y%m%d-%H%M%S.log")), "w")
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.Popen(cmd, cwd=config.PC_DIR, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
            self._json(200, {"ok": True})
        except Exception as ex:
            self._json(500, {"error": repr(ex)})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"MuteHaram helper listening on 127.0.0.1:{PORT}  (leave this window open; Ctrl+C to quit)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
