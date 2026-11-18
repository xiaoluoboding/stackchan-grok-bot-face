"""Coalesce public execution updates so speech cannot build a stale queue."""

from __future__ import annotations

import asyncio
import logging
import re
import time

logger = logging.getLogger("codex-voice")


class ProgressReporter:
    def __init__(self, send, interval: float = 12) -> None:
        self._send = send
        self._interval = interval
        self._pending = ""
        self._last = ""
        self._has_detail = False
        self._changed = asyncio.Event()
        self._worker = asyncio.create_task(self._run())

    async def offer(self, text: str, *, detailed: bool = True) -> None:
        # Once Codex explains the action, generic tool labels add no useful
        # information and must not replace it while the rate limiter waits.
        if not detailed and self._has_detail:
            return
        text = re.sub(r"\s+", " ", text).strip()
        # One short sentence; never serialize tool arguments or command output.
        text = re.split(r"(?<=[。！？])", text, maxsplit=1)[0]
        text = text[:100].strip("`*# ")
        if detailed and text:
            self._has_detail = True
        if text and text != self._last:
            self._pending = text
            self._changed.set()

    async def _run(self) -> None:
        last_sent = -float("inf")
        while True:
            await self._changed.wait()
            await asyncio.sleep(max(0, self._interval - (time.monotonic() - last_sent)))
            text = self._pending
            self._changed.clear()
            if text == self._last:
                continue
            self._last = text
            try:
                await self._send(text)
            except Exception:
                # Progress delivery must not turn successful execution into failure.
                logger.exception("Could not deliver Codex progress")
            last_sent = time.monotonic()

    async def close(self) -> None:
        self._worker.cancel()
        await asyncio.gather(self._worker, return_exceptions=True)
