# -*- coding: utf-8 -*-
"""
QQQ T-1收盘买Put策略 — 参数优化（网格搜索）

扫描：上涨阈值 × 下跌阈值 × 平仓时间
输出最优参数组合
"""

import os, itertools
import pandas as pd
import numpy as np

# ────────────────────────────────────────────────
# 路径
# ────────────────────────────────────────────────
QQQ_FILE    = os.path.join(os.path.dirname(__file__), "..", "1-qqq日K", "data", "qqq_market_data.xlsx")
OPT_FILE_3  = os.path.join(os.path.dirname(__file__), "..", "2-qqq末日期权日K-上下3股价的期权合同-前一天末日期权的收盘价", "data", "qqq_0dte_options_offset3.xlsx")
OPT_FILE_4  = os.path.join(os.path.dirname(__file__), "..", "3-qqq末日期权日K-上下4股价的期权合同-前一天末日期权的收盘价", "data", "qqq_0dte_options_offset4.xlsx")

COMMISSION = 1.7
MONITOR_START = "09:30"

# 扫描范围
UPPER_PCTS = [round(0.5 + i * 0.5, 1) for i in range(10)]   # 0.5 ~ 5.0
LOWER_PCTS = [round(0.5 + i * 0.5, 1) for i in range(10)]   # 0.5 ~ 5.0
CLOSE_TIMES = ["09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:30", "12:00", "13:00", "14:00", "15:00"]


def load_data(opt_file):
    summary = pd.read_excel(opt_file, sheet_name="摘要")
    put_1m  = pd.read_excel(opt_file, sheet_name="Put_1min")
    qqq_1m  = pd.read_excel(QQQ_FILE, sheet_name="QQQ_分时1min")
    qqq_2m  = pd.read_excel(QQQ_FILE, sheet_name="QQQ_分时2min")
    qqq_5m  = pd.read_excel(QQQ_FILE, sheet_name="QQQ_5min")
    return summary, put_1m, qqq_1m, qqq_2m, qqq_5m


def _get_qqq_day(t1, qqq_1m, qqq_2m, qqq_5m):
    for df in [qqq_1m, qqq_2m, qqq_5m]:
        day = df[df["时间"].astype(str).str.startswith(t1)].copy()
        if not day.empty:
            day["time_only"] = day["时间"].astype(str).str[-5:]
            return day
    return pd.DataFrame()


def precompute(summary, put_1m, qqq_1m, qqq_2m, qqq_5m):
    """预计算每天的时间序列，避免重复过滤"""
    days = []
    for _, row in summary.iterrows():
        t1 = str(row["到期日(T1)"])[:10]
        qqq_day = _get_qqq_day(t1, qqq_1m, qqq_2m, qqq_5m)
        if qqq_day.empty:
            continue
        put_t2_close = row.get("Put_T2收盘")
        if pd.isna(put_t2_close) or float(put_t2_close) <= 0:
            continue

        qqq_t2_close = float(row["QQQ_T2收盘"])
        put_cost = float(put_t2_close)

        p1m = put_1m[put_1m["到期日"].astype(str).str[:10] == t1].copy()
        p1m["time_only"] = p1m["时间(美东)"].astype(str).str[-5:]

        qqq_bars = []
        for _, mr in qqq_day.iterrows():
            t = mr["time_only"]
            if t < MONITOR_START:
                continue
            qqq_bars.append((t, float(mr["收盘价"])))

        put_prices = {}
        for _, pr in p1m.iterrows():
            put_prices[pr["time_only"]] = float(pr["收盘价"])

        days.append({
            "t1": t1,
            "qqq_t2_close": qqq_t2_close,
            "put_cost": put_cost,
            "qqq_bars": qqq_bars,
            "put_prices": put_prices,
        })
    return days


