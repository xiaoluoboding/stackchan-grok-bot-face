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


## Codex 语音模式

Grok Bot 保持连接原小智语音服务，保留账号中配置的音色。设备向小智注册 `self.codex.execute` MCP 工具，工具调用通过 USB
发送给 Mac，Mac 用常驻 Codex app-server 在指定项目里执行任务。提交工具会立即返回任务编号，
小智通过 `self.codex.result` 每次最多等待八秒来查询进度，完成后用原音色播报。
执行时，桥接程序读取 SDK 的事件流，将 Codex 面向用户的 `commentary` 和工具开始事件
作为进度送回设备；不会转发内部推理、原始命令参数或工具输出。
`self.codex.result` 在新进度到达时提前返回 `status=progress`，携带 `summary`、
`progress_seq` 和 `read_aloud=true`，语音服务先简短播报，再继续查询；只有
`status=running` 表示静默等待。两者分开，避免把实际进度当作普通轮询而跳过。
进度最多每十二秒发送一条，中间更新合并为最新一条，不积压播报队列；完成结果优先，
会取消尚未发出的进度。任务没有新事件时不会编造状态或百分比。
收到 Codex 的具体步骤说明后，通用的“正在执行命令”等工具状态不再覆盖这类说明。
语音会话结束后，任务继续在 Mac 上执行。结果返回时，固件用短通知重新接通语音服务，
通过 MCP 取回结果并自动播报。
任务结果会保留到下一次任务或设备重启，会话断开后可以重新唤醒并问「Codex 任务结果是什么」。
任务运行期间仍可唤醒机器人查询进度。
USB 进度包为 `STACKCHAN_CODEX_PROGRESS {"id":任务编号,"seq":递增序号,"text":"简短进度"}`。
固件只接收当前运行任务的新序号；完成后的进度不会覆盖结果。会话断开时，最新进度也会
通过短通知重新接通原语音服务，网络和语音合成本身仍会引入延迟。

在 `sdkconfig.defaults.local` 配置以下选项，并在已有 `sdkconfig` 中通过
`idf.py menuconfig` 确认实际值：

~~~text
CONFIG_OTA_URL="https://api.tenclass.net/xiaozhi/ota/"
CONFIG_STACKCHAN_CODEX_VOICE=y
CONFIG_STACKCHAN_CODEX_VOICE_USB=y
~~~

构建并刷入应用后，用数据线连接机器人和 Mac。桥接程序需要 Python 3.11 或更新版本，
使用本仓库的 `tools/codex/session.py` 常驻运行 Codex app-server，复用项目上下文和 MCP
连接。启动时在后台预热一次，日志出现 `Ready` 后 Codex 会话已就绪；富途查询不必等待预热。在本仓库根目录启动：

~~~bash
uv run --with pyserial --with openai-codex==0.170.0 --with futu-api python tools/codex/bridge.py \
  --port /dev/cu.usbmodem1101 --project "$PWD"
~~~

将串口改成当前设备路径，`--project` 指向需要 Codex 操作的项目目录。桥接程序需要
独占串口，请关闭其他串口监视器。Codex 会话使用 `workspace-write`，可以根据语音指令
修改目标项目。网关和转写服务的 OpenAI API Key 在 USB 模式中不参与链路。

对机器人说「你好小智」，等提示音后说「请让 Codex 查看当前项目分支，完成后告诉我分支名」。
日志应依次出现 `CodexVoice: TASK`、`Codex task started`、
`Codex task completed`、`CodexVoice: RESULT` 和
`Speech audio delivered to speaker`。播报由原小智服务生成，该服务可能调整结果措辞。

2026-11-18 已实机验证声音指令「查询当前项目的版本控制分支」经 MCP 交给 Codex，
返回 `feat/stackchan-codex-voice`，机器人用原音色完整播报并回到待机，听感由用户确认。
早期每次启动 CLI 的该次 Codex 执行约 45 秒。返回结果前会刷新 UDP 音频通道，并主动进入播放状态，
兼容小智在 MCP 工具结果后继续同一轮 TTS 的行为。

