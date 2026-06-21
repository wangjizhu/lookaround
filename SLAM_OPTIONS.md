# 视觉惯性 SLAM（VIO = 相机 + IMU）框架选型（决策点 / Selection Fork-Point）

> **目标：视觉 + IMU（VIO）**，用上已验证的 C920 + HI12 模组，要**真实米制尺度 + 抗丢失**。
> **本提交 = "框架选型分支点"。** 相机+IMU 基线已验证可用（见 `cam_imu_server.py`，commit `7c81ef7`；记忆 `cam-imu-rig-baseline`）。
> 选定后从本提交拉分支 `slam/<框架名>`；若不合适，`git checkout` 回到 tag **`slam-select`** 换另一种。
> 调研：2026-06-21，12 个核查 agent 针对本机逐项联网验证。

## 硬约束
- 板子 **CIX SKY1 / Mali 级 GPU，无 NVIDIA·CUDA** → 深度学习 VIO/SLAM 全排除（前端网络焊死 CUDA/TensorRT）。
- **C920** 单目（卷帘快门，MJPEG 1280×720@30）+ **HI12** 6 轴 IMU（100Hz，与相机粘死、~同步）。
- 加 IMU 的意义：解决单目尺度不可观、抗纯旋转/弱纹理丢失、给米制尺度。
- Ubuntu 24.04 → ROS2 Jazzy；ROS1-only 项目有移植/容器化代价。

## VIO 候选对比（单目 + IMU，CPU-only 可跑）
| 方案 | 类型 | 回环 / 地图重用 | License | 维护 | 部署难度(1-5) | 实时(本CPU) | 备注 |
|---|---|---|---|---|:--:|---|---|
| **ORB-SLAM3** mono-inertial | 特征 VI-SLAM | ✅回环+地图保存/重定位+多地图(Atlas) | GPLv3 | 核2021/Jazzy移植2025 | 4 | 勉强~中 | **最准+最完整 SLAM**；老码需打补丁 |
| **VINS-Fusion** mono-inertial | 优化 VIO | ✅回环(无长期地图重用) | GPLv3 | 2024/有Jazzy移植 | 4 | 流畅 | **最经典稳健**；24.04 需源码编 Ceres 2.1 |
| **OpenVINS** | MSCKF 滤波 | ❌无回环(纯里程计,会漂) | GPLv3 | 慢 | 4 | 流畅 | 轻量精确、文档最好 |
| **SchurVINS** | 滤波(最省CPU) | 部分 | — | 研究/ROS1 | 4-5 | 流畅 | 近年 **CPU 占用最低**的 VIO；ROS1 |

> Basalt 已排除（双目+IMU 取向，无原生单目）；DROID/AirSLAM/SuperVINS 等深度法因无 CUDA 排除。

## 三档建议（按你最看重什么）
1. **想要完整 SLAM（建图 + 重定位 + 回环，可保存复用地图）→ ORB-SLAM3 mono-inertial**
   最准、功能最全（Atlas 多地图、丢失后重定位）。代价：2021 老代码要打补丁（OpenCV/Pangolin/cv_bridge 版本坑）、GPLv3、实时勉强（喂 640×480、限 ~15–20Hz）。
2. **想要最稳健、社区最成熟的 VIO（里程计 + 回环）→ VINS-Fusion**
   久经无人机/手持考验，CPU 上流畅。代价：24.04 自带 Ceres 2.2 会编译失败，须源码装 Ceres 2.1.0；用 Jazzy 社区移植分支。
3. **想要最轻、纯局部里程计（不需回环/建图）→ OpenVINS**（或 **SchurVINS**：CPU 最省，但 ROS1）。

## ⚠ VIO 成败关键（不是选框架，是标定）
- 你之前"调正确"的是 **IMU 自身的姿态/朝向**；VIO 还需要两样新东西：
  1. **相机 ↔ IMU 外参标定**（两者相对位姿）+ **IMU 噪声/零偏标定** → 用 **Kalibr**（拍标定板录一段）。
  2. **相机与 IMU 的时间同步**：USB 相机时间戳抖动 vs 100Hz IMU，是 VIO 最大翻车点；需软时间戳对齐或在线时延估计（VINS-Fusion 支持 `estimate_td`）。
- **HI12 是 100Hz**：VIO 偏好 ≥200Hz，100Hz 可用但偏低；后续可查能否提采样率（HiPNUC 0xXX 配置）。

## 选定后的集成路径
- 相机：ROS2 `v4l2_camera`/`usb_cam` 发 C920（先用 `camera_calibration` 标定去畸变）。
- IMU：把 HI12 解析为 `sensor_msgs/Imu`（100Hz）发布（复用现有 `cam_imu_server.py` 的 0x91 解析）。
- 标定：Kalibr 出相机内参 + 相机-IMU 外参 + 时延 → 填入所选框架的 config。
- 从本提交拉 `slam/<框架名>` 分支；失败回 tag `slam-select` 换一种。

## 来源（关键 repo / 文档）
- ORB-SLAM3 — https://github.com/UZ-SLAMLab/ORB_SLAM3 ・ Jazzy 移植 https://github.com/Mechazo11/ros2_orb_slam3/tree/jazzy
- VINS-Fusion — https://github.com/HKUST-Aerial-Robotics/VINS-Fusion ・ Jazzy 移植 https://github.com/cannnnxu/VINS-Fusion-ROS2-jazzy
- OpenVINS — https://github.com/rpng/open_vins ・ Jazzy PR https://github.com/rpng/open_vins/pull/500
- SchurVINS — https://github.com/bytedance/SchurVINS
- Kalibr（相机-IMU 标定）— https://github.com/ethz-asl/kalibr
- （纯单目备选，若以后需要：stella_vslam / ros2_mono_vo — 见 git 历史此文件早版）
