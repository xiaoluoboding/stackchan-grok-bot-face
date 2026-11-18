"""Network-free tests: real request payloads, note acknowledgements and USB."""

import asyncio
import io
import json
from urllib.error import HTTPError

import bridge
import flomo_notes as flomo
import pytest


@pytest.mark.parametrize("args", [None, {}, {"action": []}, {"action": "delete"},
    {"action": "create", "text": " "}, {"action": "create", "text": "中" * 700},
    {"action": "update", "text": "new"}, {"action": "read"},
    {"action": "search", "text": "AI", "limit": True},
    {"action": "read", "memo_id": "abc", "offset": -1}])
def test_invalid_actions_do_not_access_credentials_or_network(args, monkeypatch):
    monkeypatch.setattr(flomo, "execute", lambda *args: pytest.fail("network called"))
    assert asyncio.run(flomo.run_notes(args))["ok"] is False


def test_webhook_posts_exact_note_and_preserves_tags(monkeypatch):
    url = "https://flomoapp.com/iwh/123/secret/"
    monkeypatch.setattr(flomo, "credentials", lambda: {"api_url": url, "mcp_token": ""})
    calls = []

    def post(request, timeout):
        calls.append(request)
        assert request.full_url == url
        assert json.loads(request.data) == {"content": "记录想法\n#StackChan/测试"}
        return io.BytesIO(b'{"code":0,"data":{"slug":"memo-123"}}')

    monkeypatch.setattr(flomo, "urlopen", post)
    result = asyncio.run(flomo.run_notes({"action": "create", "text": "记录想法\n#StackChan/测试"}))
    assert result["ok"] is True and result["memo_id"] == "memo-123"
    assert "secret" not in json.dumps(result)
    assert len(calls) == 1


@pytest.mark.parametrize("body", [b'{"code":-1}', b'{"code":true}', b'{}'])
def test_http_success_is_not_enough_to_claim_note_saved(body, monkeypatch):
    monkeypatch.setattr(flomo, "credentials", lambda: {
        "api_url": "https://flomoapp.com/iwh/123/secret/", "mcp_token": ""})
    monkeypatch.setattr(flomo, "urlopen", lambda *args, **kw: io.BytesIO(body))
    assert asyncio.run(flomo.run_notes({"action": "create", "text": "test"}))["ok"] is False


def test_timeout_never_retries_write_and_reports_uncertainty(monkeypatch):
    calls = []

    def timeout(*args):
        calls.append(args)
        raise TimeoutError("https://flomoapp.com/iwh/secret")

    monkeypatch.setattr(flomo, "execute", timeout)
    result = asyncio.run(flomo.run_notes({"action": "create", "text": "test"}))
    assert "未确认" in result["summary"] and "重复" in result["summary"]
    assert "secret" not in json.dumps(result)
    assert len(calls) == 1


@pytest.mark.parametrize("code", [401, 429, 503])
def test_error_does_not_echo_secret_webhook_url(code, monkeypatch):
    def rejected(*args):
        raise HTTPError("https://flomoapp.com/iwh/secret/", code, "secret-key", {}, None)
    monkeypatch.setattr(flomo, "execute", rejected)
    result = asyncio.run(flomo.run_notes({"action": "create", "text": "test"}))
    assert result["ok"] is False and "secret" not in json.dumps(result)


@pytest.mark.parametrize("action,kwargs", [("search", {"text": "学习"}),
                                         ("read", {"memo_id": "note-1"}),
                                         ("update", {"memo_id": "note-1", "text": "new"})])
def test_webhook_alone_cannot_read_or_update(action, kwargs, monkeypatch):
    monkeypatch.setattr(flomo, "credentials", lambda: {
        "api_url": "https://flomoapp.com/iwh/123/secret/", "mcp_token": ""})
    monkeypatch.setattr(flomo, "urlopen", lambda *args, **kw: pytest.fail("unexpected request"))
    result = asyncio.run(flomo.run_notes({"action": action, **kwargs}))
    assert result["ok"] is False and "MCP Token" in result["summary"]