同日切换常驻会话后，用 Mac 的 Tingting 语音唤醒并下达分支查询，连续两轮实机计时如下。
起点为 `say` 进程结束，播报起点以设备 `Speech audio delivered to speaker` 日志为准，
并非麦克风声学测量；两轮 USB 结果均收到设备存储回执，播报结束后回到待机。

| 分支查询 | 首次接收反馈 | Codex 执行 | 开始播报结果 |
| --- | --- | --- | --- |
| 第一轮（识别为查询所有分支） | 3.23 秒 | 5.65 秒 | 9.43 秒 |
| 第二轮（仅当前分支） | 3.57 秒 | 5.16 秒 | 8.95 秒 |

这些是两次预热后的简单查询记录，不代表复杂任务或网络搜索的完成时间。
语音识别、云端工具选择和原音色合成仍占用约四秒，完整通路尚非即时响应。

同日已实机验证执行中播报：语音请求检查项目分支并运行桥接单元测试，Codex 执行
56.35 秒，期间设备收到七条进度更新，原语音服务播报检查项目、执行命令、处理依赖
缓存限制、开始测试等状态，随后读取最终测试结果并回到待机。用户确认进度和结果均
听到。该轮包含依赖环境查找耗时，进度播报并不缩短任务执行时间。

随后发现 `running + progress` 在新会话中可能被语音服务静默忽略，且泛化工具状态
可能覆盖具体步骤。已改为独立的 `status=progress` 和播报提示，并保留具体步骤说明。
修复后实机运行单元测试，设备先播报“确认项目的测试入口，然后运行单元测试”，
再播报 71 项测试通过的结果并回到待机，用户确认进度和结果均听到；最短间隔仍为十二秒。

Firecrawl 等其他 MCP 服务在同一用户的 Codex 配置里启用，新增工具无需修改固件。
相邻网关仓库的 `AUTO_CONVERSATION.md` 提供 Firecrawl 配置示例。

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

### 网络搜索工具

启用 `CONFIG_STACKCHAN_CODEX_VOICE_USB` 并刷入包含此功能的固件后，设备向小智注册
`self.websearch.execute`。问「联网查一下 Side Copilot 是什么产品」，小智调用工具，
Mac 通过 Firecrawl MCP 搜索并将网页标题、来源链接、搜索摘要交回小智，用原音色回答。
网络搜索直接执行，不等待 Codex 会话预热，不启动额外的 Codex 任务。

工具参数：

~~~json
{"query":"\"Side Copilot\" browser extension features","limit":3}
~~~

`query` 必填，最多 512 UTF-8 字节；`limit` 可省略，默认为 3，支持 1–3 条。
产品名称有歧义时建议使用引号和准确关键词。返回的是搜索摘要，不是已阅读全文的核实结论；
小智按摘要简短回答，按需提及来源，默认不逐字朗读链接。需要深入阅读全文的研究仍可交给 Codex。

密钥按以下顺序读取，不需要将密钥复制到项目中：

1. 桥接进程环境中的 `FIRECRAWL_API_KEY`。
2. 当前用户 Codex 配置的 `mcp_servers.firecrawl.http_headers.Authorization`，或
   `bearer_token_env_var` 指定的环境变量。配置目录默认为 `~/.codex`，支持 `CODEX_HOME`。
3. 未提供密钥时，使用 Firecrawl 官方免密入口，受免费额度和频率限制。

默认地址为 `https://mcp.firecrawl.dev/v2/mcp`；已有 Firecrawl 远程配置时复用其 URL，
也可通过 `STACKCHAN_FIRECRAWL_MCP_URL` 覆盖。此桥接不启动本地 stdio Firecrawl 服务，
也不读取 Codex 的 OAuth 凭据。密钥保留在 Mac，不传入设备或写进日志。

