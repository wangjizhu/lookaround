#!/usr/bin/env python3
# 摄像头(MJPEG) + IMU(HiPNUC HI12) 实时混合监控网页服务
#
#   /            综合页面：摄像头画面 + IMU 数字 + 3D 姿态立方体
#   /stream      摄像头 MJPEG (multipart/x-mixed-replace)
#   /snapshot    摄像头单帧 JPEG
#   /imu         IMU 实时数据 (Server-Sent Events, JSON)
#   /imu.json    IMU 最新一帧 (一次性 JSON, 便于调试)
#
# 用法: python3 cam_imu_server.py [PORT] [CAMDEV] [IMUDEV] [W] [H] [FPS]
# 依赖: ffmpeg (摄像头, -c:v copy 不转码); 纯标准库, 无需 pyserial
import os, sys, struct, time, math, json, threading, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT   = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
CAMDEV = sys.argv[2] if len(sys.argv) > 2 else "/dev/video3"
IMUDEV = sys.argv[3] if len(sys.argv) > 3 else "/dev/ttyUSB0"
W      = int(sys.argv[4]) if len(sys.argv) > 4 else 1280
H      = int(sys.argv[5]) if len(sys.argv) > 5 else 720
FPS    = int(sys.argv[6]) if len(sys.argv) > 6 else 30
BAUD   = 115200
BOUND  = "frame"

# ===================== 摄像头读取线程 =====================
cam = {"jpg": None, "n": 0}
cam_cond = threading.Condition()

def cam_reader():
    cmd = ["ffmpeg", "-loglevel", "error", "-f", "v4l2", "-input_format", "mjpeg",
           "-video_size", f"{W}x{H}", "-framerate", str(FPS), "-i", CAMDEV,
           "-c:v", "copy", "-f", "mpjpeg", "pipe:1"]
    while True:
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0)
            f = p.stdout
            while True:
                line = f.readline()
                if not line:
                    break
                if line.lower().startswith(b"content-length:"):
                    n = int(line.split(b":", 1)[1].strip())
                    while True:
                        h = f.readline()
                        if h in (b"\r\n", b"\n", b""):
                            break
                    data = b""
                    while len(data) < n:
                        c = f.read(n - len(data))
                        if not c:
                            break
                        data += c
                    with cam_cond:
                        cam["jpg"] = data; cam["n"] += 1; cam_cond.notify_all()
        except Exception:
            pass
        try:
            p.kill()
        except Exception:
            pass
        time.sleep(1.0)   # ffmpeg 退出后重启

# ===================== IMU 读取线程 =====================
# 帧: 5A A5 | len(2,LE) | crc(2,LE) | payload(len)
# payload 0x91: tag(1) id(1) rev(2) prs(4) ts(4,ms) acc[3] gyr[3] mag[3] eul[3] quat[4] (f32 LE)
imu = {"data": None, "n": 0, "ok": 0, "bad": 0, "rate": 0.0, "err": None}
imu_cond = threading.Condition()

def crc16(data, crc=0):
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc

def cfg_serial(fd, baud):
    import termios
    a = termios.tcgetattr(fd)
    a[0] = 0  # iflag
    a[1] = 0  # oflag
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # cflag: 8N1
    a[3] = 0  # lflag: 非规范, 无回显
    sp = getattr(termios, f"B{baud}")
    a[4] = sp; a[5] = sp
    a[6][termios.VMIN] = 0; a[6][termios.VTIME] = 1
    termios.tcsetattr(fd, termios.TCSANOW, a)

def imu_reader():
    while True:
        try:
            fd = os.open(IMUDEV, os.O_RDONLY | os.O_NOCTTY)
            cfg_serial(fd, BAUD)
        except Exception as e:
            with imu_cond:
                imu["err"] = f"打开 {IMUDEV} 失败: {e}"; imu_cond.notify_all()
            time.sleep(2.0); continue
        with imu_cond:
            imu["err"] = None
        buf = bytearray()
        cnt = 0; t_rate = time.time()
        try:
            while True:
                chunk = os.read(fd, 4096)
                if chunk:
                    buf += chunk
                while len(buf) >= 6:
                    if buf[0] != 0x5A or buf[1] != 0xA5:
                        idx = buf.find(b"\x5a\xa5")
                        if idx < 0:
                            del buf[:max(0, len(buf) - 1)]; break
                        del buf[:idx]; continue
                    length = buf[2] | (buf[3] << 8)
                    if len(buf) < 6 + length:
                        break
                    frame = bytes(buf[:6 + length]); del buf[:6 + length]
                    crc_rx = frame[4] | (frame[5] << 8)
                    c = crc16(frame[0:4]); c = crc16(frame[6:], c)
                    if c != crc_rx:
                        with imu_cond:
                            imu["bad"] += 1
                        continue
                    payload = frame[6:]
                    if payload[0] == 0x91 and length >= 76:
                        d = {
                            "ts":   struct.unpack_from("<I", payload, 8)[0],
                            "acc":  struct.unpack_from("<3f", payload, 12),
                            "gyr":  struct.unpack_from("<3f", payload, 24),
                            "mag":  struct.unpack_from("<3f", payload, 36),
                            "eul":  struct.unpack_from("<3f", payload, 48),
                            "quat": struct.unpack_from("<4f", payload, 60),
                        }
                        cnt += 1
                        now = time.time()
                        with imu_cond:
                            imu["data"] = d; imu["n"] += 1; imu["ok"] += 1
                            if now - t_rate >= 1.0:
                                imu["rate"] = round(cnt / (now - t_rate), 1)
                                cnt = 0; t_rate = now
                            imu_cond.notify_all()
        except Exception as e:
            with imu_cond:
                imu["err"] = str(e); imu_cond.notify_all()
        finally:
            try: os.close(fd)
            except Exception: pass
        time.sleep(1.0)

