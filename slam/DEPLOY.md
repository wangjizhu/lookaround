# stella_vslam 部署计划（分支 `slam/stella_vslam`）

纯视觉单目 SLAM，**不用 IMU**。目标机 = Orange Pi 6 Plus（aarch64 / Ubuntu 24.04 / 无 CUDA / 12 核 / OpenCV 4.6 / Docker 29）。
viewer 用 **SocketViewer（网页）**，headless 远程在浏览器里看建图。

## 路线：Docker（最省心，隔离依赖）
板子上已有 Docker 29.1.3。仅需一次性把用户加入 docker 组（root 等价，需你授权）：
```bash
sudo usermod -aG docker dashuai      # 之后重新登录(新 ssh)生效
```

### 1. 拉源码 + 词典（已在 ~/slam 完成/进行中，无需 sudo）
```bash
mkdir -p ~/slam && cd ~/slam
git clone --recursive https://github.com/stella-cv/stella_vslam.git
git clone --recursive https://github.com/stella-cv/socket_viewer.git
curl -sL https://github.com/stella-cv/FBoW_orb_vocab/raw/main/orb_vocab.fbow -o orb_vocab.fbow
```

### 2. 构建镜像（首次较久；多线程）
```bash
cd ~/slam/stella_vslam && docker build -t stella_vslam-socket -f Dockerfile.socket . \
  --build-arg NUM_THREADS=$(expr $(nproc) - 1)
cd ~/slam/socket_viewer && docker build -t stella_vslam-viewer .
```

### 3. 运行
- 先停掉占用 /dev/video3 的相机服务：`pkill -f cam_imu_server.py`（SLAM 跑完可再起）。
- 起 viewer：`docker run --rm -it --name stella_vslam-viewer --net=host stella_vslam-viewer`
- 起 SLAM（挂载本目录的 c920_mono.yaml 与 vocab，透传相机）：
```bash
docker run --rm -it --net=host --name stella_vslam-socket \
  --device /dev/video3 \
  -v ~/slam:/slam:ro \
  stella_vslam-socket \
  ./run_camera_slam -v /slam/orb_vocab.fbow -c /slam/c920_mono.yaml \
    -n 3 --map-db-out /slam/map.msg     # -n = /dev/videoN 的 N
```
- 浏览器看：`http://<pi-ip>:3001/`

## 必做：相机标定（影响精度，不可省）
`c920_mono.yaml` 现为 FOV 估算内参。用棋盘格标定本机 C920（OpenCV / ros2 `camera_calibration`），把真实 fx/fy/cx/cy/k1..k3 填回配置。

## 已知风险 / 备选
- 实时性勉强：必要时降到 640×480、调小关键点数、`--frame-skip`。
- C920 是卷帘快门 + 自动曝光：快速运动会拖影；建图时尽量平移、锁定曝光。
- 若 Docker 镜像在 24.04/aarch64 构建失败，回退原生构建（系统 OpenCV 4.6 + 源码编 g2o/FBoW）。
- 切换框架：回到 tag `slam-select` 另拉分支。
