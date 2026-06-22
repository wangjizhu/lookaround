#!/usr/bin/env python3
# 轻量控制服务 + 进程管理：服务 ~/slam/web 静态文件(index.html / map.json) + 端点：
#   GET /start  -> 启动 run_camera_web（之前相机空闲、不空转）
#   GET /stop   -> 落 STOP 文件 -> SLAM 优雅退出并存图
#   GET /reset  -> 落 RESET 文件 -> SLAM 清空地图、回初始化（进程不退）
#   GET /status -> {"running": bool}
# 用法: python3 slam_web_server.py [PORT] [WEBDIR]
import os, sys, json, posixpath, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8091
HOME = os.path.expanduser('~')
WEBDIR = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else f'{HOME}/slam/web')
BIN = f'{HOME}/slam/stella_vslam_examples/build/run_camera_web'
ENV = dict(os.environ, LD_LIBRARY_PATH=f'{HOME}/slam/deps/lib')


def detect_cam(default=3):
    """重启后 /dev/videoN 可能变号 —— 自动找 C920(uvcvideo + MJPG 的采集口)。"""
    import glob
    cands = []
    for dev in sorted(glob.glob('/dev/video*')):
        s = dev[len('/dev/video'):]
        if not s.isdigit():
            continue
        n = int(s)
        try:
            drv = os.path.realpath(f'/sys/class/video4linux/video{n}/device/driver')
        except Exception:
            continue
        if 'uvcvideo' not in drv:
            continue
        try:
            out = subprocess.run(['v4l2-ctl', '-d', dev, '--list-formats'],
                                 capture_output=True, text=True).stdout
            if 'MJPG' in out or 'Motion-JPEG' in out:
                return n  # 采集口(支持 MJPG)，优先
        except Exception:
            pass
        cands.append(n)
    return cands[0] if cands else default


def slam_cmd():
    cam = str(detect_cam(3))
    return [BIN, '-v', f'{HOME}/slam/orb_vocab.fbow', '-c', f'{HOME}/slam/c920_mono.yaml',
            '-n', cam, '--web-dir', WEBDIR, '-o', f'{HOME}/slam/map_calib.msg', '--dump-every', '8']
CT = {'.html': 'text/html; charset=utf-8', '.json': 'application/json',
      '.js': 'text/javascript', '.css': 'text/css'}


def slam_running():
    try:
        return subprocess.run(['pgrep', '-x', 'run_camera_web'], capture_output=True).returncode == 0
    except Exception:
        return False


def start_slam():
    if slam_running():
        return False, 'already_running'
    subprocess.run(['pkill', '-f', 'cam_imu_server'], capture_output=True)  # 释放 /dev/video3
    for f in ('STOP', 'RESET'):
        try:
            os.remove(os.path.join(WEBDIR, f))
        except Exception:
            pass
    try:
        logf = open('/tmp/slam_run.log', 'ab')
        subprocess.Popen(slam_cmd(), env=ENV, stdout=logf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        return True, 'started'
    except Exception as e:
        return False, str(e)


def touch(name):
    try:
        open(os.path.join(WEBDIR, name), 'w').close()
        return True
    except Exception:
        return False


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

    def do_GET(self):
        p = self.path.split('?')[0]
        if p == '/start':
            ok, msg = start_slam(); self._send(200, json.dumps({'ok': ok, 'msg': msg})); return
        if p == '/stop':
            self._send(200, json.dumps({'ok': touch('STOP'), 'action': 'stop'})); return
        if p == '/reset':
            self._send(200, json.dumps({'ok': touch('RESET'), 'action': 'reset'})); return
        if p == '/status':
            self._send(200, json.dumps({'running': slam_running()})); return
        if p == '/':
            p = '/index.html'
        rel = posixpath.normpath(p).lstrip('/')
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
    print(f'[slam_web] http://0.0.0.0:{PORT}/  dir={WEBDIR}  endpoints: /start /stop /reset /status', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
