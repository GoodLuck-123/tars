# AVIA + 单目相机采集与主机处理

在 RK3588 上录制原始图像、AVIA 点云和 IMU，自动传回电脑；在电脑离线运行 FAST-LIVO2 和免标定板标定。入口只有 `./livo`，用户参数只有 `project.yaml`。

可复用的工程设计范式见 [算法与传感器部署 skill](skills/edge-experiment-platform/SKILL.md)，用于后续类似项目的整体设计与接入。

```bash
cp project.example.yaml project.yaml   # 已有配置时跳过
./livo doctor --sensors                # 检查板端环境、驱动产物与设备节点
./livo record --background             # 板端实机录相机/点云/IMU，确认收数后返回
./livo --host run                      # 电脑离线运行 LIVO2，自动准备环境
./livo status --follow                # 查看板端当前任务摘要和重要告警
./livo stop                           # 有序停止并回传
./livo pull                           # 断线后补收最近一次结果
./livo ssh                            # 进入板端私有 ROS 环境
```

主机任务的查询、停止、回传使用同一命令加 `--host`，例如 `./livo --host status --follow`。SSH 断线后任务继续；运行命令的 Ctrl-C 请求停止并回传，`status --follow` 的 Ctrl-C 只退出查看。完整日志保留在结果里，`status --raw` 按需查看当前模式的原始日志。

当前候选版本 `v0.2.0-rc5`；主机完整 LIVO2、压缩/raw 录包演练、停止与标定软件检查通过，详见 [验收证据](evidence/software/README.md)。

AVIA 已通过板端真实采集、主机拔线后独立录制、接回后停止回传及三维回看验收：点云约 10 Hz、IMU 约 203 Hz，见 [板端验收](evidence/board-avia/README.md)。FLIR 相机已完成主机与板端三路录包、拔线移动和同屏回看，见 [相机验收](evidence/flir-camera/README.md)；硬触发同步和标定待传感器固定后处理。主机先前短录见 [主机真机证据](evidence/avia-host/README.md)。`record --replay` 是软件演练，不代表真实相机、雷达或同步通过验收。示例数据为 `/home/eric/data/hku_campus_seq_03.bag`；示例内外参仅用于已有数据，精度未验证，不能直接用于之后购买的相机。

## 板端独立录制 AVIA

只需维护 `project.yaml` 的两处设备地址，所有 CLI 自动复用，不用改源码、SSH 脚本或内部 ROS 配置：

```yaml
board:
  target: firefly@192.168.1.51
  # 其余 board 字段保留
capture:
  duration_s: null       # 持续录制；也可设秒数，到时自动结束
  livox:
    ip: 192.168.1.189
    # 其余 capture / livox 字段保留
```

以上仅展示已有配置中要改的字段，不要用它覆盖整份配置。主机/板端去雷达的源 IP、网卡自动按路由选择；主机有线静态地址由系统维护，不在工程内重复配置。其他设备今后也统一在 project.yaml 接入。

```bash
./livo deploy                         # SDK/驱动、私有 ROS 等 ARM64 包推上板子
./livo doctor --sensors                # 空闲查环境/发现；运行中查实际点云和 IMU
./livo lidar --background              # 等到真实收数后返回，即可拔电脑网线
# 带着板子、雷达、交换机采集，保留它们的供电和互连；电脑不需参与
./livo status                         # 重新接回电脑网线后查看状态
./livo stop                           # 正常结束并自动校验回传；可重复执行
./livo view                           # 电脑打开最近一次录包的三维回放
```

`lidar` 已包括点云/IMU 原始录包，不需要另开 recorder。后台运行不依赖 SSH、主机或外网。`capture.duration_s:null` 时仍受 `run.timeout_s`（默认 1800 秒）和剩余空间保护约束；需要更长录制就在同一配置里调整。前台 `./livo lidar` 也可用，Ctrl-C 请求正常停止和回传。

