# Spinnaker SDK

厂商 SDK 为闭源二进制。本工程从上游 ros-drivers/flir_camera_driver 的公开构建脚本所引用的下载地址获取 4.2.0.88 amd64/arm64 包；来源、版本、Ubuntu ABI 与 SHA256 见 sdk.lock.json。构建时校验 SHA，再解包到本目录，不执行厂商系统安装脚本。

- [厂商 SDK 页面](https://www.teledynevisionsolutions.com/products/spinnaker-sdk/)
- [上游公开下载脚本](https://github.com/ros-drivers/flir_camera_driver/blob/humble-devel/spinnaker_camera_driver/cmake/download_spinnaker)
- [相机模拟控制 / 白平衡 / Gamma 说明](https://softwareservices.flir.com/BFS-U3-50S5/latest/Model/public/AnalogControl.html)

`amd64/opt/spinnaker`、`arm64/opt/spinnaker` 中保留头文件、SDK 库与采集示例，便于查看调试；归档和解包目录不提交 Git。源码参考保留在平级 flir_camera_driver 子模块（noetic-devel）；实际编译的是 runtime/flir 轻量 ROS1 传输适配器，不声称编译了完整上游 nodelet 驱动。

ARM64 运行包只包含驱动、SDK 运行库、GenTL USB producer、依赖闭包及厂商许可文件，不包含 GUI/开发头文件。许可证随包保留在 licenses/spinnaker。主机通过工程 MJPEG 实时预览，无需 SpinView。
