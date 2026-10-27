# 板端 AVIA 在线采集验收 · 2026-10-27

本轮交付：板子 `192.168.1.51` 独立运行本工程编译的 SDK/驱动并录制 AVIA `192.168.1.189`；主机脱离交换机后继续录制，接回后停止、校验回传并三维回看。地址仅由 project.yaml 的 board.target / capture.livox.ip 提供；源码与运行程序没有增加现场设备 IP 常量。

## 构建、部署与检查

- [sdk-driver-build.log](sdk-driver-build.log)：直接从 third_party/Livox-SDK 构建 livox_sdk_static，从 third_party/livox_ros_driver 构建驱动，使用本工程 aarch64.cmake。SDK 静态链接进驱动，板上不需要另一套 SDK 安装或头文件。
- [release.json](release.json)：运行包 73c84007d22af222，149.1 MiB；包含私有 Noetic/Python/动态库、驱动、管理程序和保留的 LIVO2。deploy 核对 SHA256，板端导入 rospy/rosbag/yaml 通过，运行不依赖板端联网或预装 ROS。
- [doctor-idle.log](doctor-idle.log)：板端 aarch64、完整部署清单/动态库、路由源地址/网卡、广播码 3JEDNAL001PY891 通过。实际源网卡 enx6c1ff7295dee，192.168.1.51/24。
- [doctor-live.log](doctor-live.log) / [doctor-live.json](doctor-live.json)：运行中使用实际消息检查，不重新占用 SDK UDP 55000；点云约 10 Hz、IMU 约 203 Hz，无头时间倒退；相机无配置不阻止 lidar 模式。
- [host-doctor.log](host-doctor.log)：主机原有检查回归通过。板端 SDK 占用雷达时，主机可能收不到广播；停止板端后主机发现正常，未额外启动主机驱动。
- 板端系统时钟最初停在 3 月；空闲持锁校正后与主机粗测差约 −0.07 秒。只修正系统日期，不宣称精密时间同步。首条短录的旧记录时间已在 [short-validation.json](short-validation.json) 标注。

## 真实拔线和走动

任务 `20261027-202025-885b4b`。用户确认实际拔主机网线、室内走动并接回；主机网卡 carrier 采样（约 1 秒粒度）观察到 UP→DOWN→UP，断线约 23.022 秒。细节见 [walk-validation.json](walk-validation.json)。

| 检查 | 点云 | IMU |
| --- | --- | --- |
| 全包消息 | 751 | 15235 |
| 按设备头时间频率 | 10.0001 Hz | 202.8497 Hz |
| 拔线期间写入 | 230 | 4663 |
| 拔线段最大头时间间隔 | 100.660 ms | 5.093 ms |
| 消息序号缺口 / 头时间倒退 | 0 / 0 | 0 / 0 |

全包约 75.002 秒，348458849 字节（332.3 MiB）。遍历全部 751 个 CustomMsg，结构长度、点数和坐标有限性通过；共 18024000 个原始点，其中 6265063 个非零有限点。无序号缺口指驱动发布消息序号，不能等同 UDP 原始包或所有激光点零丢失。

主机重连后 doctor 仍观察到 recording 与累计收数。stop 后 monitor/master/probe/recorder/driver 全部退出 0，[walk-summary.json](walk-summary.json) 结果 stopped、无失败原因、日志 WARN/ERROR 计数均 0。所有结果逐文件 SHA256 核验通过，见 [walk-transfer.json](walk-transfer.json)。[walk-capture-report.json](walk-capture-report.json) 保存最终包计数，旁路 probe 比 recorder 少量差异不作丢包率解释。

主机 `./livo view results/20261027-202025-885b4b/data` 实际读取当前包并通过 WebGL 渲染，DOM 确认第 1/751 帧、7088 个显示点，无渲染/加载错误；见 [walk.png](walk.png)。另外通过浏览器实际拖动到断线中点的第 384 帧（零基 index=383，38.329 秒），显示 3688 个点，无全局错误，见 [断网期间点云](walk-offline-frame.png) 和 [帧状态](viewer-frame.json)。当前只显示雷达坐标系内单帧，不累计建图；无相机流。

## 生命周期与 CLI

- [duplicate-start.log](duplicate-start.log)：运行中再次启动返回 1，只有简短错误信息，原任务保持录制。
- 短录自动结束、stop 及重复 stop、pull 再次校验通过；[repeat-stop.log](repeat-stop.log) 为重复停止。
- [controlled-failure.log](controlled-failure.log)：仅对当前任务父 PID 下、路径匹配的本工程驱动模拟 SIGTERM。supervisor 检出 livox exited unexpectedly，自动关闭 recorder 并保留有索引双路 bag，结果 failed，stop 返回 1，回传通过；见 failure-summary.json / failure-capture-report.json / failure-transfer.json。这是有意注入故障，不是自发崩溃。
- 私有 ssh 命令进入板端环境，ROS_DISTRO=noetic、ROS_IP=127.0.0.1，driver 可执行路径及 rospy/rosbag 导入通过；源环境不依赖主机 ROS。
- 原有 6 个监控测试及 Python 编译检查通过；现有测试文件未修改。未重跑数据集或 LIVO2，与本轮实机采集无关。

## 边界

验收覆盖单次室内拔线走动和短录，尚未覆盖小时级采集、断电恢复、相机 SDK、曝光/三路同步及外参精度。AVIA 消息头是设备启动时间；bag 记录时间来自已粗校时的板子。时间序号检查和 bag 完整性不保证原始 UDP 零丢包或几何监督精度。

真实大包和完整原始日志保留 results/，不提交 Git。运行参数有 resolved 快照；当前 project.yaml 已设置持续录制 duration_s=null，仍受 run.timeout_s 和空间保护。无需修改内部生成配置。
