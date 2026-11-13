# StackChan 固件

本目录以 M5Stack StackChan 和 xiaozhi-esp32 为基础，目标硬件是 ESP32-S3、16 MB Flash、8 MB PSRAM 的 M5Stack StackChan。当前版本使用 Grok Bot 表情资源，并以 **“你好小智”** 为语音唤醒词。

## 相对上游的主要改动

### 表情与显示

- main/assets/assets_bin/grokbot.atlas 包含 53 组、200×200 像素的预制动画。资源采用 4 位灰度、差分帧和 zlib 压缩；运行时直接以 LVGL 索引图像显示。资源不可用时回退到原有头像。
- AI 情绪以及待机、聆听、说话、连接等状态会选择对应动画。待机时可以切换屏幕表情，但**不会因此驱动实体头部随机摆动**。
- 显示使用双 4 行 DMA 缓冲区，减少内部 RAM 占用。聆听与说话时把 Grok Bot 动画目标帧率限制为 12 FPS，使音频任务获得更多处理时间。
- 保留屏幕触摸交互和头部触摸反应。

### 语音、网络与更新

- 使用 ESP-SR 的 wn9l_nihaoxiaozhi_tts3 WakeNet9l 模型识别“你好小智”。唤醒在设备端完成；对话依赖网络、语音服务和设备激活。
- 调整音频任务优先级、队列和播放预缓冲，并将 MQTT/TLS 相关缓冲放入 PSRAM，以降低播放卡顿与内部 RAM 压力。
- 同名 Wi-Fi 热点连接时优先选择较强的 BSSID；StackChan 运行语音时不使用 Wi-Fi 省电模式。
- 启动时获取语音连接参数并完成必要的激活流程；不在启动时比较固件版本、自动安装更新或循环重试失败的配置请求。手动更新功能保留。
- 待机随机转头默认关闭。对已经保存旧设置的设备，首次运行本固件会将该项设为关闭；之后可以在设备设置中重新开启。

assets 分区扩大至 0x5F0000，仍保留两个 OTA 应用分区。上述更新设置只关闭**自动**安装；启动时的 HTTPS 配置请求仍是语音服务正常工作的必要步骤。

## 构建

需要 ESP-IDF v6.1。先激活工具链，然后在本目录运行：

~~~bash
python3 fetch_repos.py
idf.py build
idf.py -p PORT flash
~~~

fetch_repos.py 根据 repos.json 拉取依赖，并向 xiaozhi-esp32 应用 patches/xiaozhi-esp32.patch。idf.py build 同时生成应用固件和表情、字体、唤醒模型等资源镜像。普通刷机保留 NVS 中的 Wi-Fi 和设备配置。

如需使用自建服务，创建 Git 忽略的 sdkconfig.defaults.local，设置 CONFIG_STACKCHAN_SERVER_URL 和需要覆盖的 CONFIG_OTA_URL。首次生成配置或切换编译选项后，执行 idf.py reconfigure build。不要把服务密钥或设备凭据写入提交的默认配置。

主机侧运动坐标测试：

~~~bash
cmake -S tests -B build-host-tests
cmake --build build-host-tests
ctest --test-dir build-host-tests --output-on-failure
~~~

若需要单文件出厂镜像，可在构建成功后执行 idf.py merge-bin -o build/stackchan-grokbot-full.bin。仓库不提供已验证可直接刷入的合并镜像；请以本地生成的文件为准。

## 重建 Grok Bot 表情资源

表情生成器位于 ../tools/grokbot/，动画数据来源见 [第三方说明](../third_party/grokbot/README.md)。在仓库根目录运行：

~~~bash
cd tools/grokbot
npm install
cd ../..
CHROME_BIN=/path/to/chrome SIZE=200 node tools/grokbot/bake.mjs
~~~

生成结果写入 firmware/main/assets/assets_bin/grokbot.atlas。重新生成资源后需重新构建并刷入 assets 分区。

## 验证范围

已在一台 M5Stack StackChan 上确认：Wi-Fi 获取地址、MQTT/TLS 连接、语音服务激活、WakeNet 唤醒、正常对话，以及待机头部不再自动摆动。其他网络环境、服务端部署和全部上游应用功能尚需分别验证。