使用前文的 USB 桥接启动命令即可，不需要新增 Python 依赖。更新代码后需重启桥接进程。
Firecrawl 请求超时为 20 秒，设备端等待上限为 35 秒；失败会返回明确错误，
不会编造结果或静默转入耗时更长的 Codex 任务。USB 结果裁剪至 4 KB 内并保留来源 URL。

电脑端真实搜索测试（会使用 Firecrawl 额度）：

~~~bash
python3 tools/codex/websearch.py '{"query":"\"Side Copilot\" browser extension features","limit":3}'
~~~

无需网络或设备的回归测试，在仓库根目录执行：

~~~bash
uv run --with pytest --with pyserial --with openai-codex==0.170.0 \
  python -m pytest tools/codex -q
~~~

日志链路为 `WebSearchVoice: QUERY` → `Web search completed` →
`WebSearchVoice: RESULT` → 小智语音回复。`elapsed_ms` 只表示 Firecrawl 请求耗时，
不包含听写、USB 回传与语音合成。

2026-11-18 已刷入应用并通过 Mac 扬声器播放唤醒词和查询语句，完成三轮
「设备麦克风 → 小智听写 → `self.websearch.execute` → Firecrawl → 原音色播报」测试。
时间起点为 Mac `say` 进程结束，播报起点取设备的 `Speech audio delivered to speaker`
日志，并非麦克风声学测量；三轮最终均回到待机。

| 问法 | Firecrawl 请求 | 开始播报结果 | 内容效果 |
| --- | --- | --- | --- |
| 较长中文句，混读 Side Copilot | 2.93 秒 | 6.53 秒 | 听写漏掉产品名，回答了浏览器扩展的一般用途 |
| 英文问句，要求中文回答 | 3.27 秒 | 9.05 秒 | 正确命中官网，中文介绍侧边栏 AI 的研究、整理和网页自动化功能 |
| 简短中文句，混读 Side Copilot | 5.27 秒 | 11.30 秒 | 名称误识别为 `sci code pilot`，回答未找到准确产品并询问是否指 SciSpace |

正确命中的英文问句为 `Search the web for Side Copilot browser extension. What does
Side Copilot do? Please answer in Chinese.`，该轮播报结束并回到待机耗时 25.77 秒。
这几次测试说明搜索、USB 回传和播报已连通，但中文混读英文产品名仍不可靠；
搜索速度不能替代对听写准确性的检查。测试中另观察到两次 IDLE 任务看门狗告警，
涉及 `audio_input` / `taskLVGL`，未引起重启，仍有音频和界面调度方面的优化空间。

### flomo 笔记工具

USB 语音模式下，`self.flomo.execute` 直接由 Mac 执行，不启动 Codex 任务。
新建优先使用 flomo 官方传统 API URL；搜索、读取和更新使用官方远程 MCP
`https://flomoapp.com/mcp`。传统 API URL 无法读取旧笔记，需要另配 MCP Token，
不能将写入成功视为已获得读取权限。

官方文档：

