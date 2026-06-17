# -*- coding: utf-8 -*-
"""
QQQ 末日期权 — 开盘立即买入垂直看涨价差（Bull Call Spread）策略回测 + HTML 可视化

策略规则：
  T（到期日）9:30 开盘：
    - 买腿: 以 Call 开盘价 买入 base+buy_offset 的 Call
    - 卖腿: 以 Call 开盘价 卖出 base+sell_offset 的 Call
    - base = round(QQQ T 日开盘价)，约束: 1 ≤ buy_offset < sell_offset ≤ 5
  净权利金 = 买腿开盘价 - 卖腿开盘价（恒为正）
  逐分钟监控当前价差 = 买腿现价 - 卖腿现价
    若 价差 / 净权利金 ≥ take_profit_pct（如 2.0=翻倍）→ 平仓止盈
    若 价差 / 净权利金 ≤ stop_loss_pct  （如 0.3=跌到30%）→ 平仓止损
  若到强制平仓时间仍未触发，以该时间价差平仓
  手续费：$1.7/张 × 4 次（开仓2腿 + 平仓2腿）= $6.8 / 100
"""

import os, json
import pandas as pd

# ────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────
ROOT = os.path.dirname(__file__)
QQQ_FILE  = os.path.join(ROOT, "..", "1-qqq日K", "data", "qqq_market_data.xlsx")
OPT_DIR   = os.path.join(ROOT, "..", "4-qqq末日期权日K-当天开盘上下2和上下3股价的期权合同", "data")
OPT_FILES = {n: os.path.join(OPT_DIR, f"qqq_0dte_options_open_offset{n}.xlsx") for n in (1, 2, 3, 4, 5)}
VIX_FILE  = os.path.join(ROOT, "..", "..", "VIX", "data", "vix_data.xlsx")
OUTPUT_HTML = os.path.join(ROOT, "data", "qqq_vertical_call_strategy_report.html")

# 默认参数
DEFAULT_BUY_OFFSET   = 1
DEFAULT_SELL_OFFSET  = 5
DEFAULT_UPPER_PCT    = 0.80   # QQQ 当日涨幅 ≥ 此值（%）→ 止盈平仓
DEFAULT_LOWER_PCT    = 0.20   # QQQ 当日跌幅 ≥ 此值（%）→ 止损平仓
DEFAULT_CLOSE_TIME   = "13:00"
COMMISSION   = 1.7   # 每张合约手续费（美元），开仓 2 腿 + 平仓 2 腿 = 4 次
MONITOR_START = "09:30"

os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)


# ────────────────────────────────────────────────
# 数据加载
# ────────────────────────────────────────────────
def load_qqq_intraday():
    qqq_1m = pd.read_excel(QQQ_FILE, sheet_name="QQQ_分时1min")
    qqq_2m = pd.read_excel(QQQ_FILE, sheet_name="QQQ_分时2min")
    qqq_5m = pd.read_excel(QQQ_FILE, sheet_name="QQQ_5min")
    return qqq_1m, qqq_2m, qqq_5m


def load_offset_data(offset):
    summary = pd.read_excel(OPT_FILES[offset], sheet_name="摘要")
    call_1m = pd.read_excel(OPT_FILES[offset], sheet_name="Call_1min")
    return summary, call_1m


def _get_qqq_day(t1, qqq_1m, qqq_2m, qqq_5m):
    for df, label in [(qqq_1m, "1min"), (qqq_2m, "2min"), (qqq_5m, "5min")]:
        day = df[df["时间"].astype(str).str.startswith(t1)].copy()
        if not day.empty:
            day["time_only"] = day["时间"].astype(str).str[-5:]
            return day, label
    return pd.DataFrame(), "无"


# ────────────────────────────────────────────────
# 把每个 offset 的所有日 1min 数据整理成 dict[date] -> [{t,o,h,l,c,v}, ...]
# ────────────────────────────────────────────────
def build_call_per_day(call_1m):
    out = {}
    df = call_1m.copy()
    df["date"] = df["到期日"].astype(str).str[:10]
    df["time_only"] = df["时间(美东)"].astype(str).str[-5:]
    for d, grp in df.groupby("date"):
        bars = []
        for _, row in grp.iterrows():
            bars.append({
                "t": row["time_only"],
                "o": float(row["开盘价"]),
                "h": float(row["最高价"]),
                "l": float(row["最低价"]),
                "c": float(row["收盘价"]),
                "v": int(row["成交量"]),
            })
        bars.sort(key=lambda x: x["t"])
        out[d] = bars
    return out


def build_qqq_per_day(summary_dates, qqq_1m, qqq_2m, qqq_5m):
    out = {}
    gran = {}
    for t1 in summary_dates:
        day, g = _get_qqq_day(t1, qqq_1m, qqq_2m, qqq_5m)
        if day.empty:
            continue
        bars = []
        for _, row in day.iterrows():
            bars.append({
                "t": row["time_only"],
                "o": float(row["开盘价"]),
                "h": float(row["最高价"]),
                "l": float(row["最低价"]),
                "c": float(row["收盘价"]),
                "v": int(row["成交量"]),
            })
        out[t1] = bars
        gran[t1] = g
    return out, gran


# ────────────────────────────────────────────────
# Python 端默认参数下的回测（用于初始展示统计）
# ────────────────────────────────────────────────
def run_backtest(buy_offset, sell_offset, upper_pct, lower_pct, close_time,
                 buy_per_day, sell_per_day, qqq_per_day, qqq_gran, summary_buy, summary_sell):
    """
    监控 QQQ 当日涨跌幅触发：
      涨幅 >= upper_pct → 止盈平仓（垂直看涨价差看多 QQQ）
      跌幅 >= lower_pct → 止损平仓
    summary_buy / summary_sell: 用于读取 buy/sell 行权价、合约号、QQQ_T2收盘
    """
    summ_b = {str(r["到期日(T1)"])[:10]: r for _, r in summary_buy.iterrows()}
    summ_s = {str(r["到期日(T1)"])[:10]: r for _, r in summary_sell.iterrows()}

    results = []
    for t1 in sorted(set(buy_per_day.keys()) & set(sell_per_day.keys()) & set(qqq_per_day.keys())):
        if t1 not in summ_b or t1 not in summ_s:
            continue
        rb = summ_b[t1]; rs = summ_s[t1]
        buy_bars  = buy_per_day[t1]
        sell_bars = sell_per_day[t1]
        qqq_bars  = qqq_per_day[t1]
        if not buy_bars or not sell_bars or not qqq_bars:
            continue

        # 9:30 开盘价
        b_open_bar = next((b for b in buy_bars  if b["t"] == "09:30"), buy_bars[0])
        s_open_bar = next((b for b in sell_bars if b["t"] == "09:30"), sell_bars[0])
        q_open_bar = next((b for b in qqq_bars  if b["t"] == "09:30"), qqq_bars[0])
        buy_cost   = float(b_open_bar["o"])
        sell_recv  = float(s_open_bar["o"])
        if buy_cost <= 0 or sell_recv <= 0:
            continue
        net_premium = round(buy_cost - sell_recv, 4)
        if net_premium <= 0:
            continue
        qqq_open = float(q_open_bar["c"])

        # 加快查询
        buy_map  = {b["t"]: b for b in buy_bars}
        sell_map = {b["t"]: b for b in sell_bars}

        trig_time, trig_kind = None, None
        for qb in qqq_bars:
            t = qb["t"]
            if t < MONITOR_START or t > close_time:
                continue
            pct = (qb["c"] - qqq_open) / qqq_open * 100
            if pct >= upper_pct:
                trig_time = t; trig_kind = "止盈"; break
            if pct <= -lower_pct:
                trig_time = t; trig_kind = "止损"; break

        sell_time = trig_time or close_time
        # 取该时刻或之前最近一根 K 线
        bb = buy_map.get(sell_time)  or next((b for b in reversed(buy_bars)  if b["t"] <= sell_time), None)
        sb = sell_map.get(sell_time) or next((b for b in reversed(sell_bars) if b["t"] <= sell_time), None)
        if not bb or not sb:
            continue
        trig_spread = round(bb["c"] - sb["c"], 4)
        if not trig_time:
            trig_kind = "时间"

        commission_total = COMMISSION * 4 / 100
        pnl = trig_spread - net_premium - commission_total
        pnl_pct = (pnl / net_premium * 100) if net_premium > 0 else 0
        qqq_close = float(qqq_bars[-1]["c"])
        qqq_t2 = float(rb["QQQ_T2收盘"])
        qqq_day_pct = round((qqq_close - qqq_t2) / qqq_t2 * 100, 2)

        results.append({
            "到期日": t1,
            "基准日": str(rb["基准日(T2)"])[:10],
            "QQQ_T2收盘": qqq_t2,
            "QQQ开盘": qqq_open,
            "QQQ收盘": qqq_close,
            "QQQ涨跌%": qqq_day_pct,
            "买腿合约": str(rb["Call合约"]),
            "买腿行权价": float(rb["Call行权价"]),
            "买腿开盘": round(buy_cost, 4),
            "卖腿合约": str(rs["Call合约"]),
            "卖腿行权价": float(rs["Call行权价"]),
            "卖腿开盘": round(sell_recv, 4),
            "净权利金": net_premium,
            "平仓价差": trig_spread,
            "触发": trig_kind,
            "触发时间": trig_time if trig_time else (close_time + "时间"),
            "盈亏": round(pnl, 4),
            "盈亏%": round(pnl_pct, 2),
            "数据粒度": qqq_gran.get(t1, "?"),
            "VIX": None, "VIX_卖出": None,
        })

    return results


