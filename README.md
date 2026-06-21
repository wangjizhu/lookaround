# Orange Pi 6 Plus 远程开发项目

针对一台远程 **Orange Pi 6 Plus** 开发板的工具与文档集合。本机（Windows）仅作控制端，实际工作都在 Pi 上通过 SSH 执行。

> ⚠️ **凭据安全**：连接信息（IP / 用户名 / 密码）记录在本地 `info.txt`，该文件已被 `.gitignore` 排除，**不纳入版本管理、不要提交或推送**（含明文密码）。

## 目标机 (Target)

| 项 | 值 |
|---|---|
| 主机名 | `orangepi6plus` |
| 连接 | `ssh dashuai@<IP>`（SSH key 免密）；**IP 每次可能变化，使用前需确认**（2026-06-21 时为 10.8.0.34） |
| 网络 | VPN `tun0`（动态，曾为 10.8.0.34） / LAN `enp97s0` = 192.168.111.93（网关 192.168.111.1，更稳定） |
| SoC | CIX SKY1 / Phecda |
| CPU | 12 核 ARM：8×Cortex-A720@2.6GHz + 4×Cortex-A520@1.8GHz |
| 内存 | 30 GiB |
| 系统 | Ubuntu 24.04.3 LTS · kernel 6.6.89-cix · aarch64 |
| 存储 | NVMe SSD Colorful CN600 1TB（根分区 `/`，用量约 3%） |

## USB 设备

| 设备 | 型号 / ID | 节点 / 驱动 |
|---|---|---|
| USB 转串口 | Silicon Labs CP2102N (`10c4:ea60`) | `/dev/ttyUSB0`，`cp210x` |
| 摄像头 | Logitech HD Pro Webcam C920 (`046d:082d`) | **`/dev/video3`**，`uvcvideo` + `snd-usb-audio` |
| 鼠标 | SIGMACHIP (`1c4f:0048`) | `usbhid` |
| 键盘 | SEM (`1a2c:0b2a`) | `usbhid` |

完整设备报告见 `device-report.html` / `device-report.pdf`。

## 摄像头直播 (`cam_stream.py`)

多客户端 **MJPEG-over-HTTP** 直播服务；ffmpeg `-c:v copy` 不转码，CPU 占用极低；`setsid+nohup` 后台运行，SSH 断开不停。

> **重要：C920 是 `/dev/video3`**，不是 `/dev/video0`（video0 是 SoC 硬件编解码器/ISP，多平面 AFBC 格式，并非摄像头）。
> 可靠定位法：选 `driver=uvcvideo` 且 `v4l2-ctl --list-formats` 含 `MJPG` 的节点。

### 部署 & 运行

```bash
# 1. 上传脚本到 Pi
scp cam_stream.py dashuai@<IP>:/tmp/cam_stream.py

# 2. 启动：参数为 端口 设备 宽 高 帧率
ssh dashuai@<IP> "setsid nohup python3 /tmp/cam_stream.py 8090 /dev/video3 1280 720 30 >/tmp/cam_stream.log 2>&1 </dev/null &"

# 3. 浏览器观看
#   http://<IP>:8090/          直播页面
#   http://<IP>:8090/snapshot  单帧快照
#   http://<IP>:8090/stream    原始 MJPEG 流

# 停止
ssh dashuai@<IP> "pkill -f cam_stream.py"
```

> `dashuai` 已加入 `video` 组，访问摄像头/V4L2 无需 sudo。

## 文件说明

| 文件 | 说明 | 版本管理 |
|---|---|---|
| `cam_stream.py` | 摄像头 MJPEG 直播服务 | ✅ |
| `device-report.html` / `.pdf` | 设备信息报告 | ✅ |
| `README.md` | 本文档 | ✅ |
| `info.txt` | 连接凭据（含密码） | 🚫 gitignore |
| `cam_*.jpg` | 摄像头测试抓拍 | 🚫 gitignore |

## 待办 / Roadmap

- [ ] 可选：摄像头直播固化为 **systemd 服务**（开机自启 + 崩溃自重启）
- [ ] 可选：提升到 **1080p** 或调整帧率
- [ ] （后续需求在此追加）
