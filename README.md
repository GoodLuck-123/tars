# LIVO2：RK3588 数据集实验

本项目位于 `grad/实验/livo2`，独立 Git。当前已通过远控基础功能及板端完整数据集回放验收；真实设备与录包后续接入。

```bash
cp project.example.yaml project.yaml   # 已有配置时跳过；用户只修改此文件
./livo doctor                         # 检查板端与三路输入
./livo run                            # 自动构建/部署、上传、运行并回传
./livo status --follow                # 摘要和告警，避免原始日志刷屏
./livo stop                           # 正常停止并收取结果
./livo ssh                            # 进入板端运行环境
./livo pull                           # 中断后重收最近一次结果
```

主机需要 Linux x86_64、uv、CMake、GNU Make、SSH、rsync、curl、dpkg-deb；构建使用项目私有 ARM64 工具链与依赖，不需要板端联网或预装 ROS。主机 Python 环境和缓存由入口自动管理。

唯一配置为 `project.yaml`。默认板端 firefly@10.106.0.220，工作区 `~/livo2-workspace`；默认数据集 `/home/eric/data/hku_campus_seq_03.bag`。首次构建从锁定 URL 下载依赖；可在 build.seed_caches 中提供已有包缓存加速，内容必须通过 SHA256。

原始日志、实际配置、轨迹、分块地图和监控事件自动收回 `results/<run-id>/`；被 Git 忽略的大文件仍保存在本地。`status --raw` 可按需查看原始算法日志。SSH 断线不停止任务；run 的 Ctrl-C 请求正常停止并回传，status --follow 的 Ctrl-C 只退出查看。

可单独使用 `./livo build` 和 `./livo deploy`。设计、验收要求与实施记录见 [PLAN.md](PLAN.md)。

源码统一位于可见的 `third_party/`，全部固定 Git 提交；构建直接使用这些目录。

| 目录 | 用途 |
| --- | --- |
| `third_party/FAST-LIVO2` | 主算法，可直接查看与调试 |
| `third_party/Sophus`、`third_party/rpg_vikit` | 几何与视觉依赖 |
| `third_party/livox_ros_driver` | AVIA 驱动；当前数据集版只构建原始消息定义 |
| `third_party/perception_pcl`、`third_party/pcl_msgs` | 点云转换与消息 |
| `third_party/R3LIVE-reference` | 现有数据集标定方向的只读参考，不参与构建 |

`patches/` 保存本工程的少量适配，构建时应用到可见源码，不重置人工修改。子模块显示这些补丁造成的本地修改是预期状态；补丁由外层 Git 追踪。手动修改源码后运行 `./livo build`，`run` 也会根据源码指纹自动重建过期产物。隐藏目录仅保存归档缓存、工具链/sysroot 及生成产物。

正式产物在 `dist/livo2-<id>-arm64.tar.gz`；也可在板端解压后执行 `install/install.sh ~/livo2-workspace` 独立安装。常规使用不需要手动操作产物。

当前版本 `v0.1.1`：同一完整序列处理约 250 秒（原 `v0.1.0` 约 528 秒），2575 个位姿/视觉更新、27 个非空 PCD，末端差 0.093 秒，进程退出和结果回传校验通过。默认按 1 倍速输入，随后排空积压；本数据约 15 Hz，当前配置仍未达到实时。核查来源、对照与边界见 [性能记录](evidence/performance/README.md)。

`project.yaml` 的 `runtime` 集中设置 A76 核绑定、OpenCV 线程数和临时调频策略。默认 `cpu_governor: performance` 在任务期间使用板子支持的最高频率，需要板端 `sudo -n`；完成、stop 或受控异常后恢复原值，`null` 可关闭。运行期间 CPU 频率、原调频器和配置随结果保存。图像在解码端按 camera.scale 输出，模型内参同步缩放。

回放退出时 ROS master 的 publisherUpdate XMLRPC 错误仍完整保留；此次未造成末段覆盖缺失或节点退出失败。未做真值精度评测，硬件传感器模式尚未接入。旧版功能证据保留在 [evidence/README.md](evidence/README.md)，tag `v0.1.0` 与 `v0.1.0-rc1` 保留。正式包约 149 MiB，不包含数据集。
