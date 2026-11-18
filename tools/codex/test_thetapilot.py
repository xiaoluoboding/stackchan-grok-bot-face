import asyncio
import json

import bridge
import pytest
import thetapilot


def gex(symbol="NVDA"):
    return {"symbol": symbol, "name": "英伟达", "stock_price": 155,
            "total_net_gex": -1e8, "gamma_coverage_pct": 90, "oi_coverage_pct": 95,
            "mvc_strike": 200, "cells": [
                {"strike": 150, "expiration": "2026-11-20", "net_gex": -5e7},
                {"strike": 150, "expiration": "2026-11-27", "net_gex": -2e7},
                {"strike": 160, "expiration": "2026-11-20", "net_gex": 1e7},
            ]}


@pytest.mark.parametrize("symbol", ["HK.00700", "NVDA/../../settings", "", 123])
def test_rejects_invalid_symbols(symbol):
    with pytest.raises((ValueError, TypeError)):
        thetapilot.validate_request({"symbol": symbol})


def test_aggregates_exposures_without_claiming_support_or_dealer_positions():
    result = thetapilot.compact_context("NVDA", {"gex": gex()}, ["iv_stats"])
    context = result["context"]["gex"]
    assert context["nearby_and_largest_strikes"] == [
        {"strike": 150, "net_gex": -7e7}, {"strike": 160, "net_gex": 1e7},
    ]
    assert context["largest_expirations"][0]["net_gex"] == -4e7
    assert context["unit"] == "USD per 1% underlying move"
    assert "not observed dealer" in context["sign_convention"]
    assert result["unavailable_panels"] == ["iv_stats"]
    assert "时间" not in result["summary"]
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 3400


def test_missing_optional_panels_are_explicit(monkeypatch):
    def fetch(base, path):
        if path.startswith("gex-heatmap"):
            return gex()
        raise OSError("offline")
    monkeypatch.setattr(thetapilot, "fetch", fetch)
    result = asyncio.run(thetapilot.run_analysis({"symbol": "US.NVDA"}))
    assert result["ok"]
    assert len(result["unavailable_panels"]) == 5


def test_usb_budget_retains_primary_evidence_and_marks_omitted_panel():
    result = thetapilot.compact_context("NVDA", {
        "gex": gex(), "gex_interval": {"method": "long method " * 600},
    }, [])
    assert "gex" in result["context"]
    assert result["omitted_for_voice"] == ["gex_interval"]
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 3400


def test_absent_gamma_is_not_reported_as_neutral():
    data = {**gex(), "total_net_gex": 0, "gamma_coverage_pct": 0}
    with pytest.raises(ValueError, match="Gamma"):
        thetapilot.compact_context("NVDA", {"gex": data}, [])


def test_missing_primary_or_wrong_symbol_is_failure(monkeypatch):
    monkeypatch.setattr(thetapilot, "fetch", lambda *args: gex("TSM"))
    assert not asyncio.run(thetapilot.run_analysis({"symbol": "NVDA"}))["ok"]


def test_theory_tool_dispatch_and_usb_packet(monkeypatch):
    task = bridge.parse_task_event('I (123) ThetaVoice: QUERY {"id":42,"arguments":{"symbol":"NVDA"}}')
    assert task["tool"] == "thetapilot"
    async def analyze(arguments):
        assert arguments["symbol"] == "NVDA"
        return {"ok": True, "summary": "英伟达波动率较高。"}
    monkeypatch.setattr(bridge, "run_analysis", analyze)
    class Device:
        data = bytearray()
        def write(self, data):
            self.data.extend(data)
            return len(data)
        def flush(self):
            pass
    device = Device()
    asyncio.run(bridge.handle_task(task, device, asyncio.Lock(), None))
    prefix, body = device.data.decode().strip().split(" ", 1)
    assert prefix == "STACKCHAN_THETA_RESULT"
    assert json.loads(body)["id"] == 42
    assert json.loads(json.loads(body)["text"])["summary"] == "英伟达波动率较高。"
