"""Codex MCP server for speaking through a USB-connected StackChan."""

import asyncio
import base64
import glob
import json
import os
import tempfile
import threading
import time
import wave
from functools import lru_cache
from pathlib import Path

import serial
from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("StackChan")
device_lock = threading.Lock()


def usb_port():
    configured_port = os.environ.get("STACKCHAN_USB_PORT")
    ports = [configured_port] if configured_port else glob.glob("/dev/cu.usbmodem*")
    if len(ports) != 1:
        raise RuntimeError("Connect one StackChan over USB or set STACKCHAN_USB_PORT.")
    return ports[0]


@mcp.tool()
def say(text: str) -> str:
    """Ask XiaoZhi to repeat the supplied text using its configured voice."""
    if not text.strip() or len(text) > 300 or "\0" in text:
        raise ValueError("Provide 1 to 300 characters without NUL.")

    prompt = json.dumps("请原样念：" + text, ensure_ascii=False)
    with device_lock, serial.Serial(usb_port(), 115200, timeout=0.25,
                                    write_timeout=2, exclusive=True) as device:
        device.reset_input_buffer()
        device.write(f"STACKCHAN_CHAT {prompt}\n".encode())
        deadline = time.monotonic() + 48
        pending = bytearray()
        submitted = False
        speaking = False
        audio_output = False
        reply = []
        while time.monotonic() < deadline:
            chunk = device.readline(512)
            pending.extend(chunk)
            if not chunk.endswith(b"\n") and len(pending) < 4096:
                continue
            line = bytes(pending).decode(errors="replace").rstrip("\r\n")
            pending.clear()
            if any(error in line for error in (
                "USB chat unavailable or busy", "USB chat channel failed", "USB chat invalid text",
            )):
                raise RuntimeError(line.split(": ", 1)[-1])
            if "Application: USB chat text submitted" in line:
                submitted = True
            if not submitted:
                continue
            if "StateMachine: State: idle -> speaking" in line:
                speaking = True
            if "USBChat: Speech audio delivered to speaker" in line:
                audio_output = True
            marker = "Application: << "
            if speaking and marker in line:
                reply.append(line.split(marker, 1)[1])
            if speaking and "StateMachine: State: speaking -> idle" in line:
                if not audio_output:
                    raise RuntimeError("小智返回了回复，但设备未确认扬声器收到语音。")
                if not reply:
                    return "小智说完了，设备没有回传字幕。"
                return "小智回复：" + "".join(reply)
        raise RuntimeError("Timed out waiting for XiaoZhi to finish speaking.")


@lru_cache(maxsize=1)
def transcription_model():
    from faster_whisper import WhisperModel

    return WhisperModel("base", device="cpu", compute_type="int8", local_files_only=True)


def record(seconds, ready):
    audio = bytearray()
    with device_lock, serial.Serial(usb_port(), 115200, timeout=0.25,
                                      write_timeout=2, exclusive=True) as device:
        device.reset_input_buffer()
        device.write(f"STACKCHAN_RECORD {seconds}\n".encode())
        deadline = time.monotonic() + 35
        pending = bytearray()
        while time.monotonic() < deadline:
            chunk = device.readline(512)
            pending.extend(chunk)
            if not chunk.endswith(b"\n") and len(pending) < 2048:
                continue
            line = bytes(pending).strip()
            pending.clear()
            if line == b"STACKCHAN_RECORD_START":
                ready.set()
            elif line.startswith(b"STACKCHAN_PCM "):
                audio.extend(base64.b64decode(line.split(b" ", 1)[1], validate=True))
            elif line.startswith(b"STACKCHAN_RECORD_ERROR "):
                reason = line.decode().removeprefix("STACKCHAN_RECORD_ERROR ")
                raise RuntimeError(f"StackChan could not record: {reason}.")
            elif line == b"STACKCHAN_RECORD_END":
                if len(audio) != seconds * 32000:
                    raise RuntimeError("StackChan sent an incomplete recording.")
                return bytes(audio)
        raise RuntimeError("Timed out waiting for the StackChan recording.")


def transcribe(audio):
    with tempfile.TemporaryDirectory() as directory:
        recording = Path(directory) / "microphone.wav"
        with wave.open(str(recording), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(audio)
        segments, _ = transcription_model().transcribe(
            str(recording), language="zh", vad_filter=True,
        )
        return "".join(segment.text for segment in segments).strip()


@mcp.tool()
async def listen(ctx: Context, seconds: int = 5) -> str:
    """Record the StackChan microphone on request and transcribe Mandarin speech."""
    if not 1 <= seconds <= 9:
        raise ValueError("Choose a recording duration between 1 and 9 seconds.")
    ready = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(record, seconds, ready))
    while not ready.is_set() and not task.done():
        await asyncio.sleep(0.05)
    if ready.is_set():
        await ctx.info("StackChan 正在录音")
    audio = await task
    transcript = await asyncio.to_thread(transcribe, audio)
    return transcript or "这段录音里没有识别到语音。"


if __name__ == "__main__":
    mcp.run()
