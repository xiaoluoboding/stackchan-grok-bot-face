# StackChan Grok Ball 表情

透明白眼皮肤支持 Grok Ball 的 10 种工作表情（30–37、40、41）。待输入、播报、联网加载、
成功、错误分别沿用设备的现有状态入口。白眼的工作状态优先于云端情绪，唤醒时的粉红害羞反馈优先播放。原版头像照常处理云端情绪。
输出回复使用原「复述回忆」的眼形（37）。直接联网搜索及 Hermes 检索或读取网页时使用「检索资料」（40），搜索结束后自动恢复。
可通过 USB 单独触发表情，例如发送一行：

```text
STACKCHAN_AGENT {"state":30,"duration_ms":6000}
```

到期后恢复设备当前状态。串口必须由一个进程独占。

预览可从仓库根目录启动本地 HTTP 服务，然后打开 `/tools/grok-ball/index.html`。
重新提取并打包表情：

```sh
node tools/grok-ball/extract-preview.mjs
python3 tools/grok-ball/pack-contours.py
```

固件以 20 帧关键姿态插值，使用 1/16 像素精度压缩轮廓和 4×4 覆盖率抗锯齿。
多边形按扫描行增量绘制，直接复用已分配的 I4 白眼动画缓冲区和现有的局部刷新。
聆听和播报沿用 12 FPS 的目标。「任务完成」仅在语音期间按 75% 的宽高绘制，降低屏幕刷新面积。

暂停 USB bridge 后，可使用 `python tools/grok-ball/check-device.py --speech`
依序测量 10 种表情并给扬声器播报施加动画负载。测试结果写入
`build/grok-ball/`，需要已安装 pyserial。
