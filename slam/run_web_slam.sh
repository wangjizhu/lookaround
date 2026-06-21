#!/bin/bash
# 启动单目 stella_vslam + 实时网页地图。停止：另开终端 `touch ~/slam/web/STOP`（会存图），或 Ctrl-C。
# 看图：浏览器打开 http://<pi-ip>:8091/
set -e
pkill -f "cam_imu_server[.]py" 2>/dev/null || true   # 释放 /dev/video3
export LD_LIBRARY_PATH="$HOME/slam/deps/lib"
cd "$HOME/slam/stella_vslam_examples/build"
exec ./run_camera_web \
  -v "$HOME/slam/orb_vocab.fbow" \
  -c "$HOME/slam/c920_mono.yaml" \
  -n 3 \
  --web-dir "$HOME/slam/web" \
  -o "$HOME/slam/map.msg" \
  --dump-every 8
