#!/usr/bin/env python3
"""Read-only OKX USDT perpetual market monitor. No credentials or trading endpoints."""

from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "monitor_data"
STATE_FILE = RUNTIME / "state.json"
LATEST_FILE = RUNTIME / "latest_report.md"
EVENTS_FILE = RUNTIME / "signals.jsonl"
SUPPLY_FILE = ROOT / "supply_overrides.csv"
BASE_URL = os.environ.get("OKX_API_BASE", "https://www.okx.com").rstrip("/")

# Tunable screening defaults. These rank unusual market structure; they do not
# estimate a probability of a 50%-200% move.
POLL_SECONDS = 300
MAX_SYMBOLS = 40
MIN_24H_QUOTE_VOLUME_USDT = 100_000
VOLUME_SPIKE_MULTIPLE = 2.5
OI_RISE_PCT = 5.0
MAX_24H_GAIN_PCT = 35.0
MIN_SCORE_TO_ALERT = 3
ALERT_COOLDOWN_MINUTES = 30
REQUEST_GAP_SECONDS = 0.12
HTTP_TIMEOUT_SECONDS = 15


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if result == result else default
    except (TypeError, ValueError):
        return default


class OkxPublicApi:
    def __init__(self) -> None:
        self.last_request = 0.0

    def get(self, path: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        elapsed = time.monotonic() - self.last_request
        if elapsed < REQUEST_GAP_SECONDS:
            time.sleep(REQUEST_GAP_SECONDS - elapsed)
        query = urllib.parse.urlencode(params or {})
        url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(url, headers={"User-Agent": "OKX-ReadOnly-Market-Monitor/1.0"})
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                    body = json.loads(response.read().decode("utf-8"))
                self.last_request = time.monotonic()
                if body.get("code") != "0":
                    raise RuntimeError(f"OKX API returned code {body.get('code')}: {body.get('msg', '')}")
                return body.get("data", [])
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"GET {path} failed: {last_error}")


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"oi": {}, "alerts": {}}


def save_state(state: dict[str, Any]) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def load_supply_overrides() -> dict[str, dict[str, str]]:
    if not SUPPLY_FILE.exists():
        return {}
    result: dict[str, dict[str, str]] = {}
    with SUPPLY_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            inst_id = (row.get("instId") or "").strip()
            if inst_id:
                result[inst_id] = row
    return result


def quote_turnover_usdt(ticker: dict[str, Any]) -> float:
    # For linear USDT swaps, volCcy24h is base currency. Multiplying by last
    # produces an approximate USDT turnover; OKX's volume units are documented.
    return number(ticker.get("volCcy24h")) * number(ticker.get("last"))


def fetch_inputs(api: OkxPublicApi) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    tickers = api.get("/api/v5/market/tickers", {"instType": "SWAP"})
    swaps = [
        item for item in tickers
        if item.get("instId", "").endswith("-USDT-SWAP")
        and number(item.get("last")) > 0
        and quote_turnover_usdt(item) >= MIN_24H_QUOTE_VOLUME_USDT
    ]
    swaps.sort(key=quote_turnover_usdt, reverse=True)
    return swaps[:MAX_SYMBOLS], {item["instId"]: item for item in swaps[:MAX_SYMBOLS]}


def fetch_market_detail(api: OkxPublicApi, inst_id: str) -> dict[str, Any]:
    candle_data = api.get("/api/v5/market/candles", {"instId": inst_id, "bar": "5m", "limit": "30"})
    # OKX returns newest first. Use only confirmed (closed) candles.
    closed = [row for row in candle_data if len(row) >= 9 and row[8] == "1"]
    closed.sort(key=lambda row: int(row[0]))
    if not closed:
        raise RuntimeError("No confirmed 5m candles")
    current = closed[-1]
    prior = closed[:-1]
    quote_volume = number(current[7])
    baseline_samples = [number(row[7]) for row in prior[-20:] if number(row[7]) > 0]
    baseline = sum(baseline_samples) / len(baseline_samples) if baseline_samples else 0.0
    open_px, close_px = number(current[1]), number(current[4])
    return {
        "candle_ts": int(current[0]),
        "candle_volume_usdt": quote_volume,
        "baseline_volume_usdt": baseline,
        "volume_multiple": quote_volume / baseline if baseline > 0 else 0.0,
        "change_5m_pct": ((close_px / open_px) - 1.0) * 100 if open_px > 0 else 0.0,
    }


