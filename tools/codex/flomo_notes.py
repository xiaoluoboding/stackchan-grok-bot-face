"""flomo notes: official incoming webhook for create, official MCP for read/write."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MCP_URL = "https://flomoapp.com/mcp"
RESULT_MARKER = "STACKCHAN_FLOMO_RESULT "
TOOL_NAMES = {"create": "memo_create", "search": "memo_search",
              "read": "memo_batch_get", "update": "memo_update"}
UNCERTAIN = "flomo 写入结果未确认，请先在 flomo 检查，避免重复保存。"


class FlomoError(Exception):
    """An operation was explicitly rejected; messages are local and credential-free."""


def failure(message: str) -> dict:
    return {"ok": False, "source": "flomo", "summary": message}


def validate(arguments: dict) -> dict:
    if (not isinstance(arguments, dict) or not isinstance(arguments.get("action"), str)
            or arguments["action"] not in TOOL_NAMES):
        raise ValueError("flomo 仅支持 create、search、read、update。")
    action = arguments["action"]
    text, memo_id = arguments.get("text", ""), arguments.get("memo_id", "")
    if not isinstance(text, str) or len(text.encode()) > 2048:
        raise ValueError("笔记内容或搜索关键词不能超过 2048 UTF-8 字节。")
    if not isinstance(memo_id, str) or len(memo_id.encode()) > 128:
        raise ValueError("笔记 ID 必须是字符串，最多 128 字节。")
    if action in {"create", "search", "update"} and not text.strip():
        raise ValueError("请提供笔记正文或搜索关键词。")
    if action in {"read", "update"} and not memo_id.strip():
        raise ValueError("请先搜索笔记，提供准确的笔记 ID。")
    limit, offset = arguments.get("limit", 3), arguments.get("offset", 0)
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError("返回笔记数量必须为一到三条。")
    if type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError("读取偏移量必须为零到十万的整数。")
    return {"action": action, "text": text, "memo_id": memo_id,
            "limit": limit, "offset": offset}


def credentials() -> dict:
    path = Path(os.getenv("STACKCHAN_FLOMO_CONFIG", str(Path.home() / ".config/stackchan/flomo.json")))
    config = json.loads(path.read_text()) if path.exists() else {}
    return {"api_url": os.getenv("FLOMO_API_URL") or config.get("api_url", ""),
            "mcp_token": os.getenv("FLOMO_MCP_TOKEN") or config.get("mcp_token", "")}


def decode_rpc(body: str, request_id: int) -> dict:
    if body.lstrip().startswith("{"):
        message = json.loads(body)
    else:
        messages = [json.loads(line[5:].strip()) for line in body.splitlines()
                    if line.startswith("data:") and line[5:].strip()]
        message = next(item for item in messages if item.get("id") == request_id)
    if message.get("error"):
        raise FlomoError("flomo MCP 拒绝了请求，请检查参数或 Token 权限。")
    result = message["result"]
    if result.get("isError"):
        raise FlomoError("flomo 未能完成操作，请检查笔记 ID、Token 权限或会员状态。")
    return result


class MCP:
    def __init__(self, token: str, deadline: float):
        self.deadline = deadline
        self.headers = {"Authorization": "Bearer " + token,
                        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        self.sequence = 0

    def rpc(self, method: str, params: dict, notify: bool = False) -> dict:
        self.sequence += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            payload["id"] = self.sequence
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        request = Request(MCP_URL, data=json.dumps(payload).encode(), headers=self.headers)
        with urlopen(request, timeout=remaining) as response:
            if session := response.headers.get("Mcp-Session-Id"):
                self.headers["Mcp-Session-Id"] = session
            body = response.read().decode()
        return {} if notify else decode_rpc(body, self.sequence)

    def initialize(self) -> list[dict]:
        result = self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                       "clientInfo": {"name": "stackchan-flomo", "version": "1.0"}})
        self.headers["MCP-Protocol-Version"] = result.get("protocolVersion", "2024-11-05")
        self.rpc("notifications/initialized", {}, notify=True)
        return self.rpc("tools/list", {}).get("tools", [])


def tool_arguments(tool: dict, args: dict) -> dict:
    """Map only fields actually advertised by the official server at runtime."""
    schema = tool.get("inputSchema", {})
    properties = schema.get("properties", {})
    result = {}

    def put(names: tuple[str, ...], value, array: bool = False):
        for name in names:
            if name in properties:
                kind = properties[name].get("type")
                if array and kind == "array":
                    result[name] = [value]
                elif not array and kind == "string":
                    result[name] = value
                else:
                    continue
                return
        raise FlomoError("flomo MCP 参数定义与当前桥接不兼容，请更新桥接。")

    if args["action"] in {"create", "update"}:
        put(("content",), args["text"])
    if args["action"] == "search":
        put(("query", "keyword"), args["text"])
        if properties.get("limit", {}).get("type") == "integer":
            result["limit"] = args["limit"]
    if args["action"] == "read":
        put(("memo_ids", "ids"), args["memo_id"], array=True)
    if args["action"] == "update":
        put(("memo_id", "id"), args["memo_id"])
    for name in schema.get("required", []):
        if name not in result:
            if "default" in properties.get(name, {}):
                result[name] = properties[name]["default"]
            else:
                raise FlomoError("flomo MCP 需要额外参数，请更新桥接后再操作。")
    return result


def content_result(result: dict, args: dict) -> dict:
    if isinstance(result.get("structuredContent"), dict):
        raw = json.dumps(result["structuredContent"], ensure_ascii=False)
    else:
        raw = "\n".join(item["text"] for item in result.get("content", [])
                        if item.get("type") == "text" and isinstance(item.get("text"), str))
    if not raw.strip():
        raise FlomoError("flomo 未返回可确认的操作结果，请在 flomo 中查看。")
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, dict) and (data.get("ok") is False or data.get("success") is False):
        raise FlomoError("flomo 未确认操作成功，请在 flomo 中检查结果。")
    # Preserve the server's exact content rather than guessing its response shape.
    # Longer notes can be read in additional chunks using the returned offset.
    offset = args["offset"] if args["action"] == "read" else 0
    page = raw[offset:].encode()[:2600].decode(errors="ignore")
    truncated = offset + len(page) < len(raw)
    output = {"ok": True, "source": "flomo_mcp", "action": args["action"],
              "text": page, "truncated": truncated,
              "next_offset": offset + len(page) if truncated else None,
              "summary": {"create": "flomo 已返回保存结果。", "update": "flomo 已返回更新结果。",
                          "search": "flomo 已返回搜索结果。", "read": "flomo 已返回笔记内容。"}[args["action"]]}
    while len(json.dumps(output, ensure_ascii=False).encode()) > 3400:
        page = page[:-50]
        output.update(text=page, truncated=True, next_offset=offset + len(page))
    return output


def create_webhook(url: str, text: str, timeout: float) -> dict:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "flomoapp.com" or not parsed.path.startswith("/iwh/"):
        raise FlomoError("请配置 flomo 官方提供的完整 HTTPS API URL。")
    request = Request(url, data=json.dumps({"content": text}).encode(),
                      headers={"Content-Type": "application/json"})
    # Never retry this POST: a lost response may still have created the note.
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode())
    if type(data.get("code")) is not int or data["code"] != 0:
        raise FlomoError("flomo 拒绝了保存，请检查 API URL、会员状态或每日额度。")
    result = {"ok": True, "source": "flomo_api", "action": "create", "summary": "已保存到 flomo。"}
    memo = data.get("data")
    if isinstance(memo, dict):
        memo_id = memo.get("slug") or memo.get("id")
        if isinstance(memo_id, (str, int)):
            result["memo_id"] = str(memo_id)[:128]
    return result


def execute(arguments: dict, timeout: float = 25) -> dict:
    args = validate(arguments)
    auth = credentials()
    started = time.monotonic()
    if args["action"] == "create" and auth["api_url"]:
        result = create_webhook(auth["api_url"], args["text"], timeout)
    elif auth["mcp_token"]:
        client = MCP(auth["mcp_token"], started + timeout)
        name = TOOL_NAMES[args["action"]]
        tools = client.initialize()
        tool = next((tool for tool in tools if tool.get("name") == name), None)
        if tool is None:
            raise FlomoError("flomo MCP 未开放此操作，请检查会员及 Token 权限。")
        params = tool_arguments(tool, args)
        result = content_result(client.rpc("tools/call", {"name": name, "arguments": params}), args)
    else:
        return failure("尚未配置 flomo API URL。" if args["action"] == "create" else
                       "传统 flomo API URL 只支持新建笔记；搜索、读取和更新需要另配 MCP Token。")
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


async def run_notes(arguments: dict, timeout: float = 25) -> dict:
    try:
        args = validate(arguments)
    except (ValueError, TypeError) as error:
        return failure(str(error))
    writing = args["action"] in {"create", "update"}
    try:
        return await asyncio.wait_for(asyncio.to_thread(execute, args, timeout), timeout + 1)
    except FlomoError as error:
        return failure(str(error))
    except HTTPError as error:
        if error.code in {401, 403}:
            return failure("flomo 授权失败，请检查本地 API URL 或 MCP Token。")
        if error.code == 429:
            return failure("flomo 请求过于频繁或超过额度，请稍后重试。")
        return failure(UNCERTAIN if writing else "flomo 服务暂时不可用。")
    except (TimeoutError, URLError):
        return failure(UNCERTAIN if writing else "flomo 读取超时或连接失败，请稍后重试。")
    except Exception:  # noqa: BLE001 - credentials can occur in raw URLs and exception messages
        return failure(UNCERTAIN if writing else "未能读取 flomo，请检查本地配置和服务状态。")


if __name__ == "__main__":
    import sys

    print(RESULT_MARKER + json.dumps(asyncio.run(run_notes(json.loads(sys.argv[1]))), ensure_ascii=False))
