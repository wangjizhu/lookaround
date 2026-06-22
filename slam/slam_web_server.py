#!/usr/bin/env python3
# 轻量控制服务：服务 ~/slam/web 静态文件(index.html / map.json) + 控制端点。
# 网页按钮通过这些端点给 run_camera_web 发信号（靠落文件）：
#   GET /reset  -> 落 RESET 文件 -> SLAM 清空地图、回初始化（进程不退）
#   GET /stop   -> 落 STOP  文件 -> SLAM 优雅退出并存图
# 用法: python3 slam_web_server.py [PORT] [WEBDIR]
import os, sys, json, posixpath
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8091
WEBDIR = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser('~/slam/web'))
CT = {'.html': 'text/html; charset=utf-8', '.json': 'application/json',
      '.js': 'text/javascript', '.css': 'text/css'}


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *a): pass

    def _send(self, code, body, ctype='application/json'):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _touch(self, name):
        try:
            open(os.path.join(WEBDIR, name), 'w').close()
            return True
        except Exception:
            return False

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/reset':
            self._send(200, json.dumps({'ok': self._touch('RESET'), 'action': 'reset'})); return
        if path == '/stop':
            self._send(200, json.dumps({'ok': self._touch('STOP'), 'action': 'stop'})); return
        if path == '/':
            path = '/index.html'
        rel = posixpath.normpath(path).lstrip('/')
        fp = os.path.abspath(os.path.join(WEBDIR, rel))
        if not fp.startswith(WEBDIR) or not os.path.isfile(fp):
            self._send(404, json.dumps({'error': 'not found'})); return
        try:
            with open(fp, 'rb') as f:
                data = f.read()
        except Exception:
            self._send(500, json.dumps({'error': 'read'})); return
        self._send(200, data, CT.get(os.path.splitext(fp)[1], 'application/octet-stream'))


def main():
    srv = ThreadingHTTPServer(('0.0.0.0', PORT), H)
    print(f'[slam_web] http://0.0.0.0:{PORT}/  dir={WEBDIR}  endpoints: /reset /stop', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
