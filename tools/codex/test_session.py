import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from session import CodexSession


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.result = SimpleNamespace(
            status=SimpleNamespace(value="completed"),
            final_response="任务完成",
            error=None,
        )
        self.turn = SimpleNamespace(
            run=AsyncMock(return_value=self.result),
            interrupt=AsyncMock(),
        )
        self.thread = SimpleNamespace(turn=AsyncMock(return_value=self.turn))
        self.client = SimpleNamespace(
            __aenter__=AsyncMock(),
            thread_start=AsyncMock(return_value=self.thread),
            close=AsyncMock(),
        )
        self.factory = patch("session.AsyncCodex", return_value=self.client)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    async def test_multiple_tasks_reuse_project_and_mcp_session(self):
        session = CodexSession(Path.cwd())
        self.assertEqual(await session.run("第一个任务"), "任务完成")
        self.assertEqual(await session.run("第二个任务"), "任务完成")
        self.client.__aenter__.assert_awaited_once()
        self.client.thread_start.assert_awaited_once()
        self.assertEqual(self.thread.turn.await_count, 2)
        await session.close()

    async def test_timeout_interrupts_and_closes_session(self):
        async def slow_result():
            await asyncio.sleep(30)

        self.turn.run.side_effect = slow_result
        session = CodexSession(Path.cwd())
        with patch.dict(os.environ, {"STACKCHAN_CODEX_TIMEOUT_S": "1"}), self.assertRaises(TimeoutError):
            await session.run("慢任务")
        self.turn.interrupt.assert_awaited_once()
        self.client.close.assert_awaited_once()
        self.assertIsNone(session._thread)

    async def test_startup_failure_releases_runtime(self):
        self.client.thread_start.side_effect = OSError("runtime disconnected")
        session = CodexSession(Path.cwd())
        with self.assertRaises(OSError):
            await session.run("任务")
        self.client.close.assert_awaited_once()
        self.assertIsNone(session._thread)

    async def test_failed_turn_does_not_return_success(self):
        self.result.status.value = "failed"
        self.result.error = "执行失败"
        session = CodexSession(Path.cwd())
        with self.assertRaises(RuntimeError):
            await session.run("失败的任务")
        await session.close()

    async def test_stream_reports_only_public_progress_and_keeps_final(self):
        def event(method, **payload):
            return SimpleNamespace(method=method, payload=SimpleNamespace(**payload))

        def item(kind, text="", phase=None):
            return SimpleNamespace(root=SimpleNamespace(type=kind, text=text, phase=phase))

        async def stream():
            yield event("item/started", item=item("reasoning", "内部推理"))
            yield event("item/completed", item=item("agentMessage", "正在检查项目。", "commentary"))
            yield event("item/started", item=item("commandExecution", "秘密命令参数"))
            yield event("item/completed", item=item("agentMessage", "任务完成", "final_answer"))
            yield event("turn/completed", turn=self.result)

        self.turn.stream = stream
        progress = AsyncMock()
        session = CodexSession(Path.cwd())
        self.assertEqual(await session.run("任务", on_progress=progress), "任务完成")
        self.assertEqual([c.args[0] for c in progress.call_args_list],
                         ["正在检查项目。", "正在电脑上执行命令。"])
        self.turn.run.assert_not_awaited()
        await session.close()

    async def test_stream_disconnect_does_not_claim_completion(self):
        async def stream():
            if False:
                yield None

        self.turn.stream = stream
        session = CodexSession(Path.cwd())
        with self.assertRaisesRegex(RuntimeError, "without turn/completed"):
            await session.run("任务", on_progress=AsyncMock())
        self.client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
