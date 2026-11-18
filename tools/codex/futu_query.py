"""Read-only OpenD worker for the device's self.futu.execute tool.

Run in a subprocess: Futu logs to stdout and a stalled SDK must not block USB.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sys
from pathlib import Path

RESULT_MARKER = "STACKCHAN_FUTU_RESULT "
CODE_PATTERN = re.compile(r"(?:HK|US|SH|SZ)\.[A-Z0-9][A-Z0-9.-]{0,23}\Z")


def validate_request(arguments: dict) -> list[str]:
    if not isinstance(arguments, dict) or arguments.get("action") != "get_market_snapshot":
        raise ValueError("目前仅支持 get_market_snapshot 行情查询。")
    codes = arguments.get("codes")
    # Device MCP supports string properties; also accept a list for local callers.
    if isinstance(codes, str):
        codes = codes.split(",")
    if not isinstance(codes, list) or not 1 <= len(codes) <= 3:
        raise ValueError("请提供一到三个股票代码，例如 HK.00700。")
    if not all(isinstance(code, str) for code in codes):
        raise ValueError("股票代码必须是字符串。")
    codes = list(dict.fromkeys(code.strip().upper() for code in codes))
    if not all(CODE_PATTERN.fullmatch(code) for code in codes):
        raise ValueError("请使用带市场前缀的代码，例如 HK.00700 或 US.AAPL。")
    return codes


def number(value) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def snapshot_result(records: list[dict], codes: list[str]) -> dict:
    rows = {row["code"]: row for row in records}
    quotes, sentences = [], []
    for code in codes:
        row = rows.get(code)
        if row is None:
            raise RuntimeError(f"OpenD 未返回 {code} 的行情。")
        last, previous = number(row.get("last_price")), number(row.get("prev_close_price"))
        if last is None or last <= 0:
            raise RuntimeError(f"{code} 暂无有效的最新成交价。")
        change = (last / previous - 1) * 100 if previous and previous > 0 else None
        # Snapshot's update_time is exchange-local, not the Mac's timezone.
        timezone = "America/New_York" if code.startswith("US.") else "Asia/Shanghai"
        quote = {
            "code": code, "name": str(row.get("name") or code),
            "last_price": last, "prev_close_price": previous,
            "change_percent": round(change, 4) if change is not None else None,
            "update_time": str(row.get("update_time") or "未知"),
            "timezone": timezone,
        }
        quotes.append(quote)
        movement = ""
        if change is not None:
            movement = "，较昨收持平" if abs(change) < 0.005 else (
                f"，较昨收{'上涨' if change > 0 else '下跌'}百分之{abs(change):.2f}"
            )
        sentences.append(f"{quote['name']}最新成交价{last:g}{movement}。")
    return {
        "ok": True, "source": "local_opend", "action": "get_market_snapshot",
        "quotes": quotes,
        "summary": "".join(sentences),
    }


def execute(arguments: dict) -> dict:
    codes = validate_request(arguments)
    from futu import RET_OK, OpenQuoteContext

    context = OpenQuoteContext(
        host=os.getenv("FUTU_OPEND_HOST", "127.0.0.1"),
        port=int(os.getenv("FUTU_OPEND_PORT", "11111")),
    )
    try:
        ret, data = context.get_market_snapshot(codes)
        if ret != RET_OK:
            raise RuntimeError(f"OpenD 行情查询失败：{data}")
        return snapshot_result(data.to_dict("records"), codes)
    finally:
        context.close()


def failure(message: str) -> dict:
    return {"ok": False, "summary": message}


async def run_query(arguments: dict, timeout: float = 12) -> dict:
    try:
        validate_request(arguments)
    except ValueError as error:
        return failure(str(error))
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).resolve()), json.dumps(arguments),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
        for line in reversed(stdout.decode(errors="replace").splitlines()):
            if line.startswith(RESULT_MARKER):
                result = json.loads(line[len(RESULT_MARKER):])
                if isinstance(result, dict) and isinstance(result.get("ok"), bool):
                    return result
        return failure("富途查询程序未返回结果，请检查电脑上的 OpenD 和 futu-api 安装。")
    except TimeoutError:
        return failure("本地 OpenD 行情查询超时，请检查 OpenD 是否已登录并可用。")
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


if __name__ == "__main__":
    try:
        result = execute(json.loads(sys.argv[1]))
    except Exception as error:  # noqa: BLE001 - worker boundary always returns a protocol result
        result = failure(f"本地 OpenD 查询失败：{error}")
    print(RESULT_MARKER + json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
