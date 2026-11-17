# Codex 传声筒

给支持 USB 文字对话入口的 Grok Bot 固件使用。用数据线连接 Mac 和 StackChan，让 StackChan 联网并在小智界面待机。传声筒会给原文加上「请原样念：」，经设备当前的小智对话服务发送。小智用角色配置的音色回答，回复措辞由小智决定。

Mac 需要 Python 3.11 或更新版本。在本目录运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install mcp pyserial
codex mcp add stackchan -- "$PWD/.venv/bin/python" "$PWD/server.py"
```

打开 Codex 聊天，发送 `传声筒：你好，我是小智`。工具等设备从说话回到待机，并返回小智的回复字幕。连接多个 USB 串口时，用 `STACKCHAN_USB_PORT` 指定 StackChan 的设备路径。

发送 `听一下 StackChan` 可录一段语音并在 Codex 中返回中文转写。默认录制 5 秒，可指定 1 到 9 秒。Mac 要安装 `faster-whisper`，并在首次使用前下载多语种 base 模型：

```bash
.venv/bin/python -m pip install faster-whisper
.venv/bin/python -c 'from faster_whisper import WhisperModel; WhisperModel("base", device="cpu", compute_type="int8")'
```
