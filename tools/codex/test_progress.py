import asyncio
import unittest
from unittest.mock import AsyncMock

from progress import ProgressReporter


class ProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_generic_activity_never_replaces_detailed_stage(self):
        send = AsyncMock()
        reporter = ProgressReporter(send, interval=0.03)
        try:
            await reporter.offer("正在检查下载文件夹。")
            await asyncio.sleep(0.01)
            await reporter.offer("系统索引未找到，正在逐层检查可访问的目录。")
            await reporter.offer("正在电脑上执行命令。", detailed=False)
            await asyncio.sleep(0.04)
            self.assertEqual([c.args[0] for c in send.call_args_list], [
                "正在检查下载文件夹。", "系统索引未找到，正在逐层检查可访问的目录。",
            ])
        finally:
            await reporter.close()

    async def test_generic_fallback_is_allowed_before_first_detail(self):
        send = AsyncMock()
        reporter = ProgressReporter(send, interval=0)
        try:
            await reporter.offer("正在查询网络资料。", detailed=False)
            await asyncio.sleep(0.01)
            send.assert_awaited_once_with("正在查询网络资料。")
        finally:
            await reporter.close()

    async def test_coalesces_burst_and_cancels_stale_updates_on_completion(self):
        send = AsyncMock()
        reporter = ProgressReporter(send, interval=0.03)
        try:
            await reporter.offer("正在读取文件。后面不播。")
            await asyncio.sleep(0.01)
            await reporter.offer("正在修改文件。")
            await reporter.offer("正在运行测试。")
            await asyncio.sleep(0.04)
            self.assertEqual([c.args[0] for c in send.call_args_list],
                             ["正在读取文件。", "正在运行测试。"])
            await reporter.offer("不应再播报。")
        finally:
            await reporter.close()
        await asyncio.sleep(0.04)
        self.assertEqual(send.await_count, 2)

    async def test_duplicate_and_failed_transport_do_not_break_execution(self):
        send = AsyncMock(side_effect=[OSError("USB busy"), None])
        reporter = ProgressReporter(send, interval=0)
        try:
            await reporter.offer("正在调用工具。")
            await asyncio.sleep(0.01)
            await reporter.offer("正在调用工具。")
            await asyncio.sleep(0.01)
            await reporter.offer("正在验证结果。")
            await asyncio.sleep(0.01)
            self.assertEqual(send.await_count, 2)
        finally:
            await reporter.close()
