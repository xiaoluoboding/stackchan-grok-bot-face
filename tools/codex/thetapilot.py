"""Compact, source-backed /gex dashboard context for XiaoZhi's analysis tool."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
from collections import defaultdict
from urllib.parse import urlsplit
from urllib.request import urlopen


def validate_request(arguments: dict) -> str:
    symbol = arguments.get("symbol") if isinstance(arguments, dict) else None
    if not isinstance(symbol, str):
        raise TypeError("请提供一个美股代码，例如 NVDA 或 US.NVDA。")
    symbol = symbol.strip().upper().removeprefix("US.")
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol) or symbol.startswith(("HK.", "SH.", "SZ.")):
        raise ValueError("ThetaPilot 标的分析目前支持美股代码，例如 NVDA。")
    return symbol


def api_url() -> str:
    url = os.getenv("STACKCHAN_THETAPILOT_URL", "http://127.0.0.1:8000").rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ThetaPilot URL 必须是本机 HTTP 服务地址。")
    return url


def fetch(base: str, path: str) -> dict:
    with urlopen(base + "/v1/" + path, timeout=9) as response:
        raw = response.read(4_000_001)
    if len(raw) > 4_000_000:
        raise ValueError("ThetaPilot 数据过大。")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError("ThetaPilot 返回格式无效。")
    return data


def finite(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return round(result, 4) if math.isfinite(result) else None


def pick(data: dict, fields: tuple[str, ...]) -> dict:
    return {key: data[key] for key in fields if key in data}


def compact_context(symbol: str, panels: dict, unavailable: list[str]) -> dict:
    gex = panels["gex"]
    spot = finite(gex.get("stock_price"))
    net = finite(gex.get("total_net_gex"))
    if spot is None or spot <= 0 or net is None:
        raise ValueError("ThetaPilot 暂无有效的 GEX 数据。")
    if gex.get("contracts_used") == 0 or gex.get("gamma_coverage_pct") == 0:
        raise ValueError("ThetaPilot 缺少有效期权 Gamma 数据，无法解读敞口。")
    strikes, expiries = defaultdict(float), defaultdict(float)
    for cell in gex.get("cells", []):
        strike, exposure = finite(cell.get("strike")), finite(cell.get("net_gex"))
        if strike is None or exposure is None:
            continue
        strikes[strike] += exposure
        expiries[str(cell.get("expiration", "未知"))] += exposure
    # Show nearby concentrations on BOTH sides, plus the largest overall. These
    # are concentrations, not mechanically labelled support/resistance targets.
    below = sorted((key for key in strikes if key <= spot), reverse=True)[:2]
    above = sorted(key for key in strikes if key > spot)[:2]
    strongest = sorted(strikes, key=lambda key: abs(strikes[key]), reverse=True)[:1]
    levels = sorted(set(below + above + strongest))
    context = {
        "gex": {
            **pick(gex, ("as_of", "session_date", "mvc_strike", "mvc_expiration",
                         "gamma_coverage_pct", "oi_coverage_pct", "contracts_used")),
            "stock_price": spot, "net_gex": net, "unit": "USD per 1% underlying move",
            "sign_convention": "Call + / Put -; estimated, not observed dealer positioning",
            "nearby_and_largest_strikes": [
                {"strike": key, "net_gex": round(strikes[key], 2)} for key in levels
            ],
            "largest_expirations": [
                {"expiration": key, "net_gex": round(expiries[key], 2)}
                for key in sorted(expiries, key=lambda key: abs(expiries[key]), reverse=True)[:3]
            ],
        },
    }
    if "iv_stats" in panels:
        context["iv_stats"] = pick(panels["iv_stats"], ("current_iv", "iv_rank", "iv_percentile"))
        context["iv_stats"]["unit"] = "percent"
    if "iv_term_structure" in panels:
        context["iv_term_structure"] = pick(panels["iv_term_structure"],
                                            ("shape", "front_iv", "back_iv", "complete"))
    if "vix_term_structure" in panels:
        context["vix_term_structure"] = pick(panels["vix_term_structure"], ("vix", "vix3m", "ratio", "regime"))
    if "volatility_smile" in panels:
        smiles = []
        for series in panels["volatility_smile"].get("series", [])[:2]:
            points = [point for point in series.get("points", []) if finite(point.get("strike")) is not None]
            nearest = sorted(points, key=lambda point: abs(float(point["strike"]) - spot))[:4]
            smiles.append({"expiration": series.get("expiration"), "near_atm": [
                pick(point, ("strike", "iv", "option_type")) for point in nearest
            ]})
        context["volatility_smile"] = smiles
    if "gex_interval" in panels:
        interval = panels["gex_interval"]
        context["gex_interval"] = {
            **pick(interval, ("session_date", "interval_minutes", "is_estimated", "method")),
            "largest_bubbles": sorted(interval.get("bubbles", []),
                                      key=lambda row: abs(finite(row.get("net_gex")) or 0), reverse=True)[:2],
        }
    result = {
        "ok": True, "source": "thetapilot_gex", "symbol": symbol,
        "name": str(gex.get("name") or symbol), "context": context,
        "unavailable_panels": unavailable,
        "summary": f"{gex.get('name') or symbol}参考价{spot:g}，净Gamma敞口估算约{net / 1e8:.2f}亿美元每百分之一波动。",
        "interpretation": "Concentrations/MVC are not directional forecasts. Missing panels are unavailable, not neutral. "
                          "Explain key levels, IV and risk in three short Chinese sentences. Omit timestamps unless asked.",
    }
    # Keep room for serial marker and ID in the firmware's 4 KB receive line.
    for key in ("gex_interval", "volatility_smile"):
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) <= 3400:
            break
        if key in context:
            del context[key]
            result.setdefault("omitted_for_voice", []).append(key)
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > 3400:
        raise ValueError("ThetaPilot 分析数据超过语音通道限制。")
    return result


async def run_analysis(arguments: dict) -> dict:
    try:
        symbol, base = validate_request(arguments), api_url()
        paths = {
            "gex": f"gex-heatmap/{symbol}?count=15", "iv_stats": f"iv-stats/{symbol}",
            "volatility_smile": f"volatility-smile/{symbol}?count=3",
            "iv_term_structure": f"iv-term-structure/{symbol}?count=15",
            "vix_term_structure": "vix-regime", "gex_interval": f"gex-interval/{symbol}?count=15",
        }
        async with asyncio.timeout(12):
            responses = await asyncio.gather(
                *(asyncio.to_thread(fetch, base, path) for path in paths.values()), return_exceptions=True,
            )
        panels, unavailable = {}, []
        for key, data in zip(paths, responses, strict=True):
            if isinstance(data, Exception) or (key != "vix_term_structure" and data.get("symbol") != symbol):
                unavailable.append(key)
            else:
                panels[key] = data
        if "gex" not in panels:
            raise ValueError("ThetaPilot 暂时无法提供该标的的 GEX 数据，请检查服务和行情权限。")
        return compact_context(symbol, panels, unavailable)
    except (ValueError, TypeError, TimeoutError, OSError):
        return {"ok": False, "source": "thetapilot_gex",
                "summary": "ThetaPilot 标的分析暂不可用，请检查本地服务、标的代码与行情权限后重试。"}
