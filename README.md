# Orange Pi 6 Plus 远程开发项目

针对一台远程 **Orange Pi 6 Plus** 开发板的工具与文档集合。本机（Windows）仅作控制端，实际工作都在 Pi 上通过 SSH 执行。摄像头(C920)与 IMU(HI12)**物理粘连在一起**，构成一个可同时输出图像与姿态的感知模组。

> ⚠️ **凭据安全**：连接信息（IP / 用户名 / 密码）记录在本地 `info.txt`，已被 `.gitignore` 排除，**不提交、不推送**（含明文密码）。

## 目标机 (Target)

| 项 | 值 |
|---|---|
| 主机名 | `orangepi6plus` |
| 连接 | `ssh dashuai@<IP>`（SSH key 免密）；**IP 每次可能变化，使用前需确认**（2026-06-21 为 10.8.0.34） |
| 网络 | VPN `tun0`（动态） / LAN `enp97s0` = 192.168.111.93 |
| SoC / CPU | CIX SKY1 / Phecda；12 核 ARM（8×A720@2.6G + 4×A520@1.8G） |
| 内存 / 系统 | 30 GiB；Ubuntu 24.04.3 LTS · 6.6.89-cix · aarch64 |
| 存储 | NVMe SSD 1TB（根分区 `/`，用量约 3%） |

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
- 默认：**100 Hz、6-DoF、ENU 坐标系**，输出 加速度/角速度/欧拉角/四元数/时间戳（该型号**无磁力计**，mag=0）
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

**`cam_imu_server.py`** — 摄像头 + IMU 实时混合监控（多客户端）。摄像头 MJPEG(`-c:v copy` 不转码) + IMU 经 **SSE** 推送；网页含数字面板与**随四元数转动的 3D 姿态立方体**（两者物理粘连，转动模组时画面与立方体同步动，可肉眼验证姿态）。

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
| `/imu` | IMU 实时数据（Server-Sent Events, JSON） |
| `/imu.json` | IMU 最新一帧（调试用） |

> `cam_stream.py` 为纯摄像头版本（保留）。

## 文件

| 文件 | 说明 | 版本管理 |
|---|---|---|
| `cam_imu_server.py` | 摄像头+IMU 实时监控服务（主） | ✅ |
| `cam_stream.py` | 纯摄像头 MJPEG 服务 | ✅ |
| `imu_read.py` | IMU 串口解码/诊断工具 | ✅ |
| `device-report.html` / `.pdf` | 设备信息报告 | ✅ |
| `HI12_DataSheet_1.5_CN.pdf` | IMU 数据手册 | ✅ |
| `info.txt` | 连接凭据（含密码） | 🚫 gitignore |
| `*.jpg` | 测试抓拍 | 🚫 gitignore |

## 待办 / Roadmap

- [ ] 监控服务固化为 **systemd 服务**（开机自启 + 崩溃自重启）
- [ ] 3D 姿态立方体坐标轴映射微调（与实际朝向对齐）
- [ ] 摄像头与 IMU 时间戳对齐 / 数据录制
- [ ] （下一步开发在此追加）
