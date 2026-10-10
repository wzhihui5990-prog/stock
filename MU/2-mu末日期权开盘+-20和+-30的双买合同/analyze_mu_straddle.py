# -*- coding: utf-8 -*-
"""
MU（美光）末日期权 开盘双买 —— 分析 · 回测 · 报告（三合一）

用法：
    python analyze_mu_straddle.py                 # 分析 + 回测 + 生成交互式 HTML 报告
    python analyze_mu_straddle.py --no-html       # 只跑分析/回测（快）
    python analyze_mu_straddle.py --offset 20     # 只跑某个偏移

输出：
    data/mu_analysis.xlsx      分析 + 回测的全部明细表
    data/mu_report.html        交互式报告（可实时切换策略参数；零外部依赖）

策略：T 日买入 Call(基准+N) + Put(基准−N)，基准 = round(T日开盘价/5)×5
"""

import os
import sys
import json
import argparse
import itertools
import numpy as np
import pandas as pd
from dataclasses import dataclass

try:  # GBK 控制台/重定向时 emoji 不报错
    sys.stdout.reconfigure(line_buffering=True, errors="replace")
except Exception:
    pass

# ────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
MU_FILE  = os.path.join(BASE_DIR, "..", "1-mu日K", "data", "mu_market_data.xlsx")
VIX_FILE = os.path.join(BASE_DIR, "..", "..", "VIX", "data", "vix_data.xlsx")

OUT_XLSX = os.path.join(DATA_DIR, "mu_analysis.xlsx")
OUT_HTML = os.path.join(DATA_DIR, "mu_report.html")

ANCHOR     = "0930"                          # 行权价锚点：0930 = T日开盘价；0945 = T日 09:45 价
ANCHOR_LAB = {"0930": "09:30 开盘价", "0945": "09:45 价格"}
ANCHOR_KEYS = ["0930", "0945"]               # 报告里可并列展示的锚点（按顺序）
ANCHOR_TIME = {"0930": "09:30", "0945": "09:45"}   # 锚点 → 【定价时刻】（行权价按这一刻的股价定）

# 每个锚点下【可选的买入时刻】。约束：买入时刻 ≥ 定价时刻，否则会用到未来价格（未来函数）。
#   · 09:30 开盘价为基准 → 开盘后 0/3/6/9 分钟买入，行权价仍是「开盘那一刻」定的那一套
#   · 09:45 价格为基准 → 只能 09:45 买
ANCHOR_BUYS = {"0930": ["09:30", "09:33", "09:36", "09:39"],
               "0945": ["09:45"]}
ALL_BUYS = sorted({t for v in ANCHOR_BUYS.values() for t in v})


def tag_of(anchor):
    """锚点 → 文件名后缀：0930（默认，兼容旧文件）返回空串。"""
    return "" if anchor == "0930" else f"_a{anchor}"


def anchor_tag():
    return tag_of(ANCHOR)


def anchor_file(off, anchor=None):
    return os.path.join(DATA_DIR, f"mu_0dte_options_open{tag_of(anchor or ANCHOR)}_offset{off}.xlsx")

OFFSETS = [20, 30]

# ⚠ 样本起始月份：早于此月份的到期日【整个丢弃】。
#    2026-07 是 VIX 高位月（均值 17.2），单独一个月贡献了全部利润，参考意义不大。
#    设成 "" 即可恢复使用全部数据。
SAMPLE_START = "2026-08"


def in_sample(t1):
    """到期日是否在样本区间内。"""
    return (not SAMPLE_START) or str(t1)[:7] >= SAMPLE_START
# 界面已移除腿型选择器，固定使用这一种腿型（想恢复选择器时改这里）
FIX_LEG = "sym20"

FEE_PER_CONTRACT = 2.0            # 手续费：每张每次 2 美元
MULT             = 100            # 1 张 = 100 股（美股期权标准乘数）
# 每股价口径的手续费：买 Call/Put（2 次）+ 卖 Call/Put（2 次）= 4 次
COMM = FEE_PER_CONTRACT * 4 / MULT

# 报告里可交互切换的参数网格
ENTRY_GRID = ["09:30", "09:45", "10:00"]
TMAX_GRID  = ["10:30", "11:00", "12:00", "13:00", "14:00", "15:55"]   # 平仓截止
TP_GRID    = [0.5, 1.0, 1.5, 1.75, 2.0, 3.0, 99.0]   # 99 = 不止盈
SL_GRID    = [0.3, 0.4, 0.5, 0.6, 0.7, 99.0]   # 99 = 不止损

# 报告打开时的默认参数
DEF_TP, DEF_SL, DEF_TMAX = 2.0, 0.7, "11:00"
DEF_ENTRY = {"0930": "09:33", "0945": "09:45"}   # 默认买入时刻
DEF_VO    = 15                                    # 默认 VIX(买入时) 门槛；None = 不过滤
VIX_GRID   = [None, 14, 15, 16, 17]             # None = 不按 VIX 过滤
# 腿型：(键, Call 腿列, Put 腿列, 显示名) —— 后两种用 ±20/±30 四个行权价混搭
# 「近」= 离买入时股价近的那一档（±20），「远」= 远的那一档（±30）
LEG_GRID   = [("sym20", "c20", "p20", "近Call + 近Put（±20）"),
              ("sym30", "c30", "p30", "远Call + 远Put（±30）"),
              ("c30p20", "c30", "p20", "远Call + 近Put（+30 / −20）"),
              ("c20p30", "c20", "p30", "近Call + 远Put（+20 / −30）")]

# 控制台分析用的网格
ENTRY_TIMES = ["09:30", "09:40", "09:45", "09:50", "10:00", "10:30", "11:00", "13:00"]
EXIT_TIMES  = ["09:45", "10:00", "10:30", "11:00", "12:00", "13:00", "14:00", "15:00", "15:55"]
TP_LIST     = [0.5, 1.0, 1.5, 2.0, 3.0]


# ────────────────────────────────────────────────
# 数据加载
# ────────────────────────────────────────────────
def load_mu():
    m = pd.read_excel(MU_FILE, sheet_name="MU_分时1min", dtype=str)
    for c in ["开盘价", "最高价", "最低价", "收盘价"]:
        m[c] = pd.to_numeric(m[c], errors="coerce")
    m["t"] = m["时间"].astype(str).str[-5:]
    m["d"] = m["时间"].astype(str).str[:10]
    return m


def load_vix():
    if not os.path.exists(VIX_FILE):
        return pd.DataFrame()
    v = pd.read_excel(VIX_FILE, sheet_name="VIX_日K", dtype=str)
    v["收盘价"] = pd.to_numeric(v["收盘价"], errors="coerce")
    v["日期"] = v["日期"].astype(str).str[:10]
    return v.sort_values("日期").reset_index(drop=True)


def load_vix5():
    """VIX 5 分钟序列 → {(日期, 'HH:MM'): 该 5 分钟柱的收盘价}。

    ⚑ VIX 指数从 03:15 ET 开始逐分钟发布（含盘前），所以：
      · '09:25' 柱的收盘价 = 09:30:00 那一刻的 VIX —— 09:30 买入时已知，无未来函数
      · '09:40' 柱的收盘价 = 09:45:00 那一刻的 VIX
    """
    if not os.path.exists(VIX_FILE):
        return {}
    try:
        m = pd.read_excel(VIX_FILE, sheet_name="VIX_5min")
    except Exception:
        return {}
    out = {}
    for t, c in zip(m["时间"].astype(str), m["收盘价"]):
        if pd.notna(c):
            out[(t[:10], t[11:16])] = float(c)
    return out


