# AVIA + 单目相机采集与主机处理

在 RK3588 上录制原始图像、AVIA 点云和 IMU，自动传回电脑；在电脑离线运行 FAST-LIVO2 和免标定板标定。入口只有 `./livo`，用户参数只有 `project.yaml`。

```bash
cp project.example.yaml project.yaml   # 已有配置时跳过
./livo doctor --sensors                # 检查板端环境、驱动产物与设备节点
./livo record --replay                 # 现在即可用：用现有 bag 演练板端录包、停止和回传
./livo --host run                      # 电脑离线运行 LIVO2，自动准备环境
./livo status --follow                # 查看板端当前任务摘要和重要告警
./livo stop                           # 有序停止并回传
./livo pull                           # 断线后补收最近一次结果
./livo ssh                            # 进入板端私有 ROS 环境
```

主机任务的查询、停止、回传使用同一命令加 `--host`，例如 `./livo --host status --follow`。SSH 断线后任务继续；运行命令的 Ctrl-C 请求停止并回传，`status --follow` 的 Ctrl-C 只退出查看。完整日志保留在结果里，`status --raw` 按需查看当前模式的原始日志。

当前软件候选版本 `v0.2.0-rc1`；主机完整 LIVO2、压缩/raw 录包演练、停止与标定软件检查通过，详见 [验收证据](evidence/software/README.md)。

当前硬件尚未接入。`record --replay` 是软件演练，不代表真实相机、雷达或同步通过验收。示例数据为 `/home/eric/data/hku_campus_seq_03.bag`；示例内外参仅用于已有数据，精度未验证，不能直接用于之后购买的相机。

## 硬件到位后

相机型号/SDK 确定后，将其放到 `third_party/`，再接入对应 ARM64 构建及启动命令。AVIA 官方 SDK 和驱动已经在工程内，不用另行下载。仅需在 `project.yaml` 设置相机启动命令、图像话题/格式、AVIA 广播码、采集时长与录包参数：

```bash
./livo record                         # 启动 AVIA/相机、录制、结束、校验并回传
```

默认录制 30 秒；`capture.duration_s: null` 表示持续采集，使用 stop 结束。录包保留驱动原始消息，不经过 LIVO2、缩放或去畸变。按体积分包；三路未到齐、时间戳倒退、持续断流、磁盘不足及进程异常会停止并保留失败证据。报告中的序列缺口来自旁路观察，不能直接解释成传感器或 recorder 丢帧率。

## 在电脑处理

```bash
./livo --host run                     # project.yaml 的 dataset.path
./livo calibrate                      # project.yaml 的 calibration.image / pcd
```

LIVO2 自动识别配置话题中的 raw / 压缩图像，在电脑端缩放和去畸变；`topics.raw_image` 是采集输入，`topics.image` 是处理输出，两者不能相同。

`calibrate` 自动使用主机环境，需要原始图像、对应静态场景 PCD 和有效相机内参。输出 `extrinsic.txt`（`T_camera_lidar`）、初始/优化投影图和候选报告，不自动覆盖配置。它是 `hku-mars/livox_camera_calib` 的文件输入标定流程；候选收敛不代表精度合格，需要后续真实场景投影及重复采样检查。

保留 `./livo run` 在板端回放 LIVO2，供实验室复用。该能力已有完整序列验收，但板端实时运行不再是采集平台的要求。板端性能历史为约 250 秒处理 174 秒输入，不能称为实时；见 [性能证据](evidence/performance/README.md)。

## 构建与源码

主机需 Linux x86_64、uv、CMake、Make、SSH、rsync、curl 和 dpkg-deb。CLI 用 uv；ARM64 工具链/sysroot 与主机 ROS/C++ 环境各自锁定。主机环境使用私有 micromamba 前缀，不依赖其他项目；首次准备需联网。板端无需联网或预装 ROS，也不在板端编译。

```bash
./livo build                         # ARM64 SDK、驱动、LIVO2 和私有 ROS 运行包
./livo deploy                        # 只部署；日常 record/run 会自动完成
./livo --host build                  # 主机 LIVO2、图像解码与标定工具
```

| 目录 | 内容 |
| --- | --- |
| `third_party/FAST-LIVO2` | LIVO2 算法源码 |
| `third_party/Livox-SDK`、`third_party/livox_ros_driver` | AVIA 官方 SDK / ROS 驱动，从源码交叉构建 |
| `third_party/livox_camera_calib` | HKU-MARS 雷达—相机免标定板标定 |
| `third_party/Sophus`、`third_party/rpg_vikit` | 几何与视觉源码依赖 |
| `third_party/perception_pcl`、`third_party/pcl_msgs` | 点云转换与消息 |
| `third_party/R3LIVE-reference` | 现有数据标定方向的参考，不参与构建 |
| `patches/`、`buildkit/` | 平台适配、源码/二进制依赖锁与构建配方 |
| `runtime/` | 远控任务、录包、摘要监控和图像解码 |
| `results/<run-id>/` | 回传的配置、摘要、原始日志、bag 或轨迹/地图 |
| `.cache/`、`.build/`、`dist/` | 私有环境、构建和运行包，均忽略 Git |

第三方源码是固定提交的 Git 子模块，构建直接使用可见目录。平台补丁由外层仓库追踪；子模块因这些补丁显示修改是预期状态。人工改源码后自动按内容指纹重建，不重置编辑。新克隆仓库运行 `git submodule update --init --recursive`。

板端包为 `dist/livo2-<id>-arm64.tar.gz`（约 149 MiB，不含数据集）。可解压后执行 `install/install.sh ~/livo2-workspace` 独立安装，常规入口会自动部署。结果逐文件 SHA256 校验，保留远端副本。详细设计、阶段状态和剩余硬件验收见 [PLAN.md](PLAN.md)。
