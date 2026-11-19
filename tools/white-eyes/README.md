# StackChan White Eyes · 独立实验皮肤

头像层只绘制白色眼睛，**背景透明**。设备已有的黑色屏幕背景就是脸部；预览页的黑色方块仅用于模拟屏幕，不属于头像资源。

原版 `third_party/grokbot/`、`tools/grokbot/` 的源码、原始 recipes 和 `grokbot.atlas` 保持原样。固件增加独立白眼资源与可切换的透明调色板。本目录提供独立浏览器样机、透明素材导出与固件图集生成。原版资源保持独立。

## 预览

在仓库根目录执行：

```sh
python3 -m http.server 8765 --bind 127.0.0.1
```

打开 http://127.0.0.1:8765/tools/white-eyes/index.html 。支持 53 个状态、整体缩放、眼睛加宽、眨眼、指针跟随、全屏。纯脸模式：`index.html?face`；确定性单帧：`index.html?frozenAt=1&state=idle`。

## 实现与评估

- 直接复用原播放器导出的 runtime、状态机、球面投影和眼形路径。不是把截图反色，也不是重新画两条固定竖线。
- 原版用头部轮廓、眼睛挖洞、图标 morph 共同表达状态。去掉头部后，眼神更突出；球面压缩、眼睑与眨眼仍有价值。
- 每个实例深拷贝 recipes；把会隐藏眼睛的 16 个 morph 状态映射成现有的眼神/动作组合，例如 thinking → curious + inspecting、dictating → streaming + calm。保留全部状态接口，但这些映射没有原图标同样明确的语义。
- 默认整体放大 1.8 倍、眼宽加至 1.15 倍。保留转动和倾斜，限制大动作在屏幕上的位移，留出 8% 安全边距；极端姿态会自动缩小以免裁切。
- 无嘴巴、无圆形头部、无装饰图标、无背景填充。全屏预览把透明眼睛叠在模拟黑屏上。
- 浏览器计算几何不能直接等同 ESP32 的实时性能。固件复用 I4 图集容器和播放器，通过 ARGB8888 调色板实现透明：每个索引映射为 RGB=白色、alpha=index×17。背景像素 alpha=0，眼睛内部 alpha=255，边缘保留 16 级覆盖率。

## 透明素材导出（独立目录）

沿用项目已有 Puppeteer 依赖：

```sh
npm ci --prefix tools/grokbot
STATES=idle,listening,thinking,streaming SIZE=200 node tools/white-eyes/export-frames.mjs
```

默认 15 FPS，可用 FPS=10/15/20/30 指定。输出为 `build/white-eyes-frames/` 下的透明 RGBA PNG 与 summary.json；省略 STATES 导出全部 53 个状态。该工具只导出帧，不改固件资产。`SIZE` 必须为 32–400 的偶数。Mac 默认使用已安装的 Google Chrome，其他系统可设置 `CHROME_BIN`。

## 验证

启动上面的本地服务器后：

```sh
node tools/white-eyes/verify.mjs
```

检查全部状态逐帧几何、空屏、裁切、源数据隔离、状态按钮、缩放控件和移动端宽度。截图与结果保存在 `build/white-eyes/`。该验证不包含真机音频负载或 LCD 显示效果。

来源与权利说明沿用 [Grok Bot 第三方说明](../../third_party/grokbot/README.md)，分析参考 https://grokbot.yin.wiki/index.html 。

## 固件集成

- `firmware/main/white_eyes.atlas`：200×200、15 FPS、53 状态，应用内嵌，重复序列共享存储；原版 `grokbot.atlas` 保留在 assets 分区。
- 设备启动默认选择 White eyes；**长按脸部**即时切换到原版，再长按切回，短按继续原有对话操作。选择持久化到 NVS `avatar/white_eyes`。
- 设置路径：**SETUP → Device → Avatar skin**。选择后退出设置，重启应用加载所选头像。
- 保持待机/聆听/说话/情绪状态分配；两套头像语音时均限制为 12 FPS。
- 重建：导出全部状态后运行 `python3 tools/white-eyes/encode_atlas.py build/white-eyes-frames firmware/main/white_eyes.atlas`。再用 ESP-IDF 构建并 `app-flash`；无需改分区或重刷原 assets。
- 串口诊断命令：`STACKCHAN_AVATAR white-eyes`、`STACKCHAN_AVATAR original`、`STACKCHAN_AVATAR status`、`STACKCHAN_AVATAR snapshot`。最后一项避开聆听和播报状态，输出设备 LVGL 的 JPEG 截图分块。
- 停止 USB bridge 后可运行 `python tools/white-eyes/check_device.py --skin white-eyes --speech`；结束后恢复 bridge。`--speech` 通过短文字验证云端语音和扬声器数据交付，不验证麦克风听写。

透明头像加载时会销毁启动 BootLogo，避免原先被不透明头部遮挡的启动文字透出。NVS 读写均在内部 RAM 栈的主任务进行；动画解压保留在 LVGL 更新任务，联网暂停时只解出切换后的首帧。
