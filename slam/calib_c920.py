#!/usr/bin/env python3
# C920 单目内参标定（OpenCV 棋盘格）。无显示，自动采集"位置足够分散"的视图，
# 凑够 --need 张后自动标定，打印 fx/fy/cx/cy/畸变 与重投影误差，并输出 stella_vslam 的 Camera 块。
#   python3 calib_c920.py --cam 3 --cols 9 --rows 6 --square 25 --need 20
#   提前停止：touch ~/slam/web/CALIB_STOP
import cv2, numpy as np, sys, time, argparse, os

ap = argparse.ArgumentParser()
ap.add_argument('--cam', type=int, default=3)
ap.add_argument('--cols', type=int, default=9, help='内角点列数 (10格→9)')
ap.add_argument('--rows', type=int, default=6, help='内角点行数 (7格→6)')
ap.add_argument('--square', type=float, default=25.0, help='方格边长 mm(不影响内参，仅记录)')
ap.add_argument('--need', type=int, default=20)
ap.add_argument('--width', type=int, default=1280)
ap.add_argument('--height', type=int, default=720)
ap.add_argument('--out', default=os.path.expanduser('~/slam/c920_calib.yaml'))
ap.add_argument('--stop', default=os.path.expanduser('~/slam/web/CALIB_STOP'))
a = ap.parse_args()

pattern = (a.cols, a.rows)
objp = np.zeros((a.cols * a.rows, 3), np.float32)
objp[:, :2] = np.mgrid[0:a.cols, 0:a.rows].T.reshape(-1, 2) * a.square

cap = cv2.VideoCapture(a.cam, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, a.width)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, a.height)
if not cap.isOpened():
    print('ERROR cannot open camera', a.cam); sys.exit(1)
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
print(f'camera opened {w}x{h}; board inner {a.cols}x{a.rows}; need {a.need} views', flush=True)
if os.path.exists(a.stop):
    os.remove(a.stop)

objpoints, imgpoints, centers = [], [], []
flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_FAST_CHECK
crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
last_t = 0.0
seen = 0
while len(objpoints) < a.need:
    if os.path.exists(a.stop):
        print('stop requested'); break
    ok, frame = cap.read()
    if not ok:
        continue
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    found, corners = cv2.findChessboardCorners(gray, pattern, flags)
    seen += 1
    if seen % 60 == 0 and not found:
        print(f'... 还没检测到棋盘（已采 {len(objpoints)}/{a.need}）—— 让整块棋盘清晰、完整、别太斜/太远', flush=True)
    if not found:
        continue
    c = corners.mean(axis=0).ravel()
    now = time.time()
    if any(np.hypot(c[0] - pc[0], c[1] - pc[1]) < 80 for pc in centers) or now - last_t < 0.8:
        continue  # 要求与已采视图位置拉开 + 间隔，保证多样性
    corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), crit)
    objpoints.append(objp.copy()); imgpoints.append(corners); centers.append(c); last_t = now
    print(f'captured {len(objpoints)}/{a.need}  center=({c[0]:.0f},{c[1]:.0f})', flush=True)
cap.release()

if len(objpoints) < 6:
    print(f'too few views ({len(objpoints)}); aborting'); sys.exit(1)
print('calibrating...', flush=True)
rms, K, dist, _, _ = cv2.calibrateCamera(objpoints, imgpoints, (w, h), None, None)
fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
d = (dist.ravel().tolist() + [0, 0, 0, 0, 0])[:5]
k1, k2, p1, p2, k3 = d
print(f'RMS reproj error = {rms:.4f} px  (好: <0.5; 可接受: <1.0)')
print(f'fx={fx:.3f} fy={fy:.3f} cx={cx:.3f} cy={cy:.3f}')
yaml = (f'Camera:\n  name: "Logitech C920 (mono, calibrated)"\n  setup: "monocular"\n  model: "perspective"\n'
        f'  fx: {fx:.4f}\n  fy: {fy:.4f}\n  cx: {cx:.4f}\n  cy: {cy:.4f}\n'
        f'  k1: {k1:.6f}\n  k2: {k2:.6f}\n  p1: {p1:.6f}\n  p2: {p2:.6f}\n  k3: {k3:.6f}\n'
        f'  fps: 30.0\n  cols: {w}\n  rows: {h}\n  color_order: "RGB"\n')
open(a.out, 'w').write(yaml)
print('written ->', a.out)
print('===== 把下面 Camera 块替换进 c920_mono.yaml =====')
print(yaml)
