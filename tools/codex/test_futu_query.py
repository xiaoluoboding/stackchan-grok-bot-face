"""Hardware-free checks for quote correctness and the USB result boundary."""

import asyncio
import json
import sys
from types import SimpleNamespace

import futu_query
import pytest


@pytest.mark.parametrize("arguments", [
    {"action": "place_order", "codes": "HK.00700"},
    {"action": "get_market_snapshot", "codes": "腾讯"},
    {"action": "get_market_snapshot", "codes": []},
    {"action": "get_market_snapshot", "codes": [123]},
    {"action": "get_market_snapshot", "codes": ["US.AAPL"] * 4},
])
def test_invalid_or_mutating_requests_never_start_sdk(arguments, monkeypatch):
    def unexpected_start(*args, **kwargs):
        pytest.fail("Invalid request started a subprocess")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_start)
    result = asyncio.run(futu_query.run_query(arguments))
    assert result["ok"] is False


def test_quote_change_uses_previous_close_and_keeps_market_time():
    result = futu_query.snapshot_result([{
        "code": "US.AAPL", "name": "苹果", "last_price": 95,
        "prev_close_price": 100, "update_time": "2026-11-17 16:00:00",
    }], ["US.AAPL"])
    assert result["quotes"][0]["change_percent"] == -5
    assert result["quotes"][0]["timezone"] == "America/New_York"
    assert result["quotes"][0]["update_time"] == "2026-11-17 16:00:00"
    assert "下跌百分之5.00" in result["summary"]
    assert "时间" not in result["summary"]
    assert "2026" not in result["summary"]


@pytest.mark.parametrize("price", [None, float("nan"), 0])
def test_missing_prices_are_errors(price):
    with pytest.raises(RuntimeError, match="有效"):
        futu_query.snapshot_result([{"code": "HK.00700", "last_price": price}], ["HK.00700"])


def test_sdk_error_closes_connection(monkeypatch):
    class Context:
        closed = False

        def __init__(self, **kwargs):
            pass

        def get_market_snapshot(self, codes):
            return -1, "行情权限不足"

        def close(self):
            Context.closed = True

    monkeypatch.setitem(sys.modules, "futu", SimpleNamespace(OpenQuoteContext=Context, RET_OK=0))
    with pytest.raises(RuntimeError, match="权限不足"):
        futu_query.execute({"action": "get_market_snapshot", "codes": "HK.00700"})
    assert Context.closed


def test_timeout_kills_worker(monkeypatch):
    class Process:
        returncode = None
        killed = False

        async def communicate(self):
            await asyncio.sleep(60)

        def kill(self):
            self.killed = True

        async def wait(self):
            self.returncode = -9

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    result = asyncio.run(futu_query.run_query(
        {"action": "get_market_snapshot", "codes": "HK.00700"}, timeout=0.01,
    ))
    assert result["ok"] is False
    assert "超时" in result["summary"]
    assert process.killed


def test_bridge_serial_result_is_correlated_and_structured(monkeypatch):
    import bridge

    async def query(arguments):
        assert arguments["codes"] == "HK.00700"
        return {"ok": True, "quotes": [{"last_price": 372}], "summary": "腾讯最新成交价372。"}

    class Device:
        packet = bytearray()

        def write(self, chunk):
            self.packet.extend(chunk)
            return len(chunk)

        def flush(self):
            pass

    device = Device()
    monkeypatch.setattr(bridge, "run_query", query)

    async def run():
        await bridge.handle_task({
            "tool": "futu", "id": 42,
            "arguments": {"action": "get_market_snapshot", "codes": "HK.00700"},
        }, device, asyncio.Lock(), None)

    asyncio.run(run())
    prefix, payload = device.packet.decode().strip().split(" ", 1)
    result = json.loads(payload)
    assert prefix == "STACKCHAN_FUTU_RESULT"
    assert result["id"] == 42 and result["ok"] is True
    assert result["quotes"][0]["last_price"] == 372
