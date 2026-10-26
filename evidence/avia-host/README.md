# 主机 AVIA 真机接入

2026-10-26，Ubuntu 22.04 主机；有线 enp10s0=192.168.1.50/24，AVIA=192.168.1.189，广播码 3JEDNAL001PY891。Wi-Fi 保持互联网默认路由，未修改已有网卡地址或雷达 IP。

## 发现失败与修复

ping 三次均通，但 Livox Viewer 和独立 UDP 55000 监听均收不到设备广播。主机 UFW 配置启用；用户在本机执行以下定向放行后，立刻收到 device_type=7 的 AVIA 广播，并完成 SDK 连接：

```bash
sudo ufw allow in on enp10s0 from 192.168.1.189 proto udp
```

没有关闭防火墙。Viewer 自带设备通信，不需要本工程 ROS 驱动才能发现雷达。工程驱动与 Viewer 都占用 Livox SDK 端口/设备连接，运行工程前先退出 Viewer；本轮在检查没有打开录制文件后正常退出 Viewer，验证结束后重新启动原来的 Viewer。

## 真机验收

命令 `./livo --host lidar`，按 project.yaml 录制 30 秒；主机私有 ROS master、Livox-SDK 2.3.0、livox_ros_driver 2.6.0，x86_64 原生构建。新增 <memory> 与新 PCL shared_ptr 的兼容补丁；消息格式和采样算法不改。

- 全部节点退出 0；monitor WARN=0、ERROR=0；文件回传 SHA256 通过。
- bag 141067168 字节，304 帧 CustomMsg，6181 条 IMU。旁路观察 6166 条 IMU，bag 中实际记录 6181 条；二者不能混为同一计数。
- bag 点云头时间频率 10.0002 Hz、接收频率 9.9996 Hz；IMU 头时间频率 203.303 Hz、接收频率 203.949 Hz。
- 两路消息序号无缺口，头时间戳无倒退；7296000 点全部坐标有限，其中 2130651 点距离大于 5 cm，其余含零点/近点，不能把有限点数都称为有效场景点。
- CustomMsg 声明点数与数组长度/原始字节长度一致，帧内逐点 offset_time 无倒退。

注意：本次消息头使用设备启动后的时间（约 485–516 秒），不是 UTC 墙钟。这里只验证设备收数、时间单调、录包和生命周期；相机同步、UTC 同步、标定精度与真实板端收数尚未验收。

证据为本目录 JSON。大 bag 与完整原始日志保留在 ../../results/20261026-205020-7c1404/，忽略 Git。