# ===================== 网页 =====================
PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cam + IMU 实时监控</title>
<style>
*{box-sizing:border-box} html,body{margin:0;background:#0d0f12;color:#cdd3da;font-family:system-ui,"Microsoft YaHei",sans-serif}
h1{font-size:16px;font-weight:600;padding:10px 14px;margin:0;background:#11151b;border-bottom:1px solid #222}
.wrap{display:flex;flex-wrap:wrap;gap:14px;padding:14px}
.cam{flex:2 1 520px;min-width:320px}
.cam img{width:100%;height:auto;background:#000;border-radius:8px;display:block}
.panel{flex:1 1 320px;min-width:300px;display:flex;flex-direction:column;gap:12px}
.card{background:#11151b;border:1px solid #222;border-radius:8px;padding:12px}
.card h2{font-size:13px;margin:0 0 8px;color:#7fd1e8;font-weight:600}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
td{padding:3px 6px;font-size:13px} td.k{color:#8b95a1;width:34%} td.v{font-family:Consolas,monospace;text-align:right}
.scene{width:100%;height:230px;perspective:700px;display:flex;align-items:center;justify-content:center}
.cube{width:120px;height:120px;position:relative;transform-style:preserve-3d;transition:transform .04s linear}
.face{position:absolute;width:120px;height:120px;display:flex;align-items:center;justify-content:center;
  font-size:22px;font-weight:700;color:#fff;border:2px solid #1aa3c8;background:rgba(26,163,200,.22)}
.fz{transform:translateZ(60px)}.nz{transform:rotateY(180deg) translateZ(60px);background:rgba(200,80,60,.22);border-color:#c8503c}
.fx{transform:rotateY(90deg) translateZ(60px);background:rgba(80,180,90,.22);border-color:#50b45a}
.nx{transform:rotateY(-90deg) translateZ(60px)}
.fy{transform:rotateX(90deg) translateZ(60px);background:rgba(220,180,60,.22);border-color:#dcb43c}
.ny{transform:rotateX(-90deg) translateZ(60px)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#888;margin-right:6px}
.dot.on{background:#3ad07a}.dot.off{background:#e0524d}
small{color:#6b7682}
</style></head><body>
<h1>📷 摄像头 + 🧭 IMU 实时混合监控 <small id="st"><span class="dot" id="d"></span>连接中…</small></h1>
<div class="wrap">
  <div class="cam"><img src="/stream" alt="camera"></div>
  <div class="panel">
    <div class="card"><h2>姿态可视化 (随四元数实时转动)</h2>
      <div class="scene"><div class="cube" id="cube">
        <div class="face fz">+Z</div><div class="face nz">-Z</div>
        <div class="face fx">+X</div><div class="face nx">-X</div>
        <div class="face fy">+Y</div><div class="face ny">-Y</div>
      </div></div>
    </div>
    <div class="card"><h2>欧拉角 (deg)</h2><table>
      <tr><td class="k">eul[0]</td><td class="v" id="e0">–</td><td class="k">eul[1]</td><td class="v" id="e1">–</td><td class="k">eul[2]</td><td class="v" id="e2">–</td></tr>
    </table></div>
    <div class="card"><h2>加速度 (g) / 角速度 (deg/s)</h2><table>
      <tr><td class="k">acc X</td><td class="v" id="a0">–</td><td class="k">gyr X</td><td class="v" id="g0">–</td></tr>
      <tr><td class="k">acc Y</td><td class="v" id="a1">–</td><td class="k">gyr Y</td><td class="v" id="g1">–</td></tr>
      <tr><td class="k">acc Z</td><td class="v" id="a2">–</td><td class="k">gyr Z</td><td class="v" id="g2">–</td></tr>
      <tr><td class="k">|acc|</td><td class="v" id="am">–</td><td class="k">磁场</td><td class="v" id="mg">–</td></tr>
    </table></div>
    <div class="card"><h2>四元数 / 状态</h2><table>
      <tr><td class="k">w</td><td class="v" id="q0">–</td><td class="k">x</td><td class="v" id="q1">–</td></tr>
      <tr><td class="k">y</td><td class="v" id="q2">–</td><td class="k">z</td><td class="v" id="q3">–</td></tr>
      <tr><td class="k">速率</td><td class="v" id="rate">–</td><td class="k">CRC ok/bad</td><td class="v" id="crc">–</td></tr>
      <tr><td class="k">时间戳</td><td class="v" id="ts" colspan="3">–</td></tr>
    </table></div>
  </div>
</div>
<script>
const $=id=>document.getElementById(id);
const f=(v,p)=>Number(v).toFixed(p);
function q2m(w,x,y,z){
  const n=Math.hypot(w,x,y,z)||1; w/=n;x/=n;y/=n;z/=n;
  const xx=x*x,yy=y*y,zz=z*z,xy=x*y,xz=x*z,yz=y*z,wx=w*x,wy=w*y,wz=w*z;
  const m11=1-2*(yy+zz),m12=2*(xy-wz),m13=2*(xz+wy);
  const m21=2*(xy+wz),m22=1-2*(xx+zz),m23=2*(yz-wx);
  const m31=2*(xz-wy),m32=2*(yz+wx),m33=1-2*(xx+yy);
  return `matrix3d(${m11},${m21},${m31},0,${m12},${m22},${m32},0,${m13},${m23},${m33},0,0,0,0,1)`;
}
const cube=$("cube");
function setStatus(on,txt){ $("d").className="dot "+(on?"on":"off"); $("st").lastChild.textContent=txt; }
const es=new EventSource("/imu");
es.onmessage=e=>{
  const d=JSON.parse(e.data);
  $("a0").textContent=f(d.acc[0],3); $("a1").textContent=f(d.acc[1],3); $("a2").textContent=f(d.acc[2],3);
  $("am").textContent=f(Math.hypot(...d.acc),3)+" g";
  $("g0").textContent=f(d.gyr[0],2); $("g1").textContent=f(d.gyr[1],2); $("g2").textContent=f(d.gyr[2],2);
  $("e0").textContent=f(d.eul[0],2); $("e1").textContent=f(d.eul[1],2); $("e2").textContent=f(d.eul[2],2);
  $("q0").textContent=f(d.quat[0],4); $("q1").textContent=f(d.quat[1],4);
  $("q2").textContent=f(d.quat[2],4); $("q3").textContent=f(d.quat[3],4);
  const mm=Math.hypot(...d.mag); $("mg").textContent = mm<1e-6 ? "无(0)" : f(mm,1)+" uT";
  $("rate").textContent=d.rate+" Hz"; $("crc").textContent=d.ok+"/"+d.bad; $("ts").textContent=d.ts+" ms";
  cube.style.transform=q2m(d.quat[0],d.quat[1],d.quat[2],d.quat[3]);
  setStatus(true,"IMU 已连接 "+d.rate+" Hz");
};
es.onerror=()=>setStatus(false,"IMU 连接断开，重试中…");
</script></body></html>""".encode("utf-8")

# ===================== HTTP =====================
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _hdr(self, ctype, extra=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers(); self.wfile.write(PAGE); return
        if path == "/snapshot":
            with cam_cond:
                if cam["jpg"] is None: cam_cond.wait(timeout=5)
                frame = cam["jpg"]
            if not frame: self.send_error(503, "no frame"); return
            self.send_response(200); self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(frame))); self.end_headers()
            self.wfile.write(frame); return
        if path == "/stream":
            self.send_response(200)
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUND}")
            self.end_headers()
            last = 0
            try:
                while True:
                    with cam_cond:
                        while cam["n"] == last:
                            cam_cond.wait(timeout=5)
                        frame = cam["jpg"]; last = cam["n"]
                    if not frame: continue
                    self.wfile.write(b"--" + BOUND.encode() + b"\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n")
                    self.wfile.write(frame); self.wfile.write(b"\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        if path == "/imu.json":
            with imu_cond:
                d = imu["data"]; meta = {"rate": imu["rate"], "ok": imu["ok"], "bad": imu["bad"], "err": imu["err"]}
            body = json.dumps({**(d or {}), **meta}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if path == "/imu":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last = 0; last_send = 0.0
            try:
                while True:
                    with imu_cond:
                        while imu["n"] == last:
                            imu_cond.wait(timeout=5)
                        d = imu["data"]; last = imu["n"]
                        meta = {"rate": imu["rate"], "ok": imu["ok"], "bad": imu["bad"]}
                    if d is None: continue
                    now = time.time()
                    if now - last_send < 0.02:   # 限频 ~50Hz
                        continue
                    last_send = now
                    msg = "data: " + json.dumps({**d, **meta}) + "\n\n"
                    self.wfile.write(msg.encode()); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        self.send_error(404)

def main():
    threading.Thread(target=cam_reader, daemon=True).start()
    threading.Thread(target=imu_reader, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[cam_imu] http://0.0.0.0:{PORT}/  cam={CAMDEV} {W}x{H}@{FPS}  imu={IMUDEV}@{BAUD}", flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
