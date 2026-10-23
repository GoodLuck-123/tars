# LIVO2：RK3588 数据集实验

本项目位于 `grad/实验/livo2`，独立 Git。当前已完成板端短片段与完整序列基线回放，正在验收源码布局整理后的正式交付包；真实设备与录包后续接入。

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

已有完整基线：173.75 秒输入，2575 个位姿和视觉更新，27 个点云块，全部进程正常退出，结果重收校验通过。处理用时约 556 秒，属于功能验收，不是实时通过；原始结果位于 results/20261023-203612-f62a97，小型证据见 evidence/full-baseline。回放退出时 ROS master 出现一次 publisherUpdate XMLRPC 错误，未导致数据末段覆盖缺失或节点退出失败，原始错误完整保留。
