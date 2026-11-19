import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from hermes_session import HermesSession


class HermesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.binary = self.root / "hermes"
        self.binary.write_text('''#!/usr/bin/env python3
import json, sys, time
from pathlib import Path
prompt = sys.stdin.read()
with Path("calls").open("a") as f:
    f.write(json.dumps({"args": sys.argv[1:], "prompt": prompt}) + "\\n")
print(json.dumps({"type":"system", "session_id":"voice-session"}), flush=True)
if "timeout-test" in prompt:
    time.sleep(20)
print("non-JSON diagnostic")
print(json.dumps({"type":"tool_use", "name":"terminal", "input":{"secret":"never speak"}}))
sys.stderr.write("diagnostic" * 20000)
if "missing-result" not in prompt:
    print(json.dumps({"type":"result", "session_id":"voice-session", "exit_code":0, "text":"已完成"}))
''')
        self.binary.chmod(0o755)
        self.env = patch.dict(os.environ, {"STACKCHAN_HERMES_BIN": str(self.binary)})
        self.env.start()
        self.session = HermesSession(self.root)

    async def asyncTearDown(self):
        await self.session.close()
        self.env.stop()
        self.temp.cleanup()

    async def test_streaming_and_explicit_resume_preserve_literal_prompt(self):
        import json
        progress = AsyncMock()
        prompt = '查询 `literal` $(literal) "原文"'
        self.assertEqual(await self.session.run(prompt, progress), "已完成")
        self.assertEqual(await self.session.run("继续"), "已完成")
        calls = [json.loads(line) for line in (self.root / "calls").read_text().splitlines()]
        self.assertIn(prompt, calls[0]["prompt"])
        self.assertNotIn("--resume", calls[0]["args"])
        self.assertEqual(calls[1]["args"][-2:], ["--resume", "voice-session"])
        progress.assert_awaited_once_with("Hermes 正在调用工具处理任务。", detailed=False)

    async def test_missing_terminal_result_is_failure(self):
        with self.assertRaises(RuntimeError):
            await self.session.run("missing-result")

    async def test_timeout_reaps_process_without_retry(self):
        with patch.dict(os.environ, {"STACKCHAN_HERMES_TIMEOUT_S": "1"}):
            with self.assertRaises(TimeoutError):
                await self.session.run("timeout-test")
        self.assertIsNone(self.session._process)
        self.assertEqual(len((self.root / "calls").read_text().splitlines()), 1)

    async def test_cancellation_reaps_process(self):
        task = asyncio.create_task(self.session.run("timeout-test"))
        for _ in range(100):
            if (self.root / "calls").exists():
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.session._process)
