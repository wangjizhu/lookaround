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


# ---------- 观察模式：加载已存地图(.msg)只读显示 ----------
BIN_DUMP = f'{HOME}/slam/stella_vslam_examples/build/dump_map'
_view_map = None  # 当前观察模式加载的存图名；None=非观察


def list_maps():
    out = []
    d = os.path.join(HOME, 'slam')
    try:
        for f in sorted(os.listdir(d)):
            if f.endswith('.msg'):
                try:
                    sz = os.path.getsize(os.path.join(d, f))
                except Exception:
                    sz = 0
                out.append({'name': f, 'mb': round(sz / 1e6, 1)})
    except Exception:
        pass
    return out


def open_map(name):
    global _view_map
    if not name or not name.endswith('.msg') or '/' in name:
        return False, 'bad name'
    path = os.path.join(HOME, 'slam', name)
    if not os.path.isfile(path):
        return False, 'not found'
    if slam_running():
        return False, 'mapping_running'  # 建图中不允许进观察
    cmd = [BIN_DUMP, '-v', f'{HOME}/slam/orb_vocab.fbow', '-c', f'{HOME}/slam/c920_mono.yaml',
           '-i', path, '-o', os.path.join(WEBDIR, 'map.json'), '--label', '观察: ' + name]
    try:
        r = subprocess.run(cmd, env=ENV, capture_output=True, text=True, timeout=120)
    except Exception as e:
        return False, str(e)
    if r.returncode != 0:
        return False, (r.stderr or 'dump failed')[-200:]
    _view_map = name
    return True, 'opened'


def close_view():
    global _view_map
    _view_map = None


def cur_mode():
    if slam_running():
        return 'mapping', None
    if _view_map:
        return 'viewing', _view_map
    return 'idle', None
CT = {'.html': 'text/html; charset=utf-8', '.json': 'application/json',
      '.js': 'text/javascript', '.css': 'text/css'}


def slam_running():
    try:
        return subprocess.run(['pgrep', '-x', 'run_camera_web'], capture_output=True).returncode == 0
    except Exception:
        return False


def start_slam():
    global _view_map
    if slam_running():
        return False, 'already_running'
    _view_map = None  # 进入建图，退出观察模式
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
            mode, m = cur_mode()
            self._send(200, json.dumps({'running': slam_running(), 'mode': mode, 'map': m})); return
        if p == '/maps':
            self._send(200, json.dumps({'maps': list_maps()})); return
        if p == '/open':
            from urllib.parse import urlparse, parse_qs
            name = parse_qs(urlparse(self.path).query).get('map', [''])[0]
            ok, msg = open_map(name)
            self._send(200, json.dumps({'ok': ok, 'msg': msg})); return
        if p == '/close':
            close_view()
            self._send(200, json.dumps({'ok': True, 'action': 'close'})); return
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
