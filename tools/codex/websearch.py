"""Read-only Firecrawl MCP search for self.websearch.execute (no Codex turn)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MCP_URL = "https://mcp.firecrawl.dev/v2/mcp"
RESULT_MARKER = "STACKCHAN_WEBSEARCH_RESULT "


def failure(message: str) -> dict:
    return {"ok": False, "source": "firecrawl", "summary": message}


def validate_request(arguments: dict) -> tuple[str, int]:
    if not isinstance(arguments, dict):
        raise TypeError("请提供网络搜索关键词。")
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip() or len(query.encode()) > 512:
        raise ValueError("请提供非空搜索关键词，长度不超过 512 字节。")
    limit = arguments.get("limit", 3)
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError("搜索结果数量必须为一到三条。")
    return query.strip(), limit


def connection() -> tuple[str, dict[str, str]]:
    # Reuse the user's existing Firecrawl connection without copying the secret
    # into the project. An environment key takes precedence for other machines.
    config = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    server = {}
    if config.exists():
        server = tomllib.loads(config.read_text()).get("mcp_servers", {}).get("firecrawl", {})
    url = os.getenv("STACKCHAN_FIRECRAWL_MCP_URL") or server.get("url") or MCP_URL
    key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    headers = {}
    if key:
        headers["Authorization"] = "Bearer " + key
    else:
        auth = server.get("http_headers", {}).get("Authorization")
        token_var = server.get("bearer_token_env_var")
        if token_var and os.getenv(token_var):
            auth = "Bearer " + os.environ[token_var]
        if auth:
            headers["Authorization"] = auth
    return url, headers


def decode_rpc(body: str) -> dict:
    if body.lstrip().startswith("{"):
        response = json.loads(body)
    else:
        # Streamable HTTP can return SSE, including comments/notifications.
        messages = [json.loads(block.removeprefix(" "))
                    for line in body.splitlines()
                    if line.startswith("data:") and (block := line[5:]).strip()]
        response = next(item for item in messages if item.get("id") == 1)
    if response.get("error"):
        raise ValueError("Firecrawl MCP returned an error")
    result = response["result"]
    if result.get("isError"):
        raise ValueError("Firecrawl search failed")
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for item in result.get("content", []):
        if item.get("type") == "text":
            data = json.loads(item["text"])
            if isinstance(data, dict):
                return data
    raise ValueError("Firecrawl returned no search data")


def search_result(data: dict, query: str, limit: int) -> dict:
    if data.get("success") is False:
        return failure("Firecrawl 搜索失败，请检查账号额度或稍后重试。")
    web = data.get("data", {})
    rows = web.get("web", []) if isinstance(web, dict) else []
    results = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get("url", "")
        if not isinstance(url, str) or len(url.encode()) > 512:
            continue
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            continue
        results.append({
            "title": " ".join(str(row.get("title") or parsed.hostname).split())[:80],
            "url": url,
            "snippet": clean_snippet(str(row.get("description") or ""))[:220],
        })
        if len(results) == limit:
            break
    if not results:
        return failure("没有找到可引用的网页结果，请换一组关键词。")
    result = {
        "ok": True, "source": "firecrawl", "query": query,
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "results": results, "summary": f"找到 {len(results)} 条网页搜索结果。",
    }
    # Leave room for the result marker, job ID, and JSON escaping on 4 KB USB.
    # Keep valid source URLs even when Chinese snippets need shortening.
    while len(json.dumps(result, ensure_ascii=False).encode()) > 3400:
        longest = max(results, key=lambda item: len(item["snippet"]))
        if longest["snippet"]:
            longest["snippet"] = longest["snippet"][:-30]
        else:
            results.pop()
        result["summary"] = f"找到 {len(results)} 条网页搜索结果。"
    return result


def clean_snippet(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#*`>]", "", text)
    return " ".join(text.split())


def execute(arguments: dict, timeout: float = 20) -> dict:
    query, limit = validate_request(arguments)
    url, auth = connection()
    request = Request(url, data=json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "firecrawl_search", "arguments": {
            "query": query, "limit": limit, "sources": ["web"],
            "domainTools": False, "objective": "Answer the user's spoken web information query.",
        }},
    }).encode(), headers={**auth, "Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"})
    started = time.monotonic()
    with urlopen(request, timeout=timeout) as response:
        data = decode_rpc(response.read().decode())
    result = search_result(data, query, limit)
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


async def run_search(arguments: dict, timeout: float = 20) -> dict:
    try:
        validate_request(arguments)
        return await asyncio.wait_for(asyncio.to_thread(execute, arguments, timeout), timeout + 1)
    except (ValueError, TypeError):
        # Validation messages contain only local fixed text, not remote bodies.
        try:
            validate_request(arguments)
        except (ValueError, TypeError) as error:
            return failure(str(error))
        return failure("Firecrawl 未返回有效搜索结果，请稍后重试。")
    except HTTPError as error:
        if error.code in {401, 403}:
            return failure("Firecrawl 授权失败，请检查电脑上的 API Key。")
        if error.code in {402, 429}:
            return failure("Firecrawl 额度不足或请求过于频繁，请稍后重试。")
        return failure("Firecrawl 服务暂时不可用，请稍后重试。")
    except (TimeoutError, URLError):
        return failure("Firecrawl 网络搜索超时或连接失败，请稍后重试。")
    except Exception:  # noqa: BLE001 - external tool boundary must return a safe protocol error
        # Never echo exceptions containing request headers or credential values.
        return failure("网络搜索未能完成，请检查电脑上的 Firecrawl 配置。")


if __name__ == "__main__":
    import sys

    result = asyncio.run(run_search(json.loads(sys.argv[1])))
    print(RESULT_MARKER + json.dumps(result, ensure_ascii=False), flush=True)
