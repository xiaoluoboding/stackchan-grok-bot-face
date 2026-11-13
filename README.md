# StackChan Grok Bot Face

基于 [M5Stack StackChan](https://github.com/m5stack/StackChan) 的固件定制版：在机器人屏幕上播放 Grok Bot 表情，保留语音对话，并针对实际设备上的联网、音频播放和待机动作做了调整。仓库同时包含上游的手机应用、服务端和 ESP-NOW 遥控器源码。

> 本项目与 M5Stack 官方固件、Grok Bot 原作者均无隶属关系。Grok Bot 动画来源及权利说明见 [第三方说明](third_party/grokbot/README.md)。

## 当前固件支持什么

| 功能 | 当前行为 |
| --- | --- |
| 语音唤醒与对话 | 对机器人说 **“你好小智”** 唤醒；使用 ESP-SR 的 WakeNet9l 模型在设备端识别唤醒词。后续对话需要 Wi-Fi、可用的语音服务以及设备激活。 |
| Grok Bot 表情 | 53 组预制动画对应待机、聆听、说话、连接状态和 AI 情绪；表情资源损坏或缺失时回退到原有头像。屏幕表情动画与实体头部运动独立。 |
| 待机头部 | 默认关闭周期性随机摆头。升级已有配置的设备首次启动时也会将旧的闲置动作设置改为关闭；以后仍可在设备设置中手动开启。触摸和主动控制头部的能力保留。 |
| Wi-Fi 与语音连接 | 同名热点优先连接扫描到的较强接入点，并在语音运行时保持高性能 Wi-Fi 模式；保留 MQTT/TLS 连接和服务端激活流程。 |
| 音频播放 | 调整音频任务、缓冲和屏幕动画的资源分配，减少播放卡顿。聆听和说话时降低表情帧率，为语音留出处理时间。 |
| 固件更新 | 启动时仍向配置服务获取连接参数，并完成必要的激活流程；**不在启动时比较版本、自动下载固件或循环重试失败的配置请求**。设备设置中的手动更新入口仍可使用。 |

这些行为已在一台 M5Stack StackChan（ESP32-S3、16 MB Flash、8 MB PSRAM）上验证：设备连接 Wi-Fi 和语音服务后，能够用“你好小智”唤醒并正常回复，待机时头部不再自动摆动。不同网络和设备上的效果仍应自行验证。

## 仓库内容

| 目录 | 内容 | 文档 |
| --- | --- | --- |
| firmware/ | ESP-IDF 固件、Grok Bot 表情资源、构建配置与硬件驱动；本项目的主要改动集中在这里 | [固件说明](firmware/README.md) |
| tools/grokbot/、third_party/grokbot/ | 表情资源生成工具与所需的第三方动画数据 | [资源说明](third_party/grokbot/README.md) |
| app/ | 上游 Flutter 手机应用源码 | [应用说明](app/README.md) |
| server/ | 上游 Go 服务端源码 | [服务端说明](server/README.MD) |
| remote/ | ESP-NOW 遥控器源码 | [遥控器说明](remote/README.md) |

手机应用、服务端和遥控器源码随仓库提供；本次硬件验证主要覆盖固件的唤醒、对话、表情和待机行为，并不代表这些模块的全部功能都已重新做过端到端测试。

原有固件中的触摸屏、摄像头、RGB 灯、头部舵机、ESP-NOW 等功能代码仍在仓库中；其中部分功能需要对应的应用、服务端或遥控器配合。

## 编译和刷机

需要 [ESP-IDF v6.1](https://docs.espressif.com/projects/esp-idf/en/v6.1/esp32s3/index.html)、Python 3 和一台兼容的 StackChan。先激活 ESP-IDF 环境，然后在仓库根目录执行：

~~~bash
cd firmware
python3 fetch_repos.py
idf.py build
idf.py -p PORT flash
~~~

fetch_repos.py 会拉取固定版本的依赖并应用本仓库的补丁；固件构建会同时生成应用和 assets 分区镜像。普通 flash 会保留 NVS 中的 Wi-Fi 与设备配置；不要为日常升级使用 erase-flash。详细说明和表情资源重建方法见 [固件说明](firmware/README.md)。

若使用自己的 StackChan 服务端或语音配置服务，可在 firmware/ 下创建不提交到 Git 的 sdkconfig.defaults.local，覆盖 CONFIG_STACKCHAN_SERVER_URL、CONFIG_OTA_URL 等配置。配置服务必须提供语音连接参数；关闭自动固件更新不等于设备可以离线完成首次激活。

## 来源与许可

固件、应用、服务端和遥控器基于上游项目；各子目录中的许可证与第三方声明分别适用。Grok Bot 名称、形象及动画数据的来源见 [第三方说明](third_party/grokbot/README.md)。
