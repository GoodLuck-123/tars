# LIVO2：RK3588 数据集实验

本项目位于 `grad/实验/livo2`，独立 Git。当前正在实现和验收远控基础功能与板端 FAST-LIVO2 数据集回放；真实设备与录包后续接入。

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
