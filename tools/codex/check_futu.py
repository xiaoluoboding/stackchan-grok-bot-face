"""Exercise XiaoZhi tool selection, local OpenD and the physical speaker over USB.

Stop the normal bridge before running this diagnostic; it owns the same port.
The request is injected as text, so this checks the tool/speech path, not ASR.
"""

import argparse
import asyncio
import json
import logging
import time

import serial
from bridge import FUTU_MARKER, THETA_MARKER, handle_task, write_packet

logger = logging.getLogger("futu-check")


async def check(port: str, code: str, wait_ready: bool = False, analysis: bool = False) -> None:
    tool = "thetapilot" if analysis else "futu"
    event_marker = THETA_MARKER if analysis else FUTU_MARKER
    voice_tag = "ThetaVoice:" if analysis else "FutuVoice:"
    with serial.Serial(port, 115200, timeout=0.25, write_timeout=3, exclusive=True) as device:
        device.reset_input_buffer()
        lock = asyncio.Lock()
        if wait_ready:
            boot_deadline = time.monotonic() + 15
            while time.monotonic() < boot_deadline:
                line = (await asyncio.to_thread(device.readline, 16384)).decode(errors="replace")
                if "StateMachine:" in line or "self.futu.execute" in line:
                    print(line.rstrip(), flush=True)
                if "StateMachine:" in line and " -> idle" in line:
                    break
            else:
                logger.info("No fresh idle transition; trying the request on the already running device")
        # XiaoZhi's detect-text endpoint accepts only short wake-word-like text.
        # Real spoken requests travel through ASR and do not use this endpoint.
        prompt = f"Theta分析{code}" if analysis else f"富途查{code}"
        packet = ("STACKCHAN_CHAT " + json.dumps(prompt, ensure_ascii=False) + "\n").encode()
        await write_packet(device, packet, lock)
        retries = 0
        deadline = time.monotonic() + 70
        queried = completed = received = audio = False
        pending = bytearray()
        replies = []
        while time.monotonic() < deadline:
            raw = await asyncio.to_thread(device.readline, 16384)
            if not raw:
                continue
            pending.extend(raw)
            if not pending.endswith(b"\n") and len(pending) < 16384:
                continue
            line = pending.decode(errors="replace").rstrip()
            pending.clear()
            logger.debug("device %s", line)
            if "ERROR: Detect is only for wake words" in line:
                raise RuntimeError("XiaoZhi rejected the diagnostic text as too long")
            if "USB chat channel failed" in line or "USB chat unavailable or busy" in line:
                if retries >= 3:
                    raise RuntimeError("Device is not ready for the diagnostic request")
                retries += 1
                await asyncio.sleep(3)
                await write_packet(device, packet, lock)
            # Keep diagnostics useful for connection/firmware failures too.
            if any(marker in line for marker in (
                voice_tag, "Application:", "Speech audio delivered", "StateMachine:",
                "McpServer:", "MqttProtocol:",
                "USB chat", "Error", "error",
            )):
                print(line, flush=True)
            if event_marker in line:
                task = json.loads(line.split(event_marker, 1)[1])
                task["tool"] = tool
                queried = True
                await handle_task(task, device, lock, None)
            if voice_tag + " RESULT" in line:
                received = True
                completed = "error=0" in line
            if received and "Application: << " in line:
                replies.append(line.split("Application: << ", 1)[1])
            if received and "Speech audio delivered to speaker" in line:
                audio = True
            if received and "State: speaking -> idle" in line:
                if not completed:
                    raise RuntimeError("Futu query failed: " + "".join(replies))
                if not (queried and audio and replies):
                    raise RuntimeError("Tool completed but no complete speech confirmation was received")
                if any(marker in "".join(replies) for marker in ("行情时间", "北京时间", "美东时间")):
                    raise RuntimeError("Default quote response unexpectedly included the quote timestamp")
                print(json.dumps({
                    "tool": f"self.{tool}.execute", "query_completed": completed,
                    "speaker_audio_confirmed": audio, "reply": "".join(replies),
                    "trigger": "usb_text",
                }, ensure_ascii=False), flush=True)
                return
        raise TimeoutError(f"Futu diagnostic timed out: queried={queried} completed={completed} audio={audio}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/cu.usbmodem1101")
    parser.add_argument("--code", default="HK.00700")
    parser.add_argument("--analysis", action="store_true", help="Test ThetaPilot GEX analysis instead of quotes")
    parser.add_argument("--wait-ready", action="store_true", help="Wait for boot to reach idle first")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    if args.analysis:
        from thetapilot import validate_request
        code = validate_request({"symbol": args.code})
    else:
        from futu_query import validate_request
        code = ",".join(validate_request({"action": "get_market_snapshot", "codes": args.code}))
    asyncio.run(check(args.port, code, args.wait_ready, args.analysis))
