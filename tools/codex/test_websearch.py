"""Network-free checks of Firecrawl requests and the device USB boundary."""

import asyncio
import io
import json
from urllib.error import HTTPError

import bridge
import pytest
import websearch


@pytest.mark.parametrize("arguments", [None, {}, {"query": " "}, {"query": 123},
                                         {"query": "中" * 200},
                                         {"query": "AI", "limit": True},
                                         {"query": "AI", "limit": 4}])
def test_invalid_query_does_not_access_network(arguments, monkeypatch):
    monkeypatch.setattr(websearch, "execute", lambda *args: pytest.fail("network called"))
    assert asyncio.run(websearch.run_search(arguments))["ok"] is False


def test_reuses_codex_secret_and_environment_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("STACKCHAN_FIRECRAWL_MCP_URL", raising=False)
    (tmp_path / "config.toml").write_text(
        '[mcp_servers.firecrawl]\nurl="https://mcp.firecrawl.dev/v2/mcp"\n'
        '[mcp_servers.firecrawl.http_headers]\nAuthorization="Bearer local-test-key"\n'
    )
    assert websearch.connection()[1] == {"Authorization": "Bearer local-test-key"}
    monkeypatch.setenv("FIRECRAWL_API_KEY", "environment-test-key")
    assert websearch.connection()[1] == {"Authorization": "Bearer environment-test-key"}


def test_mcp_search_request_and_sse_response(monkeypatch):
    monkeypatch.setattr(websearch, "connection", lambda: (websearch.MCP_URL, {}))

    def request(req, timeout):
        payload = json.loads(req.data)
        assert payload["method"] == "tools/call"
        assert payload["params"]["name"] == "firecrawl_search"
        args = payload["params"]["arguments"]
        assert args["query"] == "SideCopilot 是什么" and args["limit"] == 3
        assert args["sources"] == ["web"] and "scrapeOptions" not in args
        data = {"success": True, "data": {"web": [{
            "title": "Side Copilot", "url": "https://www.sidecopilot.com/",
            "description": "Browser sidebar AI agent",
        }]}}
        result = {"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": json.dumps(data)}],
        }}
        return io.BytesIO((": keepalive\n\nevent: message\ndata: " + json.dumps(result) + "\n\n").encode())

    monkeypatch.setattr(websearch, "urlopen", request)
    result = asyncio.run(websearch.run_search({"query": "SideCopilot 是什么"}))
    assert result["ok"] is True and result["source"] == "firecrawl"
    assert result["results"][0]["url"] == "https://www.sidecopilot.com/"
    assert result["results"][0]["snippet"] == "Browser sidebar AI agent"
    assert result["elapsed_ms"] >= 0


@pytest.mark.parametrize("code", [401, 402, 429, 503])
def test_provider_failure_does_not_leak_remote_error_or_credentials(code, monkeypatch):
    def fail(*args):
        raise HTTPError("https://example.test/secret", code, "Bearer secret-key", {}, None)
    monkeypatch.setattr(websearch, "execute", fail)
    result = asyncio.run(websearch.run_search({"query": "AI"}))
    assert result["ok"] is False
    assert "secret" not in json.dumps(result)


def test_empty_results_are_not_fabricated():
    result = websearch.search_result({"success": True, "data": {"web": []}}, "missing", 3)
    assert result["ok"] is False


def test_markdown_navigation_does_not_consume_snippet_with_raw_urls():
    assert websearch.clean_snippet(
        '# Product [Docs](https://example.test/) ![badge](https://example.test/badge) **AI agent**'
    ) == "Product Docs AI agent"


def test_large_chinese_results_fit_usb_preserving_source_urls():
    rows = [{"title": "标题" * 100, "url": "https://example.test/" + "a" * 450,
             "description": "网页摘要" * 300} for _ in range(3)]
    rows.insert(0, {"title": "invalid", "url": "javascript:alert(1)"})
    result = websearch.search_result({"data": {"web": rows}}, "中" * 170, 3)
    result["id"] = 1234567890
    result["elapsed_ms"] = 20000
    packet = (websearch.RESULT_MARKER + json.dumps(result, ensure_ascii=False) + "\n").encode()
    assert len(packet) <= 4096
    assert len(result["results"]) == 3
    assert all(row["url"] == rows[1]["url"] for row in result["results"])


def test_device_event_runs_firecrawl_and_returns_correlated_packet_without_codex(monkeypatch):
    markers = []

    async def set_searching(job_id, active):
        markers.append((job_id, active))

    async def search(arguments):
        assert markers == [(42, True)]
        assert arguments == {"query": "SideCopilot 是什么", "limit": 3}
        return {"ok": True, "source": "firecrawl", "summary": "找到一条结果。",
                "results": [{"url": "https://www.sidecopilot.com/", "snippet": "AI 侧边栏"}]}

    class Device:
        packet = bytearray()

        def write(self, data):
            self.packet.extend(data)
            return len(data)

        def flush(self):
            pass

    monkeypatch.setattr(bridge, "run_search", search)
    task = bridge.parse_task_event('I (100) WebSearchVoice: QUERY ' + json.dumps({
        "id": 42, "arguments": {"query": "SideCopilot 是什么", "limit": 3},
    }))
    device = Device()
    asyncio.run(bridge.handle_task(task, device, asyncio.Lock(), None, set_searching=set_searching))
    assert markers == [(42, True), (42, False)]
    prefix, payload = device.packet.decode().strip().split(" ", 1)
    assert prefix == "STACKCHAN_WEBSEARCH_RESULT"
    result = json.loads(payload)
    assert result["id"] == 42 and result["ok"] is True
    assert result["results"][0]["url"] == "https://www.sidecopilot.com/"


def test_sse_structured_content_and_tool_error():
    data = {"success": True, "data": {"web": []}}
    assert websearch.decode_rpc(json.dumps({"id": 1, "result": {"structuredContent": data}})) == data
    with pytest.raises(ValueError):
        websearch.decode_rpc(json.dumps({"id": 1, "result": {"isError": True}}))


def test_expired_request_returns_error(monkeypatch):
    def timeout(*args):
        raise TimeoutError()
    monkeypatch.setattr(websearch, "execute", timeout)
    assert "超时" in asyncio.run(websearch.run_search({"query": "AI"}))["summary"]