def test_mcp_session_initializes_then_searches_using_advertised_contract(monkeypatch):
    monkeypatch.setattr(flomo, "credentials", lambda: {"api_url": "", "mcp_token": "local-token"})
    methods = []

    class Response(io.BytesIO):
        def __init__(self, data):
            super().__init__(data)
            self.headers = {"Mcp-Session-Id": "test-session"}

    def post(request, timeout):
        payload = json.loads(request.data)
        methods.append(payload["method"])
        if len(methods) > 1:
            assert request.get_header("Mcp-session-id") == "test-session"
        if payload["method"] == "notifications/initialized":
            return Response(b"")
        if payload["method"] == "initialize":
            data = {"protocolVersion": "2024-11-05"}
        elif payload["method"] == "tools/list":
            data = {"tools": [{"name": "memo_search", "inputSchema": {
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
            }}]}
        else:
            assert payload["params"] == {"name": "memo_search", "arguments": {"query": "学习", "limit": 3}}
            data = {"content": [{"type": "text", "text": '{"memos":[{"id":"abc","content":"学习笔记"}]}'}]}
        result = {"jsonrpc": "2.0", "id": payload["id"], "result": data}
        return Response(("event: message\ndata: " + json.dumps(result) + "\n\n").encode())

    monkeypatch.setattr(flomo, "urlopen", post)
    result = asyncio.run(flomo.run_notes({"action": "search", "text": "学习"}))
    assert result["ok"] is True and "学习笔记" in result["text"]
    assert methods == ["initialize", "notifications/initialized", "tools/list", "tools/call"]


def test_unknown_required_schema_rejects_before_writing():
    tool = {"inputSchema": {"properties": {"content": {"type": "string"}},
                            "required": ["content", "new_required_field"]}}
    with pytest.raises(flomo.FlomoError):
        flomo.tool_arguments(tool, flomo.validate({"action": "create", "text": "test"}))


def test_read_id_is_an_array_when_official_schema_requests_array():
    tool = {"inputSchema": {"properties": {"memo_ids": {"type": "array"}}, "required": ["memo_ids"]}}
    assert flomo.tool_arguments(tool, flomo.validate({"action": "read", "memo_id": "abc"})) == {"memo_ids": ["abc"]}


def test_long_read_paginates_without_exceeding_usb_limit():
    full = '"汉字\\' * 2000
    args = flomo.validate({"action": "read", "memo_id": "abc"})
    result = {"content": [{"type": "text", "text": full}]}
    first = flomo.content_result(result, args)
    assert first["truncated"] is True
    second = flomo.content_result(result, {**args, "offset": first["next_offset"]})
    assert first["text"] + second["text"] == full[:len(first["text"] + second["text"])]
    packet = flomo.RESULT_MARKER + json.dumps({**first, "id": 1234567890, "elapsed_ms": 25000}, ensure_ascii=False) + "\n"
    assert len(packet.encode()) < 4096


def test_bridge_dispatches_note_without_codex_and_correlates_usb_reply(monkeypatch):
    calls = []

    async def notes(arguments):
        calls.append(arguments)
        return {"ok": True, "summary": "已保存到 flomo。"}

    class Device:
        data = bytearray()

        def write(self, data):
            self.data.extend(data)
            return len(data)

        def flush(self):
            pass

    monkeypatch.setattr(bridge, "run_notes", notes)
    task = bridge.parse_task_event('I (100) FlomoVoice: QUERY {"id":42,"arguments":{"action":"create","text":"记录想法"}}')
    device = Device()
    asyncio.run(bridge.handle_task(task, device, asyncio.Lock(), None))
    prefix, raw = device.data.decode().strip().split(" ", 1)
    assert prefix == "STACKCHAN_FLOMO_RESULT"
    assert json.loads(raw)["id"] == 42 and json.loads(raw)["ok"] is True
    assert len(calls) == 1