def run_single(days, upper_pct, lower_pct, close_time):
    """单次回测，返回累计盈亏"""
    commission_total = COMMISSION * 2 / 100
    total_pnl = 0
    wins = 0
    n = 0

    for d in days:
        t2_close = d["qqq_t2_close"]
        put_cost = d["put_cost"]

        sell_time = None
        for t, price in d["qqq_bars"]:
            if t > close_time:
                break
            pct = (price - t2_close) / t2_close * 100
            if pct >= upper_pct or pct <= -lower_pct:
                sell_time = t
                break

        sell_time = sell_time or close_time

        put_sell = d["put_prices"].get(sell_time)
        if put_sell is None:
            best_t = None
            for pt in sorted(d["put_prices"].keys()):
                if pt <= sell_time:
                    best_t = pt
            put_sell = d["put_prices"][best_t] if best_t else 0

        pnl = put_sell - put_cost - commission_total
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        n += 1

    return {
        "total_pnl": round(total_pnl, 4),
        "total_pnl_dollar": round(total_pnl * 100, 2),
        "wins": wins,
        "total": n,
        "win_rate": round(wins / n * 100, 1) if n > 0 else 0,
    }


def optimize(opt_file, label):
    print(f"\n{'='*70}")
    print(f"  {label}  参数优化")
    print(f"{'='*70}")

    summary, put_1m, qqq_1m, qqq_2m, qqq_5m = load_data(opt_file)
    days = precompute(summary, put_1m, qqq_1m, qqq_2m, qqq_5m)
    print(f"  有效交易日: {len(days)}")

    combos = list(itertools.product(UPPER_PCTS, LOWER_PCTS, CLOSE_TIMES))
    print(f"  参数组合数: {len(combos)}")

    results = []
    for up, lo, ct in combos:
        r = run_single(days, up, lo, ct)
        r["upper_pct"] = up
        r["lower_pct"] = lo
        r["close_time"] = ct
        results.append(r)

    results.sort(key=lambda x: x["total_pnl_dollar"], reverse=True)

    print(f"\n  ── TOP 20 ──")
    print(f"  {'排名':>4}  {'上涨%':>6}  {'下跌%':>6}  {'平仓':>6}  {'累计盈亏$':>10}  {'胜率':>6}  {'胜/负':>8}")
    for i, r in enumerate(results[:20]):
        neg = r["total"] - r["wins"]
        print(f"  {i+1:>4}  {r['upper_pct']:>6.1f}  {r['lower_pct']:>6.1f}  {r['close_time']:>6}  {r['total_pnl_dollar']:>10.2f}  {r['win_rate']:>5.1f}%  {r['wins']}/{neg}")

    print(f"\n  ── BOTTOM 5 ──")
    for i, r in enumerate(results[-5:]):
        neg = r["total"] - r["wins"]
        print(f"  {len(results)-4+i:>4}  {r['upper_pct']:>6.1f}  {r['lower_pct']:>6.1f}  {r['close_time']:>6}  {r['total_pnl_dollar']:>10.2f}  {r['win_rate']:>5.1f}%  {r['wins']}/{neg}")

    best = results[0]
    print(f"\n  ✅ 最优: 上涨 +{best['upper_pct']}% / 下跌 -{best['lower_pct']}% / 平仓 {best['close_time']}")
    print(f"     累计盈亏 ${best['total_pnl_dollar']}  胜率 {best['win_rate']}%")

    return best


def main():
    opt_file_4 = OPT_FILE_4
    if not os.path.exists(opt_file_4):
        old = os.path.join(os.path.dirname(__file__), "..", "3-qqq末日期权日K-上下4股价的期权合同-前一天末日期权的收盘价", "data", "QQQ_0DTE_4.xlsx")
        if os.path.exists(old):
            opt_file_4 = old

    best3 = optimize(OPT_FILE_3, "−3 行权价")
    best4 = optimize(opt_file_4, "−4 行权价")

    print(f"\n{'='*70}")
    print(f"  最终推荐")
    print(f"{'='*70}")
    if best3["total_pnl_dollar"] >= best4["total_pnl_dollar"]:
        b, lbl = best3, "−3"
    else:
        b, lbl = best4, "−4"
    print(f"  推荐行权价: {lbl}")
    print(f"  UPPER_TRIGGER_PCT = {b['upper_pct']}")
    print(f"  LOWER_TRIGGER_PCT = {b['lower_pct']}")
    print(f"  MONITOR_END = \"{b['close_time']}\"")
    print(f"  累计盈亏: ${b['total_pnl_dollar']}  胜率: {b['win_rate']}%")


if __name__ == "__main__":
    main()
