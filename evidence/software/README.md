# 板端采集 / 主机处理的软件验收

2026-10-23 至 24。无真实 AVIA/相机接入；下列均为软件验证，不证明硬件同步或标定精度。

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 压缩图像录包演练 | 板端 10 秒回放；151 图像、99 点云、2042 IMU；全部进程 0，SHA256 回传通过 | capture-replay/ |
| 原始载荷保持 | 上述每条消息的 SHA256 与源包对应前缀相同，包含原始头时间戳 | capture-replay/payload-equality.json |
| raw 图像录包演练 | 120 图像、79 点云、1634 IMU；与输入所有消息载荷逐条相同；回传通过 | capture-raw/ |
| 主动停止 + 重复停止 | 录包期间 stop，61 图像、40 点云、837 IMU；状态 stopped，全部进程 0，重复 stop 幂等 | capture-stop/ |
| 无设备失败注入 | 相机以不发消息的 sleep 代替，AVIA 无设备；启动超时，命令非零；SDK/recorder 正常退出 0，失败日志自动回传 | missing-sensors/ |
| 主机完整 LIVO2 | 173.75 秒输入，1 倍速；180.45 秒完成，2575 位姿、2574 视觉更新、27 PCD、末段差 0.0928 秒；全部进程 0，回传通过 | host-full/ |
| 主机 raw LIVO2 | 7.96 秒派生 raw 包；112 位姿、111 次视觉更新、3 地图块；末段覆盖、退出与回传通过 | host-raw/ |
| 标定软件 smoke | 上游 livox_camera_calib 无界面执行，4.06 秒，全部进程 0；候选矩阵/投影生成、矩阵结构检查通过 | calibration/ |

raw 包由原始 JPEG 解码成 1280×1024 BGR8 派生，仅用于接口/吞吐演练，不能称为真实相机原始数据。其图像不在板端编码。主机处理 raw/压缩输入时自动选择对应话题，并在解码器中统一去畸变和缩放。

标定 smoke 输入为同一数据集约 0.7 秒内 101303 个 AVIA 点与对应一张图像。该短时积累未证明静态/运动补偿或标定可观性；候选外参有明显变化，只验软件执行和格式，报告 accuracy_verified=false、candidate_only=true，不采用进真实配置。投影原图保留在本机 results 对应任务，不提交大文件。

## 修复与保留的问题

主机原先在约 621 次视觉更新后发生 double free / SIGABRT，两次失败保留于 host-failures.json。pcl-abi-reproducer.cpp 能单独触发 VoxelGrid 的范围溢出回退分支，复现跨动态库 malloc/free 不匹配；-march=native 时失败，即使仅设置 Eigen MAX_ALIGN_BYTES=16 也失败。GDB 指向 PointCloud 销毁时的无效 free。统一为 x86_64 基线并匹配 PCL 导出的 C++ 对齐定义后，该复现 30 次循环通过，完整 LIVO2 通过。修复在 buildkit/native-compat.cmake，不修改算法数学逻辑。

AVIA 驱动原先无数据时阻塞于 Semaphore::Wait，且自定义信号处理器退出不安全。小补丁改为有界等待、ROS 默认信号处理与幂等 SDK 清理；无设备失败注入验证 Livox SDK 2.3.0 启动和 Deinit completely，退出 0，没有 SIGKILL。

监控优先解析 INFO/WARN/ERROR 头，避免 roslaunch INFO 中环境变量文本 ERROR 导致误报；无级别 double-free、异常栈仍可见。现有 6 个监控测试全部通过，另人工断言 INFO 含 ERROR 不误报、真实 ERROR/内存异常保留。

录包演练启动前等待 /clock 的 WARN 可能在 console/rosout 中各记一次，格式未可靠匹配时没有强行去重。ROS 回放结束时的 publisherUpdate XMLRPC 错误、主机 player 注销时的 ConnectionRefusedError 仍完整保留；本次没有造成末段缺失或非零节点退出。WARN/ERROR 计数按日志记录，不能当成唯一故障次数。

summary.json 的 transfer=pending 表示远端任务完成时尚未回传；本目录 transfer.json 单独证明主机收取并 SHA256 校验完成。板端系统日期与主机不同，耗时用单调时钟，不能用双方墙钟差推断延迟。

## 可重复入口

正常用户只需工程 README 中的 ./livo 命令。软件演练使用 project.yaml 的现有数据集；raw 派生包、故障注入配置及大结果位于忽略的 .cache / results，不作为正式采集配置。

- ARM64 release 身份：arm64-release.json；SDK/驱动与算法源码版本：../../dependencies.lock。
- 主机环境锁：../../buildkit/locks/host-linux-64.txt；构建内容身份：host-build.json。
- 真实硬件待验：相机 SDK/曝光时间戳、AVIA 实际收数、同步、长时录包、掉线/磁盘故障、真实内外参与离线重建质量。
