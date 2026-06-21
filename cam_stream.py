#!/usr/bin/env python3
# Minimal multi-client MJPEG-over-HTTP server.
# Reads native MJPEG from a V4L2 camera via ffmpeg (-c:v copy, no transcode)
# and re-serves it as multipart/x-mixed-replace to any number of browsers.
#
# Usage: python3 cam_stream.py [PORT] [DEV] [WIDTH] [HEIGHT] [FPS]
import sys, subprocess, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT   = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
DEV    = sys.argv[2] if len(sys.argv) > 2 else "/dev/video0"
WIDTH  = int(sys.argv[3]) if len(sys.argv) > 3 else 1280
HEIGHT = int(sys.argv[4]) if len(sys.argv) > 4 else 720
FPS    = int(sys.argv[5]) if len(sys.argv) > 5 else 30
BOUND  = "frame"

latest = {"jpg": None, "n": 0, "err": None}
cond = threading.Condition()

def reader():
    cmd = ["ffmpeg", "-loglevel", "warning",
           "-f", "v4l2", "-input_format", "mjpeg",
           "-video_size", f"{WIDTH}x{HEIGHT}", "-framerate", str(FPS),
           "-i", DEV, "-c:v", "copy", "-f", "mpjpeg", "pipe:1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0)
    f = p.stdout
    try:
        while True:
            line = f.readline()
            if not line:
                break
            if line.lower().startswith(b"content-length:"):
                n = int(line.split(b":", 1)[1].strip())
                while True:                      # skip remaining headers until blank line
                    h = f.readline()
                    if h in (b"\r\n", b"\n", b""):
                        break
                data = b""
                while len(data) < n:             # read exactly n bytes of JPEG
                    c = f.read(n - len(data))
                    if not c:
                        break
                    data += c
                with cond:
                    latest["jpg"] = data
                    latest["n"] += 1
                    cond.notify_all()
    finally:
        rc = p.poll()
        with cond:
            latest["err"] = f"ffmpeg exited rc={rc}"
            latest["jpg"] = None
            cond.notify_all()

PAGE = b"""<!doctype html><html><head><meta charset="utf-8">
<title>C920 Live</title>
<style>html,body{margin:0;background:#0d0d0d;color:#bbb;font-family:system-ui,sans-serif;text-align:center}
h3{font-weight:400;padding:10px;margin:0}img{max-width:100%;height:auto;background:#000}
small{color:#666}a{color:#6cf}</style></head>
<body><h3>Logitech C920 &middot; /dev/video0 &middot; <span id=s>live</span></h3>
<img src="/stream" onerror="document.getElementById('s').textContent='stream error'">
<p><small>snapshot: <a href="/snapshot">/snapshot</a></small></p>
</body></html>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers(); self.wfile.write(PAGE); return
        if self.path.startswith("/snapshot"):
            with cond:
                if latest["jpg"] is None:
                    cond.wait(timeout=5)
                frame = latest["jpg"]
            if not frame:
                self.send_error(503, "no frame"); return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(frame)))
            self.end_headers(); self.wfile.write(frame); return
        if self.path.startswith("/stream"):
            self.send_response(200)
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUND}")
            self.end_headers()
            last = 0
            try:
                while True:
                    with cond:
                        while latest["n"] == last and latest["err"] is None:
                            cond.wait(timeout=5)
                        frame = latest["jpg"]; last = latest["n"]; err = latest["err"]
                    if not frame:
                        if err: break
                        continue
                    hdr = (b"--" + BOUND.encode() + b"\r\n"
                           b"Content-Type: image/jpeg\r\n"
                           b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n")
                    self.wfile.write(hdr); self.wfile.write(frame); self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        self.send_error(404)

def main():
    threading.Thread(target=reader, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"[cam_stream] serving http://0.0.0.0:{PORT}/  dev={DEV} {WIDTH}x{HEIGHT}@{FPS}", flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
