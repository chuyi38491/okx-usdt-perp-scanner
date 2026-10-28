#!/usr/bin/env python3
"""Read-only OKX USDT perpetual anomaly scanner for scheduled GitHub Actions."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "reports"
STATE_PATH = ROOT / "state.json"
BASE_URL = os.environ.get("OKX_API_BASE", "https://www.okx.com").rstrip("/")
MAX_CANDLES = int(os.environ.get("MAX_CANDLES", "80"))
MIN_24H_QUOTE_VOLUME = float(os.environ.get("MIN_24H_QUOTE_VOLUME", "100000"))
REQUEST_GAP = 0.11


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fmt_time(value: datetime | None = None) -> str:
    return (value or utc_now()).strftime("%Y-%m-%d %H:%M:%S UTC")


def num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if result == result else default
    except (TypeError, ValueError):
        return default


class PublicApi:
    def __init__(self) -> None:
        self.last_call = 0.0

    def get(self, path: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        elapsed = time.monotonic() - self.last_call
        if elapsed < REQUEST_GAP:
            time.sleep(REQUEST_GAP - elapsed)
        query = urllib.parse.urlencode(params or {})
        url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
        req = urllib.request.Request(url, headers={"User-Agent": "OKX-USDT-Perp-Scanner/1.0"})
        last_error: Exception | None = None
        for retry in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.last_call = time.monotonic()
                if payload.get("code") != "0":
                    raise RuntimeError(f"OKX API code {payload.get('code')}: {payload.get('msg', '')}")
                return payload.get("data", [])
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
                if retry < 2:
                    time.sleep(1.5 * (retry + 1))
        raise RuntimeError(f"API request failed ({path}): {last_error}")


def quote_turnover(ticker: dict[str, Any]) -> float:
    # OKX volCcy24h for derivatives is in base currency; use last to estimate USDT value.
    return num(ticker.get("volCcy24h")) * num(ticker.get("last"))


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"oi_usd": {}, "updated_at": None}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_candle_metrics(api: PublicApi, inst_id: str) -> dict[str, float] | None:
    rows = api.get("/api/v5/market/candles", {"instId": inst_id, "bar": "30m", "limit": "25"})
    closed = [row for row in rows if len(row) >= 9 and row[8] == "1"]
    closed.sort(key=lambda row: int(row[0]))
    if len(closed) < 6:
        return None
    latest = closed[-1]
    previous = closed[:-1][-20:]
    volumes = [num(row[7]) for row in previous if num(row[7]) > 0]
    baseline = sum(volumes) / len(volumes) if volumes else 0.0
    open_px, close_px = num(latest[1]), num(latest[4])
    return {
        "bar_volume_usdt": num(latest[7]),
        "baseline_usdt": baseline,
        "volume_multiple": num(latest[7]) / baseline if baseline > 0 else 0.0,
        "change_30m_pct": (close_px / open_px - 1) * 100 if open_px > 0 else 0.0,
        "bar_ts": num(latest[0]),
    }


def run_scan() -> tuple[Path, Path]:
    api = PublicApi()
    tickers = api.get("/api/v5/market/tickers", {"instType": "SWAP"})
    universe = []
    for row in tickers:
        if not row.get("instId", "").endswith("-USDT-SWAP") or num(row.get("last")) <= 0:
            continue
        row["quote24h_est"] = quote_turnover(row)
        if row["quote24h_est"] >= MIN_24H_QUOTE_VOLUME:
            universe.append(row)
    universe.sort(key=lambda row: row["quote24h_est"], reverse=True)
    selected = universe[:MAX_CANDLES]

    oi_rows = api.get("/api/v5/public/open-interest", {"instType": "SWAP"})
    oi_by_id = {row.get("instId", ""): row for row in oi_rows if row.get("instId")}
    state = load_state()
    old_oi = state.setdefault("oi_usd", {})
    current_oi: dict[str, float] = {}
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for ticker in selected:
        inst_id = ticker["instId"]
        try:
            candle = get_candle_metrics(api, inst_id)
            if not candle:
                errors.append(f"{inst_id}: 没有足够的已收盘30分钟K线")
                continue
            oi_raw = oi_by_id.get(inst_id, {})
            oi_usd = num(oi_raw.get("oiUsd"))
            prev_oi = num(old_oi.get(inst_id))
            oi_delta_pct = (oi_usd / prev_oi - 1) * 100 if prev_oi > 0 and oi_usd > 0 else None
            current_oi[inst_id] = oi_usd

            volume_multiple = candle["volume_multiple"]
            change_30m = candle["change_30m_pct"]
            volume_trigger = volume_multiple >= 4.0 or (volume_multiple >= 3.0 and change_30m >= 1.5)
            score = int(volume_multiple >= 3.0) + int(change_30m >= 1.5) + int(oi_delta_pct is not None and abs(oi_delta_pct) >= 5.0)

            funding = None
            if volume_trigger or (oi_delta_pct is not None and abs(oi_delta_pct) >= 5.0):
                funding_rows = api.get("/api/v5/public/funding-rate", {"instId": inst_id})
                funding = funding_rows[0] if funding_rows else None
                if funding and num(funding.get("fundingRate")) < 0:
                    score += 1

            last = num(ticker.get("last"))
            open24 = num(ticker.get("open24h"))
            change24 = (last / open24 - 1) * 100 if open24 > 0 else 0.0
            results.append({
                "instId": inst_id,
                "price": last,
                "change24hPct": change24,
                "turnover24hUsdtEstimate": ticker["quote24h_est"],
                "volume30mUsdt": candle["bar_volume_usdt"],
                "volumeMultiple": volume_multiple,
                "change30mPct": change_30m,
                "oiUsd": oi_usd or None,
                "oiChangeSincePreviousScanPct": oi_delta_pct,
                "fundingRate": num(funding.get("fundingRate")) if funding else None,
                "nextFundingTime": funding.get("nextFundingTime") if funding else None,
                "score": score,
                "volumeTrigger": volume_trigger,
                "circulatingSupply": "未核实：OKX公开行情接口不提供",
                "catalyst": "未自动核验；需人工查项目官方公告",
                "sampledAtUtc": fmt_time(),
            })
        except Exception as exc:
            errors.append(f"{inst_id}: {exc}")

    state["oi_usd"] = current_oi
    state["updated_at"] = fmt_time()
    save_state(state)

    results.sort(key=lambda row: (row["volumeTrigger"], row["score"], row["volumeMultiple"]), reverse=True)
    triggers = [row for row in results if row["volumeTrigger"]]
    REPORT_DIR.mkdir(exist_ok=True)
    generated = utc_now()
    md_path = REPORT_DIR / "okx-anomaly-report.md"
    json_path = REPORT_DIR / "okx-anomaly-report.json"

    lines = [
        "# OKX USDT 永续异动扫描报告",
        "",
        f"采样时间：{fmt_time(generated)}",
        f"范围：24小时估算成交额前 {len(selected)} 个可用 USDT 永续合约（扫描总量 {len(universe)}）",
        "数据源：OKX 公开 REST API；不读取账户数据、不下单。",
        "",
        "> 这是市场异动筛选，不是收益预测或交易指令。流通量、解锁、项目催化未由 OKX 行情 API 提供，均不会凭空补值。",
        "",
        "## 触发规则",
        "- 30分钟成交额 ≥ 前20根已完成K线均值的4倍；或达到3倍且同根价格上涨至少1.5%。",
        "- OI 与上次成功扫描比较，变化达到 ±5% 时单独标记；首次扫描无前次基线。",
        "- 对放量或 OI 异动标的读取当前资金费率。资金费率为负只说明空头付费，不证明空头都是散户。",
        "",
        "## 本轮触发标的",
    ]
    if not triggers:
        lines.append("没有合约达到成交量触发条件。")
    for row in triggers:
        funding = f"{row['fundingRate'] * 100:.4f}%" if row["fundingRate"] is not None else "未查询/无数据"
        oi = f"{row['oiChangeSincePreviousScanPct']:+.2f}%" if row["oiChangeSincePreviousScanPct"] is not None else "缺少前次基线"
        lines.extend([
            f"### {row['instId']}（观察分 {row['score']}/4）",
            f"- 价格：{row['price']:.10g} USDT；24h {row['change24hPct']:+.2f}%；30m {row['change30mPct']:+.2f}%",
            f"- 最近30m成交额：{row['volume30mUsdt']:,.0f} USDT；前20根均值倍数：{row['volumeMultiple']:.2f}x",
            f"- 24h成交额估算：{row['turnover24hUsdtEstimate']:,.0f} USDT；OI：{row['oiUsd']:,.0f} USD" if row["oiUsd"] else "- OI：未核实",
            f"- OI较前次成功扫描：{oi}；资金费率：{funding}；下一结算时间戳：{row['nextFundingTime'] or '未提供'}",
            f"- 流通量/解锁：{row['circulatingSupply']}；项目催化：{row['catalyst']}",
            "- 失效/风险：现货不跟随、价格跌回异动K线区间、OI回落或盘口变薄时，异动可能只是短时合约挤压。",
            "",
        ])
    lines.extend(["## 数据缺口", "- OI变化是本程序最近两次成功扫描之间的变化，不是标准化的24小时变化。", "- 24小时成交额由 OKX 合约基础币成交量乘最新价格估算；非官方 USDT 成交额字段。", "- 定时任务可能延迟或漏跑；缓存/报告首次扫描还没有 OI 历史基线。"])
    if errors:
        lines.extend(["", "## 请求或数据错误"])
        lines.extend(f"- {error}" for error in errors[:40])

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    json_path.write_text(json.dumps({
        "sampledAtUtc": fmt_time(generated),
        "universeCount": len(universe),
        "scannedCount": len(selected),
        "triggerCount": len(triggers),
        "triggers": triggers,
        "topObserved": results[:20],
        "errors": errors,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    print(f"JSON_REPORT={json_path}")
    return md_path, json_path


if __name__ == "__main__":
    run_scan()
