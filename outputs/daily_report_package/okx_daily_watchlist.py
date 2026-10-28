#!/usr/bin/env python3
"""Daily, read-only OKX USDT perpetual watchlist report + PushPlus WeChat relay."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


BASE_URL = os.environ.get("OKX_API_BASE", "https://www.okx.com").rstrip("/")
REPORT_PATH = Path(os.environ.get("REPORT_PATH", "daily-watchlist-report.md"))
WATCHLIST_ENV = os.environ.get("OKX_WATCHLIST", "").strip()
TIMEZONE_NAME = "Africa/Algiers"
TZ = ZoneInfo(TIMEZONE_NAME)
HTTP_TIMEOUT = 20
REQUEST_GAP = 0.12


class Api:
    def __init__(self) -> None:
        self.last_call = 0.0

    def get(self, path: str, params: dict[str, str] | None = None) -> list[dict]:
        elapsed = time.monotonic() - self.last_call
        if elapsed < REQUEST_GAP:
            time.sleep(REQUEST_GAP - elapsed)
        query = urllib.parse.urlencode(params or {})
        url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(url, headers={"User-Agent": "OKX-Daily-Watchlist/1.0"})
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
        self.last_call = time.monotonic()
        if body.get("code") != "0":
            raise RuntimeError(f"OKX API {path} returned code {body.get('code')}: {body.get('msg', '')}")
        return body.get("data", [])


def num(value, default=None):
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else default
    except (TypeError, ValueError):
        return default


def fmt_price(value):
    if value is None:
        return "未核实"
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")


def pct(value):
    return "未核实" if value is None else f"{value:+.2f}%"


def quote_volume(ticker):
    # OKX USDT swap volCcy24h is base currency; multiplication gives a quote estimate.
    base_vol, last = num(ticker.get("volCcy24h")), num(ticker.get("last"))
    return base_vol * last if base_vol is not None and last is not None else None


def fetch_daily(api: Api, inst_id: str):
    rows = api.get("/api/v5/market/candles", {"instId": inst_id, "bar": "1D", "limit": "35"})
    candles = [r for r in rows if len(r) >= 9 and r[8] == "1"]
    candles.sort(key=lambda r: int(r[0]))
    if len(candles) < 15:
        raise RuntimeError(f"可用已收盘日线不足（{len(candles)} 根）")
    return candles


def range_of(candles):
    return min(float(r[3]) for r in candles), max(float(r[2]) for r in candles)


def atr14(candles):
    selected = candles[-15:]
    tr = []
    for i, row in enumerate(selected[1:], start=1):
        high, low, prev_close = float(row[2]), float(row[3]), float(selected[i - 1][4])
        tr.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(tr) / len(tr) if tr else None


def fetch_one(api: Api, inst_id: str, swap_ticker: dict, spot_tickers: dict, oi_rows: dict):
    candles = fetch_daily(api, inst_id)
    price = num(swap_ticker.get("last"))
    open24 = num(swap_ticker.get("open24h"))
    chg24 = ((price / open24) - 1) * 100 if price and open24 else None
    turnover = quote_volume(swap_ticker)
    low7, high7 = range_of(candles[-7:])
    low30, high30 = range_of(candles[-30:])
    atr = atr14(candles)
    atr_pct = atr / price * 100 if atr is not None and price else None

    funding = api.get("/api/v5/public/funding-rate", {"instId": inst_id})
    funding_row = funding[0] if funding else {}
    funding_rate = num(funding_row.get("fundingRate"))
    next_funding = funding_row.get("nextFundingTime")
    funding_cycle = "该接口未提供结算周期；请按OKX合约页核实"

    oi = oi_rows.get(inst_id, {})
    oi_usd = num(oi.get("oiUsd"))
    oi_hist = api.get("/api/v5/rubik/stat/contracts/open-interest-history", {
        "instId": inst_id, "period": "1D", "limit": "2"
    })
    oi_change = None
    if len(oi_hist) >= 2:
        oi_hist.sort(key=lambda x: int(x.get("ts", "0")))
        older, newer = num(oi_hist[-2].get("oiUsd")), num(oi_hist[-1].get("oiUsd"))
        if older and newer is not None:
            oi_change = (newer / older - 1) * 100

    spot_id = inst_id.removesuffix("-SWAP")
    spot = spot_tickers.get(spot_id)
    spot_turnover = quote_volume(spot) if spot else None
    spot_confirm = "现货有成交，需结合现货K线确认方向" if spot_turnover and spot_turnover > 0 else "未核实/OKX未返回对应现货交易对"

    zone_low = low7 + (high7 - low7) * 0.15
    zone_high = low7 + (high7 - low7) * 0.35
    invalid = low7 - (atr * 0.25 if atr else 0)
    breakout = high7
    if price is not None and price > high7:
        trigger = f"等待日线收盘站稳7日高点 {fmt_price(breakout)}，且成交额高于近20日均值再复核；避免追逐瞬时突破。"
    else:
        trigger = f"仅观察回踩 {fmt_price(zone_low)}–{fmt_price(zone_high)} 后止跌，或日线收盘突破 {fmt_price(breakout)} 并伴随放量；均需复核。"

    risks = []
    if chg24 is not None and chg24 >= 15:
        risks.append("24小时涨幅较大，追高/回撤风险")
    if funding_rate is not None and funding_rate >= 0.0005:
        risks.append("资金费率偏高，多头拥挤风险")
    if funding_rate is not None and funding_rate <= -0.0005:
        risks.append("资金费率明显为负，可能有空头拥挤及挤压波动")
    if turnover is not None and turnover < 1_000_000:
        risks.append("OKX该合约24小时估算成交额低于100万美元，流动性需谨慎")
    if not risks:
        risks.append("行情可快速反转；杠杆、滑点和流动性风险仍在")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return {
        "inst": inst_id, "price": price, "change": chg24, "turnover": turnover,
        "low7": low7, "high7": high7, "low30": low30, "high30": high30,
        "atr_pct": atr_pct, "funding": funding_rate, "next_funding": next_funding,
        "funding_cycle": funding_cycle, "oi_usd": oi_usd, "oi_change": oi_change,
        "spot_turnover": spot_turnover, "spot_confirm": spot_confirm,
        "entry_zone": f"{fmt_price(zone_low)}–{fmt_price(zone_high)}（7日区间低位回踩观察，不是买入指令）",
        "breakout": fmt_price(breakout), "invalid": fmt_price(invalid),
        "trigger": trigger, "risks": risks, "sampled": ts,
    }


def render(items, failures, local_now):
    lines = [
        f"# OKX USDT 永续自选日报｜{local_now:%Y-%m-%d %H:%M} 阿尔及尔时间",
        "",
        "> 只作行情观察与技术情景参考，不是投资建议或确定买卖信号；永续合约高杠杆可能导致快速亏损。",
        "> 价格、K线、资金费率、OI：OKX公开API。流通量、解锁、项目催化：本版未接入可靠的自动核验源，明确列为未核实。",
        "",
        "## 快速摘要",
    ]
    if not items:
        lines.append("本次没有成功取得自选合约数据，请查看失败清单；不生成虚构行情。")
    else:
        ordered = sorted(items, key=lambda x: x["change"] if x["change"] is not None else -999, reverse=True)
        lines.extend(["本报告按24小时涨幅排序，触发条件仅为后续复核条件。", ""])
        for x in ordered:
            turnover = f"{x['turnover']/1_000_000:.2f}M USDT（OKX volCcy×最新价估算）" if x["turnover"] is not None else "未核实"
            funding = f"{x['funding']*100:.4f}%" if x["funding"] is not None else "未核实"
            oi = f"{x['oi_usd']/1_000_000:.2f}M USD" if x["oi_usd"] is not None else "未核实"
            oi_chg = pct(x["oi_change"])
            spot_turnover = f"{x['spot_turnover']/1_000_000:.2f}M USDT" if x["spot_turnover"] is not None else "未核实"
            lines.extend([
                f"## {x['inst']}",
                f"- 最新价：{fmt_price(x['price'])}；24小时涨跌：{pct(x['change'])}；24小时估算成交额：{turnover}",
                f"- OI：{oi}；24小时OI变化：{oi_chg}（OKX历史接口；若不可用则未核实）",
                f"- 资金费率：{funding}；下次结算时间戳：{x['next_funding'] or '未核实'}；结算周期：{x['funding_cycle']}",
                f"- 7日已收盘日线区间：{fmt_price(x['low7'])}–{fmt_price(x['high7'])}；30日区间：{fmt_price(x['low30'])}–{fmt_price(x['high30'])}；ATR14/现价：{pct(x['atr_pct'])}",
                f"- 现货确认：{x['spot_confirm']}；现货24小时估算成交额：{spot_turnover}",
                "- 流通量/解锁：未核实（当前任务未接入可交叉核验的供给/解锁数据源）；项目催化：未核实（未接入项目公告源）。",
                f"- 技术观察：回踩参考 {x['entry_zone']}；突破观察位 {x['breakout']}；失效参考（日线跌破）{x['invalid']}。",
                f"- 观察触发：{x['trigger']}",
                f"- 主要风险：{'；'.join(x['risks'])}；技术区间不是预测，解锁和公告请人工核验。",
                f"- 数据来源/采样：OKX公开API（行情/日线/资金费率/OI/现货Ticker），采样 {x['sampled']}。",
                "",
            ])
    if failures:
        lines.extend(["## 未能核实/不可交易标的", *[f"- {inst}: {reason}" for inst, reason in failures], ""])
    lines.extend([
        "## 数据口径与限制",
        "- 24小时成交额为 OKX 衍生品 volCcy24h × 最新价的估算值，不是精确撮合额；现货同理。",
        "- OI变化使用 OKX Rubik 1D历史数据；如果接口没有返回足够历史点，显示未核实。",
        "- 7日/30日区间取已收盘日线的最高/最低价，不等于未来波动预测。",
        "- 本程序不能判断散户多空比例，也不自动验证项目新闻、流通量或解锁；缺失项不会猜测。",
        "- 每日任务受 GitHub Actions 调度延迟、OKX API可用性及 PushPlus 微信绑定/平台规则影响。",
    ])
    return "\n".join(lines) + "\n"


def pushplus(content: str):
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("缺少 PUSHPLUS_TOKEN；请在 GitHub 仓库 Secrets 中配置")
    payload = json.dumps({
        "token": token,
        "title": "OKX USDT 永续自选日报",
        "content": content,
        "channel": "wechat",
        "template": "markdown",
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://www.pushplus.plus/send",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "OKX-Daily-Watchlist/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("code") != 200:
        raise RuntimeError(f"PushPlus 接口返回失败 code={result.get('code')} msg={result.get('msg', '')}")
    # Do not print token or full request/response identifiers to logs.
    print("PushPlus 已受理推送请求（此响应不等于微信最终送达）。")


def main():
    if not WATCHLIST_ENV:
        raise RuntimeError("缺少 OKX_WATCHLIST；请在 GitHub 仓库 Secrets 中配置逗号分隔的合约代码")
    watchlist = []
    for raw in WATCHLIST_ENV.split(","):
        symbol = raw.strip().upper()
        if not symbol:
            continue
        inst_id = symbol if symbol.endswith("-USDT-SWAP") else f"{symbol.removesuffix('USDT')}-USDT-SWAP"
        if inst_id not in watchlist:
            watchlist.append(inst_id)
    if not watchlist:
        raise RuntimeError("OKX_WATCHLIST 中没有有效合约代码")

    api = Api()
    swaps = api.get("/api/v5/market/tickers", {"instType": "SWAP"})
    swap_map = {x.get("instId"): x for x in swaps}
    spots = api.get("/api/v5/market/tickers", {"instType": "SPOT"})
    spot_map = {x.get("instId"): x for x in spots}
    oi = api.get("/api/v5/public/open-interest", {"instType": "SWAP"})
    oi_map = {x.get("instId"): x for x in oi}

    items, failures = [], []
    for inst in watchlist:
        ticker = swap_map.get(inst)
        if ticker is None:
            failures.append((inst, "OKX当前未返回该USDT永续Ticker；可能已下架、暂停或代码不匹配"))
            continue
        try:
            items.append(fetch_one(api, inst, ticker, spot_map, oi_map))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError, ValueError, KeyError) as exc:
            failures.append((inst, f"数据采集失败：{type(exc).__name__}: {exc}"))

    report = render(items, failures, datetime.now(TZ))
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"报告已写入 {REPORT_PATH}; 成功 {len(items)}，失败/不可用 {len(failures)}。")
    pushplus(report)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
        raise
