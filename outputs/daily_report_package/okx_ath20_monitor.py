#!/usr/bin/env python3
"""Alert when an OKX USDT perpetual approaches 20% above its listing-history low."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


BASE_URL = os.environ.get("OKX_API_BASE", "https://www.okx.com").rstrip("/")
STATE_PATH = Path(os.environ.get("ATH20_STATE_PATH", "okx_ath20_state.json"))
WATCHLIST_DEFAULT = (
    "BTCUSDT,ETHUSDT,ARUSDT,IRYSUSDT,LABUSDT,HYPEUSDT,ZECUSDT,"
    "BNBUSDT,SOLUSDT,APTUSDT,TRUMPUSDT,DOGEUSDT"
)
WATCHLIST_ENV = os.environ.get("OKX_WATCHLIST", WATCHLIST_DEFAULT).strip()
TIMEOUT = 20
PAGE_SIZE = 100
MAX_HISTORY_PAGES = 200
TZ = ZoneInfo("Africa/Algiers")


class Api:
    def __init__(self) -> None:
        self.last_call = 0.0

    def get(self, path: str, params: dict[str, str] | None = None) -> list[list[str]] | list[dict]:
        elapsed = time.monotonic() - self.last_call
        if elapsed < 0.12:
            time.sleep(0.12 - elapsed)
        query = urllib.parse.urlencode(params or {})
        url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
        req = urllib.request.Request(url, headers={"User-Agent": "OKX-ATH20-ReadOnly/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
        self.last_call = time.monotonic()
        if body.get("code") != "0":
            raise RuntimeError(f"OKX {path}: code={body.get('code')} {body.get('msg', '')}")
        return body.get("data", [])


def load_state() -> dict:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def instrument_id(raw: str) -> str:
    symbol = raw.strip().upper()
    if symbol.endswith("-USDT-SWAP"):
        return symbol
    symbol = symbol.removesuffix("USDT").removesuffix("-USDT")
    return f"{symbol}-USDT-SWAP"


def history_low(api: Api, inst_id: str) -> float:
    """Find the minimum daily low across OKX's available history for this swap."""
    after: str | None = None
    lowest: float | None = None
    oldest_seen: int | None = None
    for _ in range(MAX_HISTORY_PAGES):
        params = {"instId": inst_id, "bar": "1D", "limit": str(PAGE_SIZE)}
        if after is not None:
            params["after"] = after
        rows = api.get("/api/v5/market/history-candles", params)
        if not rows:
            break
        timestamps: list[int] = []
        for row in rows:
            if len(row) < 5:
                continue
            try:
                timestamps.append(int(row[0]))
                low = float(row[3])
                if low > 0 and (lowest is None or low < lowest):
                    lowest = low
            except (TypeError, ValueError):
                continue
        if not timestamps:
            break
        page_oldest = min(timestamps)
        if oldest_seen is not None and page_oldest >= oldest_seen:
            break
        oldest_seen = page_oldest
        if len(rows) < PAGE_SIZE:
            break
        after = str(page_oldest)
    else:
        raise RuntimeError(f"历史日线超过安全分页上限：{inst_id}")
    if lowest is None:
        raise RuntimeError(f"OKX未返回可用历史日线：{inst_id}")
    return lowest


def current_daily_low(api: Api, inst_id: str) -> float | None:
    rows = api.get("/api/v5/market/candles", {"instId": inst_id, "bar": "1D", "limit": "1"})
    if not rows or len(rows[0]) < 5:
        return None
    value = float(rows[0][3])
    return value if value > 0 else None


def send_wxpusher(content: str) -> None:
    spt = os.environ.get("WXPUSHER_SPT", "").strip()
    if not spt:
        print("未配置 WXPUSHER_SPT；提醒内容仅写入本次 Actions 日志。")
        return
    payload = json.dumps({
        "spt": spt,
        "content": content,
        "summary": "OKX 自选币接近历史低位",
        "contentType": 1,
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://wxpusher.zjiecode.com/api/send/message/simple-push",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "OKX-ATH20-ReadOnly/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        result = json.loads(response.read().decode("utf-8"))
    if str(result.get("code")) != "1000" or result.get("success") is False:
        raise RuntimeError(f"WxPusher返回失败 code={result.get('code')} msg={result.get('msg', '')}")
    print("WxPusher已受理推送请求；受理不代表微信最终送达。")


def main() -> None:
    symbols = list(dict.fromkeys(instrument_id(x) for x in WATCHLIST_ENV.split(",") if x.strip()))
    if not symbols:
        raise RuntimeError("OKX_WATCHLIST为空")
    api = Api()
    tickers = api.get("/api/v5/market/tickers", {"instType": "SWAP"})
    ticker_by_id = {row.get("instId"): row for row in tickers}
    state = load_state()
    alerts: list[str] = []
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    for inst_id in symbols:
        ticker = ticker_by_id.get(inst_id)
        if ticker is None:
            print(f"未核实/无可交易Ticker：{inst_id}")
            continue
        try:
            price = float(ticker.get("last", "0"))
            if price <= 0:
                raise RuntimeError("最新价无效")
            record = state.get(inst_id, {})
            low = float(record["all_time_low"]) if record.get("all_time_low") else history_low(api, inst_id)
            today_low = current_daily_low(api, inst_id)
            if today_low is not None:
                low = min(low, today_low)
            threshold = low * 1.20
            was_triggered = bool(record.get("triggered", False))
            triggered = was_triggered
            if price <= threshold and not was_triggered:
                alerts.append(
                    f"{inst_id}: 最新价 {price:.10g} USDT；OKX该永续上市以来日线最低 {low:.10g} USDT；"
                    f"最低价+20%观察线 {threshold:.10g} USDT。已触及，请核对盘口、趋势及支撑后再自行判断；不是买入建议。"
                )
                triggered = True
            elif price > threshold * 1.02:
                # Require a 2% move above the line before allowing a fresh crossing alert.
                triggered = False
            state[inst_id] = {
                "all_time_low": low,
                "threshold": threshold,
                "triggered": triggered,
                "source": "OKX USDT永续合约自上市以来的1D历史K线低点",
            }
            print(f"{inst_id}: price={price:.10g}; ATL={low:.10g}; threshold={threshold:.10g}; active={triggered}")
        except Exception as exc:
            # Keep an existing baseline and trigger state when this symbol's data is unavailable.
            print(f"数据未核实 {inst_id}: {type(exc).__name__}: {exc}")

    if alerts:
        content = (
            f"OKX自选永续价格到达历史低位观察线\n采样：{checked_at}\n\n"
            + "\n\n".join(alerts)
            + "\n\n历史低点只统计OKX对应USDT永续的可用日线，不等于全市场代币最低价。触及后仍可能继续下跌。"
        )
        print(content)
        send_wxpusher(content)
    else:
        print(f"{checked_at}: 本轮没有新触发的历史低位观察提醒。")
    save_state(state)


if __name__ == "__main__":
    main()
