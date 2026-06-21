# lookaround — Orange Pi 6 Plus 相机 + IMU 感知模组与视觉 SLAM

一台远程 **Orange Pi 6 Plus** 上，把 **Logitech C920 摄像头**与 **HiPNUC HI12 IMU** 物理粘连成一个"环视(look around)"感知模组：实时图传 + 姿态监控，并在其上部署**纯视觉 SLAM**（建图 / 定位）。本机（Windows）仅作控制端，实际工作都在 Pi 上经 SSH 执行。

> ⚠️ **凭据安全**：连接信息（IP / 用户名 / 密码）记录在本地 `info.txt`，已被 `.gitignore` 排除，**绝不提交、不推送**（含明文密码）。

## 功能一览

- 📷🧭 **实时混合监控**（`cam_imu_server.py`）：摄像头 MJPEG + IMU 经 SSE + **随四元数转动的 3D 姿态立方体**（右手 FLU 坐标系：上 Z＋ / 左 Y＋ / 前 X＋）。
- 🛰 **纯视觉单目 SLAM**（`stella_vslam`，分支 [`slam/stella_vslam`](https://github.com/wangjizhu/lookaround/tree/slam/stella_vslam)）：实时建图 + 自研无依赖网页查看器。
- 🔧 **IMU 解码 / 诊断**（`imu_read.py`）：HI12 0x91 二进制协议（抓包逆向 + CRC 校验）。

## 目标机 (Target)

| 项 | 值 |
|---|---|
| 主机名 | `orangepi6plus` |
| 连接 | `ssh dashuai@<IP>`（SSH key 免密）；**IP 每次可能变化，使用前需确认** |
| 网络 | VPN `tun0`（动态） / LAN `enp97s0` = 192.168.111.93 |
| SoC / CPU | CIX SKY1 / Phecda；12 核 ARM（8×A720@2.6G + 4×A520@1.8G） |
| 内存 / 系统 | 30 GiB；Ubuntu 24.04 · 6.6.x-cix · aarch64 |
| 存储 | NVMe SSD 1TB |

`dashuai` 已加入 `video`（摄像头）与 `dialout`（串口）组，访问设备无需 sudo。

## 外设 (USB)

| 设备 | 型号 / ID | 节点 / 驱动 |
|---|---|---|
| 摄像头 | Logitech C920 (`046d:082d`) | **`/dev/video3`**, uvcvideo |
| IMU | HiPNUC **HI12**（经 CP2102N USB-UART） | **`/dev/ttyUSB0`**, cp210x |
| 鼠标 / 键盘 | SIGMACHIP / SEM | usbhid |

> 摄像头节点排查：C920=`/dev/video3`，`/dev/video0` 是 SoC 编解码器（非摄像头）。
> 完整设备报告见 `device-report.html` / `.pdf`；IMU 数据手册见 `HI12_DataSheet_1.5_CN.pdf`。

## IMU：HiPNUC HI12

- 接口：UART1 经 CP2102N → `/dev/ttyUSB0`，串口 **115200 / 8N1**（无校验）
- 默认：**100 Hz、6-DoF、ENU 坐标系**，输出 加速度 / 角速度 / 欧拉角 / 四元数 / 时间戳（该型号**无磁力计**，mag=0 → 航向会缓慢漂移）
- 协议：HiPNUC **串行二进制 0x91 包**（数据手册未含完整字节定义，下表为抓包逆向并经 CRC 校验确认）

**帧结构**（小端）：`5A A5 | len(2) | crc16(2) | payload(len)`，典型 82 字节（len=0x4C=76）。
CRC = CRC-16/XMODEM（poly 0x1021, init 0），覆盖 `帧[0:4] + payload`。

| payload 偏移 | 字段 | 类型 |
|---|---|---|
| 0 / 1 | tag=0x91 / id | u8 |
| 8 | 时间戳 ts(ms) | u32 |
| 12 | 加速度 acc[3] (g) | f32×3 |
| 24 | 角速度 gyr[3] (deg/s) | f32×3 |
| 36 | 磁场 mag[3] (uT，本型号为0) | f32×3 |
| 48 | 欧拉角 eul[3] (deg) | f32×3 |
| 60 | 四元数 quat[4] (w,x,y,z) | f32×4 |

诊断工具：`python3 imu_read.py /dev/ttyUSB0 2.0`（解码 + CRC 校验 + 速率统计）。

## 实时监控网页

**`cam_imu_server.py`** — 摄像头 + IMU 实时混合监控（多客户端）。摄像头 MJPEG(`-c:v copy` 不转码) + IMU 经 **SSE** 推送；网页含数字面板与**随四元数转动的 3D 姿态立方体**（两者物理粘连，转动模组时画面与立方体同步动，可肉眼验证姿态）。立方体采用右手 FLU 系、置零回正、轴对应已在硬件上确认（直通 +x+y+z）。

```bash
# 上传 + 启动（端口 摄像头 IMU 宽 高 帧率）
scp cam_imu_server.py dashuai@<IP>:/tmp/
ssh dashuai@<IP> "setsid nohup python3 /tmp/cam_imu_server.py 8090 /dev/video3 /dev/ttyUSB0 1280 720 30 >/tmp/cam_imu.log 2>&1 </dev/null &"
# 浏览器： http://<IP>:8090/      停止： ssh dashuai@<IP> "pkill -f cam_imu_server.py"
```

| 端点 | 说明 |
|---|---|
| `/` | 综合页面（摄像头 + IMU + 3D 姿态） |
| `/stream` / `/snapshot` | 摄像头 MJPEG 流 / 单帧 |
| `/imu` / `/imu.json` | IMU 实时数据（SSE）/ 最新一帧（调试） |

> `cam_stream.py` 为纯摄像头版本（保留）。监控与 SLAM 二选一：两者都占用 `/dev/video3`。

## 视觉 SLAM（`stella_vslam` · 分支 `slam/stella_vslam`）

纯视觉**单目** SLAM（不用 IMU）。框架选型对比见 [`SLAM_OPTIONS.md`](SLAM_OPTIONS.md)；完整部署步骤见分支上的 [`slam/DEPLOY.md`](https://github.com/wangjizhu/lookaround/blob/slam/stella_vslam/slam/DEPLOY.md)。

- 因本机 **Docker Hub 被墙**，采用**原生构建**，复用系统 OpenCV 4.6（aarch64 / Ubuntu 24.04），全部装在 `~/slam`（无需 sudo）。
- **自研轻量网页查看器**（避开官方 SocketViewer 的 Node / protobuf / npm）：SLAM 进程导出 `map.json` → `python3 -m http.server` → 无依赖 canvas 三维点云 + 关键帧轨迹 + 当前相机。
- 一键运行（Pi 上）：`~/slam/run_web_slam.sh`，浏览器看 `http://<IP>:8091/`；停止并存图 `touch ~/slam/web/STOP`。
- 单目需**平移**相机（产生视差）才会初始化建图；静止只停在 Initializing。
- 现状：构建 + 开相机(1280×720) + 导出地图已通过；**待带运动验证实际建图 + 相机标定**。

## 分支 / 标签

| 引用 | 说明 |
|---|---|
| `main` | 监控与文档（稳定，仓库首页） |
| `slam/stella_vslam` | 视觉 SLAM 部署（活跃） |
| tag `slam-select` | SLAM 框架选型决策点；换框架从此处另拉 `slam/<框架>` 分支 |

## 文件

| 文件 | 说明 | 版本管理 |
|---|---|---|
| `cam_imu_server.py` | 摄像头 + IMU 实时监控服务（主） | ✅ |
| `cam_stream.py` | 纯摄像头 MJPEG 服务 | ✅ |
| `imu_read.py` | IMU 串口解码 / 诊断工具 | ✅ |
| `SLAM_OPTIONS.md` | 视觉 SLAM 框架选型对比 | ✅ |
| `slam/` | stella_vslam 部署（配置 / 脚本 / 网页查看器 / 补丁，**在 `slam/stella_vslam` 分支**） | ✅ |
| `device-report.html` / `.pdf` | 设备信息报告 | ✅ |
| `HI12_DataSheet_1.5_CN.pdf` | IMU 数据手册 | ✅ |
| `info.txt` | 连接凭据（含密码） | 🚫 gitignore |
| `*.jpg` / `*.log` | 测试抓拍 / 日志 | 🚫 gitignore |

## 待办 / Roadmap

- [x] 3D 姿态立方体：右手 FLU 系 + 正视回正 + 轴对应固化（硬件确认）
- [x] 视觉 SLAM 选型并部署（stella_vslam，原生构建 + 网页查看器）
- [ ] SLAM 带运动验证实际建图（拿模组缓慢平移）
- [ ] 摄像头标定（Kalibr / OpenCV 棋盘格 → 修正 `c920_mono.yaml` 内参）
- [ ] 监控 / SLAM 固化为 **systemd 服务**（开机自启 + 崩溃自重启）
- [ ] （可选）接 IMU 升 **VIO**（ORB-SLAM3 mono-inertial）→ 真实米制尺度 + 更抗丢
