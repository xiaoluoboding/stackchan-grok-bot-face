#!/usr/bin/env python3
"""Play the 10 StackChan eye states in order and measure firmware cadence over USB."""
import argparse
import json
from pathlib import Path
import re
import statistics
import time

import serial

STATES = json.loads(Path(__file__).with_name("states-preview.json").read_text())
FRAME = re.compile(r"\((\d+)\).*fps=(\d+).*decode=(\d+)us agent=(-?\d+)")
FAILURE = re.compile(r"assert failed|stack overflow|Guru Meditation|panic'ed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/cu.usbmodem1101")
    parser.add_argument("--label", default="grok-ball")
    parser.add_argument("--speech", action="store_true", help="Also measure the largest eyes during speech.")
    parser.add_argument("--speech-only", action="store_true", help="Skip the idle pass and measure speech.")
    args = parser.parse_args()
    output = Path("build/grok-ball")
    output.mkdir(parents=True, exist_ok=True)
    results = {}
    timestamp = None
    speaking = False
    audio = False
    watchdogs = 0

    with serial.Serial(args.port, 115200, timeout=.2, write_timeout=3, exclusive=True) as device, (
        output / (args.label + ".log")
    ).open("w") as log:
        def command(name, data):
            device.write(f"\n{name} {data}\n".encode())
            device.flush()

        def lines(seconds):
            nonlocal timestamp, speaking, audio, watchdogs
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                line = device.readline().decode(errors="replace").strip()
                if not line:
                    continue
                print(line, file=log, flush=True)
                if FAILURE.search(line):
                    raise RuntimeError(line)
                if "task_wdt:" in line and "got triggered" in line:
                    watchdogs += 1
                if "State:" in line and "-> speaking" in line:
                    speaking = True
                if "State: speaking ->" in line:
                    speaking = False
                if "Speech audio delivered to speaker" in line:
                    audio = True
                previous = timestamp
                frame = FRAME.search(line)
                if frame:
                    timestamp = int(frame[1])
                yield line, previous, frame

        def summarize(samples):
            return {
                "samples": len(samples),
                "median_fps": round(statistics.median(fps for fps, _ in samples), 2),
                "min_fps": round(min(fps for fps, _ in samples), 2),
                "max_interval_avg_render_us": max(render for _, render in samples),
            }

        def measure(state, seconds, require_speech=False):
            command("STACKCHAN_AGENT", json.dumps({"state": state, "duration_ms": round(seconds * 1000)}))
            acknowledged = False
            consecutive = False
            samples = []
            for line, previous, frame in lines(seconds + .1):
                if f"Agent state command: {state} FAILED" in line:
                    raise RuntimeError(line)
                acknowledged |= f"Agent state command: {state} OK" in line
                if not frame:
                    continue
                match = acknowledged and int(frame[4]) == state and (speaking or not require_speech)
                if consecutive and match and previous is not None and timestamp > previous:
                    samples.append((1000 * int(frame[2]) / (timestamp - previous), int(frame[3])))
                consecutive = match
                if require_speech and not speaking:
                    break
            if len(samples) < 2:
                raise RuntimeError(f"Not enough stable frames for state {state}: {samples}")
            return summarize(samples)

        command("STACKCHAN_AVATAR", "status")
        for line, _, _ in lines(4):
            if "Avatar preference: white-eyes" in line:
                break
        else:
            raise RuntimeError("Expected the white-eyes skin to be active")

        for entry in ([] if args.speech_only else STATES):
            state = entry["id"]
            result = measure(state, max(4, entry["period"] / 1000 + 1))
            results[str(state)] = result
            print(state, entry["name"], result, flush=True)

        if args.speech or args.speech_only:
            for line, _, _ in lines(1):
                pass
            text = (
                "傍晚，山里的小邮差在桥边捡到一把蓝伞。雨已经停了，"
                "水洼里映着一小块橙色的天。他把伞靠在桥栏上，留下一张纸条，"
                "上面写着：捡到你的伞，我在村口的木屋等你。"
                "没过多久，一个抱着书的小女孩跑了过来。她说这把伞是爷爷给她的，"
                "每次撑开，就像把一小片晴空带在身边。邮差笑了笑，把伞交给她。"
                "小女孩没有急着回家。她把书拿出来，坐在木屋门前读了一会儿。"
                "邮差给她倒了一杯温水，两个人听着屋檐上最后几滴雨落下来。"
                "远处亮起第一盏灯时，村子安静下来。第二天，木屋门口多了一束野花，"
                "里面夹着一张小纸条：谢谢你帮我把晴空找了回来。"
            )
            prompt = json.dumps("请直接讲一个一分钟的小故事。", ensure_ascii=False)
            command("STACKCHAN_CHAT", prompt)
            retries = 0
            for line, _, _ in lines(55):
                if speaking and audio:
                    break
                if "USB chat channel failed" in line or "Detect is only for wake words" in line:
                    raise RuntimeError(line)
                # The USB bridge is paused during tests. If the cloud routes
                # our prompt to Hermes, complete that test job with fixed text
                # so that the device can use its actual speech service.
                if "HermesVoice: TASK " in line:
                    job = json.loads(line.split("HermesVoice: TASK ", 1)[1])
                    command("STACKCHAN_CODEX_RESULT", json.dumps(
                        {"id": job["id"], "text": text}, ensure_ascii=False))
                if "USB chat unavailable or busy" in line:
                    if retries == 5:
                        raise RuntimeError(line)
                    retries += 1
                    time.sleep(2)
                    command("STACKCHAN_CHAT", prompt)
            else:
                raise RuntimeError("No audio reached the speaker")

            # Check the real speaking state, then explicitly exercise the most
            # expensive expression while the same response continues.
            reply_samples = []
            consecutive = False
            for line, previous, frame in lines(6):
                if not frame:
                    continue
                match = speaking and int(frame[4]) == 37
                if match and consecutive and previous is not None and timestamp > previous:
                    reply_samples.append((1000 * int(frame[2]) / (timestamp - previous), int(frame[3])))
                consecutive = match
            if len(reply_samples) < 2:
                raise RuntimeError("No stable automatic reply animation during speech")
            results["speech_reply"] = summarize(reply_samples)
            results["speech_largest"] = measure(33, 7, require_speech=True)
            if speaking:
                for line, _, _ in lines(150):
                    if "State: speaking -> idle" in line:
                        break
                else:
                    raise RuntimeError("Speech did not return to idle")
            print("speech", results["speech_reply"], results["speech_largest"], flush=True)

    results["watchdog_events"] = watchdogs
    (output / (args.label + ".json")).write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