`doctor --sensors` 会校验部署清单、私有 Python/ROS、驱动动态库、到 AVIA 的源地址/网卡、广播码；录制期间复用现有任务的实时消息检查，不抢占驱动 UDP 端口。相机未配置不会阻止 lidar 验收。雷达已连接另一个采集端时可能不广播，应在实际采集端检查，或先停止另一个客户端。

`board.sync_time:true` 默认在部署/启动前**持有空闲任务锁**时将大于 5 秒的板端时钟误差按主机修正，需要板端 `sudo -n date` 权限；录制期间不会校时。此操作只纠正系统日期，不是曝光、LiDAR 或 IMU 的精密时间同步。板子无需联网校时。

`status --follow` 给低频计数/频率和聚合 WARN/ERROR，`status --raw` 按需查看驱动/相机/recorder 原始日志。重复启动会拒绝且不干扰当前采集；驱动退出或断流会结束并保存失败证据，`stop` 返回失败退出码。正在写入的包不能 pull，先 stop；断线后已经结束的任务可用 pull 补收。

## 在主机单独运行 AVIA

```bash
./livo --host lidar                    # 本机只运行雷达/IMU，并按配置录包
./livo --host lidar --background       # 后台启动，使用 --host status / stop 管理
```

先退出 Livox Viewer 或其他占用雷达的程序。在 project.yaml 的 capture.livox.ip 填设备 IP，入口自动发现广播码并仅连接该设备；所有记录时长仍使用 capture.duration_s。主机已支持完整 SDK/驱动原生构建。若 ping 通但发现不到设备，检查来自雷达的 UDP 入站规则，详见真机证据。后台命令返回只表示启动成功，要用 status 确认 recording 与实际收数。

## 三维回看录包

```bash
./livo view                            # 自动打开最近一次录包
./livo view results/某次任务/data       # 也可指定一个 .bag 或分包目录
```

浏览器左侧显示真实三维点云：左键旋转、右键或 Shift+左键平移、滚轮缩放，点击「复位视角」重新居中。支持反射强度/高度/距离着色、点大小、播放暂停、拖动进度、逐帧和倍速。空格暂停，左右箭头逐帧；焦点在输入控件上时使用控件自身按键。

右侧相机与点云共用播放时刻；无相机时显示预留区域，已有包可直接播放 JPEG/PNG 压缩图像及常见 raw 图像。缺帧或图像损坏只提示相机状态，点云继续播放。这里只按 **bag 记录时间** 找最近图像，不能据此认定硬件同步已通过；不做点云图像投影或外参变换。

`project.yaml` 的 `viewer.bag` 可固定默认录包，`viewer.max_points` 控制每帧最多显示点数，`viewer.image_tolerance_s` 控制图像允许时差；默认分别为自动选最近、100000、0.15 秒。抽样及零点/无效点过滤仅影响显示，不修改原始包。无需启动 ROS、SDK 或雷达，只依赖工程 Python 环境和支持 WebGL 的浏览器，资源均在本地。`--no-open` 只打印地址，Ctrl-C 关闭播放器。今天 AVIA 实录的全部 304 帧已通过读取检查，详见 [回放验收](evidence/viewer/README.md)。

## FLIR 相机与三路采集

BFS-U3-50S5C-C 使用 USB3。首次在主机初始化权限和缓冲（需要一次 sudo）；板端 deploy 会自动完成同样的初始化，板子无需安装系统 ROS 或全套 SDK：

```bash
bash runtime/setup_camera_usb.sh
./livo --host preview                 # 空闲时只看相机，不录包；录制中复用图像流
./livo --host preview --stop           # 关闭预览，录包不受影响
./livo --host record --background      # 主机同时录原始图像、点云、IMU
./livo --host stop                    # 停止、校验结果
./livo view                           # 同一网页播放三维点云和彩色图像
./livo deploy                        # 主机交叉编译驱动，打包 ARM64 SDK 后部署
./livo doctor --sensors               # 换接板子前检查产物；换接后检查 USB3/权限/SDK
./livo record --background            # 换接相机到板子后同时录三路
./livo stop                          # 拔线移动、重连后执行，结束并回传
./livo view
```

