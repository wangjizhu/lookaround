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
.camwrap{position:relative;display:block;line-height:0}
.imgaxes{position:absolute;left:6px;top:6px;pointer-events:none;opacity:.92}
.panel{flex:1 1 320px;min-width:300px;display:flex;flex-direction:column;gap:12px}
.card{background:#11151b;border:1px solid #222;border-radius:8px;padding:12px}
.card h2{font-size:13px;margin:0 0 8px;color:#7fd1e8;font-weight:600}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
td{padding:3px 6px;font-size:13px} td.k{color:#8b95a1;width:34%} td.v{font-family:Consolas,monospace;text-align:right}
.scene{width:100%;height:230px;perspective:700px;display:flex;align-items:center;justify-content:center}
.cube{width:120px;height:120px;position:relative;transform-style:preserve-3d;transition:transform .04s linear}
.face{position:absolute;width:120px;height:120px;display:flex;align-items:center;justify-content:center;
  font-size:15px;line-height:1.15;text-align:center;font-weight:700;color:#fff;text-shadow:0 0 4px #000;border:2px solid #444;background:rgba(120,120,120,.12)}
/* 用户右手系：+X=前(红) +Y=左(绿) +Z=上(蓝)。local→world: +x→前, -y→左, +z→上 */
.fz{transform:translateZ(60px);background:rgba(90,155,255,.32);border-color:#5a9bff}             /* +z = 上 +Z */
.nz{transform:rotateY(180deg) translateZ(60px);background:rgba(90,155,255,.08);border-color:#2c4a7a} /* -z = 下 -Z */
.fx{transform:rotateY(90deg) translateZ(60px);background:rgba(255,90,90,.32);border-color:#ff5a5a}    /* +x = 前 +X */
.nx{transform:rotateY(-90deg) translateZ(60px);background:rgba(255,90,90,.08);border-color:#7a2c2c}   /* -x = 后 -X */
.fy{transform:rotateX(90deg) translateZ(60px);background:rgba(70,211,106,.32);border-color:#46d36a}   /* -y = 左 +Y */
.ny{transform:rotateX(-90deg) translateZ(60px);background:rgba(70,211,106,.08);border-color:#2c6a3a}  /* +y = 右 -Y */
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#888;margin-right:6px}
.dot.on{background:#3ad07a}.dot.off{background:#e0524d}
small{color:#6b7682}
/* ---- 立方体控制行 (归零 / 坐标轴) ---- */
.calrow{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0;font-size:13px}
.calrow button{background:#1a2630;color:#cdd3da;border:1px solid #2a3a47;border-radius:5px;padding:4px 8px;cursor:pointer;font-size:12px}
.calrow button:hover{background:#22323e}
.calrow label{display:inline-flex;align-items:center;gap:4px}
/* ---- axis gizmo (children of #cube, inherit its matrix3d) ---- */
.cube .gz{position:absolute;left:60px;top:60px;width:70px;height:4px;margin-top:-2px;transform-origin:0 50%;border-radius:2px;pointer-events:none}
.cube .gzx{background:#ff5a5a}                          /* +x 前 X */
.cube .gzy{background:#46d36a;transform:rotateZ(-90deg)}/* -y 左 Y */
.cube .gzz{background:#5a9bff;transform:rotateY(-90deg)}/* +z 上 Z (朝观察者) */
.cube .gzt{position:absolute;left:60px;top:58px;font:700 12px Consolas,monospace;color:#fff;text-shadow:0 0 3px #000;pointer-events:none;transform-origin:0 50%}
.cube .gztx{color:#ff9b9b;transform:translateX(74px)}
.cube .gzty{color:#8be8a3;transform:rotateZ(-90deg) translateX(74px)}
.cube .gztz{color:#9bbcff;transform:rotateY(-90deg) translateX(74px)}
.cube:not(.gizmo) .gz,.cube:not(.gizmo) .gzt{display:none}
</style></head><body>
<h1>📷 摄像头 + 🧭 IMU 实时混合监控 <small id="st"><span class="dot" id="d"></span>连接中…</small></h1>
<div class="wrap">
  <div class="cam"><div class="camwrap"><img src="/stream" alt="camera">
    <svg class="imgaxes" width="118" height="96" viewBox="0 0 118 96">
      <defs><marker id="iax" markerWidth="9" markerHeight="9" refX="6" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6 Z" fill="#ffd54a"/></marker></defs>
      <circle cx="7" cy="7" r="3.2" fill="#ffd54a"/>
      <text x="12" y="11" fill="#ffd54a" font-size="9" font-family="Consolas">原点(0,0)</text>
      <line x1="7" y1="7" x2="96" y2="7" stroke="#ffd54a" stroke-width="2" marker-end="url(#iax)"/>
      <text x="83" y="21" fill="#ffd54a" font-size="13" font-weight="bold">x+</text>
      <line x1="7" y1="7" x2="7" y2="80" stroke="#ffd54a" stroke-width="2" marker-end="url(#iax)"/>
      <text x="11" y="74" fill="#ffd54a" font-size="13" font-weight="bold">y+</text>
    </svg></div></div>
  <div class="panel">
    <div class="card"><h2>姿态立方体 (右手系 · 上 Z＋ / 左 Y＋ / 前 X＋ · 正视回正)</h2>
      <div class="scene"><div class="cube gizmo" id="cube">
        <div class="face fz">上<br>+Z</div><div class="face nz">下<br>−Z</div>
        <div class="face fx">前<br>+X</div><div class="face nx">后<br>−X</div>
        <div class="face fy">左<br>+Y</div><div class="face ny">右<br>−Y</div>
        <div class="gz gzx"></div><div class="gz gzy"></div><div class="gz gzz"></div>
        <div class="gzt gztx">X前</div><div class="gzt gzty">Y左</div><div class="gzt gztz">Z上</div>
      </div></div>
      <small>红=X(前·正对屏幕里) 绿=Y(左) 蓝=Z(上)。归零后回正：从正后方看到一个正立方体。</small>
      <div class="calrow" style="margin-top:8px">
        <button id="calZero" type="button">归零 / Capture Zero</button>
        <label><input type="checkbox" id="calGizmo" checked> 坐标轴</label>
      </div>
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

// ---------- quaternion / 3x3-matrix helpers (scalar-first [w,x,y,z]) ----------
function quatNormalize(q){ const n=Math.hypot(q[0],q[1],q[2],q[3])||1; return [q[0]/n,q[1]/n,q[2]/n,q[3]/n]; }
function quatConj(q){ return [q[0],-q[1],-q[2],-q[3]]; }
function quatMul(a,b){
  const aw=a[0],ax=a[1],ay=a[2],az=a[3],bw=b[0],bx=b[1],by=b[2],bz=b[3];
  return [aw*bw-ax*bx-ay*by-az*bz, aw*bx+ax*bw+ay*bz-az*by, aw*by-ax*bz+ay*bw+az*bx, aw*bz+ax*by-ay*bx+az*bw];
}
function quatToR(q){
  const u=quatNormalize(q),w=u[0],x=u[1],y=u[2],z=u[3];
  const xx=x*x,yy=y*y,zz=z*z,xy=x*y,xz=x*z,yz=y*z,wx=w*x,wy=w*y,wz=w*z;
  return [[1-2*(yy+zz),2*(xy-wz),2*(xz+wy)],[2*(xy+wz),1-2*(xx+zz),2*(yz-wx)],[2*(xz-wy),2*(yz+wx),1-2*(xx+yy)]];
}
function mat3Mul(A,B){ const C=[[0,0,0],[0,0,0],[0,0,0]];
  for(let i=0;i<3;i++)for(let j=0;j<3;j++){let s=0;for(let k=0;k<3;k++)s+=A[i][k]*B[k][j];C[i][j]=s;} return C; }
function mat3T(A){ return [[A[0][0],A[1][0],A[2][0]],[A[0][1],A[1][1],A[2][1]],[A[0][2],A[1][2],A[2][2]]]; }
function mat3Det(M){ return M[0][0]*(M[1][1]*M[2][2]-M[1][2]*M[2][1])-M[0][1]*(M[1][0]*M[2][2]-M[1][2]*M[2][0])+M[0][2]*(M[1][0]*M[2][1]-M[1][1]*M[2][0]); }
function axisVec(s){ const sgn=s[0]==='-'?-1:1,ax=s[s.length-1];
  if(ax==='x')return[sgn,0,0]; if(ax==='y')return[0,sgn,0]; return[0,0,sgn]; }
function mat3FromAxisMap(sx,sy,sz){ const C=[axisVec(sx),axisVec(sy),axisVec(sz)];
  if(Math.abs(mat3Det(C))<0.5) return null; return C; }
function applyChangeOfBasis(C,R){ return mat3Mul(mat3Mul(C,R),mat3T(C)); }
function buildMatrix3d(M){ return 'matrix3d('+M[0][0]+','+M[1][0]+','+M[2][0]+',0,'+M[0][1]+','+M[1][1]+','+M[2][1]+',0,'+M[0][2]+','+M[1][2]+','+M[2][2]+',0,0,0,0,1)'; }

// 固定观察视角 VIEW_LEFT：正视——从正后方沿 +X 看，Z 正上 / Y 正左 / X 正对屏幕里(隐没)，归零后是一个正立方体。
// 用户右手 FLU 世界系到屏幕的直投影；已核验归零三轴落点正确、全链 det=+1 不镜像(正对观察者为 -X 后面)。
// 渲染链： CSS_matrix = VIEW_LEFT · R_rel · FDIAG ，FDIAG=diag(1,-1,1) 修正 CSS 的 y 朝下。
const VIEW_LEFT=[[0,-1,0],[0,0,-1],[-1,0,0]];
const FDIAG=[[1,0,0],[0,-1,0],[0,0,1]];

// ---------- 姿态状态：轴对应已固定为直通(+x/+y/+z, det=+1)，无需标定 ----------
let QREF=[1,0,0,0];
let lastProcessedQ=[1,0,0,0];

const cube=$("cube");
function captureZero(){ QREF=quatNormalize(lastProcessedQ.slice()); }
function computeRelative(q){ return quatNormalize(quatMul(quatConj(QREF),q)); }
function renderCubeFromQuat(rawQuat){
  const q=quatNormalize(rawQuat);
  lastProcessedQ=q;
  const R=quatToR(computeRelative(q));            // 相对置零的旋转 (IMU 本体系 = 立方体本体系，直通)
  cube.style.transform=buildMatrix3d(mat3Mul(mat3Mul(VIEW_LEFT,R),FDIAG)); // 正视投影 + CSS y翻转
}
$("calGizmo").addEventListener("change",e=>{ cube.classList.toggle("gizmo",e.target.checked); });
$("calZero").addEventListener("click",captureZero);
cube.classList.add("gizmo");

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
  renderCubeFromQuat(d.quat);
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