# ────────────────────────────────────────────────
# HTML 生成
# ────────────────────────────────────────────────
def generate_html(all_offsets_data, default_results, vix_daily_data):
    """
    all_offsets_data: dict[int] -> {summary: [{date,strike,contract,open,t2_close,qqq_open,qqq_close,qqq_pct,granularity}], call: dict[date]->bars}
    default_results: 默认参数下的回测明细（用于初始展示）
    """
    total_trades = len(default_results)
    wins = sum(1 for r in default_results if r["盈亏"] > 0)
    losses = total_trades - wins
    total_pnl = sum(r["盈亏"] for r in default_results)
    total_cost_sum = sum(r["净权利金"] for r in default_results)
    win_rate = round(wins / total_trades * 100, 1) if total_trades > 0 else 0
    avg_pnl = round(total_pnl / total_trades, 4) if total_trades > 0 else 0
    triggered = sum(1 for r in default_results if r["触发"] != "时间")

    date_min = default_results[0]["到期日"] if default_results else "-"
    date_max = default_results[-1]["到期日"] if default_results else "-"

    # 行权价组合按钮
    combos = [(b, s) for b in range(1, 5) for s in range(2, 6) if s > b]
    combo_btns = ""
    for b, s in combos:
        active = "active" if (b == DEFAULT_BUY_OFFSET and s == DEFAULT_SELL_OFFSET) else ""
        combo_btns += f'<button class="{active}" onclick="switchCombo({b},{s})" id="btn-{b}-{s}">+{b}/+{s}</button>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QQQ 开盘垂直看涨价差策略</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; background: #0a0e17; color: #e0e0e0; }}
