# -*- coding: utf-8 -*-
"""
QQQ 末日期权双买策略回测 + HTML 可视化
数据来源：
  - QQQ 股价：1-qqq日K/data/qqq_market_data.xlsx
  - 期权数据：2-qqq末日期权…/data/qqq_0dte_options_offset3.xlsx

策略规则：
  T-1 收盘：同时买入 Call(+3) 和 Put(-3)，各1张，成本 = Call_T2收盘 + Put_T2收盘
  T（到期日）9:30~12:00：若 QQQ 涨跌幅超过 ±1.5%（基于T-1收盘价），立即卖出双腿
  若到平仓时间（默认 12:00）仍未触发，以该时间价格止损卖出
"""

import os, json
import pandas as pd
import numpy as np

# ────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────
QQQ_FILE    = os.path.join(os.path.dirname(__file__), "..", "1-qqq日K", "data", "qqq_market_data.xlsx")
OPT_FILE_3  = os.path.join(os.path.dirname(__file__), "data", "qqq_0dte_options_offset3.xlsx")
OPT_FILE_4  = os.path.join(os.path.dirname(__file__), "..", "3-qqq末日期权日K-上下4股价的期权合同-前一天末日期权的收盘价", "data", "qqq_0dte_options_offset4.xlsx")
VIX_FILE    = os.path.join(os.path.dirname(__file__), "..", "..", "VIX", "data", "vix_data.xlsx")
OUTPUT_HTML = os.path.join(os.path.dirname(__file__), "data", "qqq_0dte_strategy_report.html")

UPPER_TRIGGER_PCT = 2.0    # 上涨触发阈值 +2.0%（最优配置）
LOWER_TRIGGER_PCT = 1.25   # 下跌触发阈值 -1.25%（最优配置）
COMMISSION   = 1.7   # 每张合约手续费（美元），买入+卖出共2次×2腿=4次
MONITOR_START = "09:30"
MONITOR_END   = "10:30"


def load_data(opt_file):
    summary   = pd.read_excel(opt_file, sheet_name="摘要")
    call_1m   = pd.read_excel(opt_file, sheet_name="Call_1min")
    put_1m    = pd.read_excel(opt_file, sheet_name="Put_1min")
    qqq_1m    = pd.read_excel(QQQ_FILE, sheet_name="QQQ_分时1min")
    qqq_2m    = pd.read_excel(QQQ_FILE, sheet_name="QQQ_分时2min")
    qqq_5m    = pd.read_excel(QQQ_FILE, sheet_name="QQQ_5min")
    qqq_daily = pd.read_excel(QQQ_FILE, sheet_name="QQQ_日K")
    return summary, call_1m, put_1m, qqq_1m, qqq_2m, qqq_5m, qqq_daily


def _get_qqq_day(t1, qqq_1m, qqq_2m, qqq_5m):
    """按 1min→2min→5min 降级获取当天 QQQ 数据，返回 (df, granularity)"""
    for df, label in [(qqq_1m, "1min"), (qqq_2m, "2min"), (qqq_5m, "5min")]:
        day = df[df["时间"].astype(str).str.startswith(t1)].copy()
        if not day.empty:
            day["time_only"] = day["时间"].astype(str).str[-5:]
            return day, label
    return pd.DataFrame(), "无"


def run_backtest(summary, call_1m, put_1m, qqq_1m, qqq_2m, qqq_5m):
    results = []

    for _, row in summary.iterrows():
        t1 = str(row["到期日(T1)"])[:10]
        t2 = str(row["基准日(T2)"])[:10]

        call_cost = row.get("Call_T2收盘")
        put_cost  = row.get("Put_T2收盘")
        if pd.isna(call_cost) or pd.isna(put_cost):
            continue
        call_cost = float(call_cost)
        put_cost  = float(put_cost)
        total_cost = call_cost + put_cost

        # T-1 当天的 QQQ 数据（1min → 2min → 5min 降级）
        qqq_day, granularity = _get_qqq_day(t1, qqq_1m, qqq_2m, qqq_5m)
        if qqq_day.empty:
            continue

        # QQQ T-1 收盘价（触发基准）
        qqq_t2_close = float(row["QQQ_T2收盘"])

        # QQQ T-1 开盘价（9:30 的收盘价，用于展示）
        qqq_open_row = qqq_day[qqq_day["time_only"] == "09:30"]
        if qqq_open_row.empty:
            qqq_open_row = qqq_day.iloc[:1]
        qqq_open = float(qqq_open_row.iloc[0]["收盘价"])

        # Call/Put 1min
        c1m = call_1m[call_1m["到期日"].astype(str).str[:10] == t1].copy()
        p1m = put_1m[put_1m["到期日"].astype(str).str[:10] == t1].copy()
        c1m["time_only"] = c1m["时间(美东)"].astype(str).str[-5:]
        p1m["time_only"] = p1m["时间(美东)"].astype(str).str[-5:]

        # 监控区间
        monitor_qqq = qqq_day[(qqq_day["time_only"] >= MONITOR_START) & (qqq_day["time_only"] <= MONITOR_END)]

        trigger_time = None
        trigger_pct  = None
        trigger_dir  = None

        for _, mrow in monitor_qqq.iterrows():
            t = mrow["time_only"]
            price = float(mrow["收盘价"])
            pct = (price - qqq_t2_close) / qqq_t2_close * 100
            if pct >= UPPER_TRIGGER_PCT or pct <= -LOWER_TRIGGER_PCT:
                trigger_time = t
                trigger_pct  = round(pct, 2)
                trigger_dir  = "涨" if pct > 0 else "跌"
                break

        # 卖出时间
        sell_time = trigger_time if trigger_time else MONITOR_END

        # 获取卖出时的期权价格
        c_sell_row = c1m[c1m["time_only"] == sell_time]
        p_sell_row = p1m[p1m["time_only"] == sell_time]

        # 如果精确时间没数据，找最近的
        if c_sell_row.empty:
            c_sell_candidates = c1m[c1m["time_only"] <= sell_time]
            if not c_sell_candidates.empty:
                c_sell_row = c_sell_candidates.iloc[[-1]]
        if p_sell_row.empty:
            p_sell_candidates = p1m[p1m["time_only"] <= sell_time]
            if not p_sell_candidates.empty:
                p_sell_row = p_sell_candidates.iloc[[-1]]

        call_sell = float(c_sell_row.iloc[0]["收盘价"]) if not c_sell_row.empty else 0
        put_sell  = float(p_sell_row.iloc[0]["收盘价"]) if not p_sell_row.empty else 0
        total_sell = call_sell + put_sell

        commission_total = COMMISSION * 4 / 100  # 4张次，转换为权利金单位（÷100）
        pnl = total_sell - total_cost - commission_total
        pnl_pct = (pnl / total_cost * 100) if total_cost > 0 else 0

        # QQQ 当日涨跌幅（相对 T-1 收盘价）
        qqq_close_row = qqq_day.iloc[-1]
        qqq_close = float(qqq_close_row["收盘价"])
        qqq_day_pct = round((qqq_close - qqq_t2_close) / qqq_t2_close * 100, 2)

        results.append({
            "到期日": t1,
            "基准日": t2,
            "QQQ_T2收盘": qqq_t2_close,
            "QQQ开盘": qqq_open,
            "QQQ收盘": qqq_close,
            "QQQ涨跌%": qqq_day_pct,
            "Call成本": call_cost,
            "Put成本": put_cost,
            "总成本": round(total_cost, 4),
            "触发": trigger_dir if trigger_dir else "未触发",
            "触发时间": trigger_time if trigger_time else "12:00止损",
            "触发涨跌%": trigger_pct if trigger_pct else None,
            "Call卖出": call_sell,
            "Put卖出": put_sell,
            "总卖出": round(total_sell, 4),
            "盈亏": round(pnl, 4),
            "盈亏%": round(pnl_pct, 2),
            "Call合约": row["Call合约"],
            "Put合约": row["Put合约"],
            "数据粒度": granularity,
            "VIX": None,  # 后续由 main() 注入
            "VIX_卖出": None,  # 后续由 main() 注入
        })

    return results


def build_daily_charts(results, call_1m, put_1m, qqq_1m, qqq_2m, qqq_5m, vix_5min_map=None):
    """为每个交易日构建 QQQ / Call / Put / VIX 的数据 + 标记点"""
    daily_data = []

    for r in results:
        t1 = r["到期日"]

        # QQQ（使用与回测相同粒度）
        qqq_day, _ = _get_qqq_day(t1, qqq_1m, qqq_2m, qqq_5m)
        qqq_arr = []
        for _, row in qqq_day.iterrows():
            t = str(row["时间"])[-5:]
            qqq_arr.append({
                "t": t,
                "o": float(row["开盘价"]),
                "h": float(row["最高价"]),
                "l": float(row["最低价"]),
                "c": float(row["收盘价"]),
                "v": int(row["成交量"]),
            })

        # Call 1min
        c1m = call_1m[call_1m["到期日"].astype(str).str[:10] == t1].copy()
        call_arr = []
        for _, row in c1m.iterrows():
            t = str(row["时间(美东)"])[-5:]
            call_arr.append({
                "t": t,
                "o": float(row["开盘价"]),
                "h": float(row["最高价"]),
                "l": float(row["最低价"]),
                "c": float(row["收盘价"]),
                "v": int(row["成交量"]),
            })

        # Put 1min
        p1m = put_1m[put_1m["到期日"].astype(str).str[:10] == t1].copy()
        put_arr = []
        for _, row in p1m.iterrows():
            t = str(row["时间(美东)"])[-5:]
            put_arr.append({
                "t": t,
                "o": float(row["开盘价"]),
                "h": float(row["最高价"]),
                "l": float(row["最低价"]),
                "c": float(row["收盘价"]),
                "v": int(row["成交量"]),
            })

        # VIX 5min
        vix_arr = []
        if vix_5min_map and t1 in vix_5min_map:
            vix_arr = vix_5min_map[t1]

        daily_data.append({
            "date": t1,
            "granularity": r["数据粒度"],
            "qqq": qqq_arr,
            "call": call_arr,
            "put": put_arr,
            "vix": vix_arr,
        })

    return daily_data