独立预览独占相机；开始录制前先关闭它。录制中打开的预览不占设备、不修改曝光。所有参数仍在 project.yaml：

```yaml
capture:
  camera:
    type: spinnaker
    serial: "24223920"
    fps: 20.0
    exposure_auto: "Off"
    exposure_us: 10000.0       # 微秒，10000 = 10ms；你主要调整此项及镜头光圈
    gain_auto: "Off"
    gain_db: 12.0
    white_balance_auto: "Off"
    white_balance_red: 1.68262
    white_balance_blue: 2.31433
```

这是要改的字段摘录，不要覆盖整份配置。帧率、分辨率、增益、白平衡和 Gamma 默认固定；曝光默认手动，每次启动按配置重新设置，修改后需停止并重新启动才生效。20Hz 的帧周期为 50ms，曝光不可超过帧周期；过长曝光会造成运动拖影，尽量开大光圈、缩短曝光。硬件有离散步长，例如请求 10000us，实测为 10004us，逐帧真实值与设备时间戳保存在 /camera/metadata。

相机 20Hz 与 AVIA 点云 10Hz 不要求相等；它们仅为自由运行的固定速率，**没有硬触发同步**。相机 Header 使用主机接收时间，另存设备时间戳；雷达时间来源不同。网页按 bag 记录时间同时回看，仅用于检查数据链路。本轮不做硬同步、相机内参或传感器外参标定，待传感器固定后再做。

录制保留 2448×2048 BayerRG8 原始图像、点云/IMU、逐帧曝光/增益元数据和未标定 CameraInfo，使用 LZ4 无损压缩。实时预览与网页仅为显示做彩色解码及 viewer.bayer_gamma（默认 2.2）转换，JPEG 也仅用于显示，不改变包内像素。帧率提高会增加原始数据量；主机的已完成包回收到 results 时共享本机文件，不再重复占用相同录包空间。

capture.duration_s=null 表示持续录制，使用 stop 结束；也可设秒数。三路未到齐、时间戳倒退、持续断流、磁盘不足及进程异常会停止并保留证据。相机未连接时 doctor 明确显示 absent，运行库检查通过不等于硬件验收。源码与 SDK 来源见 [Spinnaker 说明](third_party/spinnaker/README.md)。

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
| `viewer/` | 浏览器三维点云及图像回放，不依赖 ROS |
| `results/<run-id>/` | 回传的配置、摘要、原始日志、bag 或轨迹/地图 |
| `.cache/`、`.build/`、`dist/` | 私有环境、构建和运行包，均忽略 Git |

第三方源码是固定提交的 Git 子模块，构建直接使用可见目录。平台补丁由外层仓库追踪；子模块因这些补丁显示修改是预期状态。人工改源码后自动按内容指纹重建，不重置编辑。新克隆仓库运行 `git submodule update --init --recursive`。

板端包为 `dist/livo2-<id>-arm64.tar.gz`（约 149 MiB，不含数据集）。可解压后执行 `install/install.sh ~/livo2-workspace` 独立安装，常规入口会自动部署。`stop` / `pull` 回传后逐文件校验 SHA-256 并落盘，默认自动删除该任务的板端录包，保留日志、配置和清单。传输中断或校验失败时不删除；清理阶段断线可再次 `pull`。需要保留双份时，在 `project.yaml` 设置 `transfer.delete_remote_bags_after_verify: false`。主机 `--host` 模式不清理原始任务。历史任务也可逐个 `./livo pull <run-id>`，完整回传校验后才清理，需预留主机空间。详细设计、阶段状态和剩余硬件验收见 [PLAN.md](PLAN.md)。