- [传统 API](https://help.flomoapp.com/advance/api.html)
- [MCP Token 创建与管理](https://help.flomoapp.com/advance/mcp/token.html)
- [官方 Token 连接格式及权限](https://help.flomoapp.com/advance/mcp/connect-openclaw.html)

在仓库根目录运行本地隐藏输入脚本：

~~~bash
python3 tools/codex/configure_flomo.py
~~~

先粘贴完整 API URL；没有 MCP Token 时，在第二个提示处直接回车跳过即可。
配置脚本不会创建笔记，提供 Token 时只握手并获取工具定义。凭据保存在
`~/.config/stackchan/flomo.json`，文件权限为 600，不进入仓库、固件或工具参数。
可用 `STACKCHAN_FLOMO_CONFIG` 覆盖读取位置，或通过桥接环境设置 `FLOMO_API_URL`
及 `FLOMO_MCP_TOKEN`，环境变量优先。不要将凭据发送到聊天、放进命令参数或提交到 Git。

首次加入设备工具需编译并刷入应用，再重启 USB 桥接。以后修改凭据不需要刷机，
桥接每次操作会重新读取配置。只有 API URL 时，对机器人说：
「帮我在浮墨笔记记一下：今天完成了机器人的网络搜索功能，标签 StackChan。」

工具参数例子：

~~~json
{"action":"create","text":"今天完成了机器人的网络搜索功能。\n#StackChan"}
{"action":"search","text":"StackChan","limit":3}
{"action":"read","memo_id":"从搜索结果取得的真实笔记 ID"}
{"action":"update","memo_id":"明确指定的真实笔记 ID","text":"替换后的完整正文。\n#StackChan"}
~~~

`action` 必填；`text` 是正文或搜索词，最多 2048 UTF-8 字节；`memo_id` 最多 128 字节。
`limit` 默认 3，范围 1–3。长笔记返回 `truncated` 和 `next_offset`，可在 `read` 的
下一次调用中传 `offset` 继续读取；单条 USB 响应控制在 4 KB 内。官方 MCP 工具参数
在运行时通过 `tools/list` 核对，遇到不兼容参数会报错，不猜测参数后执行修改。

写入仅在明确要求保存时执行，更新需要明确指定笔记；不提供删除工具。
正文中的 `#标签` 直接保留。保存只在 API 业务响应成功后确认；写入超时或断线时
会提示「结果未确认」，不会自动重试，避免丢失响应后重复创建笔记。
网络操作上限 25 秒，设备等待上限 35 秒。桥接不记录完整 flomo 请求正文或返回笔记，
但原小智服务的听写和播报日志可能包含用户口述的笔记内容。

电脑端调用（`create` 会真实保存一条笔记）：

~~~bash
python3 tools/codex/flomo_notes.py '{"action":"create","text":"StackChan 接入测试。\n#StackChan/测试"}'
~~~

日志链路为 `FlomoVoice: QUERY` → `Flomo operation completed` → `FlomoVoice: RESULT`。
当前仅配置传统 API URL，读取能力的真实账号验证需待 MCP Token 配置后完成。

2026-11-18 已配置传统 API URL，完成应用编译和刷机，并重启 USB 桥接：

- 电脑端写入一条明确标注的 `StackChan 接入测试` 笔记，API 业务响应成功，耗时约 0.35 秒。
- Mac 扬声器唤醒后口述保存测试笔记，设备选择 `self.flomo.execute` 的 `create`，
  API 响应成功，耗时 0.30 秒；机器人确认「好啦，已经帮你保存到浮墨笔记啦」并回到待机。
- 以设备听写文本日志为起点，到扬声器音频接收确认为止约 2.77 秒，并非声学测量。
  本轮听写在整段合成语音结束前已收尾，漏掉末尾标签要求；因此不将播放结束后的
  1.48 秒作为完整问句的有效延迟，也不将本轮视为标签听写验证。
- 已创建两条标注为测试的笔记；未读取、修改或删除既有笔记。正文及 `#标签` 的
  API 原样传递、写入不自动重试、USB 消息边界和缺少读取凭据时的行为均有离线回归测试。
- 无 MCP Token 的真实搜索调用返回明确的权限配置说明，没有伪造读取结果。

### WebSocket 网关模式

关闭 `CONFIG_STACKCHAN_CODEX_VOICE_USB` 后，也可以使用相邻 `stackchan-mcp`
的自动语音网关。由配置服务向固件下发 WebSocket URL 和 token，并启用网关的
`STACKCHAN_AUTO_BACKEND=codex`。此模式使用网关的 STT 和 TTS，播报音色由网关选择。
自动录音在检测到人声后静音约两秒收尾，单轮最长十五秒，播报结束后回到待机。