def generate_html(results3, daily3, results4, daily4, vix_daily_data=None):
    results = results3  # 初始显示 ±3
    daily_data = daily3
    total_trades = len(results)
    wins = sum(1 for r in results if r["盈亏"] > 0)
    losses = sum(1 for r in results if r["盈亏"] <= 0)
    total_pnl = sum(r["盈亏"] for r in results)
    total_cost_sum = sum(r["总成本"] for r in results)
    win_rate = round(wins / total_trades * 100, 1) if total_trades > 0 else 0
    avg_pnl = round(total_pnl / total_trades, 4) if total_trades > 0 else 0
    triggered = sum(1 for r in results if r["触发"] != "未触发")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QQQ 末日期权双买策略分析</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; background: #0a0e17; color: #e0e0e0; }}
.header {{ background: linear-gradient(135deg, #1a1f2e 0%, #0d1117 100%); padding: 20px 30px; border-bottom: 1px solid #2a3040; }}
.header h1 {{ font-size: 22px; color: #58a6ff; }}
.header .sub {{ font-size: 13px; color: #8b949e; margin-top: 5px; }}
.header .sub-detail {{ margin-top: 10px; display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 6px 30px; }}
.header .sub-detail .item {{ font-size: 12px; color: #6e7681; display: flex; align-items: baseline; gap: 6px; }}
.header .sub-detail .item .tag {{ font-size: 10px; font-weight: bold; border-radius: 3px; padding: 1px 5px; white-space: nowrap; }}
.header .sub-detail .item .tag.blue {{ background: rgba(88,166,255,0.15); color: #58a6ff; }}
.header .sub-detail .item .tag.green {{ background: rgba(63,185,80,0.15); color: #3fb950; }}
.header .sub-detail .item .tag.red {{ background: rgba(248,81,73,0.15); color: #f85149; }}
.header .sub-detail .item .tag.yellow {{ background: rgba(210,153,34,0.15); color: #d29922; }}
.stats-row {{ display: flex; gap: 12px; padding: 15px 30px; flex-wrap: wrap; }}
.stat-card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px 18px; min-width: 140px; flex: 1; }}
.stat-card .label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; }}
.stat-card .value {{ font-size: 22px; font-weight: bold; margin-top: 4px; }}
.stat-card .value.green {{ color: #3fb950; }}
.stat-card .value.red {{ color: #f85149; }}
.stat-card .value.blue {{ color: #58a6ff; }}
.stat-card .value.yellow {{ color: #d29922; }}
.section {{ padding: 15px 30px; }}
.section h2 {{ font-size: 16px; color: #c9d1d9; margin-bottom: 10px; border-left: 3px solid #58a6ff; padding-left: 10px; }}
#cumChart {{ width: 100%; height: 200px; background: #161b22; border: 1px solid #30363d; border-radius: 8px; display: block; }}
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ background: #161b22; color: #8b949e; padding: 8px 10px; text-align: right; border-bottom: 1px solid #30363d; position: sticky; top: 0; z-index: 10; white-space: nowrap; }}
th:first-child, td:first-child {{ text-align: left; }}
td {{ padding: 7px 10px; border-bottom: 1px solid #21262d; text-align: right; cursor: pointer; white-space: nowrap; }}
tr.data-row:hover td {{ background: #1c2333; }}
tr.data-row.selected td {{ background: #1e3a5f !important; }}
.pnl-pos {{ color: #3fb950; font-weight: bold; }}
.pnl-neg {{ color: #f85149; font-weight: bold; }}
.trigger-up {{ color: #3fb950; }}
.trigger-down {{ color: #f85149; }}
.trigger-none {{ color: #d29922; }}
.detail-tr td {{ padding: 0; background: #0a0e17 !important; border-bottom: 2px solid #58a6ff; cursor: default; }}
.detail-inner {{ padding: 12px 20px 16px; }}
.detail-header {{ display: flex; align-items: center; gap: 20px; margin-bottom: 10px; flex-wrap: wrap; font-size: 13px; }}
.chart-grid3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }}
.chart-box {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px; overflow: hidden; }}
.chart-box-title {{ font-size: 11px; color: #8b949e; text-align: center; padding: 5px 0 0; }}
.chart-box canvas {{ width: 100%; height: 340px; display: block; }}
.legend {{ display: flex; gap: 16px; margin: 6px 0; justify-content: center; font-size: 11px; }}
.legend span {{ display: flex; align-items: center; gap: 4px; }}
.dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
@media (max-width: 900px) {{
  .chart-grid3 {{ grid-template-columns: 1fr; }}
  .stats-row {{ flex-direction: column; }}
}}
.ctrl-bar {{ display:flex; align-items:center; gap:12px; padding:10px 30px 4px; flex-wrap:wrap; background:#0d1117; border-bottom:1px solid #21262d; }}
.ctrl-bar label {{ font-size:12px; color:#8b949e; }}
.ctrl-bar input[type=number] {{
  width:68px; background:#161b22; border:1px solid #30363d; border-radius:5px;
  color:#e0e0e0; font-size:14px; padding:4px 8px; text-align:center; outline:none;
}}
.ctrl-bar input[type=number]:focus {{ border-color:#58a6ff; }}
.ctrl-bar input[type=time] {{
  width:86px; background:#161b22; border:1px solid #30363d; border-radius:5px;
  color:#e0e0e0; font-size:13px; padding:4px 6px; outline:none;
  color-scheme:dark;
}}
.ctrl-bar input[type=time]:focus {{ border-color:#58a6ff; }}
.ctrl-btn {{
  background:#1f6feb; border:none; border-radius:5px; color:#fff;
  font-size:12px; padding:5px 14px; cursor:pointer; font-weight:bold;
}}
.ctrl-btn:hover {{ background:#388bfd; }}
.ctrl-hint {{ font-size:11px; color:#636e7b; margin-left:6px; }}
.strike-switch {{ display:flex; gap:0; border:1px solid #30363d; border-radius:6px; overflow:hidden; margin-right:4px; }}
.strike-switch button {{ background:#161b22; border:none; color:#8b949e; font-size:12px; font-weight:bold; padding:5px 14px; cursor:pointer; transition:background 0.15s,color 0.15s; }}
.strike-switch button.active {{ background:#1f6feb; color:#fff; }}
.strike-switch button:hover:not(.active) {{ background:#21262d; color:#e0e0e0; }}
</style>
</head>
<body>
<div class="header">
  <h1>QQQ 末日期权双买策略回测分析</h1>
  <div class="sub">双买 0DTE 期权策略回测 &nbsp;·&nbsp; 数据范围：{results3[0]["到期日"]} ~ {results3[-1]["到期日"]} &nbsp;·&nbsp; 共 {total_trades} 个交易日</div>
  <div class="sub-detail">
    <div class="item"><span class="tag blue">建仓 T-1</span>以 T-1（到期日前一天）收盘价买入 Call(行权价±3或±4) + Put(行权价±3或±4) 各一张</div>
    <div class="item"><span class="tag green">触发卖出</span>T（到期日当天）美东 9:35 起监控，QQQ 相对 T-1 收盘价涨幅 ≥ +阈值% 或跌幅 ≥ −阈值%，立即同时卖出 Call + Put</div>
    <div class="item"><span class="tag red">12:00 止损</span>若 9:35～12:00 全程未触发，于 12:00 以当时价止损平仓，通常大幅亏损</div>
    <div class="item"><span class="tag yellow">数据降级</span>优先使用 1min K 线定位触发时间；无 1min 则降级 2min → 5min，粒度越粗触发时间误差越大</div>
    <div class="item"><span class="tag blue">盈亏计算</span>（Call卖出价 + Put卖出价）−（Call买入价 + Put买入价）− 手续费，单位为每张合约权利金（×100 = 实际美元）</div>
    <div class="item"><span class="tag red">手续费</span>买入 Call + Put（2张）+ 卖出 Call + Put（2张）= 共 4 张次，默认 ${COMMISSION}/张，总手续费 ${round(COMMISSION*4,2)}（可在上方调整）</div>
    <div class="item"><span class="tag green">合约选择</span>距到期日行权价最近的整数档：Call = round(T-1收盘 + N)，Put = round(T-1收盘 − N)，N可切换 3 或 4</div>
  </div>
</div>
<div class="ctrl-bar">
  <div class="strike-switch">
    <button id="btn-strike3" class="active" onclick="switchStrike(3)">±3 行权价</button>
    <button id="btn-strike4" onclick="switchStrike(4)">±4 行权价</button>
  </div>
  <label>上涨触发</label>
  <span style="color:#3fb950;font-weight:bold">+</span>
  <input type="number" id="upperPct" value="{UPPER_TRIGGER_PCT}" min="0.1" max="20" step="0.1">
  <span style="color:#8b949e;font-size:12px">%</span>
  <label style="margin-left:10px">下跌触发</label>
  <span style="color:#f85149;font-weight:bold">−</span>
  <input type="number" id="lowerPct" value="{LOWER_TRIGGER_PCT}" min="0.1" max="20" step="0.1">
  <span style="color:#8b949e;font-size:12px">%</span>
  <label style="margin-left:10px">手续费</label>
  <input type="number" id="commission" value="{COMMISSION}" min="0" max="50" step="0.1">
  <span style="color:#8b949e;font-size:12px">$/张</span>
  <label style="margin-left:10px">平仓时间</label>
  <input type="time" id="closeTime" value="{MONITOR_END}" min="09:35" max="15:00">
  <button class="ctrl-btn" onclick="applyThreshold()">▶ 重新计算</button>
  <span class="ctrl-hint" id="ctrlHint">当前：上涨 +{UPPER_TRIGGER_PCT}% / 下跌 −{LOWER_TRIGGER_PCT}% / 手续费 ${COMMISSION}/张 / 平仓 {MONITOR_END}</span>
</div>
<div class="stats-row">
  <div class="stat-card"><div class="label">交易天数</div><div class="value blue" id="s-days">{total_trades}</div></div>
  <div class="stat-card"><div class="label">胜率</div><div class="value" id="s-winrate">{win_rate}%</div></div>
  <div class="stat-card"><div class="label">盈利 / 亏损</div><div class="value" id="s-wl"><span class="green">{wins}</span>&nbsp;/&nbsp;<span class="red">{losses}</span></div></div>
  <div class="stat-card"><div class="label">触发次数</div><div class="value yellow" id="s-trig">{triggered}&nbsp;/&nbsp;{total_trades}</div></div>
  <div class="stat-card"><div class="label">累计盈亏</div><div class="value {'green' if total_pnl>=0 else 'red'}" id="s-totpnl">${round(total_pnl*100,2)}</div></div>
  <div class="stat-card"><div class="label">总投入成本</div><div class="value blue" id="s-cost">${round(total_cost_sum*100,2)}</div></div>
  <div class="stat-card"><div class="label">平均每日盈亏</div><div class="value {'green' if avg_pnl>=0 else 'red'}" id="s-avgpnl">${round(avg_pnl*100,2)}</div></div>
</div>
<div class="section">
  <h2>累计盈亏曲线</h2>
  <canvas id="cumChart"></canvas>
</div>
<div class="section">
  <h2>VIX 日K线</h2>
  <canvas id="vixDailyCanvas" style="width:100%;height:280px;background:#161b22;border:1px solid #30363d;border-radius:8px;display:block"></canvas>
</div>
<div class="section">
  <h2>VIX 与策略盈亏相关性</h2>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
    <div><canvas id="vixScatter" style="width:100%;height:280px;background:#161b22;border:1px solid #30363d;border-radius:8px"></canvas></div>
    <div><canvas id="vixBarChart" style="width:100%;height:280px;background:#161b22;border:1px solid #30363d;border-radius:8px"></canvas></div>
  </div>
</div>
<div class="section">
  <h2>每日交易明细 <span style="font-size:12px;color:#8b949e">（点击行展开日内K线，再次点击收起）</span></h2>
  <div class="table-wrap">
  <table id="tradeTable">
    <thead>
      <tr>
        <th>到期日</th><th>VIX买</th><th>VIX卖</th><th>粒度</th><th>T-1收盘</th><th>T开盘</th><th>QQQ涨跌%(vs T-1收)</th>
        <th>Call成本</th><th>Put成本</th><th>总成本</th>
        <th>触发</th><th>触发时间</th><th>触发涨跌%</th>
        <th>Call卖出</th><th>Put卖出</th><th>总卖出</th>
        <th>盈亏</th><th>盈亏%</th><th>盈亏($)</th>
      </tr>
    </thead>
    <tbody>
"""

    for i, r in enumerate(results):
        pnl_class = "pnl-pos" if r["盈亏"] > 0 else "pnl-neg"
        trig_class = "trigger-up" if r["触发"] == "涨" else ("trigger-down" if r["触发"] == "跌" else "trigger-none")
        trig_pct_str = f'{r["触发涨跌%"]}%' if r["触发涨跌%"] is not None else "-"
        qqq_pct_class = "pnl-pos" if r["QQQ涨跌%"] > 0 else "pnl-neg"
        pnl_dollar = round(r["盈亏"] * 100, 2)
        gran_color = "" if r["数据粒度"] == "1min" else ("color:#d29922" if r["数据粒度"] == "2min" else "color:#f85149")

        vix_buy = r.get("VIX")
        vix_sell_val = r.get("VIX_卖出")
        vix_buy_str = f'{vix_buy:.1f}' if vix_buy is not None else '-'
        vix_sell_str = f'{vix_sell_val:.1f}' if vix_sell_val is not None else '-'
        vix_buy_color = 'color:#f85149' if vix_buy and vix_buy >= 25 else ('color:#d29922' if vix_buy and vix_buy >= 20 else 'color:#3fb950')
        vix_sell_color = 'color:#f85149' if vix_sell_val and vix_sell_val >= 25 else ('color:#d29922' if vix_sell_val and vix_sell_val >= 20 else 'color:#3fb950')
        html += f"""      <tr class="data-row" data-idx="{i}" onclick="selectDay({i})">
        <td style="text-align:left">{r["到期日"]}</td>
        <td style="{vix_buy_color};font-weight:bold">{vix_buy_str}</td>
        <td style="{vix_sell_color};font-weight:bold">{vix_sell_str}</td>
        <td style="{gran_color}">{r["数据粒度"]}</td>
        <td>${r["QQQ_T2收盘"]}</td>
        <td>${r["QQQ开盘"]}</td>
        <td class="{qqq_pct_class}">{r["QQQ涨跌%"]}%</td>
        <td>${r["Call成本"]}</td><td>${r["Put成本"]}</td><td>${r["总成本"]}</td>
        <td class="{trig_class}">{r["触发"]}</td>
        <td>{r["触发时间"]}</td><td>{trig_pct_str}</td>
        <td>${r["Call卖出"]}</td><td>${r["Put卖出"]}</td><td>${r["总卖出"]}</td>
        <td class="{pnl_class}">${r["盈亏"]}</td>
        <td class="{pnl_class}">{r["盈亏%"]}%</td>
        <td class="{pnl_class}">${pnl_dollar}</td>
      </tr>
"""

    html += """    </tbody>
  </table>
  </div>
</div>

<script>
"""
    html += f"const RESULTS_3  = {json.dumps(results3, ensure_ascii=False)};\n"
    html += f"const DAILY_3    = {json.dumps(daily3, ensure_ascii=False)};\n"
    html += f"const RESULTS_4  = {json.dumps(results4, ensure_ascii=False)};\n"
    html += f"const DAILY_4    = {json.dumps(daily4, ensure_ascii=False)};\n"
    # 初始盈亏序列（±3）
    cum3 = []
    s = 0
    for r in results3:
        s += r['盈亏']
        cum3.append(round(s, 4))
    cum4 = []
    s = 0
    for r in results4:
        s += r['盈亏']
        cum4.append(round(s, 4))
    html += f"const CUM_PNL_3  = {json.dumps(cum3)};\n"
    html += f"const CUM_PNL_4  = {json.dumps(cum4)};\n"
    html += f"const VIX_DAILY_DATA = {json.dumps(vix_daily_data or [], ensure_ascii=False)};\n"
    html += f"const UPPER_TRIGGER_PCT = {UPPER_TRIGGER_PCT};\n"
    html += f"const LOWER_TRIGGER_PCT = {LOWER_TRIGGER_PCT};\n"
    html += f"const COMMISSION   = {COMMISSION};\n"

    html += r"""
const MONITOR_START = '09:30';
const MONITOR_END   = '10:30';

// ─── 动态重算引擎 ───
let _upperPct   = UPPER_TRIGGER_PCT;
let _lowerPct   = LOWER_TRIGGER_PCT;
let _commission = COMMISSION;
let _monitorEnd = MONITOR_END;  // 可调平仓时间
let _strike     = 3;  // 当前行权价偏移：3 或 4
// 当前数据源（随行权价切换）
let _baseResults = RESULTS_3.slice();
let _baseDaily   = DAILY_3;
// 重算后有效数据（供 selectDay/累计图使用）
let _activeResults = RESULTS_3.slice();
let _activeCumPnl  = CUM_PNL_3.slice();

function switchStrike(n) {
  _strike = n;
  _baseResults = n === 3 ? RESULTS_3.slice() : RESULTS_4.slice();
  _baseDaily   = n === 3 ? DAILY_3 : DAILY_4;
  document.getElementById('btn-strike3').classList.toggle('active', n === 3);
  document.getElementById('btn-strike4').classList.toggle('active', n === 4);
  // 关闭展开行
  if (currentIdx >= 0) {
    const old = document.getElementById('detailRow');
    if (old) old.remove();
    document.querySelectorAll('#tradeTable .data-row').forEach(tr => tr.classList.remove('selected'));
    currentIdx = -1;
  }
  applyThreshold();
}

function applyThreshold() {
  const up = parseFloat(document.getElementById('upperPct').value);
  const lo = parseFloat(document.getElementById('lowerPct').value);
  const cm = parseFloat(document.getElementById('commission').value);
  const ct = document.getElementById('closeTime').value || _monitorEnd;
  if (isNaN(up) || isNaN(lo) || up <= 0 || lo <= 0) return;
  if (isNaN(cm) || cm < 0) return;
  if (ct < '09:35' || ct > '15:00') { alert('平仓时间需在 09:35 ~ 15:00 之间'); return; }
  _upperPct = up; _lowerPct = lo; _commission = cm; _monitorEnd = ct;
  document.getElementById('ctrlHint').textContent =
    '当前：上涨 +' + up + '% / 下跌 −' + lo + '% / 手续费 $' + cm + '/张 / 平仓 ' + ct;

  // 重算每日触发 & 盈亏
  const newResults = [];
  let cumPnl = 0;
  const newCum = [];

  for (let i = 0; i < _baseResults.length; i++) {
    const r  = Object.assign({}, _baseResults[i]);
    const d  = _baseDaily[i];
    const t2 = r['QQQ_T2收盘'];

    // 扫描 QQQ 分钟数据找触发点
    let trigTime = null, trigPct = null, trigDir = null;
    for (const bar of d.qqq) {
      if (bar.t < MONITOR_START || bar.t > _monitorEnd) continue;
      const pct = (bar.c - t2) / t2 * 100;
      if (pct >= up)        { trigTime = bar.t; trigPct = +pct.toFixed(2); trigDir = '涨'; break; }
      if (pct <= -lo)       { trigTime = bar.t; trigPct = +pct.toFixed(2); trigDir = '跌'; break; }
    }
    const sellTime = trigTime || _monitorEnd;

    // 找对应时间的期权价格（精确 or 最近一根）
    const getPrice = (arr, t) => {
      let row = arr.find(x => x.t === t);
      if (!row) row = arr.filter(x => x.t <= t).slice(-1)[0];
      return row ? row.c : 0;
    };
    const callSell = getPrice(d.call, sellTime);
    const putSell  = getPrice(d.put,  sellTime);
    const totalSell = callSell + putSell;
    const commissionTotal = _commission * 4 / 100;  // 4张次 ÷100 转为权利金单位
    const pnl = totalSell - r['总成本'] - commissionTotal;

    r['触发']     = trigDir || '未触发';
    r['触发时间'] = trigTime || (_monitorEnd + '止损');
    r['触发涨跌%'] = trigPct;
    r['Call卖出'] = +callSell.toFixed(4);
    r['Put卖出']  = +putSell.toFixed(4);
    r['总卖出']   = +totalSell.toFixed(4);
    r['盈亏']     = +pnl.toFixed(4);
    r['盈亏%']    = r['总成本'] > 0 ? +(pnl / r['总成本'] * 100).toFixed(2) : 0;
    cumPnl += pnl;
    newResults.push(r);
    newCum.push(+cumPnl.toFixed(4));
  }

  _activeResults = newResults;
  _activeCumPnl  = newCum;

  // 更新统计卡片
  const n     = newResults.length;
  const wins  = newResults.filter(r => r['盈亏'] > 0).length;
  const losses = n - wins;
  const trig  = newResults.filter(r => r['触发'] !== '未触发').length;
  const totPnl = cumPnl * 100;
  const avgPnl = n ? totPnl / n : 0;
  const wr    = n ? +(wins / n * 100).toFixed(1) : 0;
  const totalCost = newResults.reduce((s, r) => s + r['总成本'] * 100, 0).toFixed(2);

  const setEl = (id, html, col) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = html;
    el.className = 'value ' + (col || '');
  };
  setEl('s-days',    n, 'blue');
  setEl('s-winrate', wr + '%', wr >= 50 ? 'green' : 'red');
  setEl('s-wl',      '<span class="green">' + wins + '</span>&nbsp;/&nbsp;<span class="red">' + losses + '</span>');
  setEl('s-trig',    trig + '&nbsp;/&nbsp;' + n, 'yellow');
  setEl('s-totpnl',  '$' + totPnl.toFixed(2), totPnl >= 0 ? 'green' : 'red');
  setEl('s-cost',    '$' + totalCost, 'blue');
  setEl('s-avgpnl',  '$' + avgPnl.toFixed(2), avgPnl >= 0 ? 'green' : 'red');

  // 更新表格行
  _rebuildTableRows(newResults);

  // 更新累计盈亏图（用新的cum数组）
  _cumHoverIdx = -1;
  _renderCumChartWith(newResults, newCum);

  // 更新 VIX 图表
  drawVixDailyChart();
  drawVixCharts();

  // 如果有展开行，关闭它
  if (currentIdx >= 0) {
    const old = document.getElementById('detailRow');
    if (old) old.remove();
    document.querySelectorAll('#tradeTable .data-row').forEach(tr => tr.classList.remove('selected'));
    currentIdx = -1;
  }
}

function _rebuildTableRows(results) {
  const tbody = document.querySelector('#tradeTable tbody');
  tbody.innerHTML = '';
  results.forEach((r, i) => {
    const pnl = r['盈亏'];
    const pc  = pnl > 0 ? 'pnl-pos' : 'pnl-neg';
    const qpc = r['QQQ涨跌%'] >= 0 ? 'pnl-pos' : 'pnl-neg';
    const tc  = r['触发'] === '涨' ? 'trigger-up' : r['触发'] === '跌' ? 'trigger-down' : 'trigger-none';
    const gc  = r['数据粒度'] === '1min' ? '' : r['数据粒度'] === '2min' ? 'color:#d29922' : 'color:#f85149';
    const pctStr = r['触发涨跌%'] != null ? r['触发涨跌%'] + '%' : '-';
    const pnlD = (pnl * 100).toFixed(2);
    const tr = document.createElement('tr');
    tr.className = 'data-row';
    tr.dataset.idx = i;
    tr.onclick = () => selectDay(i);
    const vBuy = r['VIX'];
    const vSell = r['VIX_卖出'];
    const vBuyS = vBuy != null ? vBuy.toFixed(1) : '-';
    const vSellS = vSell != null ? vSell.toFixed(1) : '-';
    const vBuyC = vBuy >= 25 ? 'color:#f85149' : vBuy >= 20 ? 'color:#d29922' : 'color:#3fb950';
    const vSellC = vSell >= 25 ? 'color:#f85149' : vSell >= 20 ? 'color:#d29922' : 'color:#3fb950';
    tr.innerHTML =
      '<td style="text-align:left">' + r['到期日'] + '</td>' +
      '<td style="' + vBuyC + ';font-weight:bold">' + vBuyS + '</td>' +
      '<td style="' + vSellC + ';font-weight:bold">' + vSellS + '</td>' +
      '<td style="' + gc + '">' + r['数据粒度'] + '</td>' +
      '<td>$' + r['QQQ_T2收盘'] + '</td>' +
      '<td>$' + r['QQQ开盘'] + '</td>' +
      '<td class="' + qpc + '">' + r['QQQ涨跌%'] + '%</td>' +
      '<td>$' + r['Call成本'] + '</td><td>$' + r['Put成本'] + '</td><td>$' + r['总成本'] + '</td>' +
      '<td class="' + tc + '">' + r['触发'] + '</td>' +
      '<td>' + r['触发时间'] + '</td><td>' + pctStr + '</td>' +
      '<td>$' + r['Call卖出'] + '</td><td>$' + r['Put卖出'] + '</td><td>$' + r['总卖出'] + '</td>' +
      '<td class="' + pc + '">$' + r['盈亏'] + '</td>' +
      '<td class="' + pc + '">' + r['盈亏%'] + '%</td>' +
      '<td class="' + pc + '">$' + pnlD + '</td>';
    tbody.appendChild(tr);
  });
}

// ─── 累计盈亏曲线（支持外部数据） ───
let _cumHoverIdx = -1;

function _renderCumChartWith(results, cumVals) {
  const canvas = document.getElementById('cumChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width) return;
  canvas.width = rect.width * dpr; canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  const W = rect.width, H = rect.height;
  const pad = {t:20, r:20, b:35, l:65};
  const dates  = results.map(r => r['到期日'].slice(5));
  const vals   = cumVals.map(v => v * 100);
  const dailyP = results.map(r => +(r['盈亏'] * 100).toFixed(2));
  const minV = Math.min(0, ...vals), maxV = Math.max(0, ...vals);
  const range = maxV - minV || 1;
  const xStep = (W - pad.l - pad.r) / (vals.length - 1 || 1);
  const toY = v => pad.t + (maxV - v) / range * (H - pad.t - pad.b);

  ctx.strokeStyle = '#30363d'; ctx.lineWidth = 1; ctx.setLineDash([4,4]);
  const y0 = toY(0);
  ctx.beginPath(); ctx.moveTo(pad.l, y0); ctx.lineTo(W-pad.r, y0); ctx.stroke();
  ctx.setLineDash([]);

  ctx.beginPath();
  for (let i = 0; i < vals.length; i++) {
    const x = pad.l + i * xStep, y = toY(vals[i]);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  const lastColor = vals[vals.length-1] >= 0 ? '#3fb950' : '#f85149';
  ctx.strokeStyle = lastColor; ctx.lineWidth = 2; ctx.stroke();
  ctx.lineTo(pad.l + (vals.length-1)*xStep, y0); ctx.lineTo(pad.l, y0); ctx.closePath();
  ctx.fillStyle = vals[vals.length-1] >= 0 ? 'rgba(63,185,80,0.12)' : 'rgba(248,81,73,0.12)';
  ctx.fill();

  for (let i = 0; i < vals.length; i++) {
    const x = pad.l + i * xStep, y = toY(vals[i]);
    ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI*2);
    ctx.fillStyle = vals[i] >= 0 ? '#3fb950' : '#f85149'; ctx.fill();
  }

  ctx.fillStyle = '#8b949e'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
  const ls = Math.max(1, Math.floor(vals.length/15));
  for (let i = 0; i < vals.length; i += ls) ctx.fillText(dates[i], pad.l + i*xStep, H-8);
  ctx.textAlign = 'right';
  for (let i = 0; i <= 5; i++) {
    const v = minV + range*i/5, y = toY(v);
    ctx.fillStyle = '#8b949e'; ctx.fillText('$'+v.toFixed(0), pad.l-6, y+4);
    ctx.strokeStyle = '#21262d'; ctx.lineWidth = 0.5;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W-pad.r, y); ctx.stroke();
  }

  // hover
  const hi = _cumHoverIdx;
  if (hi >= 0 && hi < vals.length) {
    const hx = pad.l + hi * xStep, hy = toY(vals[hi]);
    ctx.setLineDash([3,3]); ctx.strokeStyle = 'rgba(160,180,220,0.5)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(hx, pad.t); ctx.lineTo(hx, H-pad.b); ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(hx, hy, 5, 0, Math.PI*2);
    ctx.fillStyle = vals[hi] >= 0 ? '#3fb950' : '#f85149'; ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();

    const dp = dailyP[hi], cv = vals[hi];
    const line1 = results[hi]['到期日'] + '  ' + (results[hi]['触发'] !== '未触发' ? results[hi]['触发时间'] + '触发' : '未触发止损');
    const line2 = '当日盈亏: ' + (dp >= 0 ? '+' : '') + '$' + dp.toFixed(2);
    const line3 = '累计盈亏: ' + (cv >= 0 ? '+' : '') + '$' + cv.toFixed(2);
    ctx.font = 'bold 11px sans-serif';
    const tw = Math.max(ctx.measureText(line1).width, ctx.measureText(line2).width, ctx.measureText(line3).width);
    const bw = tw + 20, bh = 58, br = 5;
    let bx = hx + 10, by = hy - bh - 8;
    if (bx + bw > W - pad.r) bx = hx - bw - 10;
    if (by < pad.t) by = hy + 10;
    ctx.fillStyle = 'rgba(22,27,34,0.95)';
    ctx.beginPath(); ctx.roundRect(bx, by, bw, bh, br); ctx.fill();
    ctx.strokeStyle = dp >= 0 ? '#3fb950' : '#f85149'; ctx.lineWidth = 1; ctx.stroke();
    ctx.textAlign = 'left';
    ctx.font = '10px sans-serif'; ctx.fillStyle = '#8b949e'; ctx.fillText(line1, bx+10, by+16);
    ctx.font = 'bold 12px sans-serif';
    ctx.fillStyle = dp >= 0 ? '#3fb950' : '#f85149'; ctx.fillText(line2, bx+10, by+34);
    ctx.fillStyle = cv >= 0 ? '#3fb950' : '#f85149'; ctx.fillText(line3, bx+10, by+52);
  }
}

function drawCumChart() {
  _renderCumChartWith(_activeResults, _activeCumPnl);
  const canvas = document.getElementById('cumChart');
  canvas.onmousemove = (e) => {
    const rect = canvas.getBoundingClientRect();
    const pad = {l:65, r:20};
    const n = _activeResults.length;
    const xStep = (rect.width - pad.l - pad.r) / (n - 1 || 1);
    const mx = e.clientX - rect.left;
    _cumHoverIdx = Math.max(0, Math.min(n-1, Math.round((mx - pad.l) / xStep)));
    _renderCumChartWith(_activeResults, _activeCumPnl);
  };
  canvas.onmouseleave = () => {
    _cumHoverIdx = -1;
    _renderCumChartWith(_activeResults, _activeCumPnl);
  };
}

// ─── 蜡烛图引擎 ───
const _chartState = {}; // canvasId -> {data, markers, thresholds, meta, hoverIdx}

function drawCandleChart(canvasId, data, markers, thresholds, meta, refPrice) {
  _chartState[canvasId] = {data, markers, thresholds, meta, refPrice: refPrice||null, hoverIdx: -1};
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  _renderCandle(canvas, canvasId);
  canvas.onmousemove = (e) => {
    const s = _chartState[canvasId];
    if (!s || !s.data || !s.data.length) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const W = rect.width, padL = 60, padR = s.refPrice ? 46 : 22;
    const n = s.data.length;
    const barW = (W - padL - padR) / n;
    const idx = Math.max(0, Math.min(n-1, Math.floor((mx - padL) / barW)));
    s.hoverIdx = idx;
    _renderCandle(canvas, canvasId);
  };
  canvas.onmouseleave = () => {
    if (_chartState[canvasId]) { _chartState[canvasId].hoverIdx = -1; _renderCandle(canvas, canvasId); }
  };
}

function _renderCandle(canvas, canvasId) {
  const s = _chartState[canvasId];
  if (!s) return;
  const {data, markers, thresholds, meta, refPrice, hoverIdx} = s;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width) return;
  canvas.width = rect.width * dpr; canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  const W = rect.width, H = rect.height;
  const pad = {t:52, r: refPrice ? 46 : 22, b:28, l:60};
  const volH = 48;
  const chartH = H - pad.t - pad.b - volH - 4;
  const chartBot = pad.t + chartH;
  const volTop = chartBot + 4;
  const volBot = H - pad.b;

  ctx.fillStyle = '#161b22'; ctx.fillRect(0,0,W,H);

  if (!data || data.length === 0) {
    ctx.fillStyle = '#8b949e'; ctx.font = '13px sans-serif';
    ctx.textAlign = 'center'; ctx.fillText('无数据', W/2, H/2); return;
  }
  const n = data.length;
  let minP = Math.min(...data.map(d=>d.l));
  let maxP = Math.max(...data.map(d=>d.h));
  if (thresholds) {
    minP = Math.min(minP, ...thresholds.map(t=>t.val));
    maxP = Math.max(maxP, ...thresholds.map(t=>t.val));
  }
  const pm = (maxP - minP) * 0.06 || 0.5;
  minP -= pm; maxP += pm;
  const pRange = maxP - minP;
  const maxVol = Math.max(...data.map(d=>d.v||0)) || 1;
  const barW = (W - pad.l - pad.r) / n;
  const toX  = i => pad.l + (i + 0.5) * barW;
  const toY  = v => pad.t + (maxP - v) / pRange * chartH;
  const toVY = v => volBot - (v / maxVol) * (volBot - volTop);

  // grid
  ctx.strokeStyle = '#21262d'; ctx.lineWidth = 0.5;
  ctx.textAlign = 'right'; ctx.font = '10px sans-serif'; ctx.fillStyle = '#8b949e';
  for (let i = 0; i <= 5; i++) {
    const v = minP + pRange*i/5, y = toY(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W-pad.r, y); ctx.stroke();
    ctx.fillText('$'+v.toFixed(2), pad.l-4, y+3);
    // 右侧显示相对 T-1 收盘的涨跌幅
    if (refPrice) {
      const rPct = (v - refPrice) / refPrice * 100;
      const rCol = rPct >= _upperPct ? '#3fb950' : rPct <= -_lowerPct ? '#f85149' : '#636e7b';
      ctx.fillStyle = rCol; ctx.textAlign = 'left'; ctx.font = '9px sans-serif';
      ctx.fillText((rPct>=0?'+':'')+rPct.toFixed(1)+'%', W-pad.r+3, y+3);
      ctx.fillStyle = '#8b949e'; ctx.textAlign = 'right'; ctx.font = '10px sans-serif';
    }
  }
  // vol separator
  ctx.strokeStyle = '#30363d'; ctx.lineWidth = 0.5;
  ctx.beginPath(); ctx.moveTo(pad.l, volTop); ctx.lineTo(W-pad.r, volTop); ctx.stroke();

  // threshold lines
  if (thresholds) {
    ctx.setLineDash([6,3]);
    for (const tl of thresholds) {
      const y = toY(tl.val);
      ctx.strokeStyle = tl.color; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W-pad.r, y); ctx.stroke();
      ctx.fillStyle = tl.color; ctx.font = '10px sans-serif'; ctx.textAlign = 'left';
      ctx.fillText(tl.label, W-pad.r+2, y+3);
    }
    ctx.setLineDash([]);
  }

  // volume bars
  for (let i = 0; i < n; i++) {
    const d = data[i], x = pad.l + i*barW;
    ctx.fillStyle = d.c >= d.o ? 'rgba(63,185,80,0.35)' : 'rgba(248,81,73,0.35)';
    const vy = toVY(d.v||0);
    ctx.fillRect(x+1, vy, barW-2, volBot-vy);
  }

  // candles
  for (let i = 0; i < n; i++) {
    const d = data[i];
    const x = toX(i);
    const isGreen = d.c >= d.o;
    const col = isGreen ? '#3fb950' : '#f85149';
    // wick
    ctx.strokeStyle = col; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, toY(d.h)); ctx.lineTo(x, toY(d.l)); ctx.stroke();
    // body
    const bW = Math.max(1.5, barW*0.65);
    const bTop = toY(Math.max(d.o, d.c));
    const bBot = toY(Math.min(d.o, d.c));
    const bH = Math.max(1, bBot - bTop);
    ctx.fillStyle = col;
    ctx.fillRect(x-bW/2, bTop, bW, bH);
  }

  // x-axis labels
  ctx.fillStyle = '#8b949e'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
  const ls = Math.max(1, Math.floor(n/8));
  for (let i = 0; i < n; i += ls) ctx.fillText(data[i].t, toX(i), H-8);

  // markers
  if (markers) {
    for (const m of markers) {
      const idx = data.findIndex(d => d.t >= m.time);
      if (idx < 0) continue;
      const x = toX(idx), y = toY(data[idx].h) - 18;
      ctx.beginPath();
      ctx.moveTo(x, toY(data[idx].h) - 4);
      ctx.lineTo(x-7, y-10); ctx.lineTo(x+7, y-10); ctx.closePath();
      ctx.fillStyle = m.color; ctx.fill();
      ctx.fillStyle = m.color; ctx.font = 'bold 9px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(m.label, x, y-12);
    }
  }

  // hover crosshair
  if (hoverIdx >= 0 && hoverIdx < n) {
    const x = toX(hoverIdx), cy = toY(data[hoverIdx].c);
    ctx.setLineDash([3,3]); ctx.strokeStyle = 'rgba(160,180,220,0.45)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, chartBot); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(pad.l, cy); ctx.lineTo(W-pad.r, cy); ctx.stroke();
    ctx.setLineDash([]);
    // price badge (left)
    const hPrice = data[hoverIdx].c;
    if (refPrice) {
      // 双行标签：价格 + 涨跌幅
      const hPct = (hPrice - refPrice) / refPrice * 100;
      const hPctStr = (hPct>=0?'+':'')+hPct.toFixed(2)+'%';
      const bCol = hPct >= _upperPct ? '#3fb950' : hPct <= -_lowerPct ? '#c0392b' : '#58a6ff';
      ctx.fillStyle = bCol; ctx.fillRect(0, cy-18, pad.l-4, 36);
      ctx.fillStyle = '#fff'; ctx.font = 'bold 10px sans-serif'; ctx.textAlign = 'right';
      ctx.fillText('$'+hPrice.toFixed(2), pad.l-6, cy-4);
      ctx.font = 'bold 10px sans-serif';
      ctx.fillText(hPctStr, pad.l-6, cy+12);
    } else {
      ctx.fillStyle = '#58a6ff'; ctx.fillRect(0, cy-9, pad.l-4, 18);
      ctx.fillStyle = '#fff'; ctx.font = 'bold 10px sans-serif'; ctx.textAlign = 'right';
      ctx.fillText('$'+hPrice.toFixed(2), pad.l-6, cy+4);
    }
  }

  // ─── OHLC info bar at top ───
  const di = (hoverIdx >= 0 && hoverIdx < n) ? hoverIdx : n-1;
  const dd = data[di];
  const isGr = dd.c >= dd.o;
  const chg = dd.c - dd.o;
  const chgPct = dd.o ? (chg/dd.o*100) : 0;
  const ic = isGr ? '#3fb950' : '#f85149';

  ctx.fillStyle = 'rgba(22,27,34,0.92)';
  ctx.fillRect(pad.l, 1, W-pad.l-pad.r, 48);

  // time
  ctx.fillStyle = '#c9d1d9'; ctx.font = 'bold 11px sans-serif'; ctx.textAlign = 'left';
  ctx.fillText(dd.t, pad.l+5, 16);

  // OHLC items row 1
  const refPct  = refPrice ? (dd.c - refPrice) / refPrice * 100 : null;
  const refCol  = refPct === null ? ic : refPct >= _upperPct ? '#3fb950' : refPct <= -_lowerPct ? '#f85149' : '#d29922';
  const row1 = [
    ['开', (dd.o||0).toFixed(2), '#c9d1d9'],
    ['高', (dd.h||0).toFixed(2), '#3fb950'],
    ['低', (dd.l||0).toFixed(2), '#f85149'],
    ['收', (dd.c||0).toFixed(2), ic],
    [(chgPct>=0?'▲':'▼'), Math.abs(chgPct).toFixed(2)+'%', ic],
  ];
  let ix = pad.l + 52;
  for (const [lbl, val, col] of row1) {
    ctx.font = '10px sans-serif'; ctx.fillStyle = '#8b949e'; ctx.textAlign = 'left';
    ctx.fillText(lbl+':', ix, 16);
    const lw = ctx.measureText(lbl+':').width + 2;
    ctx.font = 'bold 11px sans-serif'; ctx.fillStyle = col;
    ctx.fillText(val, ix+lw, 16);
    ix += lw + ctx.measureText(val).width + 12;
  }

  // 第二行左侧：当日涨跌幅（动态，相对T-1收盘）
  ctx.font = '10px sans-serif'; ctx.fillStyle = '#8b949e'; ctx.textAlign = 'left';
  ctx.fillText('量:', pad.l+5, 34);
  ctx.font = 'bold 10px sans-serif'; ctx.fillStyle = '#8b949e';
  const volStr = dd.v >= 1000000 ? (dd.v/1000000).toFixed(2)+'M' : dd.v >= 1000 ? (dd.v/1000).toFixed(0)+'K' : String(dd.v||0);
  ctx.fillText(volStr, pad.l+22, 34);

  if (refPrice && refPct !== null) {
    const refStr = (refPct>=0?'+':'')+refPct.toFixed(2)+'%';
    ctx.font = '10px sans-serif'; ctx.fillStyle = '#8b949e'; ctx.textAlign = 'left';
    const labelTxt = '当日涨跌:';
    ctx.fillText(labelTxt, pad.l+70, 34);
    const ltw = ctx.measureText(labelTxt).width + 3;
    ctx.font = 'bold 13px sans-serif'; ctx.fillStyle = refCol;
    ctx.fillText(refStr, pad.l+70+ltw, 35);
  }

  // meta row 2 (其余静态信息)
  if (meta) {
    let mx = pad.l + (refPrice ? 175 : 80);
    for (const [k,v,c] of meta) {
      ctx.font = '10px sans-serif'; ctx.fillStyle = '#8b949e';
      ctx.fillText(k+':', mx, 34);
      const kw = ctx.measureText(k+':').width + 2;
      ctx.font = 'bold 10px sans-serif'; ctx.fillStyle = c || '#c9d1d9';
      ctx.fillText(v, mx+kw, 34);
      mx += kw + ctx.measureText(v).width + 16;
    }
  }
}

// ─── 行内展开 ───
let currentIdx = -1;
function selectDay(idx) {
  // remove old detail row
  const old = document.getElementById('detailRow');
  if (old) old.remove();
  document.querySelectorAll('#tradeTable .data-row').forEach(tr => tr.classList.remove('selected'));

  if (currentIdx === idx) { currentIdx = -1; return; }
  currentIdx = idx;

  const r = _activeResults[idx];
  const d = _baseDaily[idx];
  const dataRow = document.querySelector(`#tradeTable .data-row[data-idx="${idx}"]`);
  dataRow.classList.add('selected');

  const pnlColor = r["盈亏"] >= 0 ? '#3fb950' : '#f85149';
  const trigText = r["触发"] !== "未触发"
    ? `${r["触发时间"]} 触发（${r["触发"]} ${r["触发涨跌%"]}%）`
    : '12:00 未触发 → 止损';
  const callPnl = ((r["Call卖出"]-r["Call成本"])*100).toFixed(2);
  const putPnl  = ((r["Put卖出"] -r["Put成本"] )*100).toFixed(2);
  const vixBuy  = r['VIX'] != null ? r['VIX'].toFixed(1) : '-';
  const vixSell = r['VIX_卖出'] != null ? r['VIX_卖出'].toFixed(1) : '-';

  const detailTr = document.createElement('tr');
  detailTr.id = 'detailRow';
  detailTr.className = 'detail-tr';
  detailTr.innerHTML = `
    <td colspan="19">
      <div class="detail-inner">
        <div class="detail-header">
          <span style="font-size:15px;font-weight:bold;color:#58a6ff">${r["到期日"]} [${d.granularity}]</span>
          <span style="color:${pnlColor};font-weight:bold">合计盈亏: $${(r["盈亏"]*100).toFixed(2)}</span>
          <span style="color:#c9d1d9">${trigText}</span>
          <span style="color:#8b949e">Call: 买$${r["Call成本"]}→卖$${r["Call卖出"]} (<span style="color:${parseFloat(callPnl)>=0?'#3fb950':'#f85149'}">$${callPnl}</span>)</span>
          <span style="color:#8b949e">Put:  买$${r["Put成本"]}→卖$${r["Put卖出"]} (<span style="color:${parseFloat(putPnl)>=0?'#3fb950':'#f85149'}">$${putPnl}</span>)</span>
          <span style="color:#d29922;font-weight:bold">VIX: ${vixBuy}→${vixSell}</span>
        </div>
        <div class="legend">
          <span><span class="dot" style="background:#58a6ff"></span>买入</span>
          <span><span class="dot" style="background:#f0883e"></span>卖出</span>
          <span><span class="dot" style="background:#3fb950"></span>+${_upperPct}%线</span>
          <span><span class="dot" style="background:#f85149"></span>-${_lowerPct}%线</span>
        </div>
        <div class="chart-grid3">
          <div class="chart-box">
            <div class="chart-box-title">QQQ 走势（${d.granularity}）</div>
            <canvas id="qqqCanvas" style="width:100%;height:340px;display:block;"></canvas>
          </div>
          <div class="chart-box">
            <div class="chart-box-title">Call 期权 ${r["Call合约"].slice(-13)}</div>
            <canvas id="callCanvas" style="width:100%;height:340px;display:block;"></canvas>
          </div>
          <div class="chart-box">
            <div class="chart-box-title">Put 期权 ${r["Put合约"].slice(-13)}</div>
            <canvas id="putCanvas" style="width:100%;height:340px;display:block;"></canvas>
          </div>
        </div>
        <div style="margin-top:10px">
          <div class="chart-box">
            <div class="chart-box-title">VIX 5min — 买入VIX:${vixBuy} / 卖出VIX:${vixSell}</div>
            <canvas id="vixIntraCanvas" style="width:100%;height:200px;display:block;"></canvas>
          </div>
        </div>
      </div>
    </td>`;
  dataRow.insertAdjacentElement('afterend', detailTr);

  requestAnimationFrame(() => {
    const sellTime  = r["触发时间"].replace("止损","");
    const t2Close   = r["QQQ_T2收盘"];
    const qqqOpen   = r["QQQ开盘"];
    const upper     = t2Close * (1 + _upperPct/100);
    const lower     = t2Close * (1 - _lowerPct/100);

    drawCandleChart('qqqCanvas', d.qqq,
      [{time: sellTime, color:'#f0883e', label:'卖出'}],
      [{val:upper,   color:'#3fb950', label:'+'+_upperPct+'%'},
       {val:lower,   color:'#f85149', label:'-'+_lowerPct+'%'},
       {val:t2Close, color:'#8b94ae', label:'T-1收$'+t2Close.toFixed(2)},
       {val:qqqOpen, color:'#8b949e', label:'T开$'+qqqOpen.toFixed(2)}],
      [['卖出时间', sellTime, '#f0883e']],
      t2Close);

    drawCandleChart('callCanvas', d.call,
      [{time:'09:30', color:'#58a6ff', label:'买$'+r["Call成本"]},
       {time: sellTime, color:'#f0883e', label:'卖$'+r["Call卖出"]}],
      null,
      [['买入','$'+r["Call成本"],'#58a6ff'],
       ['卖出','$'+r["Call卖出"],'#f0883e'],
       ['盈亏','$'+callPnl, parseFloat(callPnl)>=0?'#3fb950':'#f85149']]);

    drawCandleChart('putCanvas', d.put,
      [{time:'09:30', color:'#58a6ff', label:'买$'+r["Put成本"]},
       {time: sellTime, color:'#f0883e', label:'卖$'+r["Put卖出"]}],
      null,
      [['买入','$'+r["Put成本"],'#58a6ff'],
       ['卖出','$'+r["Put卖出"],'#f0883e'],
       ['盈亏','$'+putPnl, parseFloat(putPnl)>=0?'#3fb950':'#f85149']]);

    if (d.vix && d.vix.length) {
      drawCandleChart('vixIntraCanvas', d.vix,
        [{time: sellTime, color:'#f0883e', label:'卖出'}],
        null,
        [['VIX买(T-1)', vixBuy, '#58a6ff'],
         ['VIX卖', vixSell, '#f0883e']]);
    }

    detailTr.scrollIntoView({behavior:'smooth', block:'nearest'});
  });
}

// ─── VIX 日K线图 ───
function drawVixDailyChart() {
  if (!VIX_DAILY_DATA || !VIX_DAILY_DATA.length) return;
  const markers = [];
  for (const r of _activeResults) {
    const pnl = r['盈亏'] * 100;
    markers.push({
      time: r['到期日'],
      color: pnl >= 0 ? '#3fb950' : '#f85149',
      label: (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(0)
    });
  }
  drawCandleChart('vixDailyCanvas', VIX_DAILY_DATA, markers, null,
    [['VIX 日K', '', '#d29922']], null);
}

// ─── VIX 散点 + 分段柱状图 ───
function drawVixCharts() {
  drawVixScatter();
  drawVixBar();
}
function drawVixScatter() {
  const canvas = document.getElementById('vixScatter');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width) return;
  canvas.width = rect.width * dpr; canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  const W = rect.width, H = rect.height;
  const pad = {t:30, r:20, b:35, l:55};
  const pts = _activeResults.filter(r => r['VIX'] != null).map(r => ({vix: r['VIX'], pnl: r['盈亏']*100, trig: r['触发']}));
  if (!pts.length) { ctx.fillStyle='#8b949e'; ctx.font='13px sans-serif'; ctx.textAlign='center'; ctx.fillText('无 VIX 数据', W/2, H/2); return; }
  const minV = Math.min(...pts.map(p=>p.vix))-1, maxV = Math.max(...pts.map(p=>p.vix))+1;
  const minP = Math.min(...pts.map(p=>p.pnl))-20, maxP = Math.max(...pts.map(p=>p.pnl))+20;
  const toX = v => pad.l + (v-minV)/(maxV-minV)*(W-pad.l-pad.r);
  const toY = v => pad.t + (maxP-v)/(maxP-minP)*(H-pad.t-pad.b);
  // grid
  ctx.strokeStyle='#21262d'; ctx.lineWidth=0.5; ctx.fillStyle='#8b949e'; ctx.font='10px sans-serif';
  ctx.textAlign='right';
  for (let i=0;i<=4;i++) { const v=minP+(maxP-minP)*i/4, y=toY(v); ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(W-pad.r,y);ctx.stroke(); ctx.fillText('$'+v.toFixed(0),pad.l-4,y+3); }
  ctx.textAlign='center';
  for (let i=0;i<=5;i++) { const v=minV+(maxV-minV)*i/5, x=toX(v); ctx.beginPath();ctx.moveTo(x,pad.t);ctx.lineTo(x,H-pad.b);ctx.stroke(); ctx.fillText(v.toFixed(1),x,H-10); }
  // zero line
  const y0=toY(0); ctx.setLineDash([4,4]);ctx.strokeStyle='#58a6ff';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(pad.l,y0);ctx.lineTo(W-pad.r,y0);ctx.stroke();ctx.setLineDash([]);
  // points
  for (const p of pts) { const x=toX(p.vix),y=toY(p.pnl); ctx.beginPath();ctx.arc(x,y,5,0,Math.PI*2); ctx.fillStyle=p.pnl>=0?'#3fb950':'#f85149'; ctx.fill(); ctx.strokeStyle='rgba(255,255,255,0.3)';ctx.lineWidth=1;ctx.stroke(); }
  // labels
  ctx.fillStyle='#c9d1d9'; ctx.font='bold 12px sans-serif'; ctx.textAlign='center';
  ctx.fillText('VIX vs 策略盈亏 散点图', W/2, 16);
  ctx.fillStyle='#8b949e'; ctx.font='10px sans-serif';
  ctx.fillText('VIX (T-1收盘)', W/2, H-2);
  ctx.save(); ctx.translate(12, H/2); ctx.rotate(-Math.PI/2); ctx.fillText('盈亏 ($)', 0, 0); ctx.restore();
  // correlation
  const n=pts.length, sx=pts.reduce((a,p)=>a+p.vix,0), sy=pts.reduce((a,p)=>a+p.pnl,0);
  const mx=sx/n, my=sy/n;
  const sxy=pts.reduce((a,p)=>a+(p.vix-mx)*(p.pnl-my),0);
  const sxx=pts.reduce((a,p)=>a+(p.vix-mx)**2,0), syy=pts.reduce((a,p)=>a+(p.pnl-my)**2,0);
  const r2 = sxx&&syy ? sxy/Math.sqrt(sxx*syy) : 0;
  ctx.fillStyle=r2>=0?'#3fb950':'#f85149'; ctx.font='bold 11px sans-serif'; ctx.textAlign='right';
  ctx.fillText('相关系数 r = '+(r2>=0?'+':'')+r2.toFixed(3), W-pad.r, 16);
  // trend line
  if (sxx) { const slope=sxy/sxx, intercept=my-slope*mx; const x1=minV,x2=maxV; ctx.setLineDash([6,3]);ctx.strokeStyle='rgba(88,166,255,0.5)';ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(toX(x1),toY(slope*x1+intercept));ctx.lineTo(toX(x2),toY(slope*x2+intercept));ctx.stroke();ctx.setLineDash([]); }
}
function drawVixBar() {
  const canvas = document.getElementById('vixBarChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width) return;
  canvas.width = rect.width * dpr; canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  const W = rect.width, H = rect.height;
  const pad = {t:30, r:20, b:55, l:55};
  const pts = _activeResults.filter(r => r['VIX'] != null);
  if (!pts.length) return;
  // 分段
  const bins = [{label:'<15',lo:0,hi:15},{label:'15-20',lo:15,hi:20},{label:'20-25',lo:20,hi:25},{label:'25-30',lo:25,hi:30},{label:'≥30',lo:30,hi:999}];
  const binData = bins.map(b => {
    const items = pts.filter(r => r['VIX']>=b.lo && r['VIX']<b.hi);
    const pnl = items.reduce((s,r)=>s+r['盈亏']*100,0);
    const w = items.filter(r=>r['盈亏']>0).length;
    return {label:b.label,count:items.length,pnl:+pnl.toFixed(2),wins:w,wr:items.length?+(w/items.length*100).toFixed(0):0};
  }).filter(b=>b.count>0);
  if (!binData.length) return;
  const maxP = Math.max(...binData.map(b=>Math.abs(b.pnl)),1);
  const n = binData.length;
  const barW = Math.min(60, (W-pad.l-pad.r)/n*0.6);
  const gap = (W-pad.l-pad.r)/n;
  const toY = v => pad.t + (maxP-v)/(2*maxP)*(H-pad.t-pad.b);
  // grid
  const y0=toY(0); ctx.strokeStyle='#58a6ff';ctx.lineWidth=1;ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(pad.l,y0);ctx.lineTo(W-pad.r,y0);ctx.stroke();ctx.setLineDash([]);
  ctx.strokeStyle='#21262d';ctx.lineWidth=0.5;ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';ctx.textAlign='right';
  for (let i=0;i<=4;i++){const v=-maxP+2*maxP*i/4,y=toY(v);ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(W-pad.r,y);ctx.stroke();ctx.fillText('$'+v.toFixed(0),pad.l-4,y+3);}
  // bars
  for (let i=0;i<n;i++){
    const b=binData[i], x=pad.l+i*gap+gap/2-barW/2, y=toY(b.pnl);
    ctx.fillStyle=b.pnl>=0?'rgba(63,185,80,0.7)':'rgba(248,81,73,0.7)';
    if (b.pnl>=0){ctx.fillRect(x,y,barW,y0-y);}else{ctx.fillRect(x,y0,barW,y-y0);}
    // label
    ctx.fillStyle='#c9d1d9';ctx.font='bold 11px sans-serif';ctx.textAlign='center';
    ctx.fillText('$'+b.pnl.toFixed(0), x+barW/2, b.pnl>=0?y-6:y+14);
    // x label
    ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';
    ctx.fillText(b.label, x+barW/2, H-pad.b+14);
    ctx.fillText(b.count+'天', x+barW/2, H-pad.b+28);
    ctx.fillStyle=b.wr>=50?'#3fb950':'#f85149';ctx.font='bold 10px sans-serif';
    ctx.fillText(b.wr+'%胜率', x+barW/2, H-pad.b+42);
  }
  ctx.fillStyle='#c9d1d9';ctx.font='bold 12px sans-serif';ctx.textAlign='center';
  ctx.fillText('VIX 分段累计盈亏',W/2,16);
  ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';
  ctx.fillText('VIX 区间',W/2,H-2);
}

window.addEventListener('load', () => { drawCumChart(); drawVixDailyChart(); drawVixCharts(); });
window.addEventListener('resize', () => {
  drawCumChart();
  drawVixDailyChart();
  drawVixCharts();
  if (currentIdx >= 0) {
    const c1 = document.getElementById('qqqCanvas');
    const c2 = document.getElementById('callCanvas');
    const c3 = document.getElementById('putCanvas');
    if (c1) _renderCandle(c1, 'qqqCanvas');
    if (c2) _renderCandle(c2, 'callCanvas');
    if (c3) _renderCandle(c3, 'putCanvas');
  }
});
</script>
</body>
</html>"""

    return html



def main():
    # 兼容尚未重命名的旧文件，避免运行中断
    opt_file_4 = OPT_FILE_4
    if not os.path.exists(opt_file_4):
        old_opt_file_4 = os.path.join(
            os.path.dirname(__file__),
            "..",
            "3-qqq末日期权日K-上下4股价的期权合同-前一天末日期权的收盘价",
            "data",
            "QQQ_0DTE_4.xlsx",
        )
        if os.path.exists(old_opt_file_4):
            print("提示：检测到旧命名 QQQ_0DTE_4.xlsx，临时使用旧文件。")
            opt_file_4 = old_opt_file_4

    print("加载数据...")
    summary3, call3_1m, put3_1m, qqq_1m, qqq_2m, qqq_5m, qqq_daily = load_data(OPT_FILE_3)
    summary4, call4_1m, put4_1m, _,      _,      _,      _          = load_data(opt_file_4)

    # 加载 VIX 数据（日K + 5min）
    vix_map = {}  # date_str -> VIX收盘价
    vix_daily_data = []  # for chart: [{t, o, h, l, c, v}, ...]
    vix_5min_map = {}  # date_str -> [{t, o, h, l, c, v}, ...]
    if os.path.exists(VIX_FILE):
        vix_daily = pd.read_excel(VIX_FILE, sheet_name="VIX_日K")
        for _, vr in vix_daily.iterrows():
            d = str(vr["日期"])[:10]
            vix_map[d] = float(vr["收盘价"])
            vix_daily_data.append({
                "t": d, "o": round(float(vr["开盘价"]), 2),
                "h": round(float(vr["最高价"]), 2), "l": round(float(vr["最低价"]), 2),
                "c": round(float(vr["收盘价"]), 2), "v": 0,
            })
        print(f"  VIX 日K 已加载，共 {len(vix_map)} 天")
        try:
            vix_5m = pd.read_excel(VIX_FILE, sheet_name="VIX_5min")
            for _, vr in vix_5m.iterrows():
                ts = str(vr["时间"])
                d, t = ts[:10], ts[11:16]
                if d not in vix_5min_map:
                    vix_5min_map[d] = []
                vix_5min_map[d].append({
                    "t": t, "o": round(float(vr["开盘价"]), 2),
                    "h": round(float(vr["最高价"]), 2), "l": round(float(vr["最低价"]), 2),
                    "c": round(float(vr["收盘价"]), 2), "v": int(vr.get("成交量", 0)),
                })
            print(f"  VIX 5min 已加载，共 {len(vix_5min_map)} 天")
        except Exception:
            print("  ⚠ VIX 5min 数据加载失败")
    else:
        print(f"  ⚠ 未找到 VIX 数据文件: {VIX_FILE}")

    print("运行策略回测（±3）...")
    results3 = run_backtest(summary3, call3_1m, put3_1m, qqq_1m, qqq_2m, qqq_5m)
    print(f"  ±3 共 {len(results3)} 个交易日")
    for g, cnt in pd.Series([r['数据粒度'] for r in results3]).value_counts().items():
        print(f"    {g}: {cnt} 天")

    print("运行策略回测（±4）...")
    results4 = run_backtest(summary4, call4_1m, put4_1m, qqq_1m, qqq_2m, qqq_5m)
    print(f"  ±4 共 {len(results4)} 个交易日")
    for g, cnt in pd.Series([r['数据粒度'] for r in results4]).value_counts().items():
        print(f"    {g}: {cnt} 天")

    # 注入 VIX 数据到回测结果
    def _inject_vix(results):
        for r in results:
            r["VIX"] = vix_map.get(r["基准日"])  # 买入时VIX（T-1收盘）
            sell_time = r["触发时间"].replace("止损", "")
            vix_day = vix_5min_map.get(r["到期日"], [])
            vix_sell = None
            for bar in reversed(vix_day):
                if bar["t"] <= sell_time:
                    vix_sell = bar["c"]
                    break
            r["VIX_卖出"] = vix_sell
    _inject_vix(results3)
    _inject_vix(results4)

    print("构建日内图表数据...")
    daily3 = build_daily_charts(results3, call3_1m, put3_1m, qqq_1m, qqq_2m, qqq_5m, vix_5min_map)
    daily4 = build_daily_charts(results4, call4_1m, put4_1m, qqq_1m, qqq_2m, qqq_5m, vix_5min_map)

    print("生成 HTML...")
    html = generate_html(results3, daily3, results4, daily4, vix_daily_data)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 已生成：{os.path.abspath(OUTPUT_HTML)}")

    total_pnl3 = sum(r["盈亏"] for r in results3)
    total_pnl4 = sum(r["盈亏"] for r in results4)
    wins3 = sum(1 for r in results3 if r["盈亏"] > 0)
    wins4 = sum(1 for r in results4 if r["盈亏"] > 0)
    print(f"\n── ±3 策略汇总 ──")
    print(f"  胜/负: {wins3}/{len(results3)-wins3}  胜率: {round(wins3/len(results3)*100,1)}%  累计盈亏: ${round(total_pnl3*100,2)}")
    print(f"── ±4 策略汇总 ──")
    print(f"  胜/负: {wins4}/{len(results4)-wins4}  胜率: {round(wins4/len(results4)*100,1)}%  累计盈亏: ${round(total_pnl4*100,2)}")


if __name__ == "__main__":
    main()
