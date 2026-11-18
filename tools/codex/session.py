"""Keep a Codex app-server and project context warm between voice tasks."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox

logger = logging.getLogger("codex-voice")

VOICE_INSTRUCTIONS = (
    "用户通过桌面机器人的语音下达任务，播报由桥接程序完成。直接问题通常用一句话回答，复杂任务最多三句话，使用自然的中文纯文本，"
    "说明结果和必要的验证信息。任务失败或只完成一部分时明确说明。"
    "处理明确的简单请求时，直接使用必要的工具，避免无关扫描。"
    "多步骤或较长任务，在开始和关键步骤切换时用一句简短中文向用户说明正在做什么，"
    "使用 commentary 消息。不要报告内部推理，不编造进度百分比；简单任务无需额外说明。"
    "遵守项目指令，保留用户已有改动。"
)


class CodexSession:
    def __init__(self, project: Path) -> None:
        self._project = str(project.resolve())
        self._config = CodexConfig(
            codex_bin=os.getenv("STACKCHAN_CODEX_BIN") or None,
            cwd=self._project,
        )
        self._client = AsyncCodex(self._config)
        self._thread = None
        self._lock = asyncio.Lock()

    async def run(
        self, text: str, on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> str:
        timeout = int(os.getenv("STACKCHAN_CODEX_TIMEOUT_S", "600"))
        if timeout <= 0:
            raise ValueError("STACKCHAN_CODEX_TIMEOUT_S must be positive")
        async with self._lock:
            started = time.monotonic()
            turn = None
            try:
                async with asyncio.timeout(timeout):
                    if self._thread is None:
                        await self._client.__aenter__()
                        self._thread = await self._client.thread_start(
                            approval_mode=ApprovalMode.deny_all,
                            cwd=self._project,
                            developer_instructions=VOICE_INSTRUCTIONS,
                            ephemeral=True,
                            sandbox=Sandbox.workspace_write,
                        )
                    turn = await self._thread.turn(text)
                    logger.info("Codex turn accepted after %.2fs", time.monotonic() - started)
                    if on_progress is not None:
                        summary = await self._stream_turn(turn, on_progress)
                    else:
                        result = await turn.run()
                        if result.status.value != "completed":
                            raise RuntimeError(f"Codex turn {result.status.value}: {result.error}")
                        summary = (result.final_response or "").strip()
                    if not summary:
                        raise RuntimeError("Codex completed without a summary")
            except (Exception, asyncio.CancelledError):
                if turn is not None:
                    try:
                        await asyncio.wait_for(turn.interrupt(), 5)
                    except Exception:
                        logger.exception("Could not interrupt the failed Codex turn")
                await self.close()
                raise
            logger.info("Codex turn finished after %.2fs", time.monotonic() - started)
            return summary

    @staticmethod
    async def _stream_turn(turn, on_progress) -> str:
        final = ""
        fallback = ""
        completed = None
        stream = turn.stream()
        try:
            async for event in stream:
                if event.method == "turn/completed":
                    completed = event.payload.turn
                elif event.method in {"item/started", "item/completed"}:
                    item = event.payload.item.root
                    if item.type == "agentMessage" and event.method == "item/completed":
                        phase = getattr(item.phase, "value", item.phase)
                        if phase == "commentary":
                            await on_progress(item.text)
                        elif phase == "final_answer":
                            final = item.text
                        elif phase is None:
                            fallback = item.text
                    elif event.method == "item/started":
                        labels = {
                            "commandExecution": "正在电脑上执行命令。",
                            "mcpToolCall": "正在调用外部工具。",
                            "webSearch": "正在查询网络资料。",
                            "fileChange": "正在修改项目文件。",
                        }
                        if label := labels.get(item.type):
                            await on_progress(label, detailed=False)
        finally:
            await stream.aclose()
        if completed is None:
            raise RuntimeError("Codex stream ended without turn/completed")
        if completed.status.value != "completed":
            raise RuntimeError(f"Codex turn {completed.status.value}: {completed.error}")
        return (final or fallback).strip()

    async def close(self) -> None:
        await self._client.close()
        self._thread = None
        self._client = AsyncCodex(self._config)