def fetch_oi(api: OkxPublicApi) -> dict[str, dict[str, Any]]:
    rows = api.get("/api/v5/public/open-interest", {"instType": "SWAP"})
    return {row.get("instId", ""): row for row in rows if row.get("instId")}


def fetch_funding(api: OkxPublicApi, inst_id: str) -> dict[str, Any] | None:
    rows = api.get("/api/v5/public/funding-rate", {"instId": inst_id})
    return rows[0] if rows else None


def make_report(
    api: OkxPublicApi,
    state: dict[str, Any],
    tickers: list[dict[str, Any]],
    ticker_map: dict[str, dict[str, Any]],
    supplies: dict[str, dict[str, str]],
) -> tuple[str, list[dict[str, Any]]]:
    oi_map = fetch_oi(api)
    results: list[dict[str, Any]] = []
    old_oi = state.setdefault("oi", {})
    last_alerts = state.setdefault("alerts", {})

    for item in tickers:
        inst_id = item["instId"]
        try:
            candle = fetch_market_detail(api, inst_id)
            oi_item = oi_map.get(inst_id)
            oi_usd = number(oi_item.get("oiUsd")) if oi_item else 0.0
            oi_value = number(oi_item.get("oi")) if oi_item else 0.0
            previous_oi = number(old_oi.get(inst_id, {}).get("oiUsd"))
            oi_change_pct = ((oi_usd / previous_oi) - 1) * 100 if previous_oi > 0 and oi_usd > 0 else None
            funding_item = fetch_funding(api, inst_id)
            funding_rate = number(funding_item.get("fundingRate")) if funding_item else None
            funding_time = funding_item.get("nextFundingTime", "未提供") if funding_item else "未提供"
            price = number(item.get("last"))
            daily_change = ((price / number(item.get("open24h"))) - 1) * 100 if number(item.get("open24h")) > 0 else 0.0
            turnover = quote_turnover_usdt(item)

            score = 0
            reasons: list[str] = []
            if candle["volume_multiple"] >= VOLUME_SPIKE_MULTIPLE:
                score += 1
                reasons.append(f"5分钟成交量为前20根均值 {candle['volume_multiple']:.1f} 倍")
            if oi_change_pct is not None and oi_change_pct >= OI_RISE_PCT:
                score += 1
                reasons.append(f"OI较上次采样增加 {oi_change_pct:.1f}%")
            if funding_rate is not None and funding_rate < 0:
                score += 1
                reasons.append("资金费率为负，空头付费")
            if candle["change_5m_pct"] > 0:
                score += 1
                reasons.append(f"已确认5分钟K线上涨 {candle['change_5m_pct']:.2f}%")
            if daily_change <= MAX_24H_GAIN_PCT:
                score += 1
            else:
                reasons.append(f"24小时已涨 {daily_change:.1f}%，可能追高")

            supply = supplies.get(inst_id)
            circulating = supply.get("circulating_supply", "未核实") if supply else "未核实（可填 supply_overrides.csv）"
            supply_source = supply.get("source", "") if supply else ""
            supply_time = supply.get("updated_at", "") if supply else ""
            results.append({
                "inst_id": inst_id, "price": price, "change24h": daily_change,
                "turnover24h": turnover, **candle, "oiUsd": oi_usd, "oi": oi_value,
                "oiChangePct": oi_change_pct, "fundingRate": funding_rate,
                "nextFundingTime": funding_time, "score": score,
                "reasons": reasons, "circulating": circulating,
                "supply_source": supply_source, "supply_time": supply_time,
            })
            if oi_usd > 0:
                old_oi[inst_id] = {"oiUsd": oi_usd, "sampled_at": stamp()}
        except Exception as exc:  # Per-instrument gaps should not stop the universe scan.
            results.append({"inst_id": inst_id, "error": str(exc), "score": 0})

    results.sort(key=lambda row: (row.get("score", 0), row.get("volume_multiple", 0)), reverse=True)
    alerts: list[dict[str, Any]] = []
    now_ts = time.time()
    for row in results:
        if row.get("score", 0) < MIN_SCORE_TO_ALERT or row.get("error"):
            continue
        inst_id = row["inst_id"]
        previous_alert = number(last_alerts.get(inst_id))
        if now_ts - previous_alert >= ALERT_COOLDOWN_MINUTES * 60:
            alerts.append(row)
            last_alerts[inst_id] = now_ts

    report = [
        f"# OKX USDT 永续 5 分钟异动扫描",
        f"采样时间：{stamp()}（UTC）",
        f"数据源：OKX 公开 REST API；只读监控，不连接账户、不下单。扫描 {len(tickers)} 个高成交额合约。",
        "",
        "评分是异动筛选条件，不是上涨概率或 50%–200% 涨幅预测。流通量需通过人工核实的覆盖表补充；项目新闻/解锁日历未接入自动核验。",
        "",
        "## 候选观察",
    ]
    candidates = [r for r in results if not r.get("error") and r.get("score", 0) >= 2]
    if not candidates:
        report.append("本轮没有达到观察阈值的币种。")
    for row in candidates[:20]:
        rate_text = f"{row['fundingRate'] * 100:.4f}%" if row.get("fundingRate") is not None else "未核实"
        oi_change = f"{row['oiChangePct']:+.1f}%" if row.get("oiChangePct") is not None else "缺少前次基线"
        report.extend([
            f"### {row['inst_id']} — 异动分 {row['score']}/5",
            f"- 价格：{row['price']:.10g} USDT；24小时：{row['change24h']:+.2f}%；5分钟：{row['change_5m_pct']:+.2f}%",
            f"- 24小时成交额估算：{row['turnover24h']:,.0f} USDT；最近已收盘5分钟成交额：{row['candle_volume_usdt']:,.0f} USDT（前20根均值的 {row['volume_multiple']:.2f} 倍）",
            f"- OI：{row['oiUsd']:,.0f} USD；较本程序上次成功采样：{oi_change}",
            f"- 当前资金费率：{rate_text}；下一结算时间戳：{row['nextFundingTime']}",
            f"- 流通量：{row['circulating']}" + (f"（来源：{row['supply_source']}；更新时间：{row['supply_time']}）" if row['supply_source'] else ""),
            f"- 命中条件：{'；'.join(row['reasons']) if row['reasons'] else '价格、成交量、OI、资金费率组合尚不突出'}",
            "- 风险提示：负资金费率不等于散户必然看空；OI 增加也可能是多空双方同时加仓。短期强平、低深度和解锁风险会放大双向波动。",
        ])
    errors = [r for r in results if r.get("error")]
    if errors:
        report.extend(["", "## 数据缺口"])
        report.extend([f"- {r['inst_id']}: {r['error']}" for r in errors[:20]])
    report.extend(["", "## 解释", "- OKX 只提供交易和合约市场数据；‘散户看空’不能由资金费率单独确认。", "- 本程序不判断操纵或庄家行为，也不提供交易指令。", "- 数据口径：5分钟成交额取最近已收盘K线；成交量倍数与此前最多20根已收盘5分钟K线比较；OI变化是相邻成功采样间变化。"])

    RUNTIME.mkdir(parents=True, exist_ok=True)
    text = "\n".join(report) + "\n"
    LATEST_FILE.write_text(text, encoding="utf-8")
    if alerts:
        with EVENTS_FILE.open("a", encoding="utf-8") as f:
            for row in alerts:
                f.write(json.dumps({"sampled_at": stamp(), **row}, ensure_ascii=False) + "\n")
    save_state(state)
    return text, alerts