.header {{ background: linear-gradient(135deg, #1a1f2e 0%, #0d1117 100%); padding: 20px 30px; border-bottom: 1px solid #2a3040; }}
.header h1 {{ font-size: 22px; color: #58a6ff; }}
.header .sub {{ font-size: 13px; color: #8b949e; margin-top: 5px; }}
.header .sub-detail {{ margin-top: 10px; display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 6px 30px; }}
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
#cumChart {{ width: 100%; height: 220px; background: #161b22; border: 1px solid #30363d; border-radius: 8px; display: block; }}
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ background: #161b22; color: #8b949e; padding: 8px 8px; text-align: right; border-bottom: 1px solid #30363d; position: sticky; top: 0; z-index: 10; white-space: nowrap; }}
th:first-child, td:first-child {{ text-align: left; }}
td {{ padding: 7px 8px; border-bottom: 1px solid #21262d; text-align: right; cursor: pointer; white-space: nowrap; }}
tr.data-row:hover td {{ background: #1c2333; }}
tr.data-row.selected td {{ background: #1e3a5f !important; }}
.pnl-pos {{ color: #3fb950; font-weight: bold; }}
.pnl-neg {{ color: #f85149; font-weight: bold; }}
.trig-tp {{ color: #3fb950; font-weight: bold; }}
.trig-sl {{ color: #f85149; font-weight: bold; }}
.trig-time {{ color: #d29922; }}
.detail-tr td {{ padding: 0; background: #0a0e17 !important; border-bottom: 2px solid #58a6ff; cursor: default; }}
.detail-inner {{ padding: 12px 20px 16px; }}
.detail-header {{ display: flex; align-items: center; gap: 20px; margin-bottom: 10px; flex-wrap: wrap; font-size: 13px; }}
.chart-grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
.chart-grid3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }}
.chart-box {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px; overflow: hidden; }}
.chart-box-title {{ font-size: 11px; color: #8b949e; text-align: center; padding: 5px 0 0; }}
.chart-box canvas {{ width: 100%; height: 320px; display: block; }}
.legend {{ display: flex; gap: 16px; margin: 6px 0; justify-content: center; font-size: 11px; }}
.legend span {{ display: flex; align-items: center; gap: 4px; }}
.dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
@media (max-width: 1100px) {{ .chart-grid3 {{ grid-template-columns: 1fr; }} .chart-grid2 {{ grid-template-columns: 1fr; }} .stats-row {{ flex-direction: column; }} }}
.ctrl-bar {{ display:flex; align-items:center; gap:10px; padding:10px 30px 4px; flex-wrap:wrap; background:#0d1117; border-bottom:1px solid #21262d; }}
.ctrl-bar label {{ font-size:12px; color:#8b949e; }}
.ctrl-bar input[type=number] {{ width:70px; background:#161b22; border:1px solid #30363d; border-radius:5px; color:#e0e0e0; font-size:13px; padding:4px 8px; text-align:center; outline:none; }}
.ctrl-bar input[type=number]:focus {{ border-color:#58a6ff; }}
.ctrl-bar input[type=time] {{ width:88px; background:#161b22; border:1px solid #30363d; border-radius:5px; color:#e0e0e0; font-size:13px; padding:4px 6px; outline:none; color-scheme:dark; }}
.ctrl-btn {{ background:#1f6feb; border:none; border-radius:5px; color:#fff; font-size:12px; padding:5px 14px; cursor:pointer; font-weight:bold; }}
.ctrl-btn:hover {{ background:#388bfd; }}
.ctrl-hint {{ font-size:11px; color:#636e7b; margin-left:6px; flex-basis:100%; padding-top:3px; }}
.combo-grid {{ display:flex; gap:0; border:1px solid #30363d; border-radius:6px; overflow:hidden; flex-wrap:wrap; }}
.combo-grid button {{ background:#161b22; border:none; border-right:1px solid #30363d; color:#8b949e; font-size:12px; font-weight:bold; padding:6px 10px; cursor:pointer; min-width:60px; }}
.combo-grid button:last-child {{ border-right:none; }}
.combo-grid button.active {{ background:#1f6feb; color:#fff; }}
.combo-grid button:hover:not(.active) {{ background:#21262d; color:#e0e0e0; }}
</style>
</head>
<body>
<div class="header">
  <h1>QQQ 末日期权 — 开盘垂直看涨价差（Bull Call Spread）回测分析</h1>
  <div class="sub">0DTE 开盘建仓 · 数据范围：{date_min} ~ {date_max} · 共 <span id="hdr-days">{total_trades}</span> 个交易日</div>
  <div class="sub-detail">
    <div class="item"><span class="tag green">开盘建仓</span>9:30 同时：买入 base+buy_offset Call + 卖出 base+sell_offset Call（净权利金 = 买腿 − 卖腿）</div>
    <div class="item"><span class="tag blue">止盈触发</span>逐分钟监控 QQQ 当日涨幅，「QQQ涨幅 ≥ 上涨触发%」立即平仓</div>
    <div class="item"><span class="tag red">止损触发</span>「QQQ跌幅 ≥ 下跌触发%」立即平仓（垂直看涨价差为看多结构）</div>
    <div class="item"><span class="tag yellow">手续费</span>开仓 2 腿 + 平仓 2 腿 = 4 张次，默认 ${COMMISSION}/张，共 ${round(COMMISSION*4,2)}</div>
  </div>
</div>
<div class="ctrl-bar">
  <label>价差组合</label>
  <div class="combo-grid">{combo_btns}</div>
  <label style="margin-left:10px">上涨触发%</label>
  <input type="number" id="upper" value="{DEFAULT_UPPER_PCT}" min="0.05" max="10" step="0.05">
  <label>下跌触发%</label>
  <input type="number" id="lower" value="{DEFAULT_LOWER_PCT}" min="0.05" max="10" step="0.05">
  <label>手续费</label>
  <input type="number" id="commission" value="{COMMISSION}" min="0" max="50" step="0.1">
  <span style="color:#8b949e;font-size:12px">$/张</span>
  <label>平仓时间</label>
  <input type="time" id="closeTime" value="{DEFAULT_CLOSE_TIME}" min="09:35" max="15:00">
  <button class="ctrl-btn" onclick="applyParams()">▶ 重新计算</button>
  <span class="ctrl-hint" id="ctrlHint">当前：买+{DEFAULT_BUY_OFFSET}/卖+{DEFAULT_SELL_OFFSET} · QQQ涨{DEFAULT_UPPER_PCT}% 止盈 · QQQ跌{DEFAULT_LOWER_PCT}% 止损 · 平仓 {DEFAULT_CLOSE_TIME}</span>
</div>
<div class="stats-row">
  <div class="stat-card"><div class="label">交易天数</div><div class="value blue" id="s-days">{total_trades}</div></div>
  <div class="stat-card"><div class="label">胜率</div><div class="value" id="s-winrate">{win_rate}%</div></div>
  <div class="stat-card"><div class="label">盈利 / 亏损</div><div class="value" id="s-wl"><span class="green">{wins}</span>&nbsp;/&nbsp;<span class="red">{losses}</span></div></div>
  <div class="stat-card"><div class="label">触发次数</div><div class="value yellow" id="s-trig">{triggered}&nbsp;/&nbsp;{total_trades}</div></div>
  <div class="stat-card"><div class="label">累计盈亏</div><div class="value {'green' if total_pnl>=0 else 'red'}" id="s-totpnl">${round(total_pnl*100,2)}</div></div>
  <div class="stat-card"><div class="label">总投入净权利金</div><div class="value blue" id="s-cost">${round(total_cost_sum*100,2)}</div></div>
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
  <h2>每日交易明细 <span style="font-size:12px;color:#8b949e">（点击行展开 QQQ + 买腿 + 卖腿日内K线）</span></h2>
  <div class="table-wrap">
  <table id="tradeTable">
    <thead>
      <tr>
        <th>到期日</th><th>VIX买</th><th>VIX卖</th><th>粒度</th><th>QQQ开盘</th><th>QQQ涨跌%</th>
        <th>买K</th><th>买开</th><th>卖K</th><th>卖开</th>
        <th>净权利金</th><th>平仓价差</th>
        <th>触发</th><th>触发时间</th>
        <th>盈亏($)</th><th>盈亏%</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
  </div>
</div>

<script>
"""

    # 把每个 offset 的数据按日期组装
    # all_offsets_data[n] = {"summary_by_date": {date: {...}}, "call_by_date": {date: [bars]}}
    js_offsets = {}
    for n, payload in all_offsets_data.items():
        if not isinstance(n, int):
            continue
        js_offsets[n] = {
            "summary": payload["summary_by_date"],
            "call":    payload["call_by_date"],
        }

    html += f"const OFFSETS = {json.dumps(js_offsets, ensure_ascii=False)};\n"
    # QQQ 日数据 + 粒度
    qqq_per_day = all_offsets_data["_qqq_per_day"]
    qqq_gran    = all_offsets_data["_qqq_gran"]
    html += f"const QQQ_PER_DAY = {json.dumps(qqq_per_day, ensure_ascii=False)};\n"
    html += f"const QQQ_GRAN    = {json.dumps(qqq_gran, ensure_ascii=False)};\n"
    html += f"const VIX_MAP     = {json.dumps(all_offsets_data['_vix_map'], ensure_ascii=False)};\n"
    html += f"const VIX_5MIN    = {json.dumps(all_offsets_data['_vix_5min'], ensure_ascii=False)};\n"
    html += f"const VIX_DAILY_DATA = {json.dumps(vix_daily_data or [], ensure_ascii=False)};\n"
    html += f"const COMMISSION = {COMMISSION};\n"
    html += f"const MONITOR_START = '{MONITOR_START}';\n"

    html += r"""
// 当前参数
let _buyOff = """ + str(DEFAULT_BUY_OFFSET) + r""";
let _sellOff = """ + str(DEFAULT_SELL_OFFSET) + r""";
let _upper = """ + str(DEFAULT_UPPER_PCT) + r""";
let _lower = """ + str(DEFAULT_LOWER_PCT) + r""";
let _commission = COMMISSION;
let _closeTime = """ + f"'{DEFAULT_CLOSE_TIME}'" + r""";

let _activeResults = [];
let _activeCumPnl = [];

function switchCombo(b, s) {
  if (b >= s) { alert('买腿偏移必须小于卖腿偏移'); return; }
  _buyOff = b; _sellOff = s;
  document.querySelectorAll('.combo-grid button').forEach(btn => btn.classList.remove('active'));
  const el = document.getElementById('btn-' + b + '-' + s);
  if (el) el.classList.add('active');
  applyParams();
}

function applyParams() {
  const up = parseFloat(document.getElementById('upper').value);
  const lo = parseFloat(document.getElementById('lower').value);
  const cm = parseFloat(document.getElementById('commission').value);
  const ct = document.getElementById('closeTime').value || _closeTime;
  if (isNaN(up) || isNaN(lo) || up <= 0 || lo <= 0) { alert('参数无效：上涨/下跌触发% 必须 > 0'); return; }
  if (isNaN(cm) || cm < 0) { alert('手续费无效'); return; }
  if (ct < '09:35' || ct > '15:00') { alert('平仓时间需在 09:35 ~ 15:00'); return; }
  _upper = up; _lower = lo; _commission = cm; _closeTime = ct;
  document.getElementById('ctrlHint').textContent =
    '当前：买+' + _buyOff + '/卖+' + _sellOff + ' · QQQ涨' + _upper + '%止盈 · QQQ跌' + _lower + '%止损 · 手续费 $' + _commission + '/张 · 平仓 ' + _closeTime;
  recomputeAll();
}

function recomputeAll() {
  const buyData  = OFFSETS[_buyOff];
  const sellData = OFFSETS[_sellOff];
  if (!buyData || !sellData) { alert('数据缺失'); return; }
  const dates = Object.keys(QQQ_PER_DAY).filter(d =>
    buyData.summary[d] && sellData.summary[d] && buyData.call[d] && sellData.call[d]
  ).sort();

  const commTotal = _commission * 4 / 100;
  const out = [];
  let cum = 0;
  const cumArr = [];

  for (const t1 of dates) {
    const sumB = buyData.summary[t1], sumS = sellData.summary[t1];
    const callB = buyData.call[t1], callS = sellData.call[t1];
    const qBars = QQQ_PER_DAY[t1];
    if (!callB.length || !callS.length || !qBars.length) continue;
    const bOpen = callB.find(b => b.t === '09:30') || callB[0];
    const sOpen = callS.find(b => b.t === '09:30') || callS[0];
    const qOpen = qBars.find(b => b.t === '09:30') || qBars[0];
    const buyCost = bOpen.o, sellRecv = sOpen.o;
    if (buyCost <= 0 || sellRecv <= 0) continue;
    const netP = +(buyCost - sellRecv).toFixed(4);
    if (netP <= 0) continue;
    const qqqOpen = qOpen.c;

    const buyMap = {};  for (const b of callB) buyMap[b.t]  = b;
    const sellMap = {}; for (const b of callS) sellMap[b.t] = b;

    let trigTime = null, trigKind = null;
    for (const qb of qBars) {
      const t = qb.t;
      if (t < MONITOR_START || t > _closeTime) continue;
      const pct = (qb.c - qqqOpen) / qqqOpen * 100;
      if (pct >= _upper) { trigTime = t; trigKind = '止盈'; break; }
      if (pct <= -_lower) { trigTime = t; trigKind = '止损'; break; }
    }
    let sellTime = trigTime || _closeTime;
    let bb = buyMap[sellTime]  || [...callB].reverse().find(b => b.t <= sellTime);
    let sb = sellMap[sellTime] || [...callS].reverse().find(b => b.t <= sellTime);
    if (!bb || !sb) continue;
    const trigSpread = +(bb.c - sb.c).toFixed(4);
    if (!trigTime) trigKind = '时间';

    const pnl = +(trigSpread - netP - commTotal).toFixed(4);
    const pnlPct = netP > 0 ? +(pnl / netP * 100).toFixed(2) : 0;
    const qClose = qBars[qBars.length - 1].c;
    const qT2 = sumB.t2_close;
    const qDayPct = +((qClose - qT2) / qT2 * 100).toFixed(2);

    const vix = VIX_MAP[sumB.t2_date] != null ? VIX_MAP[sumB.t2_date] : null;
    const vDay = VIX_5MIN[t1] || [];
    let vSell = null;
    for (let i = vDay.length - 1; i >= 0; i--) { if (vDay[i].t <= sellTime) { vSell = vDay[i].c; break; } }

    cum += pnl;
    cumArr.push(+cum.toFixed(4));
    out.push({
      date: t1, t2_date: sumB.t2_date, t2_close: qT2,
      qqq_open: qqqOpen, qqq_close: qClose, qqq_pct: qDayPct,
      buy_strike: sumB.strike, buy_contract: sumB.contract, buy_open: +buyCost.toFixed(4),
      sell_strike: sumS.strike, sell_contract: sumS.contract, sell_open: +sellRecv.toFixed(4),
      net_premium: netP, close_spread: trigSpread,
      trig: trigKind, trig_time: trigTime || (_closeTime + '时间'),
      pnl: pnl, pnl_pct: pnlPct,
      gran: QQQ_GRAN[t1] || '?',
      vix: vix, vix_sell: vSell,
    });
  }

  _activeResults = out;
  _activeCumPnl = cumArr;
  renderStats();
  renderTable();
  drawCumChart();
  drawVixDailyChart();
  drawVixCharts();
  // 关闭已展开的行
  const old = document.getElementById('detailRow');
  if (old) old.remove();
  document.querySelectorAll('#tradeTable .data-row').forEach(tr => tr.classList.remove('selected'));
  currentIdx = -1;
}

function renderStats() {
  const n = _activeResults.length;
  const wins = _activeResults.filter(r => r.pnl > 0).length;
  const losses = n - wins;
  const trig = _activeResults.filter(r => r.trig !== '时间').length;
  const totPnl = _activeResults.reduce((s, r) => s + r.pnl, 0) * 100;
  const cost = _activeResults.reduce((s, r) => s + r.net_premium, 0) * 100;
  const avg = n ? totPnl / n : 0;
  const wr = n ? +(wins / n * 100).toFixed(1) : 0;
  const setEl = (id, html, col) => { const el = document.getElementById(id); if (!el) return; el.innerHTML = html; el.className = 'value ' + (col || ''); };
  setEl('s-days', n, 'blue');
  setEl('s-winrate', wr + '%', wr >= 50 ? 'green' : 'red');
  setEl('s-wl', '<span class="green">' + wins + '</span>&nbsp;/&nbsp;<span class="red">' + losses + '</span>');
  setEl('s-trig', trig + '&nbsp;/&nbsp;' + n, 'yellow');
  setEl('s-totpnl', '$' + totPnl.toFixed(2), totPnl >= 0 ? 'green' : 'red');
  setEl('s-cost', '$' + cost.toFixed(2), 'blue');
  setEl('s-avgpnl', '$' + avg.toFixed(2), avg >= 0 ? 'green' : 'red');
  document.getElementById('hdr-days').textContent = n;
}

function renderTable() {
  const tb = document.getElementById('tbody');
  tb.innerHTML = '';
  _activeResults.forEach((r, i) => {
    const pc = r.pnl > 0 ? 'pnl-pos' : 'pnl-neg';
    const qpc = r.qqq_pct >= 0 ? 'pnl-pos' : 'pnl-neg';
    const tc = r.trig === '止盈' ? 'trig-tp' : r.trig === '止损' ? 'trig-sl' : 'trig-time';
    const gc = r.gran === '1min' ? '' : r.gran === '2min' ? 'color:#d29922' : 'color:#f85149';
    const vBuyS = r.vix != null ? r.vix.toFixed(1) : '-';
    const vSellS = r.vix_sell != null ? r.vix_sell.toFixed(1) : '-';
    const vBuyC = r.vix >= 25 ? 'color:#f85149' : r.vix >= 20 ? 'color:#d29922' : 'color:#3fb950';
    const vSellC = r.vix_sell >= 25 ? 'color:#f85149' : r.vix_sell >= 20 ? 'color:#d29922' : 'color:#3fb950';
    const tr = document.createElement('tr');
    tr.className = 'data-row'; tr.dataset.idx = i; tr.onclick = () => selectDay(i);
    tr.innerHTML =
      '<td style="text-align:left">' + r.date + '</td>' +
      '<td style="' + vBuyC + ';font-weight:bold">' + vBuyS + '</td>' +
      '<td style="' + vSellC + ';font-weight:bold">' + vSellS + '</td>' +
      '<td style="' + gc + '">' + r.gran + '</td>' +
      '<td>$' + r.qqq_open.toFixed(2) + '</td>' +
      '<td class="' + qpc + '">' + r.qqq_pct + '%</td>' +
      '<td>$' + r.buy_strike + '</td>' +
      '<td>$' + r.buy_open + '</td>' +
      '<td>$' + r.sell_strike + '</td>' +
      '<td>$' + r.sell_open + '</td>' +
      '<td style="color:#58a6ff;font-weight:bold">$' + r.net_premium + '</td>' +
      '<td>$' + r.close_spread + '</td>' +
      '<td class="' + tc + '">' + r.trig + '</td>' +
      '<td>' + r.trig_time + '</td>' +
      '<td class="' + pc + '">$' + (r.pnl * 100).toFixed(2) + '</td>' +
      '<td class="' + pc + '">' + r.pnl_pct + '%</td>';
    tb.appendChild(tr);
  });
}

// ─── 累计盈亏曲线 ───
let _cumHoverIdx = -1;
function _renderCumChartWith(results, cumVals) {
  const canvas = document.getElementById('cumChart');
  if (!canvas || !results.length) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width) return;
  canvas.width = rect.width * dpr; canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  const W = rect.width, H = rect.height;
  const pad = {t:20, r:20, b:35, l:65};
  const dates = results.map(r => r.date.slice(5));
  const vals = cumVals.map(v => v * 100);
  const dailyP = results.map(r => +(r.pnl * 100).toFixed(2));
  const minV = Math.min(0, ...vals), maxV = Math.max(0, ...vals);
  const range = maxV - minV || 1;
  const xStep = (W - pad.l - pad.r) / (vals.length - 1 || 1);
  const toY = v => pad.t + (maxV - v) / range * (H - pad.t - pad.b);
  ctx.strokeStyle = '#30363d'; ctx.lineWidth = 1; ctx.setLineDash([4,4]);
  const y0 = toY(0);
  ctx.beginPath(); ctx.moveTo(pad.l, y0); ctx.lineTo(W-pad.r, y0); ctx.stroke(); ctx.setLineDash([]);
  ctx.beginPath();
  for (let i = 0; i < vals.length; i++) { const x = pad.l + i * xStep, y = toY(vals[i]); i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); }
  const lastColor = vals[vals.length-1] >= 0 ? '#3fb950' : '#f85149';
  ctx.strokeStyle = lastColor; ctx.lineWidth = 2; ctx.stroke();
  ctx.lineTo(pad.l + (vals.length-1)*xStep, y0); ctx.lineTo(pad.l, y0); ctx.closePath();
  ctx.fillStyle = vals[vals.length-1] >= 0 ? 'rgba(63,185,80,0.12)' : 'rgba(248,81,73,0.12)'; ctx.fill();
  for (let i = 0; i < vals.length; i++) { const x = pad.l + i * xStep, y = toY(vals[i]); ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI*2); ctx.fillStyle = vals[i] >= 0 ? '#3fb950' : '#f85149'; ctx.fill(); }
  ctx.fillStyle = '#8b949e'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
  const ls = Math.max(1, Math.floor(vals.length/15));
  for (let i = 0; i < vals.length; i += ls) ctx.fillText(dates[i], pad.l + i*xStep, H-8);
  ctx.textAlign = 'right';
  for (let i = 0; i <= 5; i++) { const v = minV + range*i/5, y = toY(v); ctx.fillStyle = '#8b949e'; ctx.fillText('$'+v.toFixed(0), pad.l-6, y+4); ctx.strokeStyle = '#21262d'; ctx.lineWidth = 0.5; ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W-pad.r, y); ctx.stroke(); }
  if (_cumHoverIdx >= 0 && _cumHoverIdx < vals.length) {
    const hi = _cumHoverIdx;
    const hx = pad.l + hi * xStep, hy = toY(vals[hi]);
    ctx.setLineDash([3,3]); ctx.strokeStyle = 'rgba(160,180,220,0.5)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(hx, pad.t); ctx.lineTo(hx, H-pad.b); ctx.stroke(); ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(hx, hy, 5, 0, Math.PI*2); ctx.fillStyle = vals[hi] >= 0 ? '#3fb950' : '#f85149'; ctx.fill(); ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
    const dp = dailyP[hi], cv = vals[hi];
    const line1 = results[hi].date + '  ' + (results[hi].trig + ' @' + results[hi].trig_time);
    const line2 = '当日盈亏: ' + (dp >= 0 ? '+' : '') + '$' + dp.toFixed(2);
    const line3 = '累计盈亏: ' + (cv >= 0 ? '+' : '') + '$' + cv.toFixed(2);
    ctx.font = 'bold 11px sans-serif';
    const tw = Math.max(ctx.measureText(line1).width, ctx.measureText(line2).width, ctx.measureText(line3).width);
    const bw = tw + 20, bh = 58; let bx = hx + 10, by = hy - bh - 8;
    if (bx + bw > W - pad.r) bx = hx - bw - 10; if (by < pad.t) by = hy + 10;
    ctx.fillStyle = 'rgba(22,27,34,0.95)'; ctx.beginPath(); ctx.roundRect(bx, by, bw, bh, 5); ctx.fill();
    ctx.strokeStyle = dp >= 0 ? '#3fb950' : '#f85149'; ctx.lineWidth = 1; ctx.stroke();
    ctx.textAlign = 'left'; ctx.font = '10px sans-serif'; ctx.fillStyle = '#8b949e'; ctx.fillText(line1, bx+10, by+16);
    ctx.font = 'bold 12px sans-serif'; ctx.fillStyle = dp >= 0 ? '#3fb950' : '#f85149'; ctx.fillText(line2, bx+10, by+34);
    ctx.fillStyle = cv >= 0 ? '#3fb950' : '#f85149'; ctx.fillText(line3, bx+10, by+52);
  }
}
function drawCumChart() {
  _renderCumChartWith(_activeResults, _activeCumPnl);
  const canvas = document.getElementById('cumChart');
  if (!canvas) return;
  canvas.onmousemove = (e) => { const rect = canvas.getBoundingClientRect(); const n = _activeResults.length; const xStep = (rect.width - 85) / (n - 1 || 1); _cumHoverIdx = Math.max(0, Math.min(n-1, Math.round((e.clientX - rect.left - 65) / xStep))); _renderCumChartWith(_activeResults, _activeCumPnl); };
  canvas.onmouseleave = () => { _cumHoverIdx = -1; _renderCumChartWith(_activeResults, _activeCumPnl); };
}

// ─── 蜡烛图引擎 ───
const _chartState = {};
function drawCandleChart(canvasId, data, markers, thresholds, meta, refPrice, extraState) {
  _chartState[canvasId] = {data, markers, thresholds, meta, refPrice: refPrice||null, extraState: extraState||null, hoverIdx: -1};
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  _renderCandle(canvas, canvasId);
  canvas.onmousemove = (e) => {
    const s = _chartState[canvasId]; if (!s || !s.data || !s.data.length) return;
    const rect = canvas.getBoundingClientRect(); const mx = e.clientX - rect.left;
    const W = rect.width, padL = 60, padR = 22, n = s.data.length;
    s.hoverIdx = Math.max(0, Math.min(n-1, Math.floor((mx - padL) / ((W - padL - padR) / n))));
    _renderCandle(canvas, canvasId);
  };
  canvas.onmouseleave = () => { if (_chartState[canvasId]) { _chartState[canvasId].hoverIdx = -1; _renderCandle(canvas, canvasId); } };
}

function _renderCandle(canvas, canvasId) {
  const s = _chartState[canvasId]; if (!s) return;
  const {data, markers, thresholds, meta, hoverIdx} = s;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect(); if (!rect.width) return;
  canvas.width = rect.width * dpr; canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  const W = rect.width, H = rect.height;
  const pad = {t:50, r:22, b:28, l:60};
  const volH = 44, chartH = H - pad.t - pad.b - volH - 4, chartBot = pad.t + chartH;
  const volTop = chartBot + 4, volBot = H - pad.b;
  ctx.fillStyle = '#161b22'; ctx.fillRect(0,0,W,H);
  if (!data || !data.length) { ctx.fillStyle = '#8b949e'; ctx.font = '13px sans-serif'; ctx.textAlign = 'center'; ctx.fillText('无数据', W/2, H/2); return; }
  const n = data.length;
  let minP = Math.min(...data.map(d=>d.l)), maxP = Math.max(...data.map(d=>d.h));
  if (thresholds) { minP = Math.min(minP, ...thresholds.map(t=>t.val)); maxP = Math.max(maxP, ...thresholds.map(t=>t.val)); }
  const pm = (maxP - minP) * 0.06 || 0.5; minP -= pm; maxP += pm;
  const pRange = maxP - minP, maxVol = Math.max(...data.map(d=>d.v||0)) || 1;
  const barW = (W - pad.l - pad.r) / n;
  const toX = i => pad.l + (i + 0.5) * barW;
  const toY = v => pad.t + (maxP - v) / pRange * chartH;
  const toVY = v => volBot - (v / maxVol) * (volBot - volTop);
  ctx.strokeStyle = '#21262d'; ctx.lineWidth = 0.5; ctx.textAlign = 'right'; ctx.font = '10px sans-serif'; ctx.fillStyle = '#8b949e';
  for (let i = 0; i <= 5; i++) {
    const v = minP + pRange*i/5, y = toY(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W-pad.r, y); ctx.stroke();
    ctx.fillText('$'+v.toFixed(2), pad.l-4, y+3);
  }
  ctx.strokeStyle = '#30363d'; ctx.lineWidth = 0.5; ctx.beginPath(); ctx.moveTo(pad.l, volTop); ctx.lineTo(W-pad.r, volTop); ctx.stroke();
  if (thresholds) { ctx.setLineDash([6,3]); for (const tl of thresholds) { const y = toY(tl.val); ctx.strokeStyle = tl.color; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W-pad.r, y); ctx.stroke(); ctx.fillStyle = tl.color; ctx.font = '10px sans-serif'; ctx.textAlign = 'left'; ctx.fillText(tl.label, W-pad.r+2, y+3); } ctx.setLineDash([]); }
  for (let i = 0; i < n; i++) { const d = data[i], x = pad.l + i*barW; ctx.fillStyle = d.c >= d.o ? 'rgba(63,185,80,0.35)' : 'rgba(248,81,73,0.35)'; ctx.fillRect(x+1, toVY(d.v||0), barW-2, volBot-toVY(d.v||0)); }
  for (let i = 0; i < n; i++) {
    const d = data[i], x = toX(i), col = d.c >= d.o ? '#3fb950' : '#f85149';
    ctx.strokeStyle = col; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x, toY(d.h)); ctx.lineTo(x, toY(d.l)); ctx.stroke();
    const bW = Math.max(1.5, barW*0.65), bTop = toY(Math.max(d.o, d.c)), bBot = toY(Math.min(d.o, d.c));
    ctx.fillStyle = col; ctx.fillRect(x-bW/2, bTop, bW, Math.max(1, bBot - bTop));
  }
  ctx.fillStyle = '#8b949e'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
  const xls = Math.max(1, Math.floor(n/8));
  for (let i = 0; i < n; i += xls) ctx.fillText(data[i].t, toX(i), H-8);
  if (markers) { for (const m of markers) { const idx = data.findIndex(d => d.t >= m.time); if (idx < 0) continue; const x = toX(idx), y = toY(data[idx].h) - 18; ctx.beginPath(); ctx.moveTo(x, toY(data[idx].h)-4); ctx.lineTo(x-7, y-10); ctx.lineTo(x+7, y-10); ctx.closePath(); ctx.fillStyle = m.color; ctx.fill(); ctx.font = 'bold 9px sans-serif'; ctx.textAlign = 'center'; ctx.fillStyle = m.color; ctx.fillText(m.label, x, y-12); } }
  if (hoverIdx >= 0 && hoverIdx < n) {
    const x = toX(hoverIdx), cy = toY(data[hoverIdx].c);
    ctx.setLineDash([3,3]); ctx.strokeStyle = 'rgba(160,180,220,0.45)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, chartBot); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(pad.l, cy); ctx.lineTo(W-pad.r, cy); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = '#58a6ff'; ctx.fillRect(0, cy-9, pad.l-4, 18); ctx.fillStyle = '#fff'; ctx.font = 'bold 10px sans-serif'; ctx.textAlign = 'right'; ctx.fillText('$'+data[hoverIdx].c.toFixed(2), pad.l-6, cy+4);
  }
  // 顶部 OHLC 信息条
  const di = (hoverIdx >= 0 && hoverIdx < n) ? hoverIdx : n-1;
  const dd = data[di], isGr = dd.c >= dd.o, ic = isGr ? '#3fb950' : '#f85149';
  ctx.fillStyle = 'rgba(22,27,34,0.92)'; ctx.fillRect(pad.l, 1, W-pad.l-pad.r, 46);
  ctx.fillStyle = '#c9d1d9'; ctx.font = 'bold 11px sans-serif'; ctx.textAlign = 'left';
  ctx.fillText(dd.t, pad.l+5, 16);
  const chgPct = dd.o ? ((dd.c-dd.o)/dd.o*100) : 0;
  const ohlc = [['开',(dd.o||0).toFixed(2),'#c9d1d9'],['高',(dd.h||0).toFixed(2),'#3fb950'],['低',(dd.l||0).toFixed(2),'#f85149'],['收',(dd.c||0).toFixed(2),ic],[(chgPct>=0?'▲':'▼'),Math.abs(chgPct).toFixed(2)+'%',ic]];
  let ix = pad.l + 52;
  for (const [lbl, val, col] of ohlc) { ctx.font = '10px sans-serif'; ctx.fillStyle = '#8b949e'; ctx.textAlign = 'left'; ctx.fillText(lbl+':', ix, 16); const lw = ctx.measureText(lbl+':').width + 2; ctx.font = 'bold 11px sans-serif'; ctx.fillStyle = col; ctx.fillText(val, ix+lw, 16); ix += lw + ctx.measureText(val).width + 12; }
  ctx.font = '10px sans-serif'; ctx.fillStyle = '#8b949e'; ctx.textAlign = 'left';
  ctx.fillText('量:', pad.l+5, 34);
  const volStr = dd.v >= 1000000 ? (dd.v/1000000).toFixed(2)+'M' : dd.v >= 1000 ? (dd.v/1000).toFixed(0)+'K' : String(dd.v||0);
  ctx.font = 'bold 10px sans-serif'; ctx.fillText(volStr, pad.l+22, 34);
  let renderMeta = meta;
  if (meta && (s.refPrice || s.extraState)) {
    let newMeta = meta.slice();
    if (s.refPrice) {
      const dynPct = (dd.c - s.refPrice) / s.refPrice * 100;
      const dynStr = (dynPct >= 0 ? '+' : '') + dynPct.toFixed(2) + '%';
      const dynCol = dynPct >= 0 ? '#3fb950' : '#f85149';
      newMeta = newMeta.map(([k, v, c]) => k === 'QQQ涨跌' ? [k, dynStr, dynCol] : [k, v, c]);
    }
    if (s.extraState) {
      const es = s.extraState;
      const hoverT = dd.t;
      const sb = es.spreadBars.find(b => b.t === hoverT) || es.spreadBars.filter(b => b.t <= hoverT).slice(-1)[0];
      if (sb) {
        const dynPnl = (sb.c - es.netPremium - es.commission) * 100;
        const pnlStr = (dynPnl >= 0 ? '+' : '') + '$' + dynPnl.toFixed(2);
        const pnlCol = dynPnl >= 0 ? '#3fb950' : '#f85149';
        const pnlPctStr = es.netPremium > 0 ? (' (' + ((dynPnl / (es.netPremium * 100)) * 100).toFixed(1) + '%)') : '';
        newMeta = newMeta.filter(([k]) => k !== '实时收益');
        newMeta.push(['实时收益', pnlStr + pnlPctStr, pnlCol]);
      }
    }
    renderMeta = newMeta;
  }
  if (renderMeta) { let mx2 = pad.l + 80; for (const [k,v,c] of renderMeta) { ctx.font = '10px sans-serif'; ctx.fillStyle = '#8b949e'; ctx.fillText(k+':', mx2, 34); const kw = ctx.measureText(k+':').width+2; ctx.font = 'bold 10px sans-serif'; ctx.fillStyle = c || '#c9d1d9'; ctx.fillText(v, mx2+kw, 34); mx2 += kw + ctx.measureText(v).width + 16; } }
}

// ─── 行内展开：QQQ + 买腿 + 卖腿 三图 ───
let currentIdx = -1;
function selectDay(idx) {
  const old = document.getElementById('detailRow');
  if (old) old.remove();
  document.querySelectorAll('#tradeTable .data-row').forEach(tr => tr.classList.remove('selected'));
  if (currentIdx === idx) { currentIdx = -1; return; }
  currentIdx = idx;
  const r = _activeResults[idx];
  const dataRow = document.querySelector(`#tradeTable .data-row[data-idx="${idx}"]`);
  dataRow.classList.add('selected');
  const pnlColor = r.pnl >= 0 ? '#3fb950' : '#f85149';
  const sellTime = r.trig_time.replace('时间', '');
  const vBuyS = r.vix != null ? r.vix.toFixed(1) : '-';
  const vSellS = r.vix_sell != null ? r.vix_sell.toFixed(1) : '-';
  const buyBars  = OFFSETS[_buyOff].call[r.date] || [];
  const sellBars = OFFSETS[_sellOff].call[r.date] || [];
  const qBars    = QQQ_PER_DAY[r.date] || [];
  // 价差时间序列
  const sellMap = {};
  for (const b of sellBars) sellMap[b.t] = b;
  const spreadBars = [];
  for (const bb of buyBars) {
    const sb = sellMap[bb.t]; if (!sb) continue;
    const o = +(bb.o - sb.o).toFixed(4);
    const h = +(bb.h - sb.l).toFixed(4);
    const l = +(bb.l - sb.h).toFixed(4);
    const c = +(bb.c - sb.c).toFixed(4);
    spreadBars.push({t: bb.t, o:o, h:Math.max(o,h,l,c), l:Math.min(o,h,l,c), c:c, v: bb.v + sb.v});
  }

  const detailTr = document.createElement('tr');
  detailTr.id = 'detailRow'; detailTr.className = 'detail-tr';
  detailTr.innerHTML = `
    <td colspan="16">
      <div class="detail-inner">
        <div class="detail-header">
          <span style="font-size:15px;font-weight:bold;color:#58a6ff">${r.date} [${r.gran}]</span>
          <span style="color:${pnlColor};font-weight:bold">盈亏: $${(r.pnl*100).toFixed(2)} (${r.pnl_pct}%)</span>
          <span style="color:#c9d1d9">${r.trig} @ ${sellTime}</span>
          <span style="color:#8b949e">买腿 $${r.buy_strike}: $${r.buy_open} | 卖腿 $${r.sell_strike}: $${r.sell_open}</span>
          <span style="color:#58a6ff;font-weight:bold">净权利金 $${r.net_premium} → 平仓价差 $${r.close_spread}</span>
          <span style="color:#d29922;font-weight:bold">VIX: ${vBuyS}→${vSellS}</span>
        </div>
        <div class="legend">
          <span><span class="dot" style="background:#58a6ff"></span>9:30 买入开盘</span>
          <span><span class="dot" style="background:#f0883e"></span>${sellTime} 平仓</span>
          <span><span class="dot" style="background:#3fb950"></span>QQQ 止盈线（开盘 + ${_upper}%）</span>
          <span><span class="dot" style="background:#f85149"></span>QQQ 止损线（开盘 − ${_lower}%）</span>
        </div>
        <div class="chart-grid3">
          <div class="chart-box">
            <div class="chart-box-title">QQQ 走势 (${r.gran})</div>
            <canvas id="qqqCanvas" style="width:100%;height:320px;display:block;"></canvas>
          </div>
          <div class="chart-box">
            <div class="chart-box-title">买腿 Call $${r.buy_strike} (+${_buyOff})</div>
            <canvas id="buyCanvas" style="width:100%;height:320px;display:block;"></canvas>
          </div>
          <div class="chart-box">
            <div class="chart-box-title">卖腿 Call $${r.sell_strike} (+${_sellOff})</div>
            <canvas id="sellCanvas" style="width:100%;height:320px;display:block;"></canvas>
          </div>
        </div>
        <div style="margin-top:10px" class="chart-box">
          <div class="chart-box-title">价差走势（买腿 − 卖腿） · 净权利金 $${r.net_premium}</div>
          <canvas id="spreadCanvas" style="width:100%;height:280px;display:block;"></canvas>
        </div>
      </div>
    </td>`;
  dataRow.insertAdjacentElement('afterend', detailTr);

  requestAnimationFrame(() => {
    const upThr = +(r.qqq_open * (1 + _upper/100)).toFixed(2);
    const dnThr = +(r.qqq_open * (1 - _lower/100)).toFixed(2);
    drawCandleChart('qqqCanvas', qBars,
      [{time: '09:30', color:'#58a6ff', label:'买入'}, {time: sellTime, color:'#f0883e', label:'平仓'}],
      [{val: r.qqq_open, color:'#58a6ff', label:'开盘$'+r.qqq_open.toFixed(2)},
       {val: upThr, color:'#3fb950', label:'止盈 +'+_upper+'% $'+upThr},
       {val: dnThr, color:'#f85149', label:'止损 -'+_lower+'% $'+dnThr},
       {val: r.t2_close, color:'#8b949e', label:'T-1收$'+r.t2_close.toFixed(2)}],
      [['QQQ涨跌', r.qqq_pct + '%', r.qqq_pct >= 0 ? '#3fb950' : '#f85149'], ['实时收益', '--', '#8b949e']],
      r.qqq_open,
      {spreadBars, netPremium: r.net_premium, commission: _commission * 4 / 100});
    drawCandleChart('buyCanvas', buyBars,
      [{time:'09:30', color:'#58a6ff', label:'买$'+r.buy_open}, {time: sellTime, color:'#f0883e', label:'平'}],
      null,
      [['行权价','$'+r.buy_strike,'#c9d1d9'], ['开盘','$'+r.buy_open,'#58a6ff']]);
    drawCandleChart('sellCanvas', sellBars,
      [{time:'09:30', color:'#58a6ff', label:'卖$'+r.sell_open}, {time: sellTime, color:'#f0883e', label:'平'}],
      null,
      [['行权价','$'+r.sell_strike,'#c9d1d9'], ['开盘','$'+r.sell_open,'#f0883e']]);
    drawCandleChart('spreadCanvas', spreadBars,
      [{time:'09:30', color:'#58a6ff', label:'净权利金'}, {time: sellTime, color:'#f0883e', label:'平仓'}],
      [{val: r.net_premium, color:'#58a6ff', label:'净权利金 $'+r.net_premium}],
      [['净权利金','$'+r.net_premium,'#58a6ff'], ['平仓价差','$'+r.close_spread, r.pnl>=0?'#3fb950':'#f85149']]);
    detailTr.scrollIntoView({behavior:'smooth', block:'nearest'});
  });
}

// ─── VIX 日K线图 ───
function drawVixDailyChart() {
  if (!VIX_DAILY_DATA || !VIX_DAILY_DATA.length) return;
  const markers = [];
  for (const r of _activeResults) {
    const pnl = r.pnl * 100;
    markers.push({ time: r.date, color: pnl >= 0 ? '#3fb950' : '#f85149', label: (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(0) });
  }
  drawCandleChart('vixDailyCanvas', VIX_DAILY_DATA, markers, null, [['VIX 日K', '', '#d29922']], null);
}

// ─── VIX 散点 + 分段柱状图 ───
function drawVixCharts() { drawVixScatter(); drawVixBar(); }
function drawVixScatter() {
  const canvas = document.getElementById('vixScatter'); if (!canvas) return;
  const ctx = canvas.getContext('2d'); const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect(); if (!rect.width) return;
  canvas.width = rect.width * dpr; canvas.height = rect.height * dpr; ctx.scale(dpr, dpr);
  const W = rect.width, H = rect.height, pad = {t:30, r:20, b:35, l:55};
  ctx.fillStyle = '#161b22'; ctx.fillRect(0,0,W,H);
  const pts = _activeResults.filter(r => r.vix != null).map(r => ({vix:r.vix, pnl:r.pnl*100}));
  if (!pts.length) { ctx.fillStyle='#8b949e'; ctx.font='13px sans-serif'; ctx.textAlign='center'; ctx.fillText('无 VIX 数据', W/2, H/2); return; }
  const minV = Math.min(...pts.map(p=>p.vix))-1, maxV = Math.max(...pts.map(p=>p.vix))+1;
  const minP = Math.min(...pts.map(p=>p.pnl))-20, maxP = Math.max(...pts.map(p=>p.pnl))+20;
  const toX = v => pad.l + (v-minV)/(maxV-minV)*(W-pad.l-pad.r);
  const toY = v => pad.t + (maxP-v)/(maxP-minP)*(H-pad.t-pad.b);
  ctx.strokeStyle='#21262d'; ctx.lineWidth=0.5; ctx.fillStyle='#8b949e'; ctx.font='10px sans-serif';
  ctx.textAlign='right'; for (let i=0;i<=4;i++) { const v=minP+(maxP-minP)*i/4, y=toY(v); ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(W-pad.r,y);ctx.stroke(); ctx.fillText('$'+v.toFixed(0),pad.l-4,y+3); }
  ctx.textAlign='center'; for (let i=0;i<=5;i++) { const v=minV+(maxV-minV)*i/5, x=toX(v); ctx.beginPath();ctx.moveTo(x,pad.t);ctx.lineTo(x,H-pad.b);ctx.stroke(); ctx.fillText(v.toFixed(1),x,H-10); }
  const y0=toY(0); ctx.setLineDash([4,4]);ctx.strokeStyle='#58a6ff';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(pad.l,y0);ctx.lineTo(W-pad.r,y0);ctx.stroke();ctx.setLineDash([]);
  for (const p of pts) { const x=toX(p.vix),y=toY(p.pnl); ctx.beginPath();ctx.arc(x,y,5,0,Math.PI*2); ctx.fillStyle=p.pnl>=0?'#3fb950':'#f85149'; ctx.fill(); ctx.strokeStyle='rgba(255,255,255,0.3)';ctx.lineWidth=1;ctx.stroke(); }
  ctx.fillStyle='#c9d1d9'; ctx.font='bold 12px sans-serif'; ctx.textAlign='center'; ctx.fillText('VIX vs 策略盈亏', W/2, 16);
  ctx.fillStyle='#8b949e'; ctx.font='10px sans-serif'; ctx.fillText('VIX (T-1收盘)', W/2, H-2);
  ctx.save(); ctx.translate(12, H/2); ctx.rotate(-Math.PI/2); ctx.fillText('盈亏 ($)', 0, 0); ctx.restore();
  const n2=pts.length, sx=pts.reduce((a,p)=>a+p.vix,0), sy=pts.reduce((a,p)=>a+p.pnl,0), mx2=sx/n2, my=sy/n2;
  const sxy=pts.reduce((a,p)=>a+(p.vix-mx2)*(p.pnl-my),0), sxx=pts.reduce((a,p)=>a+(p.vix-mx2)**2,0), syy=pts.reduce((a,p)=>a+(p.pnl-my)**2,0);
  const r2 = sxx&&syy ? sxy/Math.sqrt(sxx*syy) : 0;
  ctx.fillStyle=r2>=0?'#3fb950':'#f85149'; ctx.font='bold 11px sans-serif'; ctx.textAlign='right';
  ctx.fillText('r = '+(r2>=0?'+':'')+r2.toFixed(3), W-pad.r, 16);
  if (sxx) { const slope=sxy/sxx, intercept=my-slope*mx2; ctx.setLineDash([6,3]);ctx.strokeStyle='rgba(88,166,255,0.5)';ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(toX(minV),toY(slope*minV+intercept));ctx.lineTo(toX(maxV),toY(slope*maxV+intercept));ctx.stroke();ctx.setLineDash([]); }
}
function drawVixBar() {
  const canvas = document.getElementById('vixBarChart'); if (!canvas) return;
  const ctx = canvas.getContext('2d'); const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect(); if (!rect.width) return;
  canvas.width = rect.width * dpr; canvas.height = rect.height * dpr; ctx.scale(dpr, dpr);
  const W = rect.width, H = rect.height, pad = {t:30, r:20, b:55, l:55};
  ctx.fillStyle = '#161b22'; ctx.fillRect(0,0,W,H);
  const pts = _activeResults.filter(r => r.vix != null);
  if (!pts.length) return;
  const bins = [{label:'<15',lo:0,hi:15},{label:'15-20',lo:15,hi:20},{label:'20-25',lo:20,hi:25},{label:'25-30',lo:25,hi:30},{label:'≥30',lo:30,hi:999}];
  const binData = bins.map(b => { const items = pts.filter(r => r.vix>=b.lo && r.vix<b.hi); const pnl = items.reduce((s,r)=>s+r.pnl*100,0); const w = items.filter(r=>r.pnl>0).length; return {label:b.label,count:items.length,pnl:+pnl.toFixed(2),wins:w,wr:items.length?+(w/items.length*100).toFixed(0):0}; }).filter(b=>b.count>0);
  if (!binData.length) return;
  const maxP = Math.max(...binData.map(b=>Math.abs(b.pnl)),1);
  const n2 = binData.length, barW2 = Math.min(60, (W-pad.l-pad.r)/n2*0.6), gap = (W-pad.l-pad.r)/n2;
  const toY = v => pad.t + (maxP-v)/(2*maxP)*(H-pad.t-pad.b);
  const y0=toY(0); ctx.strokeStyle='#58a6ff';ctx.lineWidth=1;ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(pad.l,y0);ctx.lineTo(W-pad.r,y0);ctx.stroke();ctx.setLineDash([]);
  ctx.strokeStyle='#21262d';ctx.lineWidth=0.5;ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';ctx.textAlign='right';
  for (let i=0;i<=4;i++){const v=-maxP+2*maxP*i/4,y=toY(v);ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(W-pad.r,y);ctx.stroke();ctx.fillText('$'+v.toFixed(0),pad.l-4,y+3);}
  for (let i=0;i<n2;i++){
    const b=binData[i], x=pad.l+i*gap+gap/2-barW2/2, y=toY(b.pnl);
    ctx.fillStyle=b.pnl>=0?'rgba(63,185,80,0.7)':'rgba(248,81,73,0.7)';
    if (b.pnl>=0){ctx.fillRect(x,y,barW2,y0-y);}else{ctx.fillRect(x,y0,barW2,y-y0);}
    ctx.fillStyle='#c9d1d9';ctx.font='bold 11px sans-serif';ctx.textAlign='center';
    ctx.fillText('$'+b.pnl.toFixed(0), x+barW2/2, b.pnl>=0?y-6:y+14);
    ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';
    ctx.fillText(b.label, x+barW2/2, H-pad.b+14); ctx.fillText(b.count+'天', x+barW2/2, H-pad.b+28);
    ctx.fillStyle=b.wr>=50?'#3fb950':'#f85149';ctx.font='bold 10px sans-serif'; ctx.fillText(b.wr+'%胜率', x+barW2/2, H-pad.b+42);
  }
  ctx.fillStyle='#c9d1d9';ctx.font='bold 12px sans-serif';ctx.textAlign='center'; ctx.fillText('VIX 分段累计盈亏',W/2,16);
}

window.addEventListener('load', () => { recomputeAll(); });
window.addEventListener('resize', () => {
  drawCumChart(); drawVixDailyChart(); drawVixCharts();
  if (currentIdx >= 0) {
    ['qqqCanvas','buyCanvas','sellCanvas','spreadCanvas'].forEach(id => { const c = document.getElementById(id); if (c && _chartState[id]) _renderCandle(c, id); });
  }
});
</script>
</body>
</html>"""

    return html


# ────────────────────────────────────────────────
# 主程序
# ────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  QQQ 开盘垂直看涨价差策略 — 回测 + HTML 可视化")
    print("=" * 60)

    print("加载 QQQ 分时数据...")
    qqq_1m, qqq_2m, qqq_5m = load_qqq_intraday()

    # 加载 5 个 offset 数据
    offsets_payload = {}
    summary_by_offset = {}
    call_by_offset    = {}
    all_dates = set()
    for n in (1, 2, 3, 4, 5):
        if not os.path.exists(OPT_FILES[n]):
            print(f"  ⚠ +{n} 数据文件不存在，跳过：{OPT_FILES[n]}")
            continue
        print(f"  加载 +{n} 期权数据...")
        summ, c1m = load_offset_data(n)
        summary_by_offset[n] = summ
        call_per_day = build_call_per_day(c1m)
        call_by_offset[n] = call_per_day
        # summary 简化结构（提供给 JS）
        sum_by_date = {}
        for _, row in summ.iterrows():
            d = str(row["到期日(T1)"])[:10]
            sum_by_date[d] = {
                "strike":   float(row["Call行权价"]),
                "contract": str(row["Call合约"]),
                "open":     float(row["Call_T1开盘"]) if pd.notna(row.get("Call_T1开盘")) else None,
                "t2_close": float(row["QQQ_T2收盘"]),
                "t2_date":  str(row["基准日(T2)"])[:10],
                "qqq_open": float(row["QQQ_T1开盘"]),
            }
            all_dates.add(d)
        offsets_payload[n] = {"summary_by_date": sum_by_date, "call_by_date": call_per_day}
        print(f"    +{n}: {len(sum_by_date)} 天")

    if DEFAULT_BUY_OFFSET not in offsets_payload or DEFAULT_SELL_OFFSET not in offsets_payload:
        print("缺少默认参数所需的数据文件")
        return

    # QQQ 日内数据（按所有日期）
    print("整理 QQQ 日内数据...")
    qqq_per_day, qqq_gran = build_qqq_per_day(sorted(all_dates), qqq_1m, qqq_2m, qqq_5m)
    print(f"  QQQ 日内数据共 {len(qqq_per_day)} 天")

    # VIX
    vix_map, vix_5min_map, vix_daily_data = {}, {}, []
    if os.path.exists(VIX_FILE):
        print("加载 VIX 数据...")
        try:
            vix_daily = pd.read_excel(VIX_FILE, sheet_name="VIX_日K")
            for _, vr in vix_daily.iterrows():
                d = str(vr["日期"])[:10]
                vix_map[d] = float(vr["收盘价"])
                vix_daily_data.append({"t": d, "o": round(float(vr["开盘价"]), 2), "h": round(float(vr["最高价"]), 2),
                                       "l": round(float(vr["最低价"]), 2), "c": round(float(vr["收盘价"]), 2), "v": 0})
            print(f"  VIX 日K: {len(vix_map)} 天")
        except Exception as e:
            print(f"  ⚠ VIX 日K 加载失败: {e}")
        try:
            vix_5m = pd.read_excel(VIX_FILE, sheet_name="VIX_5min")
            for _, vr in vix_5m.iterrows():
                ts = str(vr["时间"])
                d, t = ts[:10], ts[11:16]
                vix_5min_map.setdefault(d, []).append({
                    "t": t, "o": round(float(vr["开盘价"]), 2), "h": round(float(vr["最高价"]), 2),
                    "l": round(float(vr["最低价"]), 2), "c": round(float(vr["收盘价"]), 2),
                    "v": int(vr.get("成交量", 0))
                })
            print(f"  VIX 5min: {len(vix_5min_map)} 天")
        except Exception as e:
            print(f"  ⚠ VIX 5min 加载失败: {e}")
    else:
        print(f"  ⚠ 未找到 VIX 数据文件: {VIX_FILE}")

    # 默认参数下回测一次（仅用于打印汇总）
    print(f"\n运行默认参数回测（买+{DEFAULT_BUY_OFFSET} / 卖+{DEFAULT_SELL_OFFSET}, QQQ涨{DEFAULT_UPPER_PCT}%止盈, QQQ跌{DEFAULT_LOWER_PCT}%止损, 平仓 {DEFAULT_CLOSE_TIME}）...")
    default_results = run_backtest(
        DEFAULT_BUY_OFFSET, DEFAULT_SELL_OFFSET,
        DEFAULT_UPPER_PCT, DEFAULT_LOWER_PCT, DEFAULT_CLOSE_TIME,
        call_by_offset[DEFAULT_BUY_OFFSET], call_by_offset[DEFAULT_SELL_OFFSET],
        qqq_per_day, qqq_gran,
        summary_by_offset[DEFAULT_BUY_OFFSET], summary_by_offset[DEFAULT_SELL_OFFSET],
    )
    if default_results:
        wins = sum(1 for r in default_results if r["盈亏"] > 0)
        total_pnl = sum(r["盈亏"] for r in default_results) * 100
        print(f"  共 {len(default_results)} 个交易日 | 胜/负 {wins}/{len(default_results)-wins} | 累计盈亏 ${total_pnl:.2f}")
    else:
        print("  默认参数下无可回测交易日")

    # 注入 VIX 到默认结果（仅展示用）
    for r in default_results:
        r["VIX"] = vix_map.get(r["基准日"])
        sell_time = r["触发时间"].replace("时间", "")
        vix_day = vix_5min_map.get(r["到期日"], [])
        v_sell = None
        for bar in reversed(vix_day):
            if bar["t"] <= sell_time:
                v_sell = bar["c"]; break
        r["VIX_卖出"] = v_sell

    # 生成 HTML
    print("\n生成 HTML...")
    offsets_payload["_qqq_per_day"] = qqq_per_day
    offsets_payload["_qqq_gran"]    = qqq_gran
    offsets_payload["_vix_map"]     = vix_map
    offsets_payload["_vix_5min"]    = vix_5min_map
    html = generate_html(offsets_payload, default_results, vix_daily_data)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 已生成：{os.path.abspath(OUTPUT_HTML)}")


if __name__ == "__main__":
    main()
