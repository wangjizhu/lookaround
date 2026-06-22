#!/bin/bash
# 启动单目 stella_vslam + 实时网页地图（含 /reset /stop 控制服务）。
# 看图： http://<pi-ip>:8091/ ；网页按钮：🗑清空地图(不停SLAM) / 💾停止存图。
# 命令行停止： touch ~/slam/web/STOP（存图），或 Ctrl-C。
set -e
pkill -f "cam_imu_server[.]py" 2>/dev/null || true   # 释放 /dev/video3
# 确保控制网页服务在跑（带 /reset /stop；替换旧的纯静态 http.server）
if ! pgrep -f "[s]lam_web_server" >/dev/null; then
  pkill -f "http[.]server 8091" 2>/dev/null || true
  setsid nohup python3 "$HOME/slam/slam_web_server.py" 8091 "$HOME/slam/web" >/tmp/slamweb.log 2>&1 </dev/null &
fi
export LD_LIBRARY_PATH="$HOME/slam/deps/lib"
cd "$HOME/slam/stella_vslam_examples/build"
exec ./run_camera_web \
  -v "$HOME/slam/orb_vocab.fbow" \
  -c "$HOME/slam/c920_mono.yaml" \
  -n 3 \
  --web-dir "$HOME/slam/web" \
  -o "$HOME/slam/map_calib.msg" \
  --dump-every 8