def run_once() -> tuple[str, list[dict[str, Any]]]:
    api = OkxPublicApi()
    selected, ticker_map = fetch_inputs(api)
    state = load_state()
    supplies = load_supply_overrides()
    return make_report(api, state, selected, ticker_map, supplies)


def main() -> int:
    once = "--once" in sys.argv[1:]
    print(f"OKX 只读监控启动：{BASE_URL}；间隔 {POLL_SECONDS} 秒。报告目录：{RUNTIME}", flush=True)
    while True:
        cycle_start = time.time()
        try:
            report, alerts = run_once()
            print(f"[{stamp()}] 扫描完成；新提醒 {len(alerts)} 个。", flush=True)
            for row in alerts:
                print(f"  异动观察：{row['inst_id']} score={row['score']}/5 price={row['price']}USDT", flush=True)
        except KeyboardInterrupt:
            print("监控已停止。", flush=True)
            return 0
        except Exception as exc:
            RUNTIME.mkdir(parents=True, exist_ok=True)
            with (RUNTIME / "monitor_errors.log").open("a", encoding="utf-8") as f:
                f.write(f"[{stamp()}] {type(exc).__name__}: {exc}\n")
            print(f"[{stamp()}] 本轮失败：{exc}", flush=True)
        if once:
            return 0
        next_cycle = ((int(cycle_start) // POLL_SECONDS) + 1) * POLL_SECONDS
        time.sleep(max(1, next_cycle - time.time()))


if __name__ == "__main__":
    raise SystemExit(main())
