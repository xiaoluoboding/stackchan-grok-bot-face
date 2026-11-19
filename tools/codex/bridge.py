"""Forward Grok Bot voice tasks to local agents and speak results on the device."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import threading
from pathlib import Path

import serial
from flomo_notes import run_notes
from futu_query import run_query
from progress import ProgressReporter
from hermes_session import HermesSession
from thetapilot import run_analysis
from websearch import run_search

logger = logging.getLogger("codex-voice")
TASK_MARKER = "CodexVoice: TASK "
HERMES_MARKER = "HermesVoice: TASK "
FUTU_MARKER = "FutuVoice: QUERY "
WEBSEARCH_MARKER = "WebSearchVoice: QUERY "
FLOMO_MARKER = "FlomoVoice: QUERY "
THETA_MARKER = "ThetaVoice: QUERY "


def parse_task_event(line: str) -> dict | None:
    for marker, tool in ((THETA_MARKER, "thetapilot"), (FLOMO_MARKER, "flomo"), (WEBSEARCH_MARKER, "websearch"), (FUTU_MARKER, "futu"),
                         (HERMES_MARKER, "hermes"), (TASK_MARKER, "codex")):
        if marker not in line:
            continue
        try:
            task = json.loads(line.split(marker, 1)[1])
        except (ValueError, TypeError):
            return None
        if not isinstance(task, dict) or type(task.get("id")) is not int:
            return None
        if tool in {"codex", "hermes"}:
            if not isinstance(task.get("text"), str) or not task["text"].strip():
                return None
        elif not isinstance(task.get("arguments"), dict):
            return None
        return {**task, "tool": tool}
    return None


async def write_packet(device, packet: bytes, lock: asyncio.Lock) -> None:
    # Preserve whole packets across concurrent jobs and pace the 256-byte USB ring.
    async with lock:
        for offset in range(0, len(packet), 32):
            chunk = packet[offset:offset + 32]
            if device.write(chunk) != len(chunk):
                raise serial.SerialTimeoutException("Incomplete USB result write")
            await asyncio.sleep(0.02)
        device.flush()


async def handle_task(
    task: dict, device, lock: asyncio.Lock, run_codex_task,
    result_acks: dict[int, asyncio.Event] | None = None,
    run_hermes_task=None, set_searching=None,
) -> None:
    tool = task["tool"]
    async def on_search(active: bool) -> None:
        if set_searching is not None:
            await set_searching(task["id"], active)

    if tool == "thetapilot":
        logger.info("ThetaPilot analysis started id=%s arguments=%r", task["id"], task["arguments"])
        result = await run_analysis(task["arguments"])
        logger.info("ThetaPilot analysis completed id=%s result=%s", task["id"], result)
        # Keep the rich dashboard JSON opaque on the ESP32. Parsing its many
        # nested fields there would consume scarce internal RAM before TTS.
        result = {"id": task["id"], "ok": result["ok"],
                  "text": json.dumps(result, ensure_ascii=False, allow_nan=False)}
        prefix = "STACKCHAN_THETA_RESULT "
    elif tool == "flomo":
        logger.info("Flomo operation started id=%s action=%s", task["id"], task["arguments"].get("action"))
        result = await run_notes(task["arguments"])
        logger.info("Flomo operation completed id=%s ok=%s elapsed_ms=%s", task["id"],
                    result.get("ok"), result.get("elapsed_ms"))
        result["id"] = task["id"]
        prefix = "STACKCHAN_FLOMO_RESULT "
    elif tool == "websearch":
        logger.info("Web search started id=%s", task["id"])
        try:
            await on_search(True)
            result = await run_search(task["arguments"])
        finally:
            await on_search(False)
        logger.info("Web search completed id=%s result=%s", task["id"], result)
        result["id"] = task["id"]
        prefix = "STACKCHAN_WEBSEARCH_RESULT "
    elif tool == "futu":
        logger.info("Futu query started id=%s arguments=%r", task["id"], task["arguments"])
        try:
            result = await run_query(task["arguments"])
        except Exception:
            logger.exception("Futu query failed")
            result = {"ok": False, "summary": "电脑未能完成本地 OpenD 查询，请检查桥接日志。"}
        logger.info("Futu query completed id=%s result=%s", task["id"], result)
        result["id"] = task["id"]
        prefix = "STACKCHAN_FUTU_RESULT "
    else:
        name = "Hermes" if tool == "hermes" else "Codex"
        runner = run_hermes_task if tool == "hermes" else run_codex_task
        logger.info("%s task started id=%s", name, task["id"])
        sequence = 0

        async def send_progress(text):
            nonlocal sequence
            sequence += 1
            packet = ("STACKCHAN_CODEX_PROGRESS " + json.dumps(
                {"id": task["id"], "seq": sequence, "text": text}, ensure_ascii=False,
            ) + "\n").encode()
            await write_packet(device, b"\n" + packet, lock)
            logger.info("Codex progress submitted id=%s seq=%s text=%r", task["id"], sequence, text)

        reporter = ProgressReporter(send_progress)
        try:
            if runner is None and tool == "codex":
                summary = "Codex 接入已停用，请使用 Hermes。"
            elif runner is None:
                raise RuntimeError(f"{name} runner is unavailable")
            elif tool == "hermes" and set_searching is not None:
                summary = await runner(task["text"], on_progress=reporter.offer, on_search=on_search)
            else:
                summary = await runner(task["text"], on_progress=reporter.offer)
            logger.info("%s task completed summary=%r", name, summary)
        except TimeoutError:
            summary = ("Hermes 等待超时，任务可能仍在后台执行，请在电脑上的 Hermes 中检查。"
                       if tool == "hermes" else "Codex 任务超时，请检查项目中是否有部分改动。")
            logger.warning("%s task timed out", name)
        except Exception:
            summary = f"{name} 任务未能完成，请查看电脑上的日志。"
            logger.exception("%s task failed", name)
        finally:
            # Stop queued progress before sending the authoritative final result.
            await reporter.close()
            if tool == "hermes":
                await on_search(False)
        result = {"id": task["id"], "text": summary}
        prefix = "STACKCHAN_CODEX_RESULT "
    packet = (prefix + json.dumps(result, ensure_ascii=False) + "\n").encode()
    if len(packet) > 4096:
        # ThetaPilot's outer envelope carries its compact JSON as opaque text.
        result = {"id": task["id"], "ok": False,
                  "summary" if tool in {"futu", "websearch", "flomo"} else "text": "结果过长，请在电脑日志中查看。"}
        packet = (prefix + json.dumps(result, ensure_ascii=False) + "\n").encode()
    receipt = asyncio.Event() if tool in {"codex", "hermes"} and result_acks is not None else None
    if receipt is not None:
        result_acks[task["id"]] = receipt
    try:
        for attempt in range(3):
            # A newline also resets a truncated command from a previous attempt.
            await write_packet(device, b"\n" + packet, lock)
            if receipt is None:
                break
            try:
                await asyncio.wait_for(receipt.wait(), 2)
                logger.info("Device stored Codex result id=%s", task["id"])
                break
            except TimeoutError:
                logger.warning("No result receipt id=%s attempt=%s", task["id"], attempt + 1)
        else:
            logger.error("Device did not confirm result id=%s; full result retained in log", task["id"])
            return
        logger.info("Result submitted to the device's speech service tool=%s", tool)
    except serial.SerialException:
        logger.exception("Could not deliver %s result to device", tool)
    finally:
        if receipt is not None:
            result_acks.pop(task["id"], None)


async def sync_local_jobs(device, lock, jobs, interval=5, *, search_jobs=None):
    """Reconcile owned jobs without resubmitting any agent work."""
    await asyncio.sleep(2)  # Drain queued USB task events before the first inventory.
    while True:
        payload = {"jobs": sorted(jobs)}
        if search_jobs is not None:
            payload["searching"] = bool(search_jobs)
        packet = ("\nSTACKCHAN_AGENT_SYNC " + json.dumps(payload) + "\n").encode()
        await write_packet(device, packet, lock)
        await asyncio.sleep(interval)


async def serve(port: str, project: Path, *, enable_codex: bool = False) -> None:
    session = None
    if enable_codex:
        # Hermes-only startup must not import the optional Codex SDK.
        from session import CodexSession
        session = CodexSession(project)
    hermes = HermesSession(project)
    loop = asyncio.get_running_loop()
    tasks: asyncio.Queue[dict | None] = asyncio.Queue()
    result_acks: dict[int, asyncio.Event] = {}
    owned_jobs: set[int] = set()
    search_jobs: set[int] = set()

    def enqueue(task):
        if task["tool"] in {"codex", "hermes"}:
            if task["id"] in owned_jobs:
                return
            owned_jobs.add(task["id"])
        tasks.put_nowait(task)

    def acknowledge(job_id: int) -> None:
        if receipt := result_acks.get(job_id):
            receipt.set()

    with serial.Serial(port, 115200, timeout=0.5, write_timeout=3, exclusive=True) as device:
        stopped = threading.Event()

        def read_device() -> None:
            pending = bytearray()
            try:
                while not stopped.is_set():
                    raw = device.readline(16384)
                    if not raw:
                        continue
                    pending.extend(raw)
                    if not pending.endswith(b"\n") and len(pending) < 16384:
                        continue
                    line = pending.decode(errors="replace").rstrip()
                    pending.clear()
                    if FLOMO_MARKER in line:
                        logger.info("device FlomoVoice: QUERY (note content omitted)")
                    else:
                        logger.info("device %s", line)
                    match = re.search(r"CodexVoice: RESULT job=(\d+) stored", line)
                    if match:
                        loop.call_soon_threadsafe(acknowledge, int(match.group(1)))
                    if task := parse_task_event(line):
                        loop.call_soon_threadsafe(enqueue, task)
            except serial.SerialException:
                logger.exception("Device serial connection failed")
                loop.call_soon_threadsafe(tasks.put_nowait, None)

        reader = threading.Thread(target=read_device, daemon=True)
        reader.start()
        logger.info("USB connected; Hermes enabled; Codex %s port=%s project=%s",
                    "enabled" if session else "disabled", port, project)
        write_lock = asyncio.Lock()
        active = set()
        async def set_searching(job_id: int, searching: bool) -> None:
            was_searching = bool(search_jobs)
            if searching:
                search_jobs.add(job_id)
            else:
                search_jobs.discard(job_id)
            if bool(search_jobs) == was_searching:
                return
            try:
                await write_packet(device, f"\nSTACKCHAN_SEARCH {int(searching)}\n".encode(), write_lock)
            except serial.SerialException:
                logger.warning("Could not update search eyes over USB")

        async def reconcile():
            try:
                await sync_local_jobs(device, write_lock, owned_jobs, search_jobs=search_jobs)
            except serial.SerialException:
                logger.exception("USB task reconciliation failed")
                tasks.put_nowait(None)

        sync_job = asyncio.create_task(reconcile())

        async def warmup():
            try:
                await session.run("语音会话初始化，请仅回复就绪。")
                logger.info("Ready: Codex project and MCP session are warm")
            except Exception:
                logger.exception("Codex warmup failed")

        warmup_job = asyncio.create_task(warmup()) if session else None
        try:
            while task := await tasks.get():
                job = asyncio.create_task(handle_task(
                    task, device, write_lock, session.run if session else None, result_acks, hermes.run, set_searching,
                ))
                active.add(job)
                job.add_done_callback(active.discard)
                if task["tool"] in {"codex", "hermes"}:
                    job.add_done_callback(lambda _, job_id=task["id"]: owned_jobs.discard(job_id))
        finally:
            stopped.set()
            jobs = list(active) + [sync_job]
            if warmup_job is not None:
                jobs.append(warmup_job)
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            await asyncio.to_thread(reader.join, 2)
            if session is not None:
                await session.close()
            await hermes.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/cu.usbmodem1101")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--enable-codex", action="store_true",
                        help="Enable the optional Codex agent and startup warmup (default: disabled)")
    args = parser.parse_args()
    if not args.project.is_dir():
        parser.error("--project must be an existing directory")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(serve(args.port, args.project, enable_codex=args.enable_codex))


if __name__ == "__main__":
    main()