def _vix_at(vix5, date, hhmm):
    """取 hhmm 那一刻【已知】的 VIX = 那一刻（或之前）刚收完的那根 5 分钟柱的收盘价。

    5 分钟柱按【起始时刻】命名（'09:25' 柱 = 09:25~09:30，其收盘价就是 09:30:00 的值），
    所以 t 时刻已知的最后一根柱 = floor(t/5)*5 − 5：
      · 09:30 → 09:25 柱   · 09:33 → 09:25 柱   · 09:36 / 09:39 → 09:30 柱   · 09:45 → 09:40 柱
    全部只用买入前已知的信息，无未来函数。
    """
    tm = int(hhmm[:2]) * 60 + int(hhmm[3:])
    t = (tm // 5) * 5 - 5
    key = (date, f"{t // 60:02d}:{t % 60:02d}")
    return vix5.get(key, np.nan)


def load_opt(off, tag=""):
    p = os.path.join(DATA_DIR, f"mu_0dte_options_open{tag}_offset{off}.xlsx")
    if not os.path.exists(p):
        return None
    s = pd.read_excel(p, sheet_name="摘要", dtype=str)
    c = pd.read_excel(p, sheet_name="Call_1min", dtype=str)
    u = pd.read_excel(p, sheet_name="Put_1min", dtype=str)
    for df in (c, u):
        for col in ["开盘价", "最高价", "最低价", "收盘价"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["t"] = df["时间(美东)"].astype(str).str[-5:]
    return s, c, u


def build_days(off, mu_1m, vix, tag="", vix5=None):
    """每个到期日构建 09:30~15:55 的 1 分钟序列（期权稀疏分钟用最近成交价前向填充）。"""
    got = load_opt(off, tag)
    if got is None:
        return []
    s, c, u = got

    vdates = list(vix["日期"]) if not vix.empty else []
    vclose = {r["日期"]: (float(r["收盘价"]) if pd.notna(r["收盘价"]) else np.nan)
              for _, r in vix.iterrows()}

    days = []
    for _, row in s.iterrows():
        t1 = str(row["到期日(T1)"])[:10]
        if not in_sample(t1):
            continue
        md = mu_1m[mu_1m["d"] == t1][["t", "开盘价", "收盘价"]] \
            .rename(columns={"开盘价": "muo", "收盘价": "mu"})
        md = md[(md["t"] >= "09:30") & (md["t"] <= "15:55")].drop_duplicates("t").sort_values("t")
        if md.empty:
            continue
        cd = c[c["到期日"].astype(str).str[:10] == t1].drop_duplicates("t")[["t", "收盘价"]] \
              .rename(columns={"收盘价": "call"})
        ud = u[u["到期日"].astype(str).str[:10] == t1].drop_duplicates("t")[["t", "收盘价"]] \
              .rename(columns={"收盘价": "put"})
        if cd.empty or ud.empty:
            continue

        df = md.merge(cd, on="t", how="left").merge(ud, on="t", how="left")
        df["call"] = df["call"].ffill()
        df["put"] = df["put"].ffill()
        df = df.dropna(subset=["call", "put"]).reset_index(drop=True)
        if df.empty or "09:30" not in set(df["t"]):
            continue
        df["total"] = df["call"] + df["put"]

        vprev = np.nan
        if t1 in vdates:
            k = vdates.index(t1)
            if k > 0:
                vprev = vclose.get(vdates[k - 1], np.nan)

        # T 日真实开盘价 = 09:30 那根 1 分钟 bar 的【开盘价】
        # ⚠ 不能用 09:30 bar 的收盘价（那是 09:30:59 的价格，实测与开盘价可差 1%）
        r930 = df[df["t"] == "09:30"]
        mu_open = float(r930.iloc[0]["muo"]) if not r930.empty else float(df.iloc[0]["mu"])
        t2c = float(row["MU_T2收盘"]) if row.get("MU_T2收盘") else np.nan
        # 基准行权价优先取摘要里的实际值（换锚点后不能用开盘价重算）
        braw = row.get("基准行权价")
        if braw is not None and not pd.isna(braw) and str(braw).strip() not in ("", "nan"):
            base = round(float(braw) / 5) * 5
        else:
            base = round(mu_open / 5) * 5
        days.append({
            "date": t1,
            "weekday": pd.Timestamp(t1).day_name()[:3],
            "mu_open": mu_open,
            "mu_close": float(df.iloc[-1]["mu"]),
            "mu_high": float(df["mu"].max()),
            "mu_low": float(df["mu"].min()),
            "mu_range": (float(df["mu"].max()) - float(df["mu"].min())) / mu_open * 100,
            "gap": (mu_open - t2c) / t2c * 100 if t2c == t2c else np.nan,
            "prev_close": t2c,
            "vix_prev": vprev,
            "cost_open": float(df[df["t"] == "09:30"].iloc[0]["total"])
                         if not df[df["t"] == "09:30"].empty else np.nan,
            "base": base,
            "df": df,
        })
    return days


# ────────────────────────────────────────────────
# 回测引擎
# ────────────────────────────────────────────────
@dataclass
class Rule:
    name: str = ""
    entry: str = "09:45"
    tp: float = 99.0        # 止盈（相对成本；+200% → 市值 3 倍；99 = 不止盈）
    sl: float = 99.0        # 止损（相对成本；0.6 → 跌到 40%）
    vix_min: float | None = None
    skip_dow: tuple = ()


def evaluate(days, rule):
    """返回 (逐日结果 DataFrame, 被跳过列表)。停损/止盈一次性全平。"""
    trades, skipped = [], []
    for d in days:
        df = d["df"]
        e = df[df["t"] == rule.entry]
        if e.empty:
            skipped.append((d["date"], "无入场分钟")); continue
        if d["weekday"] in rule.skip_dow:
            skipped.append((d["date"], d["weekday"])); continue
        v = d.get("vix_prev")
        if rule.vix_min is not None and (v is None or np.isnan(v) or v < rule.vix_min):
            skipped.append((d["date"], "VIX 不足")); continue

        cost = float(e.iloc[0]["total"])
        if cost <= 0:
            skipped.append((d["date"], "成本异常")); continue

        path = df[df["t"] >= rule.entry]
        exitv, kind, etime = None, "END", "-"
        for _, r in path.iterrows():
            val = float(r["total"])
            if rule.tp < 90 and val >= cost * (1 + rule.tp):
                exitv, kind, etime = cost * (1 + rule.tp), "TP", r["t"]; break
            if rule.sl < 90 and val <= cost * (1 - rule.sl):
                exitv, kind, etime = cost * (1 - rule.sl), "SL", r["t"]; break
        if exitv is None:
            exitv, etime = float(path.iloc[-1]["total"]), path.iloc[-1]["t"]

        pnl = exitv - cost - COMM
        trades.append({
            "date": d["date"], "weekday": d["weekday"], "vix": v,
            "cost": cost, "cost_rate": cost / d["mu_open"] * 100,
            "recovered": exitv, "pnl": pnl, "pnl_pct": pnl / cost * 100,
            "exit": kind, "exit_t": etime,
        })
    return pd.DataFrame(trades), skipped


def _contract(df):
    """把「每股价」金额换算成「整组（1 Call + 1 Put，共 2 张 = 200 股）」的美元金额。

    所有以 `$` 结尾的列都乘 MULT；比率类列（成本率%、胜率%、振幅%）不动。
    """
    if df is None or len(df) == 0:
        return df
    df = df.copy()
    for c in df.columns:
        if isinstance(c, str) and c.endswith("$"):
            df[c] = (pd.to_numeric(df[c], errors="coerce") * MULT).round(2)
    return df


def stats(df, name, n_all):
    if df.empty:
        return {"规则": name, "交易数": 0, "跳过": n_all, "胜率%": 0, "总盈亏$": 0,
                "平均盈亏$": 0, "平均盈亏率%": 0, "最大回撤$": 0, "止盈/止损/到期": "-"}
    eq = df["pnl"].cumsum()
    k = df["exit"].value_counts()
    return {
        "规则": name, "交易数": len(df), "跳过": n_all - len(df),
        "胜率%": round((df["pnl"] > 0).mean() * 100, 1),
        "总盈亏$": round(df["pnl"].sum(), 2),
        "平均盈亏$": round(df["pnl"].mean(), 3),
        "平均盈亏率%": round(df["pnl_pct"].mean(), 1),
        "最大回撤$": round((eq.cummax() - eq).max(), 2),
        "止盈/止损/到期": f"{k.get('TP',0)}/{k.get('SL',0)}/{k.get('END',0)}",
    }


# 控制台输出用的固定场景
S0 = Rule("S0 基准：09:30买 / 持到收盘", entry="09:30")
S1 = Rule("S1 只改入场：09:45买 / 持到收盘", entry="09:45")
S3 = Rule("S3 推荐：09:45 + 止盈200%/止损60% + VIX≥16", entry="09:45",
          tp=2.0, sl=0.6, vix_min=16)
SCENARIOS = [
    S0, S1,
    Rule("S2 S1 + 止盈200%/止损60%", entry="09:45", tp=2.0, sl=0.6),
    S3,
    Rule("S4 S3 + 避开周一", entry="09:45", tp=2.0, sl=0.6, vix_min=16, skip_dow=("Mon",)),
]


# ────────────────────────────────────────────────
# 分析表（写入 Excel）
# ────────────────────────────────────────────────
def entry_exit_tables(days):
    entry_rows, exit_rows = [], []
    for et in ENTRY_TIMES:
        cs, rs = [], []
        for d in days:
            r = d["df"][d["df"]["t"] == et]
            if r.empty or d["mu_open"] <= 0:
                continue
            c = float(r.iloc[0]["total"])
            cs.append(c); rs.append(c / d["mu_open"] * 100)
        if cs:
            entry_rows.append({"入场时间": et, "样本": len(cs),
                               "平均成本$": round(float(np.mean(cs)), 3),
                               "成本率%": round(float(np.mean(rs)), 3)})
    for xt in EXIT_TIMES:
        pnl, pct, wins, n = [], [], 0, 0
        for d in days:
            df = d["df"]
            e, x = df[df["t"] == "09:30"], df[df["t"] == xt]
            if e.empty or x.empty:
                continue
            cost = float(e.iloc[0]["total"])
            if cost <= 0:
                continue
            p = float(x.iloc[0]["total"]) - cost - COMM
            pnl.append(p); pct.append(p / cost * 100); wins += 1 if p > 0 else 0; n += 1
        if n:
            exit_rows.append({"出场时间": xt, "样本": n, "胜率%": round(wins / n * 100, 1),
                              "总盈亏$": round(sum(pnl), 2),
                              "平均盈亏$": round(float(np.mean(pnl)), 4),
                              "平均盈亏率%": round(float(np.mean(pct)), 2)})
    return pd.DataFrame(entry_rows), pd.DataFrame(exit_rows)


def tp_sl_table(days, entry="09:30"):
    rows = []
    for tp, sl in itertools.product(TP_LIST, [0.3, 0.5, 0.6, 0.7]):
        tr, _ = evaluate(days, Rule(entry=entry, tp=tp, sl=sl))
        if tr.empty:
            continue
        rows.append({"止盈": f"+{int(tp*100)}%", "止损": f"-{int(sl*100)}%",
                     "样本": len(tr), "胜率%": round((tr["pnl"] > 0).mean() * 100, 1),
                     "总盈亏$": round(tr["pnl"].sum(), 2),
                     "平均盈亏$": round(tr["pnl"].mean(), 3),
                     "平均盈亏率%": round(tr["pnl_pct"].mean(), 1)})
    return pd.DataFrame(rows).sort_values("总盈亏$", ascending=False)


def daily_pnl(days, entry="09:30", horizon="13:00"):
    recs = []
    for d in days:
        df = d["df"]
        e, x = df[df["t"] == entry], df[df["t"] <= horizon]
        if e.empty or x.empty:
            continue
        cost = float(e.iloc[0]["total"])
        if cost <= 0:
            continue
        val = float(x.iloc[-1]["total"])
        recs.append({**{k: d[k] for k in ["date", "weekday", "mu_range", "gap", "vix_prev"]},
                     "cost_rate": cost / d["mu_open"] * 100,
                     "pnl": val - cost - COMM, "pnl_pct": (val - cost - COMM) / cost * 100})
    return pd.DataFrame(recs)


def _agg(x):
    return pd.Series({
        "样本": len(x), "胜率%": round((x["pnl"] > 0).mean() * 100, 1),
        "总盈亏$": round(x["pnl"].sum(), 2), "平均盈亏$": round(x["pnl"].mean(), 3),
        "平均盈亏率%": round(x["pnl_pct"].mean(), 1),
        "平均MU振幅%": round(x["mu_range"].mean(), 2),
        "平均成本率%": round(x["cost_rate"].mean(), 2),
    })


def buckets(days, col, bins, labels, **kw):
    t = daily_pnl(days, **kw)
    if t.empty:
        return pd.DataFrame()
    t = t.dropna(subset=[col])
    if t.empty:
        return pd.DataFrame()
    t["分档"] = pd.cut(t[col], bins=bins, labels=labels)
    return t.groupby("分档", observed=True).apply(_agg, include_groups=False).reset_index()


def group_stats(days, col, **kw):
    t = daily_pnl(days, **kw)
    if t.empty:
        return pd.DataFrame()
    t = t.dropna(subset=[col])
    return t.groupby(col).apply(_agg, include_groups=False).reset_index() if not t.empty else pd.DataFrame()


# ────────────────────────────────────────────────
# 报告数据（供前端 JS 实时切换）
#
# 设计：不再预存「参数组合 × 每日结果」（那会让文件随维度爆炸），
#       改为存 ±20/±30 四个行权价的【全分辨率 1 分钟价格序列】。
#       实测每天都是 09:30~15:55 共 386 根、横轴完全一致，因此只需一条全局时间轴。
#       止盈 / 止损 / 平仓截止 的模拟交给前端跑 —— 加新维度不增加文件体积。
# ────────────────────────────────────────────────
def build_axis():
    out, h, m = [], 9, 30
    while (h, m) <= (15, 55):
        out.append(f"{h:02d}:{m:02d}")
        m += 1
        if m == 60:
            h, m = h + 1, 0
    return out


TAXIS = build_axis()
TAXIS_N = len(TAXIS)

# 注意：期权价格四舍五入到 4 位小数（最便宜的腿可低到 $0.01，精度足够）
OPT_COLS = ("c20", "p20", "c30", "p30")


def build_panel(mu_1m, vix, tag="", vix5=None):
    """构建立足于 ±20 / ±30 四个行权价的 1 分钟面板。

    返回 (days, 可用腿型, 被跳过的日期及原因, 被剔除的腿型及原因)
    """
    srcs = {}
    for off in OFFSETS:
        got = load_opt(off, tag)
        if got is None:
            continue
        _, c, u = got
        for side, d in (("c", c), ("p", u)):
            dd = d[["到期日", "t", "收盘价"]].copy()
            dd["_d"] = dd["到期日"].astype(str).str[:10]
            srcs[f"{side}{off}"] = dd

    s = None
    for off in OFFSETS:
        got = load_opt(off, tag)
        if got is not None:
            s, c0, u0 = got
            break
    if s is None or s.empty:
        return [], [], [], []

    # ⚠ 数据完整性：某个偏移的文件若还没拉完（缺到期日），对应腿型【整体剔除】。
    #    否则当日面板会因该列全空而被整天跳过，把其余腿型的样本也一并砍掉。
    all_days = {d for d in s["到期日(T1)"].astype(str).str[:10] if in_sample(d)}
    partial = {n: len(all_days - set(d["_d"])) for n, d in srcs.items()
               if all_days - set(d["_d"])}
    legs, bad_legs = [], []
    for l in LEG_GRID:
        if l[1] not in srcs or l[2] not in srcs:
            continue
        miss = {n: partial[n] for n in (l[1], l[2]) if n in partial}
        if miss:
            bad_legs.append((l[3], "、".join(f"{n} 缺 {k} 天" for n, k in miss.items())))
        else:
            legs.append(l)
    if not legs:
        return [], [], [], bad_legs
    legs.sort(key=lambda l: l[0] != FIX_LEG)      # 固定腿型排首位
    keep = [n for n in srcs if n not in partial]

    vdates = list(vix["日期"]) if not vix.empty else []
    vclose = {r["日期"]: (float(r["收盘价"]) if pd.notna(r["收盘价"]) else np.nan)
              for _, r in vix.iterrows()}

    days, skipped = [], []
    for _, row in s.iterrows():
        t1 = str(row["到期日(T1)"])[:10]
        if not in_sample(t1):
            continue
        md = mu_1m[mu_1m["d"] == t1][["t", "开盘价", "收盘价"]] \
            .rename(columns={"开盘价": "muo", "收盘价": "mu"})
        md = md[(md["t"] >= "09:30") & (md["t"] <= "15:55")].drop_duplicates("t").sort_values("t")
        if md.empty or "09:30" not in set(md["t"]):
            skipped.append((t1, "MU 分时缺 09:30"))
            continue
        df = md.reset_index(drop=True)
        bad = None
        for name in keep:
            x = srcs[name][srcs[name]["_d"] == t1].drop_duplicates("t")[["t", "收盘价"]] \
                .rename(columns={"收盘价": name})
            df = df.merge(x, on="t", how="left").sort_values("t").reset_index(drop=True)
            col = df[name].ffill()
            lead = int(col.isna().sum())
            if lead:
                # 开盘头几分钟该行权价无成交 → 用首个有成交价回填（只容忍极少分钟）
                if lead > 5:
                    bad = f"{name} 缺 {lead} 分钟"
                    break
                col = col.bfill()
            df[name] = col
        if bad:
            skipped.append((t1, bad))
            continue
        if len(df) != TAXIS_N or list(df["t"]) != TAXIS:
            skipped.append((t1, f"分钟轴不符（{len(df)} 根）"))
            continue
        df["call"], df["put"] = df["c20"], df["p20"]
        df["total"] = df["c20"] + df["p20"]

        vprev = np.nan
        if t1 in vdates:
            k = vdates.index(t1)
            if k > 0:
                vprev = vclose.get(vdates[k - 1], np.nan)

        mu_open = float(df.iloc[0]["muo"])
        t2c = float(row["MU_T2收盘"]) if row.get("MU_T2收盘") else np.nan
        braw = row.get("基准行权价")
        if braw is not None and not pd.isna(braw) and str(braw).strip() not in ("", "nan"):
            base = round(float(braw) / 5) * 5
        else:
            base = round(mu_open / 5) * 5
        days.append({
            "date": t1, "weekday": pd.Timestamp(t1).day_name()[:3],
            "mu_open": mu_open, "mu_close": float(df.iloc[-1]["mu"]),
            "mu_range": (float(df["mu"].max()) - float(df["mu"].min())) / mu_open * 100,
            "gap": (mu_open - t2c) / t2c * 100 if t2c == t2c else np.nan,
            "prev_close": t2c, "vix_prev": vprev, "base": base,
            # 开盘那一刻 / 09:45 那一刻的 VIX（盘前即有，属买入时已知信息）
            "vix_open": _vix_at(vix5, t1, "09:30") if vix5 else np.nan,
            "vix_0945": _vix_at(vix5, t1, "09:45") if vix5 else np.nan,
            # 每个「可选买入时刻」那一刻已知的 VIX（前端按买入时刻切换）
            "vix_at": {t: (_vix_at(vix5, t1, t) if vix5 else np.nan) for t in ALL_BUYS},
            "df": df,
        })
    return days, legs, skipped, bad_legs


def _px_at(d, t):
    """t 时刻的股价。09:30 用当日【真实开盘价】（拉数据定价用的就是它，
    不能用 09:30 那根 1 分钟 bar 的收盘价 —— 那是 09:30:59 的价格）；
    其余时刻用该分钟 bar 的收盘价（与期权价格取同一根 bar，口径一致）。"""
    if t == "09:30":
        return float(d["mu_open"])
    return float(d["df"].iloc[TAXIS.index(t)]["mu"])


def build_report_data(days, legs, off_key="all", anchor=None, buy_t=None):
    anchor = anchor or ANCHOR
    src = ANCHOR_LAB[anchor]                 # 例如「09:30 开盘价」
    price_t = ANCHOR_TIME[anchor]            # 定价时刻（行权价就是按这一刻的股价定的）
    buys = ANCHOR_BUYS[anchor]               # 该锚点下可选的买入时刻
    buy_t = buy_t or buys[0]
    base_rows = []
    for d in days:
        # 每个买入时刻那一刻【已知】的 VIX。盘中数据缺失时【回退用 T-1 收盘】，
        # 并把 real 置 0，表里用 * 标出来。
        va = d.get("vix_at") or {}
        vmap = {}
        for t in buys:
            x = va.get(t, np.nan)
            real = 1 if (x is not None and x == x) else 0
            if not real:
                x = d.get("vix_prev")
            vmap[t] = [None if (x is None or x != x) else round(float(x), 2), real]
        base_rows.append({
            "date": d["date"], "dow": d["weekday"],
            "vix": None if np.isnan(d["vix_prev"]) else round(float(d["vix_prev"]), 2),
            "vo": vmap[buy_t][0], "vof": vmap[buy_t][1],
            "vx": vmap,                      # {买入时刻: [VIX, 是否真实盘中值]}
            "muo": round(d["mu_open"], 2), "muc": round(d["mu_close"], 2),
            "rng": round(d["mu_range"], 2),
            "gap": None if np.isnan(d["gap"]) else round(float(d["gap"]), 2),
            "pc": None if np.isnan(d["prev_close"]) else round(float(d["prev_close"]), 2),
            "base": d["base"],
            # 【定价时刻】的股价 → 基准应当等 round(它/5)×5（表中会自检，不符标 ⚠）
            "pbuy": round(_px_at(d, price_t), 2),
            # 各买入时刻的股价（表里「买入时股价(买入时刻)」列 + 成本率的分母）
            "pf": {t: round(_px_at(d, t), 2) for t in buys},
        })

    opt, mus = {}, {}
    for d in days:
        df = d["df"]
        # 只导出实际存在的行权价列（某个锚点可能只拉了 ±20，没有 c30/p30）
        opt[d["date"]] = {c: [round(float(x), 4) for x in df[c]]
                          for c in OPT_COLS if c in df.columns}
        mus[d["date"]] = [round(float(x), 2) for x in df["mu"]]

    # 该锚点实际拉到的偏移档位（如 [20, 30]；若 ±30 还没拉完则只有 [20]）
    have_offs = sorted({int(c[1:]) for c in OPT_COLS if c in days[0]["df"].columns})

    return {"off": off_key, "n": len(days),
            "span": f"{days[0]['date']} ~ {days[-1]['date']}",
            "anchor": src, "anchor_code": anchor, "src": src,
            # ⚑ 行权价 = round(【定价时刻】股价/5)×5 ± 偏移。
            #    买入时刻可以更晚（用同一套行权价，但成交价是买入那一刻的），只要 ≥ 定价时刻。
            "entries": buys, "buys": buys,
            "buyT": buy_t, "priceT": price_t,
            "entry_note": f"定价时刻 {price_t}（行权价基准，固定）；买入时刻可选：{' / '.join(buys)}",
            "tmaxes": TMAX_GRID,
            "tps": TP_GRID, "sls": SL_GRID, "vixes": VIX_GRID,
            "legs": [{"k": k, "a": a, "b": b, "label": lb} for k, a, b, lb in legs],
            "taxis": TAXIS, "comm": COMM, "mult": MULT,
            "fee": FEE_PER_CONTRACT, "buyT": buy_t,
            "deftp": DEF_TP, "defsl": DEF_SL, "deftmax": DEF_TMAX,
            "defentry": DEF_ENTRY.get(anchor, buys[0]), "defvo": DEF_VO,
            # 近/远两档偏移（近 = OFFSETS[0]，远 = OFFSETS[-1]），供前端展示行权价一览
            "onear": OFFSETS[0], "ofar": OFFSETS[-1], "offs": have_offs,
            "fixleg": legs[0][3],
            # 前端可选的 Call / Put 档位（腿型由「近/远 × Call/Put」两个选择器组成）
            "calls": [f"c{o}" for o in have_offs], "puts": [f"p{o}" for o in have_offs],
            "defa": legs[0][1], "defb": legs[0][2],
            "days": base_rows, "opt": opt, "mus": mus}


# ────────────────────────────────────────────────
# HTML（前端 JS 渲染，零外部依赖）
# ────────────────────────────────────────────────
_JS = r"""
const FMT = (v, d = 2) => (v === null || v === undefined || isNaN(v)) ? '-' : (+v).toFixed(d);
/* 金额（美元）格式化：千分位 */
const MONEY = v => (v === null || v === undefined || isNaN(v)) ? '-' :
  '$' + (+v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/* 在（可能降采样的）时间标签里找与 at 最接近的位置；超出 4 分钟则不画 */
function nearestLabel(labels, at) {
  const tm = s => (+s.slice(0, 2)) * 60 + (+s.slice(3, 5));
  const t = tm(at);
  let best = -1, bd = Infinity;
  for (let i = 0; i < labels.length; i++) {
    const d = Math.abs(tm(labels[i]) - t);
    if (d < bd) { bd = d; best = i; }
  }
  return bd <= 4 ? best : -1;
}

const CHART = {};   // ctxKey -> 当前图表的坐标与数据，供悬浮提示使用

function svgChart(opt) {
  const w = 1240, h = opt.h || 370, pl = 68, pr = 68, pt = 58, pb = 42;
  const labels = opt.labels, n = labels.length;
  if (!n) return '<div style="color:#8b949e;padding:20px">无数据</div>';
  const X = i => pl + (w - pl - pr) * i / Math.max(n - 1, 1);
  const ax = { 0: [Infinity, -Infinity], 1: [Infinity, -Infinity] };
  for (const s of opt.series) for (const y of s.pts) {
    const a = ax[s.axis || 0]; if (y < a[0]) a[0] = y; if (y > a[1]) a[1] = y;
  }
  for (const k of [0, 1]) {
    if (ax[k][0] !== Infinity) {
      let [lo, hi] = ax[k]; if (lo === hi) { lo -= 1; hi += 1; }
      ax[k] = [lo - (hi - lo) * 0.06, hi + (hi - lo) * 0.06];
    }
  }
  const hasR = ax[1][0] !== Infinity;
  const Y = (v, a) => { const [lo, hi] = ax[a]; return pt + (hi - v) / (hi - lo) * (h - pt - pb); };
  const mny = opt.money || {};
  const AX = (v, a) => mny[a] ? MONEY(v) : v.toLocaleString(undefined, { maximumFractionDigits: 1 });
  let o = `<svg viewBox="0 0 ${w} ${h}" style="width:100%;background:#0d1117;border:1px solid #30363d;border-radius:8px">`;
  if (opt.title) o += `<text x="${pl}" y="18" fill="#e6edf3" font-size="13">${opt.title}</text>`;
  for (let t = 0; t < 5; t++) {
    const yy = pt + (h - pt - pb) * t / 4;
    const v = ax[0][1] - (ax[0][1] - ax[0][0]) * t / 4;
    o += `<line x1="${pl}" y1="${yy}" x2="${w - pr}" y2="${yy}" stroke="#21262d"/>`;
    o += `<text x="${pl - 8}" y="${yy + 4}" fill="#8b949e" font-size="10" text-anchor="end">${AX(v, 0)}</text>`;
    if (hasR) {
      const v2 = ax[1][1] - (ax[1][1] - ax[1][0]) * t / 4;
      o += `<text x="${w - pr + 8}" y="${yy + 4}" fill="#8b949e" font-size="10">${AX(v2, 1)}</text>`;
    }
  }
  const step = Math.max(1, Math.floor(n / 14));
  for (let i = 0; i < n; i += step)
    o += `<text x="${X(i)}" y="${h - pb + 18}" fill="#8b949e" font-size="10" text-anchor="middle">${labels[i]}</text>`;
  let mi = 0;
  for (const m of (opt.markers || [])) {
    const i = nearestLabel(labels, m.at);
    if (i < 0) continue;
    o += `<line x1="${X(i)}" y1="${pt}" x2="${X(i)}" y2="${h - pb}" stroke="${m.color}" stroke-width="1.2" stroke-dasharray="4 3"/>`;
    const ly = pt + 14 + (mi % 2) * 15;          // 交错排列，避免两条标注文字重叠
    const anchor = (X(i) > w - 240) ? 'end' : 'start';
    const dx = anchor === 'end' ? -4 : 4;
    o += `<text x="${X(i) + dx}" y="${ly}" fill="${m.color}" font-size="10" text-anchor="${anchor}">${m.label}</text>`;
    mi++;
  }
  for (const s of opt.series) {
    const pts = s.pts.map((y, i) => `${X(i).toFixed(1)},${Y(y, s.axis || 0).toFixed(1)}`).join(' ');
    o += `<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2}"${s.dash ? ' stroke-dasharray="5 4"' : ''}/>`;
  }
  // 买入 / 卖出点（画在“组合市值”曲线上，随所选策略变化）
  for (const d of (opt.dots || [])) {
    const cx = X(d.i), cy = Y(d.y, d.axis || 1);
    o += `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="5" fill="${d.color}" stroke="#0d1117" stroke-width="1.6"/>`;
    o += `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="9" fill="none" stroke="${d.color}" stroke-width="1" opacity="0.45"/>`;
    if (d.label) {
      const right = cx > w - 200;
      o += `<text x="${(right ? cx - 13 : cx + 13).toFixed(1)}" y="${(cy - 9).toFixed(1)}" fill="${d.color}"
        font-size="11.5" font-weight="600" text-anchor="${right ? 'end' : 'start'}">${d.label}</text>`;
    }
  }
  let lx = pl;                                    // 图例单独一行（y=40），与标题（y=18）分开
  for (const s of opt.series) {
    o += `<line x1="${lx}" y1="36" x2="${lx + 16}" y2="36" stroke="${s.color}" stroke-width="2.5"/>`;
    o += `<text x="${lx + 21}" y="40" fill="#8b949e" font-size="11">${s.name}</text>`;
    lx += 40 + 9.2 * s.name.length + 20;
  }
  o += '</svg>';
  if (opt.ctxKey) CHART[opt.ctxKey] = { w, h, pl, pr, pt, pb, labels, series: opt.series, base: opt.base };
  return o;
}

const OFF = 'm';                   // 单一界面的 DOM 后缀（两档买入时刻共用一个界面）
let CUR = DATA.blocks[0].off;      // 当前显示的买入时刻（数据区块）

const state = {};
for (const B of DATA.blocks) {
  state[B.off] = { entry: B.defentry, tp: B.deftp, sl: B.defsl, tmax: B.deftmax,
                   callLeg: B.defa, putLeg: B.defb,          // 行权价在「行权价一览」里点选
                   downOnly: false, vix: null, vixOpen: B.defvo,
                   dows: { Mon: 1, Tue: 1, Wed: 1, Thu: 1, Fri: 1 },
                   filter: 'all', day: B.days[B.days.length - 1].date };
}
/* 月份过滤是全局的（切换买入时刻时保持），不属于某个锚点的策略参数 */
const MONTHS_ON = Object.fromEntries((DATA.months || []).map(m => [m, 1]));
/* 每个买入时刻各自记着一套参数，来回切换不会丢失 */
const blk = () => DATA.blocks.find(b => b.off === CUR);
const stC = () => state[CUR];

const TIDX = {};
const TAXIS = DATA.blocks[0].taxis;
const M = DATA.blocks[0].mult;              // 1 张 = 100 股；金额显示 = 每股价 × M
const FEE = DATA.blocks[0].fee;
for (let i = 0; i < TAXIS.length; i++) TIDX[TAXIS[i]] = i;

/* 当前腿型 = Call 选一档 × Put 选一档（默认都是「近」） */
function legOf(B, st) {
  const a = B.calls.indexOf(st.callLeg) >= 0 ? st.callLeg : B.calls[0];
  const b = B.puts.indexOf(st.putLeg) >= 0 ? st.putLeg : B.puts[0];
  const hit = B.legs.find(l => l.a === a && l.b === b);
  if (hit) return hit;
  const nm = (c, side) => (+c.slice(1) === B.onear ? '近' : '远') + side;
  return { k: a + b, a: a, b: b,
           label: `${nm(a, 'Call')} + ${nm(b, 'Put')}（+${+a.slice(1)} / −${+b.slice(1)}）` };
}

/* 标题（随买入时刻 / 腿型变化） */
function hdText(B, st) {
  return `◆ 买入时刻 ${st.entry} · 定价基准 ${B.anchor}（行权价 = <code>round(${B.priceT} 股价 / 5)×5 ± 偏移</code>）`
    + `<span class="dim">（${B.n} 个交易日 · 当前腿型 <b style="color:#79c0ff">${legOf(B, st).label}</b>`
    + (B.buys.length > 1 ? ` · 买入时刻可切 ${B.buys.join(' / ')}` : '') + `）</span>`;
}

/* 单日模拟：从 st.entry 买入，先看止盈/止损是否触发，否则在 st.tmax 平仓。
   返回 {cost, pnl, pnl_pct, code(0到期/1止盈/2止损), exitT, value} 或 null */
function simDay(B, d, st, leg) {
  const A = B.opt[d.date][leg.a], P = B.opt[d.date][leg.b];
  if (!A || !P) return null;
  const i0 = TIDX[st.entry];
  const iEnd = TIDX[st.tmax];
  const cost = A[i0] + P[i0];
  if (!(cost > 0)) return null;
  let v = cost, code = 0, ie = iEnd;
  for (let i = i0; i <= iEnd; i++) {
    v = A[i] + P[i];
    if (st.tp < 90 && v >= cost * (1 + st.tp)) { v = cost * (1 + st.tp); code = 1; ie = i; break; }
    if (st.sl < 90 && v <= cost * (1 - st.sl)) { v = cost * (1 - st.sl); code = 2; ie = i; break; }
  }
  const pnl = v - cost - B.comm;
  return { cost, pnl, pnl_pct: pnl / cost * 100, code, ei: ie,
           exitT: TAXIS[ie], value: v };
}

function exitName(st, code) {
  return [st.tmax === '15:55' ? '持到收盘' : '到点平仓', '止盈', '止损'][code];
}

/* 「入场前是否下跌」——只用入场前已发生的信息，与入场时间严格对齐：
   · 入场晚于 09:30 → 用【入场前 1 分钟的价格 < 当日开盘价】
   · 入场就是 09:30 → 用【低开】= 开盘价 < 前一日收盘价（09:30 当下即知）
   ⚠ 不能固定用 09:44 判断：那对 09:30 入场而言是 14 分钟后的未来数据 */
function driftDown(B, d, st) {
  const i = TIDX[st.entry] - 1;
  const m = B.mus[d.date];
  if (i >= 0 && m && i < m.length) return m[i] < d.muo;
  return d.pc !== null && d.pc !== undefined && d.muo < d.pc;
}

function driftLabel(st) {
  return TIDX[st.entry] > 0 ? '入场前未下跌（vs 开盘）' : '未低开（vs 前收）';
}

/* 某个买入时刻那一刻的 VIX。Python 已按「那一刻已知的 5 分钟柱」算好；
   缺盘中数据的日子回退为 T-1 收盘（real = 0，表里用 * 标出）。
   09:30 / 09:33 取 09:25 柱，09:36 / 09:39 取 09:30 柱 —— 所以换买入时刻 VIX 会跟着变 */
function vixOf(d, entry) {
  const v = d.vx ? d.vx[entry] : null;
  return v ? { v: v[0], real: !!v[1] } : { v: d.vo, real: !!d.vof };
}

/* 某个买入时刻的股价（09:30 = 当日真实开盘价，其余 = 该分钟价） */
function pxOf(d, entry) {
  const p = d.pf ? d.pf[entry] : null;
  return (p === undefined || p === null) ? d.pbuy : p;
}

function compute(B, st) {
  const leg = legOf(B, st);
  const rows = [];
  let cum = 0;
  const eq = [];
  for (const d of B.days) {
    const vx = vixOf(d, st.entry);
    const dowOk = !!st.dows[d.dow];
    const voOk = (st.vixOpen === null) || (vx.v !== null && vx.v >= st.vixOpen);
    const dnOk = !st.downOnly || driftDown(B, d, st);
    const mOk = !!MONTHS_ON[d.date.slice(0, 7)];
    const r = (dowOk && voOk && dnOk && mOk) ? simDay(B, d, st, leg) : null;
    const row = Object.assign({}, d);
    row.vo = vx.v; row.vof = vx.real ? 1 : 0;      // 当前买入时刻那一刻的 VIX
    row.px = pxOf(d, st.entry);                    // 当前买入时刻的股价
    row.traded = !!r;
    if (r) {
      row.cost = r.cost; row.cost_rate = r.cost / row.px * 100;
      row.pnl = r.pnl; row.pnl_pct = r.pnl_pct; row.code = r.code;
      row.exit = exitName(st, r.code); row.exit_t = r.exitT;
      cum += r.pnl;
      eq.push([d.date, +cum.toFixed(2)]);
    } else if (!dowOk) {
      row.reason = '星期过滤';
    } else if (!mOk) {
      row.reason = '月份过滤（已剔除 ' + d.date.slice(0, 7) + '）';
    } else if (!voOk) {
      row.reason = '买入时VIX ' + FMT(vx.v) + ' 不足';
    } else if (!dnOk) {
      row.reason = driftLabel(st);
    } else {
      row.reason = '无数据';
    }
    rows.push(row);
  }
  const tr = rows.filter(r => r.traded);
  const n = tr.length;
  const pnls = tr.map(r => r.pnl);
  let peak = 0, dd = 0, c2 = 0;
  for (const p of pnls) { c2 += p; if (c2 > peak) peak = c2; if (peak - c2 > dd) dd = peak - c2; }
  const kpi = {
    n: n, skipped: rows.length - n,
    win: n ? +(tr.filter(r => r.pnl > 0).length / n * 100).toFixed(1) : 0,
    total: +pnls.reduce((a, b) => a + b, 0).toFixed(2),
    avg: n ? +(pnls.reduce((a, b) => a + b, 0) / n).toFixed(3) : 0,
    avgPct: n ? +(tr.reduce((a, r) => a + r.pnl_pct, 0) / n).toFixed(1) : 0,
    dd: +dd.toFixed(2),
    tp: tr.filter(r => r.code === 1).length,
    sl: tr.filter(r => r.code === 2).length,
    en: tr.filter(r => r.code === 0).length,
  };
  return { rows, tr, kpi, eq, leg };
}

function metricsOf(p) {
  const n = p.length;
  if (!n) return null;
  let peak = 0, dd = 0, c = 0;
  for (const x of p) { c += x; if (c > peak) peak = c; if (peak - c > dd) dd = peak - c; }
  const tot = p.reduce((a, b) => a + b, 0);
  return { n: n, win: p.filter(x => x > 0).length / n * 100, tot: tot,
           avg: tot / n, dd: dd, ratio: tot / Math.max(dd, 1) };
}

function baselineEq(B) {
  const st0 = Object.assign({}, state[B.off],
    { entry: '09:30', tp: 99, sl: 99, tmax: '15:55', vix: null,
      callLeg: B.defa, putLeg: B.defb, downOnly: false });
  return compute(B, st0).eq;
}

function render() {
  const off = OFF, B = blk(), st = stC();
  hideHover('ewrap_' + off);
  const { rows, tr, kpi, eq, leg } = compute(B, st);
  document.getElementById('kpi_' + off).innerHTML = [
    ['交易数', kpi.n], ['跳过', kpi.skipped],
    ['胜率', kpi.win + '%'], ['总盈亏', MONEY(kpi.total * M)],
    ['平均每笔', MONEY(kpi.avg * M)], ['平均盈亏率', kpi.avgPct + '%'],
    ['最大回撤', MONEY(kpi.dd * M)],
    ['止盈/止损/' + (st.tmax === '15:55' ? '收盘' : '到点'), kpi.tp + ' / ' + kpi.sl + ' / ' + kpi.en],
  ].map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');

  const be = baselineEq(B);
  const bm = Object.fromEntries(be);
  const dates = Array.from(new Set([...be.map(x => x[0]), ...eq.map(x => x[0])])).sort();
  let bc = 0, cc = 0;
  const s0 = [], s1 = [];
  const cm = Object.fromEntries(eq);
  for (const d of dates) {
    if (bm[d] !== undefined) bc = bm[d];
    if (cm[d] !== undefined) cc = cm[d];
    s0.push(bc);
    s1.push(cc);
  }
  const eqKey = 'eq_' + off;
  document.getElementById(eqKey).innerHTML = svgChart({
    h: 300, labels: dates.map(d => d.slice(5)), title: '累计盈亏（美元 · 整组 = 1 Call + 1 Put）',
    ctxKey: eqKey, money: { 0: true },
    series: [
      { name: 'S0 基准', pts: s0.map(v => v * M), color: '#8b949e', width: 1.6, money: true },
      { name: '当前策略', pts: s1.map(v => v * M), color: '#3fb950', width: 2.4, money: true },
    ]
  });
  addCrosshair(eqKey, 'ecross_' + off, eqKey);

  const show = st.filter === 'all' ? rows : (st.filter === 'traded' ? tr : rows.filter(r => !r.traded));
  const kA = +leg.a.slice(1), kB = +leg.b.slice(1);
  const buyT = st.entry;
  // 定价时刻的股价与开盘价不同时，才单独出一列「MU开盘」
  const showOpen = B.days.some(x => Math.abs((x.pbuy || 0) - (x.muo || 0)) > 0.005);
  const hd = ['日期', '星期', 'VIX(买入时)', '前收']
    .concat(showOpen ? ['MU开盘'] : [])
    .concat([`买入时股价(${buyT})`, 'MU收盘', '振幅%', '跳空%',
             '基准', 'Call行权价', 'Put行权价', '成本率%', '是否交易',
             '入场成本', '出场', '出场时间', '盈亏$', '盈亏%']);
  let html = '<thead><tr>' + hd.map(x => `<th class="${x === '基准' ? 'cbase' : ''}">${x}</th>`).join('')
           + '</tr></thead><tbody>';
  for (const r of show) {
    const cls = (r.traded ? (r.pnl > 0 ? 'win' : 'lose') : 'skip') + (r.date === st.day ? ' sel' : '');
    const ok = r.base === Math.round(r.pbuy / 5) * 5;      // 自检：基准 = round(买入价/5)×5
    html += `<tr class="${cls}" data-date="${r.date}">`
      + `<td>${r.date}</td><td>${r.dow}</td>`
      + `<td title="${r.vof ? '买入那一刻的 VIX（盘前即有，无未来函数）' : '该日缺 VIX 盘中数据，已回退为 T-1 收盘'}">`
      + `${r.vo === null ? '-' : FMT(r.vo)}<span style="color:#d29922">${r.vof ? '' : '*'}</span></td>`
      + `<td>${r.pc === null ? '-' : r.pc}</td>`
      + (showOpen ? `<td>${r.muo}</td>` : '')
      + `<td><b>${r.px}</b></td>`
      + `<td>${r.muc}</td><td>${r.rng}</td>`
      + `<td>${r.gap === null ? '-' : (r.gap > 0 ? '+' : '') + r.gap}</td>`
      + `<td class="cbase${ok ? '' : ' bad'}" title="基准 = round(定价时刻(${B.priceT})股价 / 5)×5">${r.base}${ok ? '' : ' ⚠'}</td>`
      + `<td>${r.base + kA}</td><td>${r.base - kB}</td>`
      + `<td>${r.traded ? FMT(r.cost_rate, 3) : '-'}</td>`
      + `<td>${r.traded ? '✅ 交易' : '⛔ ' + (r.reason || '跳过')}</td>`
      + `<td>${r.traded ? MONEY(r.cost * M) : '-'}</td>`
      + `<td>${r.traded ? r.exit : '-'}</td><td>${r.traded ? r.exit_t : '-'}</td>`
      + `<td class="num">${r.traded ? (r.pnl > 0 ? '+' : '') + MONEY(r.pnl * M) : '-'}</td>`
      + `<td>${r.traded ? (r.pnl_pct > 0 ? '+' : '') + FMT(r.pnl_pct, 1) + '%' : '-'}</td></tr>`;
  }
  document.getElementById('tbl_' + off).innerHTML = html + '</tbody>';
  document.getElementById('cnt_' + off).textContent = `（显示 ${show.length} / ${rows.length} 天）`;
  document.getElementById('h2_' + off).innerHTML = hdText(B, st);

  /* 行权价一览：近/远 Call、近/远 Put —— 点一下就能换腿（随买入时刻整体改变） */
  const dr = rows.find(x => x.date === st.day);
  const ks = document.getElementById('kstrike_' + off);
  if (ks) {
    if (!dr) ks.innerHTML = '';
    else {
      const nC = B.onear, fC = B.ofar;
      const chip = (side, ofs, px) => {
        const col = side[0].toLowerCase() + ofs;
        const ok = B.offs.indexOf(ofs) >= 0;
        const on = (side === 'Call' ? st.callLeg : st.putLeg) === col;
        return `<button class="chip${on ? ' sel' : ''}${ok ? '' : ' na'}" data-col="${col}"`
          + `${ok ? '' : ' disabled'} title="${ok ? '点一下改用这一档' : '该档期权数据还没拉，暂不可用'}">`
          + `${ofs === nC ? '近' : '远'} ${px}</button>`;
      };
      const btsel = B.buys.length > 1
        ? `<select class="btsel" data-bt="1">`
          + B.buys.map(t => `<option value="${t}"${t === st.entry ? ' selected' : ''}>${t}</option>`).join('')
          + `</select>`
        : `<b>${st.entry}</b>`;
      ks.innerHTML =
        `<span><span class="lab">买入时刻</span>${btsel}</span>`
        + `<span><span class="lab">定价基准</span><b>${B.anchor}</b></span>`
        + `<span><span class="lab">股价</span><b>${dr.px}</b></span>`
        + `<span class="arrow">→</span>`
        + `<span><span class="lab">基准</span><b class="kb">${dr.base}</b></span>`
        + `<span class="grp"><span class="lab">Call</span>`
        + chip('Call', nC, dr.base + nC) + chip('Call', fC, dr.base + fC) + `</span>`
        + `<span class="grp"><span class="lab">Put</span>`
        + chip('Put', nC, dr.base - nC) + chip('Put', fC, dr.base - fC) + `</span>`
        + `<span class="hint">（${st.day} · <b>点一下就能换行权价</b> · 近 = ±${nC} · 远 = ±${fC}`
        + (B.buys.length > 1 ? ` · 买入时刻可切 = 同一套行权价、晚几分钟成交` : '') + `）</span>`;
    }
  }

  renderChart();
}

/* 从明细表跳转到某一天（只更新表格高亮 + 分时图，不重算权益） */
function gotoDay(date) {
  const off = OFF, st = stC();
  if (!date || st.day === date) return;
  st.day = date;
  setSel('sel_' + off, date);
  document.querySelectorAll('#tbl_' + off + ' tbody tr').forEach(tr => {
    tr.classList.toggle('sel', tr.dataset.date === date);
  });
  renderChart();
  const el = document.getElementById('cwrap_' + off);
  if (el) {
    const r = el.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight)
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function renderChart() {
  const off = OFF, B = blk(), st = stC();
  hideHover('cwrap_' + off);
  const d = B.days.find(x => x.date === st.day);
  const leg = legOf(B, st);
  const okDay = !!st.dows[d.dow] && (st.vixOpen === null || (vixOf(d, st.entry).v !== null && vixOf(d, st.entry).v >= st.vixOpen))
                && (!st.downOnly || driftDown(B, d, st));
  const r = okDay ? simDay(B, d, st, leg) : null;

  const A = B.opt[st.day];
  const callS = A[leg.a], putS = A[leg.b];
  const totP = callS.map((x, i) => Math.round((x + putS[i]) * 1000) / 1000);
  const callM = callS.map(x => x * M), putM = putS.map(x => x * M);
  const totM = totP.map(x => x * M);
  const step = 3;                                  // 386 根 → 129 点，够密且不卡
  const idx = [];
  for (let i = 0; i < TAXIS.length; i += step) idx.push(i);
  if (idx[idx.length - 1] !== TAXIS.length - 1) idx.push(TAXIS.length - 1);
  const pick = a => idx.map(i => a[i]);
  const labels = pick(TAXIS);
  const totD = pick(totM);

  const marks = [], dots = [];
  if (r) {
    const exName = exitName(st, r.code);
    marks.push({ at: st.entry, label: '入场 ' + st.entry, color: '#1f6feb' });
    marks.push({ at: r.exitT, label: '出场 ' + exName + ' ' + r.exitT, color: '#f85149' });
    const iEn = nearestLabel(labels, st.entry), iEx = nearestLabel(labels, r.exitT);
    if (iEn >= 0) dots.push({ i: iEn, y: totD[iEn], color: '#1f6feb', label: '买入 ' + MONEY(totD[iEn]) });
    if (iEx >= 0) dots.push({ i: iEx, y: totD[iEx], color: '#f85149', label: '卖出 ' + MONEY(totD[iEx]) });
  }

  const ttl = `${st.day} (${d.dow})   VIX=${FMT(d.vix)}   MU ${d.muo}→${d.muc}   ${leg.label}`
    + (r ? `   买入 ${MONEY(r.cost * M)}   盈亏 ${r.pnl > 0 ? '+' : ''}${MONEY(r.pnl * M)} (${r.pnl_pct > 0 ? '+' : ''}${FMT(r.pnl_pct, 1)}%)`
         : '   （该策略下未交易）');

  const key = 'chart_' + off;
  document.getElementById(key).innerHTML = svgChart({
    h: 370, labels: labels, title: ttl, markers: marks, dots: dots, ctxKey: key, base: d.pc,
    money: { 1: true },
    series: [
      { name: 'MU', pts: pick(B.mus[st.day]), color: '#58a6ff', width: 2.2 },
      { name: 'Call', pts: pick(callM), color: '#3fb950', axis: 1, width: 1.3, money: true },
      { name: 'Put', pts: pick(putM), color: '#f85149', axis: 1, width: 1.3, money: true },
      { name: '组合市值', pts: totD, color: '#d29922', axis: 1, width: 1.8, dash: true, money: true },
    ]
  });
  addCrosshair(key, 'cross_' + off, key);
  updateNav();
}

/* 十字准线（悬浮时跟随鼠标）—— 通用：任意图表 div */
function addCrosshair(divId, crossId, ctxKey) {
  const c = CHART[ctxKey];
  const svg = document.querySelector('#' + divId + ' svg');
  if (!c || !svg) return;
  const ln = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  ln.setAttribute('id', crossId);
  ln.setAttribute('stroke', '#8b949e');
  ln.setAttribute('stroke-width', '1');
  ln.setAttribute('stroke-dasharray', '3 3');
  ln.setAttribute('y1', c.pt);
  ln.setAttribute('y2', c.h - c.pb);
  ln.setAttribute('x1', -10);
  ln.setAttribute('x2', -10);
  ln.setAttribute('pointer-events', 'none');
  svg.appendChild(ln);
}

/* 悬浮提示绑定表：wrapId -> {ctxKey, tipId, crossId} */
const HOV = {};

function bindHover(wrapId, tipId, ctxKey, crossId) {
  const wrap = document.getElementById(wrapId);
  if (!wrap) return;
  HOV[wrapId] = { ctxKey, tipId, crossId };
  wrap.addEventListener('mousemove', e => hoverAt(wrapId, e));
  wrap.addEventListener('mouseleave', () => hideHover(wrapId));
}

function hideHover(wrapId) {
  const h = HOV[wrapId];
  if (!h) return;
  const t = document.getElementById(h.tipId);
  if (t) t.style.display = 'none';
  const cr = document.getElementById(h.crossId);
  if (cr) { cr.setAttribute('x1', -10); cr.setAttribute('x2', -10); }
}

function hoverAt(wrapId, e) {
  const h = HOV[wrapId];
  if (!h) return;
  const c = CHART[h.ctxKey];
  const wrap = document.getElementById(wrapId);
  const tip = document.getElementById(h.tipId);
  if (!c || !wrap || !tip) return;
  const svg = wrap.querySelector('svg');
  if (!svg) return;
  const r = svg.getBoundingClientRect();
  if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) {
    hideHover(wrapId);
    return;
  }
  const rel = (e.clientX - r.left) / r.width * c.w;
  const n = c.labels.length;
  let i = Math.round((rel - c.pl) / (c.w - c.pl - c.pr) * (n - 1));
  i = Math.max(0, Math.min(n - 1, i));

  let html = `<div class="tt">${c.labels[i]}</div>`;
  for (const s of c.series) {
    let extra = '';
    if (s.name === 'MU' && s.pts.length && c.base) {
      const pct = (s.pts[i] - c.base) / c.base * 100;   // 相对【前一日收盘】
      const cls = pct > 0.0005 ? 'up' : (pct < -0.0005 ? 'down' : '');
      extra = `<span class="pct ${cls}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</span>`;
    }
    html += `<div class="tr"><i style="background:${s.color}"></i>${s.name}<b>${s.money ? MONEY(s.pts[i]) : FMT(s.pts[i], 2)}${extra}</b></div>`;
  }
  tip.innerHTML = html;

  const wr = wrap.getBoundingClientRect();
  tip.style.display = 'block';
  tip.style.left = Math.min(e.clientX - wr.left + 16, Math.max(wr.width - 178, 0)) + 'px';
  tip.style.top = Math.max(e.clientY - wr.top - 12, 4) + 'px';

  const cross = document.getElementById(h.crossId);
  if (cross) {
    const x = c.pl + (c.w - c.pl - c.pr) * i / Math.max(n - 1, 1);
    cross.setAttribute('x1', x);
    cross.setAttribute('x2', x);
  }
}

/* 为一组 (入场, 平仓截止, 腿型) 预计算每日的 TP×SL 出场矩阵，供 best() 快速扫描 */
function exitMatrix(B, entry, tmax, leg) {
  const i0 = TIDX[entry], iEnd = TIDX[tmax];
  if (!(iEnd > i0)) return null;
  const tps = B.tps, sls = B.sls, out = [];
  for (const d of B.days) {
    const A = B.opt[d.date][leg.a], P = B.opt[d.date][leg.b];
    if (!A || !P) { out.push(null); continue; }
    const cost = A[i0] + P[i0];
    if (!(cost > 0)) { out.push(null); continue; }
    const iTp = tps.map(t => {
      if (t >= 90) return -1;
      for (let i = i0; i <= iEnd; i++) if (A[i] + P[i] >= cost * (1 + t)) return i;
      return -1;
    });
    const iSl = sls.map(s => {
      if (s >= 90) return -1;
      for (let i = i0; i <= iEnd; i++) if (A[i] + P[i] <= cost * (1 - s)) return i;
      return -1;
    });
    const M = [];
    for (let a = 0; a < tps.length; a++) {
      const row = [];
      for (let b = 0; b < sls.length; b++) {
        const it = iTp[a], is = iSl[b];
        let v, code, ie;
        if (it >= 0 && (is < 0 || it <= is)) { v = cost * (1 + tps[a]); code = 1; ie = it; }
        else if (is >= 0) { v = cost * (1 - sls[b]); code = 2; ie = is; }
        else { v = A[iEnd] + P[iEnd]; code = 0; ie = iEnd; }
        row.push([v - cost - B.comm, code, ie]);
      }
      M.push(row);
    }
    out.push({ cost: cost, M: M });
  }
  return out;
}

function best() {
  const off = OFF, B = blk();
  const obj = document.getElementById('obj_' + off).value;
  const VOS = [null, 15, 15.5, 16];      // VIX(开盘那一刻)
  const FILTERS = [];
  for (const vo of VOS) for (const dn of [false, true])
    FILTERS.push({ vo: vo, dn: dn });
  let res = null, tried = 0;
  for (const entry of B.entries) {
    for (const tmax of B.tmaxes) {
      if (TIDX[tmax] <= TIDX[entry]) continue;
      for (const leg of B.legs) {
        const EM = exitMatrix(B, entry, tmax, leg);
        if (!EM) continue;
        for (let a = 0; a < B.tps.length; a++) {
          for (let b = 0; b < B.sls.length; b++) {
            for (const F of FILTERS) {
              const p = [];
              for (let k = 0; k < B.days.length; k++) {
                const d = B.days[k], e = EM[k];
                if (!e) continue;
                if (!MONTHS_ON[d.date.slice(0, 7)]) continue;      // 月份过滤
                const vv = vixOf(d, entry).v;                     // 该买入时刻那一刻的 VIX
                if (F.vo !== null && !(vv !== null && vv >= F.vo)) continue;
                if (F.dn && !driftDown(B, d, { entry: entry })) continue;
                p.push(e.M[a][b][0]);
              }
              if (p.length < 15) continue;     // 样本太少必然过拟合（尤其比值/胜率）
              tried++;
              const m = metricsOf(p);
              let score;
              if (obj === 'avg') score = m.avg;
              else if (obj === 'win') score = m.win;
              else if (obj === 'ratio') score = m.ratio;
              else score = m.tot;
              if (!res || score > res.score)
                res = { score: score, m: m,
                        cfg: { entry: entry, tmax: tmax, callLeg: leg.a, putLeg: leg.b,
                               tp: B.tps[a], sl: B.sls[b],
                               vixOpen: F.vo, downOnly: F.dn } };
            }
          }
        }
      }
    }
  }
  const el = document.getElementById('bres_' + off);
  if (!res) { if (el) el.textContent = '没有满足样本要求的组合'; return; }
  const st = stC();
  Object.assign(st, res.cfg);
  syncControls();
  render();
  const lg = B.legs.find(l => l.a === res.cfg.callLeg && l.b === res.cfg.putLeg) || legOf(B, st);
  if (el) el.innerHTML = `<b>共试 ${tried.toLocaleString()} 组 →</b> `
    + `${lg.label} / ${st.entry} 买 / ${st.tmax === '15:55' ? '持到收盘' : st.tmax + ' 平'} / `
    + `${st.tp < 90 ? '+' + Math.round(st.tp * 100) + '%' : '不止盈'} / `
    + `${st.sl < 90 ? '-' + Math.round(st.sl * 100) + '%' : '不止损'} / `
    + `${st.vixOpen === null ? 'VIX不过滤' : '开盘VIX≥' + st.vixOpen}`
    + (st.downOnly ? ' / 仅下跌日' : '')
    + `<br>&nbsp;&nbsp;→ ${res.m.n} 笔 · 胜率 ${res.m.win.toFixed(1)}% · 总盈亏 ${MONEY(res.m.tot * M)}`
    + ` · 回撤 ${MONEY(res.m.dd * M)} · 比值 ${res.m.ratio.toFixed(2)}`;
}

function setSel(id, val) {
  const s = document.getElementById(id);
  for (const o of s.options) {
    if (o.value === String(val) || (isFinite(+o.value) && +o.value === +val)) { s.value = o.value; return; }
  }
}

function syncControls() {
  const off = OFF, st = stC();
  setSel('tp_' + off, st.tp);
  setSel('sl_' + off, st.sl);
  setSel('tm_' + off, st.tmax);
  setSel('vo_' + off, st.vixOpen === null ? 'none' : st.vixOpen);
  setSel('sel_' + off, st.day);
  document.getElementById('ent_' + off).value = blk().anchor_code;
  const dn = document.getElementById('dn_' + off);
  if (dn) dn.checked = !!st.downOnly;
  document.querySelectorAll('#dows_' + off + ' input').forEach(x => x.checked = !!st.dows[x.value]);
  document.querySelectorAll('#mths_' + off + ' input').forEach(x => x.checked = !!MONTHS_ON[x.value]);
  document.querySelectorAll('#filt_' + off + ' button').forEach(b =>
    b.classList.toggle('active', b.dataset.f === st.filter));
}

/* 上一天 / 下一天 */
function stepDay(delta) {
  const off = OFF, B = blk(), st = stC();
  const i = B.days.findIndex(x => x.date === st.day);
  const j = Math.max(0, Math.min(B.days.length - 1, i + delta));
  if (j === i) return;
  st.day = B.days[j].date;
  setSel('sel_' + off, st.day);
  renderChart();
}

function updateNav() {
  const off = OFF, B = blk(), st = stC();
  const i = B.days.findIndex(x => x.date === st.day);
  const p = document.getElementById('prev_' + off);
  const n = document.getElementById('next_' + off);
  if (p) p.disabled = (i <= 0);
  if (n) n.disabled = (i >= B.days.length - 1);
}

/* 切换买入时刻 → 整套数据（天、行权价、价格）全部换掉 */
function loadBlock(scroll) {
  const off = OFF, B = blk(), st = stC();
  if (B.calls.indexOf(st.callLeg) < 0) st.callLeg = B.defa;
  if (B.puts.indexOf(st.putLeg) < 0) st.putLeg = B.defb;
  if (!B.days.some(d => d.date === st.day)) st.day = B.days[B.days.length - 1].date;
  document.getElementById('note_' + off).innerHTML = B.note;
  document.getElementById('sel_' + off).innerHTML =
    B.days.map(d => `<option value="${d.date}">${d.date} (${d.dow})</option>`).join('');
  document.getElementById('bres_' + off).innerHTML = '';
  document.getElementById('scan_' + off).innerHTML =
    `扫描 ${B.entries.length} 入场 × ${B.tmaxes.length} 平仓截止 × ${B.legs.length} 腿型 × `
    + `${B.tps.length} 止盈 × ${B.sls.length} 止损 × 4 档买入时VIX × 下跌日开关 = `
    + `<b>${(B.entries.length * B.tmaxes.length * B.legs.length * B.tps.length * B.sls.length * 4 * 2).toLocaleString()}</b> 组`
    + `（<b>要求样本 ≥ 15 笔</b>，否则比值/胜率类目标会选出 11 笔这种噪声解）`;
  syncControls();
  render();
  if (scroll) document.getElementById('h2_' + off).scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function init() {
  const off = OFF;
  document.getElementById('ent_' + off).addEventListener('change', e => {
    const B = DATA.blocks.find(b => b.anchor_code === e.target.value);
    if (!B || B.off === CUR) return;
    CUR = B.off;
    loadBlock(true);
  });
  document.getElementById('tp_' + off).addEventListener('change', e => { stC().tp = +e.target.value; render(); });
  document.getElementById('sl_' + off).addEventListener('change', e => { stC().sl = +e.target.value; render(); });
  document.getElementById('tm_' + off).addEventListener('change', e => { stC().tmax = e.target.value; render(); });
  /* 行权价一览：点 Call/Put 的档位按钮就换腿 */
  document.getElementById('kstrike_' + off).addEventListener('click', e => {
    const b = e.target.closest('button.chip');
    if (!b || b.disabled) return;
    const col = b.dataset.col, st = stC();
    if (col[0] === 'c') st.callLeg = col; else st.putLeg = col;
    render();
  });
  /* 行权价一览里的「买入时刻」下拉：只换买入时刻，行权价基准（那是哪一刻定的）不变 */
  document.getElementById('kstrike_' + off).addEventListener('change', e => {
    const t = e.target;
    if (t && t.dataset && t.dataset.bt) { stC().entry = t.value; render(); }
  });
  document.getElementById('dn_' + off).addEventListener('change', e => { stC().downOnly = e.target.checked; render(); });
  document.getElementById('vo_' + off).addEventListener('change', e => { stC().vixOpen = e.target.value === 'none' ? null : +e.target.value; render(); });
  document.getElementById('sel_' + off).addEventListener('change', e => { stC().day = e.target.value; renderChart(); });
  document.getElementById('prev_' + off).addEventListener('click', () => stepDay(-1));
  document.getElementById('next_' + off).addEventListener('click', () => stepDay(1));
  document.getElementById('tbl_' + off).addEventListener('click', e => {
    const tr = e.target.closest('tr');
    if (tr && tr.dataset.date) gotoDay(tr.dataset.date);
  });
  document.getElementById('sel_' + off).addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft') { e.preventDefault(); stepDay(-1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); stepDay(1); }
  });
  document.querySelectorAll('#dows_' + off + ' input').forEach(x =>
    x.addEventListener('change', () => { stC().dows[x.value] = x.checked ? 1 : 0; render(); }));
  document.querySelectorAll('#mths_' + off + ' input').forEach(x =>
    x.addEventListener('change', () => { MONTHS_ON[x.value] = x.checked ? 1 : 0; render(); }));
  document.getElementById('mall_' + off).addEventListener('click', () => {
    for (const m of DATA.months) MONTHS_ON[m] = 1;
    syncControls(); render();
  });
  document.querySelectorAll('#filt_' + off + ' button').forEach(b =>
    b.addEventListener('click', () => {
      stC().filter = b.dataset.f;
      document.querySelectorAll('#filt_' + off + ' button').forEach(y => y.classList.toggle('active', y === b));
      render();
    }));
  document.getElementById('best_' + off).addEventListener('click', () => best());
  bindHover('cwrap_' + off, 'tip_' + off, 'chart_' + off, 'cross_' + off);
  bindHover('ewrap_' + off, 'etip_' + off, 'eq_' + off, 'ecross_' + off);
  loadBlock(false);
}
init();
"""


def _sel(off, id_, opts, label):
    o = "".join(f'<option value="{v}">{t}</option>' for v, t in opts)
    return f'<label>{label}<select id="{id_}_{off}">{o}</select></label>'


OBJ_OPTS = [("total", "总盈亏最大"), ("avg", "平均每笔最大"),
            ("win", "胜率最高"), ("ratio", "盈亏/回撤比最大")]


def generate_html(blocks):
    B0 = blocks[0]
    tp_opts = [(str(v), ('+%.0f%%' % (v * 100)) if v < 90 else '不止盈') for v in B0['tps']]
    sl_opts = [(str(v), ('-%.0f%%' % (v * 100)) if v < 90 else '不止损') for v in B0['sls']]
    en_opts = [(v, v) for v in B0['entries']]      # （已由行权价一览里的「买入时刻」下拉取代）
    tm_opts = [(v, ('持到收盘' if v == '15:55' else v)) for v in B0['tmaxes']]
    vo_opts = [("none", "不过滤")] + [(x, f"买入时VIX ≥ {x}") for x in
                                       ("14.5", "15", "15.5", "16", "17")]
    dow_lab = [("Mon", "周一"), ("Tue", "周二"), ("Wed", "周三"), ("Thu", "周四"), ("Fri", "周五")]
    aopts = "".join(f'<option value="{b["anchor_code"]}">{ANCHOR_LAB[b["anchor_code"]]}'
                    f'（按该时刻股价定行权价）</option>' for b in blocks)
    # 单一区块，标与说明由 JS 按当前买入时刻填入
    _sp = f"，{SAMPLE_START} 之前的到期日已丢弃" if SAMPLE_START else ""
    for B in blocks:
        B["note"] = (
            f'<b style="color:#f0c040">样本区间 {B["span"]}</b>'
            f'（{B["n"]} 个到期日{_sp}）。'
            f'行权价 = <code>round({B["priceT"]} 股价 / 5)×5 ± 偏移</code>（Call = 基准+偏移，Put = 基准−偏移）—— '
            f'<b>定价基准固定在 {B["src"]}</b>，无未来函数。'
            f' 买入时刻可在这几个里切：<b style="color:#79c0ff">{" / ".join(B["buys"])}</b>'
            f'（<b>同一套行权价</b>，只是成交晚几分钟 —— 带上了开盘后的漂移）。'
            f'明细表中<span class="kbase">基准</span>列为<b style="color:#f0c040">琥珀色高亮</b>'
            f'（Call/Put 行权价都由它推出），且会自检 '
            f'<code>基准 = round({B["priceT"]} 股价 / 5)×5</code>，不符会标 <b style="color:#f85149">⚠</b>。')
    secs = []
    dows = "".join(f'<label class="cb"><input type="checkbox" value="{v}" checked>{t}</label>'
                   for v, t in dow_lab)
    # 月份过滤（默认全选）—— 例如取消 7 月就能看「排除高波动月」后的样子
    months = sorted({d["date"][:7] for b in blocks for d in b["days"]})
    mths = "".join(f'<label class="cb"><input type="checkbox" value="{m}" checked>'
                   f'{int(m[5:7])}月</label>' for m in months)
    off = "m"
    secs.append(f"""
<h2 id="h2_{off}"></h2>
<div class="anchornote" id="note_{off}"></div>
<div class="panel">
  <label title="行权价是按【定价时刻】的股价定的；换定价时刻 = 换到那一刻重新定价的一整套行情（行权价跟着变）">
    定价基准（行权价按哪一刻的股价定）<select id="ent_{off}">{aopts}</select></label>
  {_sel(off, 'tm', tm_opts, '平仓截止')}
  {_sel(off, 'tp', tp_opts, '止盈')}
  {_sel(off, 'sl', sl_opts, '止损')}
</div>
<div class="kstrip" id="kstrike_{off}"></div>
<div class="panel">
  {_sel(off, 'vo', vo_opts, 'VIX(买入时) 过滤')}
  <label class="cb" title="只用买入前已知的信息：买入晚于 09:30 用【买入前 1 分钟价 &lt; 当日开盘价】；09:30 买入用【低开】= 开盘 &lt; 前收">
    <input type="checkbox" id="dn_{off}">仅下跌日买（买入前判断）</label>
  <label>星期过滤<div class="dows" id="dows_{off}">{dows}</div></label>
</div>
<div class="panel">
  <label>月份过滤<div class="dows" id="mths_{off}">{mths}</div></label>
  <button class="navbtn" id="mall_{off}" title="把月份全部勾回来">↺ 全部月份</button>
  <span class="dim">取消勾选 = 把该月从 <b>KPI / 权益曲线 / 明细 / 自动找最优</b> 里整月剔除，重新计算（不是隐藏）</span>
</div>
<div class="panel">
  {_sel(off, 'obj', OBJ_OPTS, '优化目标')}
  <button id="best_{off}" class="best">⚡ 自动找最优参数</button>
  <span class="dim" id="bres_{off}"></span>
  <span class="dim" id="scan_{off}"></span>
</div>

<div class="cards" id="kpi_{off}"></div>

<h3>累计权益曲线 <span class="dim">（单位：美元 · 整组 = 1 Call + 1 Put，已扣手续费）</span></h3>
<div class="cwrap" id="ewrap_{off}">
  <div id="eq_{off}"></div>
  <div class="tip" id="etip_{off}"></div>
</div>

<h3>全部交易日明细 <span class="dim" id="cnt_{off}"></span> <span class="dim">（点击任意一行 → 下方分时图跳到该日）</span></h3>
<div class="filt" id="filt_{off}">
  <button data-f="all" class="active">全部</button>
  <button data-f="traded">仅交易</button>
  <button data-f="skipped">仅跳过</button>
</div>
<div class="scrolly"><table class="tbl" id="tbl_{off}"></table></div>

<h3>每日分时图 <span class="dim">（左轴 = MU 股价；<b>右轴 = 每张期权 / 整组的美元金额（已 ×100）</b>；鼠标悬浮看数值与 MU 相对【前一日收盘】的涨跌幅；蓝点 = 买入、红点 = 卖出）</span></h3>
<div class="daynav">
  <button id="prev_{off}" class="navbtn" title="上一天（← 键）">◀ 上一天</button>
  <select id="sel_{off}"></select>
  <button id="next_{off}" class="navbtn" title="下一天（→ 键）">下一天 ▶</button>
</div>
<div class="cwrap" id="cwrap_{off}">
  <div id="chart_{off}"></div>
  <div class="tip" id="tip_{off}"></div>
</div>
""")

    alabs = " / ".join(B['anchor'] for B in blocks)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>MU 末日期权 双买 — 策略实验室（定价基准：{alabs}）</title>
<style>
 body{{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;background:#0d1117;color:#e6edf3}}
 .wrap{{max-width:1440px;margin:0 auto;padding:24px}}
 h1{{font-size:22px;margin:0 0 6px}}
 h2{{font-size:18px;margin:34px 0 12px;border-left:3px solid #58a6ff;padding-left:9px}}
 h3{{font-size:14px;margin:22px 0 8px;color:#c9d1d9}}
 .sub{{color:#8b949e;font-size:13px;line-height:1.8;margin-bottom:8px}}
 .dim{{color:#8b949e;font-size:12px;font-weight:400}}
 .anchornote{{background:#161b22;border:1px solid #30363d;border-left:3px solid #d29922;border-radius:6px;
   padding:9px 13px;font-size:12.5px;color:#c9d1d9;margin:0 0 12px;line-height:1.8}}
 .anchornote code{{background:#21262d;padding:1px 5px;border-radius:4px;color:#79c0ff}}
 .anchornote .kbase,.kbase{{background:rgba(210,153,34,.16);color:#f0c040;font-weight:700;
   border:1px solid rgba(210,153,34,.45);border-radius:4px;padding:1px 6px}}
 .panel select[id^="ent_"]{{border-color:#1f6feb;color:#79c0ff;font-weight:600}}
 /* 行权价一览（随买入时刻变化） */
 .kstrip{{display:flex;flex-wrap:wrap;align-items:center;gap:4px 18px;background:#161b22;
   border:1px solid #30363d;border-left:3px solid #1f6feb;border-radius:6px;
   padding:9px 14px;margin:0 0 12px;font-size:13px;color:#c9d1d9}}
 .kstrip .lab{{color:#8b949e;font-size:12px;margin-right:5px}}
 .kstrip b{{font-size:15px;font-weight:700}}
 .kstrip b.kb{{color:#f0c040}}
 .kstrip b.kc{{color:#3fb950}}
 .kstrip b.kp{{color:#f85149}}
 .kstrip b.na{{color:#6e7681;text-decoration:line-through}}
 /* 可点的行权价按钮 */
 .kstrip .grp{{display:inline-flex;align-items:center;gap:6px}}
 .kstrip button.chip{{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:14px;
   padding:3px 11px;font-size:12.5px;cursor:pointer;font-family:inherit;line-height:1.5}}
 .kstrip button.chip:hover:not(:disabled){{border-color:#58a6ff;color:#fff;background:#161b22}}
 .kstrip button.chip.sel{{background:rgba(31,111,235,.22);border-color:#1f6feb;color:#79c0ff;font-weight:700}}
 .kstrip button.chip.sel::after{{content:' ✓';font-weight:700}}
 .kstrip button.chip.na{{color:#6e7681;border-style:dashed;cursor:not-allowed;text-decoration:line-through}}
 .kstrip .arrow{{color:#58a6ff;font-weight:700}}
 .kstrip select.btsel{{background:#0d1117;color:#79c0ff;border:1px solid #1f6feb;border-radius:5px;
   padding:2px 6px;font-size:13px;font-weight:700;font-family:inherit;cursor:pointer}}
 .kstrip .hint{{color:#8b949e;font-size:11.5px}}
 .panel{{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end;background:#161b22;border:1px solid #30363d;
   border-radius:8px;padding:12px 14px;margin-bottom:14px}}
 .panel label{{font-size:12px;color:#8b949e;display:flex;flex-direction:column;gap:4px}}
 .panel select{{background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:5px;padding:5px 8px;font-size:13px}}
 .dows{{display:flex;gap:8px}} .cb{{flex-direction:row!important;align-items:center;gap:3px!important;color:#c9d1d9!important}}
 button.best{{background:#1f6feb;color:#fff;border:none;border-radius:6px;padding:7px 14px;cursor:pointer;font-size:13px}}
 .cards{{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0 16px}}
 .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 14px;min-width:110px}}
 .card .k{{color:#8b949e;font-size:12px}} .card .v{{font-size:17px;font-weight:600;margin-top:3px}}
 .filt{{margin:6px 0 8px}} .filt button{{margin-right:6px;padding:4px 12px;background:#21262d;color:#c9d1d9;
   border:1px solid #30363d;border-radius:5px;cursor:pointer;font-size:12px}}
 .filt button.active{{background:#1f6feb;border-color:#1f6feb;color:#fff}}
 .daynav{{display:flex;align-items:center;gap:9px;margin:6px 0 10px}}
 .daynav select{{background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:5px;
   padding:5px 8px;font-size:13px}}
 .navbtn{{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:5px;
   padding:5px 12px;cursor:pointer;font-size:12.5px}}
 .navbtn:hover:not(:disabled){{background:#30363d;color:#fff}}
 .navbtn:disabled{{opacity:.35;cursor:default}}
 .tbl{{width:100%;border-collapse:collapse;font-size:12.5px;background:#161b22}}
 .tbl th{{background:#21262d;padding:7px 9px;text-align:left;white-space:nowrap;position:sticky;top:0;z-index:1}}
 .tbl td{{padding:5px 9px;border-top:1px solid #30363d;white-space:nowrap}}
 .tbl tr.win td.num{{color:#3fb950;font-weight:600}}
 .tbl tr.lose td.num{{color:#f85149;font-weight:600}}
 .tbl tbody tr{{cursor:pointer}}
 .tbl tbody tr:hover{{background:#1c2128}}
 .tbl tr.sel{{background:#173251}}
 .tbl tr.sel td:first-child{{box-shadow:inset 3px 0 0 #1f6feb}}
 .tbl tr.skip{{color:#6e7681}}
 .tbl td.diff{{color:#d29922}}
 /* 「基准」整列高亮：金琥珀色底 + 描边，一眼定位 */
 .tbl th.cbase{{background:#3b2f10;color:#f0c040;border-bottom:2px solid #d29922}}
 .tbl td.cbase{{background:rgba(210,153,34,.10);color:#f0c040;font-weight:700;
   border-left:1px solid rgba(210,153,34,.28);border-right:1px solid rgba(210,153,34,.28)}}
 .tbl tbody tr:hover td.cbase{{background:rgba(210,153,34,.20)}}
 .tbl tr.sel td.cbase{{background:rgba(210,153,34,.28)}}
 .tbl td.cbase.bad{{background:rgba(248,81,73,.20);color:#f85149}}
 a{{color:#58a6ff}}
 .scrolly{{max-height:480px;overflow:auto;border:1px solid #30363d;border-radius:8px}}
 .cwrap{{position:relative}}
 .tip{{position:absolute;display:none;pointer-events:none;background:rgba(22,27,34,.97);
   border:1px solid #30363d;border-radius:6px;padding:7px 10px;font-size:12px;z-index:10;
   box-shadow:0 4px 14px rgba(0,0,0,.55);min-width:158px}}
 .tip .tt{{color:#8b949e;margin-bottom:4px}}
 .tip .tr{{display:flex;align-items:center;gap:6px;color:#c9d1d9;line-height:1.75}}
 .tip .tr i{{width:9px;height:9px;border-radius:2px;display:inline-block}}
 .tip .tr b{{margin-left:auto;color:#e6edf3}}
 .tip .pct{{margin-left:6px;font-size:11px;font-weight:600}}
 .tip .pct.up{{color:#3fb950}} .tip .pct.down{{color:#f85149}}
 .note{{background:#161b22;border:1px solid #30363d;border-left:3px solid #d29922;border-radius:6px;
   padding:11px 14px;font-size:13px;color:#c9d1d9;margin:14px 0;line-height:1.9}}
</style></head><body><div class="wrap">
<h1>MU（美光）末日期权 — 双买 策略实验室</h1>
<div class="sub">
 <b>行权价 = round(买入时股价 / 5)×5 ± 偏移</b>（Call = 基准+偏移，Put = 基准−偏移）·
 <b>1 张 = {B0['mult']} 股</b> · 手续费 <b>${B0['fee']:.0f}/张/次</b> × 4 次（买 2 卖 2）= <b>${B0['fee']*4:.0f}/往返</b> ·
 数据源 Polygon · 零外部依赖 · <b>用「定价基准」下拉切换整套行情，用「买入时刻」下拉切开盘后何时成交</b><br>
 <b>所有金额已按「整组 = 1 Call + 1 Put（共 200 股）」换算并扣除手续费</b>；
 VIX 列显示的是「<b>买入那一刻</b>」的 VIX（09:30 / 09:33 取 09:25 那根 5 分钟柱的收盘价 = 09:30:00 的值，09:36 / 09:39 取 09:30 柱）；
 缺盘中数据的天数回退为 T-1 收盘并用 <b style="color:#d29922">*</b> 标出。<b>「仅下跌日」的判断与买入时刻对齐</b>：
 买入晚于 09:30 用【买入前 1 分钟价 &lt; 当日开盘价】，09:30 买入用【低开】（开盘 &lt; 前收）—— 两种都只用买入前已知的信息，<b>无未来函数</b>
</div>
{''.join(secs)}
<script>const DATA = {json.dumps({'blocks': blocks, 'months': months}, ensure_ascii=False)};</script>
<script>{_JS}</script>
</div></body></html>"""


# ────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offset", type=int, choices=OFFSETS, default=None)
    ap.add_argument("--anchor", choices=["all"] + ANCHOR_KEYS, default="all",
                    help="行权价锚点：all = 所有有数据的锚点并列（默认）；0930 / 0945 = 单一锚点")
    ap.add_argument("--no-html", action="store_true")
    args = ap.parse_args()

    global ANCHOR
    ANCHOR = "0930" if args.anchor == "all" else args.anchor
    tag = anchor_tag()
    out_xlsx = os.path.join(DATA_DIR, f"mu_analysis{tag}.xlsx")
    out_html = os.path.join(DATA_DIR, f"mu_report{tag}.html")
    offs = [args.offset] if args.offset else OFFSETS

    print(f"控制台/Excel 使用的锚点 = {ANCHOR_LAB[ANCHOR]}")
    if SAMPLE_START:
        print(f"⚠ 样本起始月份 = {SAMPLE_START}（{SAMPLE_START} 之前的到期日已丢弃；"
              f"改 SAMPLE_START = \"\" 可恢复全部数据）")
    print(f"交易口径：1 张 = {MULT} 股；每笔买 1 Call + 1 Put（共 2 张）；"
          f"手续费 ${FEE_PER_CONTRACT:.2f}/张/次 × 4 次 = ${FEE_PER_CONTRACT*4:.2f}/往返")
    print("金额单位：美元（已按整组 = 200 股换算、已扣手续费）")

    print("载入数据 ...")
    mu_1m = load_mu()
    vix = load_vix()
    vix5 = load_vix5()
    print(f"VIX 5 分钟序列：{len(vix5):,} 根（含 03:15 ET 起的盘前段，可取「开盘那一刻」的 VIX）")

    excel, blocks = {}, []
    for off in offs:
        days = build_days(off, mu_1m, vix, tag, vix5)
        if not days:
            print(f"±{off}: 无数据（请先拉取期权数据）")
            continue
        print(f"\n{'='*96}\n■ ±{off}   样本 {len(days)} 天 ({days[0]['date']} ~ {days[-1]['date']})\n{'='*96}")

        et, xt = entry_exit_tables(days)
        et, xt = _contract(et), _contract(xt)
        print("\n【入场时机】（成本越低越好）"); print(et.to_string(index=False))
        print("\n【固定时间出场 · 09:30 买入】"); print(xt.to_string(index=False))
        ts = _contract(tp_sl_table(days))
        print("\n【止盈/止损 Top5】"); print(ts.head(5).to_string(index=False))

        rows = []
        for r in SCENARIOS:
            tr, _ = evaluate(days, r)
            rows.append(stats(tr, r.name, len(days)))
        sc = _contract(pd.DataFrame(rows))
        print("\n【场景对比】"); print(sc.to_string(index=False))

        bk_vix = _contract(buckets(days, "vix_prev", [-np.inf, 14, 16, 18, 21, np.inf],
                                   ["<14", "14-16", "16-18", "18-21", ">21"]))
        bk_dow = _contract(group_stats(days, "weekday"))
        print("\n【VIX(T-1收盘) 分档】"); print(bk_vix.to_string(index=False) if not bk_vix.empty else " 无")
        print("\n【星期分档】"); print(bk_dow.to_string(index=False) if not bk_dow.empty else " 无")

        excel[f"±{off}_入场时机"] = et
        excel[f"±{off}_固定出场"] = xt
        excel[f"±{off}_止盈止损"] = ts
        excel[f"±{off}_场景对比"] = sc
        excel[f"±{off}_VIX分档"] = bk_vix
        excel[f"±{off}_星期分档"] = bk_dow

    # 报告：把每个「有数据的锚点」都做成独立区块，前端可分别实时切换
    if not args.no_html:
        want = ANCHOR_KEYS if args.anchor == "all" else [args.anchor]
        for a in want:
            if not os.path.exists(anchor_file(offs[0], a)):
                print(f"\n⚠ 锚点 {ANCHOR_LAB[a]} 无数据文件，跳过")
                continue
            panel, legs, skipped, bad_legs = build_panel(mu_1m, vix, tag_of(a), vix5)
            if bad_legs:
                print(f"\n⚠ [{a}] 以下腿型因数据未拉全，已整体剔除（以免拖累其他腿型样本）：")
                for nm, why in bad_legs:
                    print(f"    {nm} —— {why}")
            if skipped:
                print(f"\n⚠ [{a}] 面板跳过 {len(skipped)} 天：")
                for dt, why in skipped[:6]:
                    print(f"    {dt}  {why}")
            if not panel or not legs:
                print(f"\n⚠ 锚点 {ANCHOR_LAB[a]} 无可用面板，跳过")
                continue
            blocks.append(build_report_data(panel, legs, f"a{a}", a))
            print(f"报告区块 [{ANCHOR_LAB[a]}]：{len(panel)} 天 × {len(legs)} 种腿型 "
                  f"({', '.join(l[3] for l in legs)}) × {TAXIS_N} 分钟")
        if blocks:
            print(f"共 {len(blocks)} 个锚点区块，价格全分辨率内嵌（前端实时模拟与扫描）")

    if excel:
        with pd.ExcelWriter(out_xlsx, engine="openpyxl") as w:
            for k, df in excel.items():
                if df is not None and not df.empty:
                    df.to_excel(w, sheet_name=k[:31], index=False)
        print(f"\n[OK] 明细已保存: {os.path.abspath(out_xlsx)}")

    if blocks:
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(generate_html(blocks))
        print(f"[OK] 报告已生成: {os.path.abspath(out_html)}"
              f"  ({os.path.getsize(out_html)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
