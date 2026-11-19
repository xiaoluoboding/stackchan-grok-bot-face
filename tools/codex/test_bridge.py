import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bridge import handle_task, parse_task_event, serve
import serial


class Device:
    def __init__(self, receipts, acknowledge_after=1):
        self.receipts = receipts
        self.acknowledge_after = acknowledge_after
        self.pending = bytearray()
        self.packets = []
        self.chunk_sizes = []

    def write(self, data):
        self.chunk_sizes.append(len(data))
        for value in data:
            self.pending.append(value)
            if value == 10:
                line = bytes(self.pending).strip()
                self.pending.clear()
                if line:
                    self.packets.append(line)
                    if (line.startswith(b"STACKCHAN_CODEX_RESULT ") and
                            len(self.packets) >= self.acknowledge_after):
                        self.receipts[7].set()
        return len(data)

    def flush(self):
        pass


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_hermes_routes_to_its_own_runner_and_confirms_result(self):
        task = parse_task_event('I HermesVoice: TASK {"id":7,"text":"查看项目"}')
        self.assertEqual(task["tool"], "hermes")
        receipts = {}
        device = Device(receipts)
        codex = AsyncMock()
        hermes = AsyncMock(return_value="Hermes 已完成")
        await handle_task(task, device, asyncio.Lock(), codex, receipts, hermes)
        codex.assert_not_awaited()
        hermes.assert_awaited_once()
        self.assertIn("Hermes", json.loads(device.packets[-1].split(b" ", 1)[1])["text"])
        self.assertEqual(receipts, {})

    async def test_disabled_codex_returns_status_without_running_an_agent(self):
        receipts = {}
        device = Device(receipts)
        hermes = AsyncMock()
        await handle_task(
            {"tool": "codex", "id": 7, "text": "查看项目"},
            device, asyncio.Lock(), None, receipts, hermes,
        )
        hermes.assert_not_awaited()
        payload = json.loads(device.packets[-1].split(b" ", 1)[1])
        self.assertIn("已停用", payload["text"])
        self.assertIn("Hermes", payload["text"])
        self.assertEqual(receipts, {})

    async def test_hermes_runs_with_codex_disabled(self):
        receipts = {}
        device = Device(receipts)
        hermes = AsyncMock(return_value="已完成")
        await handle_task(
            {"tool": "hermes", "id": 7, "text": "查看项目"},
            device, asyncio.Lock(), None, receipts, hermes,
        )
        hermes.assert_awaited_once()
        self.assertEqual(json.loads(device.packets[-1].split(b" ", 1)[1])["text"], "已完成")

    async def test_default_startup_needs_no_codex_sdk(self):
        device = MagicMock()
        device.readline.side_effect = serial.SerialException("test disconnect")
        hermes = SimpleNamespace(run=AsyncMock(), close=AsyncMock())
        with patch.dict("sys.modules", {"session": None}), \
                patch("bridge.serial.Serial") as port, \
                patch("bridge.HermesSession", return_value=hermes):
            port.return_value.__enter__.return_value = device
            with self.assertLogs("codex-voice") as logs:
                await serve("test-port", Path.cwd())
        self.assertTrue(any("Codex disabled" in line for line in logs.output))
        hermes.close.assert_awaited_once()

    async def test_codex_can_be_explicitly_enabled_again(self):
        device = MagicMock()
        device.readline.side_effect = serial.SerialException("test disconnect")
        session = SimpleNamespace(run=AsyncMock(), close=AsyncMock())
        factory = MagicMock(return_value=session)
        hermes = SimpleNamespace(run=AsyncMock(), close=AsyncMock())
        with patch.dict("sys.modules", {"session": SimpleNamespace(CodexSession=factory)}), \
                patch("bridge.serial.Serial") as port, \
                patch("bridge.HermesSession", return_value=hermes):
            port.return_value.__enter__.return_value = device
            with self.assertLogs("codex-voice") as logs:
                await serve("test-port", Path.cwd(), enable_codex=True)
        factory.assert_called_once_with(Path.cwd())
        session.run.assert_awaited_once()  # optional startup warmup restored
        session.close.assert_awaited_once()
        self.assertTrue(any("Codex enabled" in line for line in logs.output))

    def test_rejects_malformed_hermes_tasks(self):
        for payload in ['[]', '{"id":true,"text":"x"}', '{"id":7,"text":" "}']:
            self.assertIsNone(parse_task_event("HermesVoice: TASK " + payload))

    async def test_result_is_confirmed_without_reexecuting_codex(self):
        receipts = {}
        device = Device(receipts)
        runner = AsyncMock(return_value="已完成")
        await handle_task(
            {"tool": "codex", "id": 7, "text": "查询分支"},
            device, asyncio.Lock(), runner, receipts,
        )
        runner.assert_awaited_once()
        self.assertEqual(runner.call_args.args, ("查询分支",))
        self.assertTrue(callable(runner.call_args.kwargs["on_progress"]))
        self.assertEqual(len(device.packets), 1)
        self.assertLessEqual(max(device.chunk_sizes), 32)
        payload = json.loads(device.packets[0].split(b" ", 1)[1])
        self.assertEqual(payload, {"id": 7, "text": "已完成"})
        self.assertEqual(receipts, {})

    async def test_missing_receipt_retries_transport_only(self):
        receipts = {}
        device = Device(receipts, acknowledge_after=2)
        runner = AsyncMock(return_value="已完成")
        original_wait_for = asyncio.wait_for

        async def short_wait(awaitable, timeout):
            return await original_wait_for(awaitable, min(timeout, 0.01))

        with patch("bridge.asyncio.wait_for", side_effect=short_wait):
            await handle_task(
                {"tool": "codex", "id": 7, "text": "查询分支"},
                device, asyncio.Lock(), runner, receipts,
            )
        runner.assert_awaited_once()
        self.assertEqual(len(device.packets), 2)
        self.assertEqual(device.packets[0], device.packets[1])
        self.assertEqual(receipts, {})

    async def test_large_summary_respects_firmware_line_limit(self):
        receipts = {}
        device = Device(receipts)
        runner = AsyncMock(return_value="长结果" * 5000)
        await handle_task(
            {"tool": "codex", "id": 7, "text": "查询"},
            device, asyncio.Lock(), runner, receipts,
        )
        self.assertLessEqual(len(device.packets[0]) + 1, 4096)
        payload = json.loads(device.packets[0].split(b" ", 1)[1])
        self.assertIn("日志", payload["text"])

    async def test_progress_does_not_replace_final_result(self):
        receipts = {}
        device = Device(receipts)

        async def runner(text, on_progress):
            await on_progress("正在检查分支。")
            await asyncio.sleep(0.15)
            return "当前分支是 main。"

        await handle_task(
            {"tool": "codex", "id": 7, "text": "查询分支"},
            device, asyncio.Lock(), runner, receipts,
        )
        self.assertTrue(device.packets[0].startswith(b"STACKCHAN_CODEX_PROGRESS "))
        self.assertTrue(device.packets[-1].startswith(b"STACKCHAN_CODEX_RESULT "))
        self.assertIn("main", json.loads(device.packets[-1].split(b" ", 1)[1])["text"])


if __name__ == "__main__":
    unittest.main()

class ReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def test_inventory_tracks_jobs_and_never_replays_work(self):
        from bridge import sync_local_jobs
        jobs = {42}
        packets = []
        async def capture(device, packet, lock):
            packets.append(json.loads(packet.decode().strip().split(' ', 1)[1]))
            jobs.clear()
            if len(packets) == 2:
                raise asyncio.CancelledError
        async def no_wait(_):
            pass
        with patch('bridge.write_packet', side_effect=capture), patch('bridge.asyncio.sleep', side_effect=no_wait):
            with self.assertRaises(asyncio.CancelledError):
                await sync_local_jobs(None, asyncio.Lock(), jobs)
        self.assertEqual(packets, [{'jobs': [42]}, {'jobs': []}])
