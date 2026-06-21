# stella_vslam 部署（分支 `slam/stella_vslam`）— 原生构建，实测可用

纯视觉单目 SLAM，**不用 IMU**。目标机 Orange Pi 6 Plus（aarch64 / Ubuntu 24.04 / 无 CUDA / OpenCV 4.6）。
> **Docker 路线放弃**：本机 **Docker Hub 被墙**（`registry-1.docker.io` i/o timeout，拉不到 `ubuntu:22.04`）。
> 改**原生构建**，复用系统 OpenCV 4.6（省掉最大的下载），全部装在 `~/slam`（无需 sudo，可整目录删）。

## 本机网络现状（2026-06-21）
- apt 镜像 `mirror.sysu.edu.cn`（快）；**GitHub 仅 HTTP/1.1 稳**（HTTP/2 会断流 `CANCEL`）；**Docker Hub 不可达**。
- 已装系统库：OpenCV 4.6 / Eigen / SuiteSparse / yaml-cpp / GLEW / TBB / sqlite / gflags（无需再 apt）。
- 子模块/词典在 Pi 上 GitHub 拉取不稳 → 改为**本机拉好 `scp` 上去**（见下）。

## 构建步骤（全部在 `~/slam`）
```bash
git config --global http.version HTTP/1.1   # 关键：避免 HTTP/2 断流
```

### 1. g2o → 装到 ~/slam/deps
```bash
cd ~/slam/src && git clone https://github.com/RainerKuemmerle/g2o.git
cd g2o && git checkout 20230223_git && mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=$HOME/slam/deps -DBUILD_SHARED_LIBS=ON \
  -DBUILD_UNITTESTS=OFF -DG2O_USE_CHOLMOD=OFF -DG2O_USE_CSPARSE=ON -DG2O_USE_OPENGL=OFF \
  -DG2O_USE_OPENMP=OFF -DG2O_BUILD_APPS=OFF -DG2O_BUILD_EXAMPLES=OFF -DG2O_BUILD_LINKED_APPS=OFF ..
make -j10 && make install
```

### 2. stella_vslam 库 → 装到 ~/slam/deps（无 viewer）
```bash
cd ~/slam && git clone --recursive https://github.com/stella-cv/stella_vslam.git
# 若 3rd/FBoW、3rd/tinycolormap 子模块拉取失败：在本机 clone 后 scp 到 ~/slam/stella_vslam/3rd/
cd stella_vslam && mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_INSTALL_PREFIX=$HOME/slam/deps \
  -DCMAKE_PREFIX_PATH=$HOME/slam/deps .. && make -j10 && make install
```
> 默认不构建 Pangolin/socket/iridescence viewer，也不需 backward-cpp（trace logger 默认关）。
> 运行程序已不在库仓库里，见下一步的 `stella_vslam_examples`。

### 3. 运行程序 stella_vslam_examples
```bash
cd ~/slam && git clone --recursive https://github.com/stella-cv/stella_vslam_examples.git
# ⚠ 打补丁：强制 V4L2 + MJPG 1280x720（否则 OpenCV 走 GStreamer/640x480，尺寸不符被拒帧）
patch -p1 -d stella_vslam_examples < slam/patches/run_camera_slam-v4l2-1280x720.patch
cd stella_vslam_examples && mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_PREFIX_PATH=$HOME/slam/deps .. && make -j10
# 产出 run_camera_slam / run_video_slam / run_image_slam ...
```

### 词典
`~/slam/orb_vocab.fbow`（本机下载后 scp）：
`curl -fL https://github.com/stella-cv/FBoW_orb_vocab/raw/main/orb_vocab.fbow -o orb_vocab.fbow`

## 运行（headless）
```bash
pkill -f cam_imu_server   # 释放 /dev/video3（与监控页二选一）
export LD_LIBRARY_PATH=$HOME/slam/deps/lib
cd ~/slam/stella_vslam_examples/build
./run_camera_slam -v ~/slam/orb_vocab.fbow -c ~/slam/c920_mono.yaml -n 3 --viewer none -o ~/slam/map.msg
```
- **单目必须“移动”相机**（平移产生视差）才会初始化建图；静止只停在初始化。
- `Ctrl-C`/SIGINT 退出时把地图存到 `map.msg`（`--map-db-in map.msg --disable-mapping` 可纯定位回放）。

## 待办
- **M2 实时网页可视化**（SocketViewer：protobuf + socket.io-client-cpp + socket_publisher + Node 服务 → 浏览器看建图）。
- 相机标定（`c920_mono.yaml` 现为 FOV 估算内参 → Kalibr/OpenCV 棋盘格标定）。

## 验证状态
- 2026-06-21：g2o + 库 + 运行程序原生编译通过；`run_camera_slam` 实测 **`camera opened 1280x720`**、帧被接受、SLAM 各模块正常启动（静止未建图，符合单目预期）。下一步需带运动验证建图 + 上 viewer。
