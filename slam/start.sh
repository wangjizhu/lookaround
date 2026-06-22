#!/bin/bash
# lookaround SLAM —— 开机后一键启动网页控制服务（之后浏览器点 ▶开始采集 即可）。
# 用法：在板子上 `~/slam/start.sh` ；或从电脑 `ssh dashuai@<IP> '~/slam/start.sh'`
pkill -f "cam_imu_server[.]py" 2>/dev/null || true   # 释放相机给 SLAM 用
fuser -k 8091/tcp 2>/dev/null || true                # 清掉占用 8091 的旧进程（幂等）
sleep 1
setsid nohup python3 "$HOME/slam/slam_web_server.py" 8091 "$HOME/slam/web" >/tmp/slamweb.log 2>&1 </dev/null &
sleep 1
echo "✅ 控制服务已启动 (端口 8091)"
echo "   本机 IP: $(hostname -I)"
echo "   浏览器打开  http://<上面的IP>:8091/   →  点 ▶开始采集  →  移动建图"
echo "   (相机节点已自动识别 uvcvideo+MJPG；🗑清空地图 / 💾停止存图 都在页面上)"
