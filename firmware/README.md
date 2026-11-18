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


## USB 本地工具

启用以下固件选项并构建、刷入应用，用 USB 连接 StackChan 和 Mac，保留小智原音色播报：

~~~text
CONFIG_STACKCHAN_CODEX_VOICE=y
CONFIG_STACKCHAN_CODEX_VOICE_USB=y
~~~

Mac 需要 Python 3.11+。在仓库根目录运行桥接，串口由该进程独占：

~~~bash
uv run --with pyserial --with openai-codex==0.170.0 --with futu-api python tools/codex/bridge.py \
  --port /dev/cu.usbmodem1101 --project "$PWD"
~~~

公共桥接同时提供 Codex 任务执行、进度和结果回执。`--project` 指定本机已登录 Codex 的
工作目录；会话使用 workspace-write。富途和 ThetaPilot 请求独立执行，无需等待 Codex 预热。

### ThetaPilot 标的分析

`self.thetapilot.execute` 使用 Mac 本机 ThetaPilot API，默认
`http://127.0.0.1:8000`，可用 `STACKCHAN_THETAPILOT_URL` 指定其他本机端口。
无需额外依赖，不读取 ThetaPilot 的密钥，也不改变其服务配置。

对机器人说「用 ThetaPilot 分析英伟达」或「分析台积电的 Gamma 和波动率」。
普通股价请求继续使用 `self.futu.execute`，分析请求使用：

~~~json
{"symbol":"NVDA"}
~~~

目前支持一个美股标的，也接受 `US.NVDA`。适配器并发读取 `/v1/gex-heatmap/{symbol}`、
`iv-stats`、`volatility-smile`、`iv-term-structure`、`vix-regime`、`gex-interval`，
与 `/gex` 八张图使用同一组数据。返回净 Gamma、附近及最大行权价集中位置、主要到期日、
MVC、IV Rank、期限结构等精简证据，**由小智生成三句话语音解读**；不调用网页 Ask Pilot
再生成一份回答，因此措辞不保证与网页聊天一致。

Gamma 按 Call 正、Put 负估算，单位为标的每 1% 波动对应的美元敞口；集中位置与 MVC
不是确定的支撑、压力或方向预测。缺失面板会明确标记，不用零值代替。超大可选面板会
被裁去并标记 `omitted_for_voice`，保留主 GEX 证据。默认不播报行情时间。

Mac 查询限时 12 秒，设备回传限时 22 秒。USB 只转发 JSON 文本，避免设备为八图数据
创建大量嵌套对象而占满内部 RAM。ThetaPilot 服务、OpenD 与所需行情权限须可用。

独占串口的文字触发实机检查（先停止正常桥接，结束后恢复）：

~~~bash
python tools/codex/check_futu.py --analysis --code NVDA --port /dev/cu.usbmodem1101
~~~

2026-11-18 已实机验证 USB 文字请求「Theta分析NVDA」触发该工具，读取英伟达
净 Gamma、IV Rank 和行权价集中位置，生成三句话分析，确认扬声器输出并返回待机。
本次 VIX 面板超时，已在返回数据标记为不可用；其余面板正常。未重测麦克风听写。
播放期间仍有界面任务看门狗告警，未重启；这与此前播报测试中记录的界面调度问题一致。

### 本地富途行情工具

启用上述 USB 模式后，设备同时向小智注册 `self.futu.execute`。该工具直接由 Mac
上的桥接进程通过 Futu Python SDK 访问本地 OpenD，返回行情后由小智用原音色播报。
Mac 需要保持 OpenD 启动、登录，并具备所查询市场的行情权限。

对机器人说「你好小智」，再说「用富途查询腾讯控股的行情，告诉我价格和涨跌幅」。
工具参数如下；`codes` 是逗号分隔的一到三个完整股票代码，支持 HK、US、SH、SZ：

~~~json
{"action":"get_market_snapshot","codes":"HK.00700,US.AAPL"}
~~~

第一版仅支持 `get_market_snapshot`，返回最新成交价、昨收、涨跌幅、行情更新时间与
中文播报摘要。时间按市场解释：港股和 A 股为北京时间，美股为美东时间。快照可能是
最近交易时段或受权限影响的数据。默认仅播报价格和涨跌幅；行情时间保留在结构化结果中，
仅在用户明确询问时回答。工具不包含交易操作。

OpenD 默认连接 `127.0.0.1:11111`，可通过 `FUTU_OPEND_HOST`、`FUTU_OPEND_PORT`
配置。查询在独立子进程运行，12 秒超时后终止；设备在 18 秒内未收到 USB 结果会
返回超时错误。行情查询可在 Codex 预热或执行期间独立完成。

USB 接收任务使用优先级 6，并将 4 KB 栈放在 PSRAM，避免接收环溢出，同时为
UDP 语音接收任务保留内部 RAM。Flash/NVS 操作仍由应用任务执行。

验证时查看桥接日志中的 `FutuVoice: QUERY`、`Futu query completed`、
`FutuVoice: RESULT`、回复字幕和 `Speech audio delivered to speaker`。
只验证电脑端 OpenD 时，在仓库根目录运行：

~~~bash
uv run --with futu-api python tools/codex/futu_query.py \
  '{"action":"get_market_snapshot","codes":"HK.00700"}'
~~~

不连接设备的回归测试：

~~~bash
uv run --with pytest --with pyserial --with openai-codex==0.170.0 \
  python -m pytest tools/codex/test_futu_query.py -q
~~~

停止占用串口的桥接进程后，可用以下诊断发送文字请求，让小智自行选择富途工具，
查询 OpenD 并播报，检查真实扬声器输出确认和返回待机状态。该诊断不验证麦克风听写；
结束后重新启动正常桥接进程：

~~~bash
uv run --with pyserial --with openai-codex==0.170.0 --with futu-api \
  python tools/codex/check_futu.py --port /dev/cu.usbmodem1101 --code HK.00700
~~~

2026-11-18 已通过 USB 短文字请求实机验证：小智调用 `self.futu.execute`，本地
OpenD 返回腾讯控股最新成交价 372、较昨收 -1.0638%，行情时间为北京时间
14:16:31。小智播报了价格、涨跌幅和时间，设备确认扬声器收到音频并回到待机。
该测试覆盖工具选择、OpenD 查询、USB 回传和原音色播报，未重测麦克风听写。
播报期间观察到一次 LVGL 任务导致的 IDLE1 看门狗告警，未发生重启；界面调度仍可继续优化。
