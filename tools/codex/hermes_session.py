"""Run the installed Hermes CLI using its own profile and explicit session IDs."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from pathlib import Path

logger = logging.getLogger("hermes-voice")
WEB_RETRIEVAL_TOOLS = {
    "mcp__firecrawl__firecrawl_search", "mcp__firecrawl__firecrawl_scrape",
    "web_search", "web_extract",
}
VOICE_PROMPT = (
    "用户通过桌面机器人语音下达任务。请执行下面的用户请求，最后用自然中文纯文本简短回答，"
    "一般一句，复杂任务最多三句。失败或部分完成须明确说明。遵守项目指令，保留已有改动。"
    "语音查询重视及时性：新闻、百科、价格标准等联网问题，直接调用 Firecrawl MCP 的 "
    "mcp__firecrawl__firecrawl_search（通常 limit=3）；工具若被延迟加载，先用 tool_search 查找 Firecrawl 搜索工具。"
    "需要核实正文时再用 Firecrawl MCP 的 mcp__firecrawl__firecrawl_scrape 读取最相关的权威来源。"
    "收费、政策、价格查询须核实生效日期和适用地区，不能把旧网页直接当作现行标准；无法确认时明确说明。"
    "普通联网查询无需扫描本地文件、读取项目脚本、寻找 Python 环境，也不要用 terminal、curl、"
    "execute_code 或本地 websearch.py 绕路搜索，也不要回退到内置 web_search/web_extract。"
    "tool_search 最多发现一次；找不到 Firecrawl 或调用失败就立即简短报告原因，"
    "不要反复换工具重试。不得编造来源、搜索结果或进度。\n\n用户请求：\n"
)


class HermesSession:
    def __init__(self, project: Path) -> None:
        self._project = str(project.resolve())
        self._session_id = None
        self._lock = asyncio.Lock()
        self._process = None

    async def run(self, text: str, on_progress=None, *, on_search=None) -> str:
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
                    searches = set()
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
                        kind = event.get("type")
                        name = event.get("name")
                        if on_search and isinstance(name, str) and name in WEB_RETRIEVAL_TOOLS:
                            key = event.get("tool_call_id") or name
                            if isinstance(key, str):
                                was_searching = bool(searches)
                                if kind == "tool_use":
                                    searches.add(key)
                                elif kind == "tool_result":
                                    searches.discard(key)
                                if was_searching != bool(searches):
                                    await on_search(bool(searches))
                        if kind == "tool_use" and on_progress:
                            name = name if isinstance(name, str) else ""
                            logger.info("Hermes tool started name=%s", name)
                            label = ("正在通过 Firecrawl 搜索资料。" if "firecrawl_search" in name else
                                     "正在通过 Firecrawl 核对网页正文。" if "firecrawl_scrape" in name else
                                     "Hermes 正在调用工具处理任务。")
                            await on_progress(label, detailed=False)
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
