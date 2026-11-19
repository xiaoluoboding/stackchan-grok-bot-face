"""Run the installed Hermes CLI using its own profile and explicit session IDs."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from pathlib import Path

logger = logging.getLogger("hermes-voice")
VOICE_PROMPT = (
    "用户通过桌面机器人语音下达任务。请执行下面的用户请求，最后用自然中文纯文本简短回答，"
    "一般一句，复杂任务最多三句。失败或部分完成须明确说明。遵守项目指令，保留已有改动。\n\n用户请求：\n"
)


class HermesSession:
    def __init__(self, project: Path) -> None:
        self._project = str(project.resolve())
        self._session_id = None
        self._lock = asyncio.Lock()
        self._process = None

    async def run(self, text: str, on_progress=None) -> str:
        timeout = int(os.getenv("STACKCHAN_HERMES_TIMEOUT_S", "600"))
        if timeout <= 0:
            raise ValueError("STACKCHAN_HERMES_TIMEOUT_S must be positive")
        async with self._lock:
            command = [os.getenv("STACKCHAN_HERMES_BIN") or "hermes", "chat",
                       "--format", "stream-json", "--query-file", "-", "--in", self._project]
            if self._session_id:
                command.extend(["--resume", self._session_id])
            stderr_job = None
            try:
                async with asyncio.timeout(timeout):
                    self._process = await asyncio.create_subprocess_exec(
                        *command, cwd=self._project, stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                        start_new_session=True, limit=4 * 1024 * 1024,
                    )
                    process = self._process
                    # Drain diagnostics concurrently; never forward credentials or raw tool output.
                    stderr_job = asyncio.create_task(self._drain(process.stderr))
                    process.stdin.write((VOICE_PROMPT + text).encode())
                    await process.stdin.drain()
                    process.stdin.close()
                    final = None
                    async for line in process.stdout:
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if not isinstance(event, dict):
                            continue
                        if event.get("type") in {"system", "result"}:
                            session_id = event.get("session_id")
                            if isinstance(session_id, str) and session_id:
                                self._session_id = session_id
                        if event.get("type") == "tool_use" and on_progress:
                            await on_progress("Hermes 正在调用工具处理任务。", detailed=False)
                        elif event.get("type") == "result":
                            final = event
                    code = await process.wait()
                    await stderr_job
                    if code or final is None or final.get("exit_code") != 0:
                        raise RuntimeError(f"Hermes failed (exit={code}, terminal result={final is not None})")
                    summary = final.get("text")
                    if not isinstance(summary, str) or not summary.strip():
                        raise RuntimeError("Hermes completed without a summary")
                    return summary.strip()
            finally:
                # Reap the CLI on timeout/shutdown. Gateway-owned work can keep running;
                # detaching the client does not prove the agent task was cancelled.
                await self._stop_process()
                if stderr_job:
                    stderr_job.cancel()
                    await asyncio.gather(stderr_job, return_exceptions=True)

    @staticmethod
    async def _drain(stream) -> None:
        while await stream.read(8192):
            pass

    async def _stop_process(self) -> None:
        process = self._process
        if process is not None:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except TimeoutError:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()
            self._process = None

    async def close(self) -> None:
        await self._stop_process()
