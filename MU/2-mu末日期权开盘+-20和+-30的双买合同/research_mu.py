# -*- coding: utf-8 -*-
"""MU 末日期权 · 研究与诊断合集（原来散成 10 个脚本，现在一个入口 + 子命令）

日常你只需要两个脚本：
  · update_mu_0dte_options_open_offset20_30.py   拉数据
  · analyze_mu_straddle.py                        出报告

这个文件是「研究/体检」用的，平时不用跑。用法：

  python research_mu.py                 # 列出全部子命令
  python research_mu.py all             # 跑最常用的一组（sweep / ideas / regime / payoff / vix）
  python research_mu.py sweep           # 全参数扫描 + 结构改进 + 分月
  python research_mu.py ideas           # 换思路：择时 / 卖波动率 / 价差 / 过滤
  python research_mu.py regime          # 按开盘跳空切换买卖方向
  python research_mu.py payoff          # 最高倍数分布 / 买卖网格 / 最惨日
  python research_mu.py monthly         # 候选分月体检 + 样本外检验
  python research_mu.py defaults        # 默认参数（平仓×止盈×止损）联合最优
  python research_mu.py vix             # VIX 覆盖体检 + 开盘 VIX 对比
  python research_mu.py comm            # 手续费是否真的扣了

加 --months 2026-09,2026-10 可临时换样本；加 --anchor 0945 换锚点。
"""
import os
import sys
import argparse
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(line_buffering=True, errors="replace")

import analyze_mu_straddle as A                      # noqa: E402

pd.set_option("display.unicode.east_asian_width", True)
_SHOW = lambda df, n=40: print(df.head(n).to_string(index=False)) if df is not None and len(df) else print("   （无）")
MONTHS_ALL = ["2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07",
              "2026-08", "2026-09", "2026-10"]


# ══════════════════════════════════════════════════════════
# 共用工具
# ══════════════════════════════════════════════════════════
def panel(anchor="0930", months=None):
    """返回 (days, legs)。days 上补齐 vo = 买入那一刻的 VIX（缺盘中数据则回退 T-1 收盘）。
    months = 只保留这些月份（None = 全部）。"""
    days, legs, skipped, bad = A.build_panel(
        A.load_mu(), A.load_vix(), A.tag_of(anchor), A.load_vix5())
    raw = "vix_open" if A.ANCHOR_TIME[anchor] == "09:30" else "vix_0945"
    for d in days:
        x = d.get(raw)
        d["vix_raw"] = x                       # 真正的盘中值（可能缺）
        if x is None or x != x:
            x = d.get("vix_prev")              # 回退
        d["vo"] = x
    if months:
        days = [d for d in days if d["date"][:7] in months]
    return days, legs


def idx(t):
    return A.TAXIS.index(t)


def comm(n_legs):
    return A.FEE_PER_CONTRACT * n_legs * 2 / A.MULT


def sim(d, i0, iE, pos, n_legs, tp=99.0, sl=99.0):
    """在 [i0,iE] 内先看止盈/止损（按目标价成交，与报告前端一致），否则 iE 平。
    pos = [(列名, w)]，w=+1 买 / -1 卖。卖方也支持。"""
    cols = [(d["df"][c].to_numpy(float), w) for c, w in pos]
    vin = sum(w * v[i0] for v, w in cols)
    if abs(vin) < 1e-9:
        return None
    long_, cc = vin > 0, comm(n_legs)
    for i in range(i0, iE + 1):
        r = sum(w * x[i] for x, w in cols) / vin
        if long_:
            if tp < 90 and r >= 1 + tp:
                return (vin * tp - cc) * A.MULT, i, 1
            if sl < 90 and r <= 1 - sl:
                return (-vin * sl - cc) * A.MULT, i, 2
        else:
            if tp < 90 and r <= 1 - tp:
                return (-vin * tp - cc) * A.MULT, i, 1
            if sl < 90 and r >= 1 + sl:
                return (vin * sl - cc) * A.MULT, i, 2
    return (sum(w * x[iE] for x, w in cols) - vin - cc) * A.MULT, iE, 0


def stats(pnls):
    a = np.asarray([x for x in pnls if x is not None], float)
    if a.size == 0:
        return dict(n=0, win=0.0, tot=0.0, avg=0.0, dd=0.0, ratio=0.0)
    c = np.cumsum(a)
    peak = np.maximum.accumulate(np.concatenate([[0.0], c]))
    dd = float(np.max(peak[1:] - c))
    return dict(n=int(a.size), win=float((a > 0).mean() * 100), tot=float(a.sum()),
                avg=float(a.mean()), dd=dd, ratio=float(a.sum() / max(dd, 1.0)))


def down_ok(d, entry):
    """入场前是否下跌（只用入场前已知信息）。"""
    m = d["df"]["mu"].to_numpy(float)
    i = idx(entry) - 1
    if i >= 0:
        return bool(m[i] < d["mu_open"])
    pc = d["prev_close"]
    return bool(pc == pc and d["mu_open"] < pc)


def exit_matrix(days, ca, cb, entry, tmax):
    """预计算 (日 × TP × SL) 出场矩阵，供大量参数扫描用。"""
    i0, iE = idx(entry), idx(tmax)
    if iE <= i0:
        return None
    tps, sls = np.asarray(A.TP_GRID, float), np.asarray(A.SL_GRID, float)
    out = []
    for d in days:
        df = d["df"]
        v = df[ca].to_numpy(float)[i0:iE + 1] + df[cb].to_numpy(float)[i0:iE + 1]
        cost = float(v[0])
        if not cost > 0:
            out.append(None)
            continue
        iTp, iSl = np.full(len(tps), -1, int), np.full(len(sls), -1, int)
        for k, t in enumerate(tps):
            if t < 90:
                w = np.flatnonzero(v >= cost * (1 + t))
                if w.size:
                    iTp[k] = i0 + int(w[0])
        for k, s in enumerate(sls):
            if s < 90:
                w = np.flatnonzero(v <= cost * (1 - s))
                if w.size:
                    iSl[k] = i0 + int(w[0])
        pnl = np.zeros((len(tps), len(sls)))
        code = np.zeros((len(tps), len(sls)), int)
        end = float(v[-1])
        for x in range(len(tps)):
            for y in range(len(sls)):
                it, isl = iTp[x], iSl[y]
                if it >= 0 and (isl < 0 or it <= isl):
                    pnl[x, y], code[x, y] = cost * tps[x] - A.COMM, 1
                elif isl >= 0:
                    pnl[x, y], code[x, y] = -cost * sls[y] - A.COMM, 2
                else:
                    pnl[x, y], code[x, y] = end - cost - A.COMM, 0
        out.append({"cost": cost, "M": pnl * A.MULT, "code": code})
    return out


def run_cfg(days, leg, entry, tmax, tp, sl, vo=None, dn=False, vof=None):
    """单配置回测：返回 (metrics, [(date, pnl, code)])。vo = 开盘VIX 门槛。"""
    m, rows = [], []
    for d in days:
        if vo is not None and not (d.get("vo") is not None and d.get("vo") == d.get("vo")
                                   and d["vo"] >= vo):
            continue
        if dn and not down_ok(d, entry):
            continue
        r = sim(d, idx(entry), idx(tmax), [(leg[1], +1), (leg[2], +1)], 2, tp, sl)
        if r is None:
            continue
        m.append(r[0])
        rows.append((d["date"], r[0], r[2]))
    return stats(m), rows


def monthly_cols(rows, months):
    out = {}
    for mm in months:
        q = [r[1] for r in rows if r[0][:7] == mm]
        out[mm] = round(sum(q)) if q else 0
    return out


def tp_s(v):
    return "不止盈" if v >= 90 else "+%d%%" % round(v * 100)


def sl_s(v):
    return "不止损" if v >= 90 else "-%d%%" % round(v * 100)


def head(title):
    print("\n" + "=" * 108 + "\n" + title + "\n" + "=" * 108)


# ══════════════════════════════════════════════════════════
# ① comm —— 手续费验证
# ══════════════════════════════════════════════════════════
def cmd_comm(days, legs, args):
    head("① 手续费验证（把 FEE_PER_CONTRACT 从 2.0 改成 0，看差额）")
    i0, iE = idx("09:30"), idx("11:00")
    STRAD = [("c20", 1), ("p20", 1)]
    keep = A.FEE_PER_CONTRACT

    def run(fee):
        A.FEE_PER_CONTRACT = fee
        return [sim(d, i0, iE, STRAD, 2, 1.75, 0.4) for d in days]

    r8, r0 = run(2.0), run(0.0)
    A.FEE_PER_CONTRACT = keep
    s8, s0 = stats([x[0] for x in r8]), stats([x[0] for x in r0])
    print(f"口径：1 张 = {A.MULT} 股 · $2/张/次 · 往返 4 张-次 = ${2*4/A.MULT*A.MULT:.2f}/笔")
    print(f"\n{'':22}{'笔数':>6}{'总盈亏$':>12}{'均笔$':>11}")
    print(f"{'含手续费':22}{s8['n']:>6}{s8['tot']:>12.2f}{s8['avg']:>11.2f}")
    print(f"{'手续费 = 0':22}{s0['n']:>6}{s0['tot']:>12.2f}{s0['avg']:>11.2f}")
    diff = s0["tot"] - s8["tot"]
    print(f"\n差额 = ${diff:,.2f}  预期 = {s8['n']} 笔 × $8 = ${s8['n']*8:,.2f}  "
          + ("✅ 每笔都扣了" if abs(diff - s8["n"] * 8) < 0.01 else "⚠ 对不上"))
    print("\n逐日抽检前 5 天：")
    name = ["到点平仓", "止盈", "止损"]
    for k, d_ in enumerate(days[:5]):
        print(f"  {d_['date']}  {name[r8[k][2]]:<6}  含费 {r8[k][0]:>9.2f}   不含费 {r0[k][0]:>9.2f}"
              f"   差 {r8[k][0]-r0[k][0]:>6.2f}")


# ══════════════════════════════════════════════════════════
# ② defaults —— 默认参数联合最优
# ══════════════════════════════════════════════════════════
def cmd_defaults(days, legs, args):
    head("② 默认参数（平仓时刻 × 止盈 × 止损）联合最优 + 分月体检")
    lg = legs[0]
    mm = sorted({d["date"][:7] for d in days})
    rows = []
    for tm in A.TMAX_GRID:
        for tp in A.TP_GRID:
            for sl in A.SL_GRID:
                s, rr = run_cfg(days, lg, "09:30", tm, tp, sl)
                if s["n"] < 10:
                    continue
                row = {"平仓": tm, "止盈": tp_s(tp), "止损": sl_s(sl),
                       "笔数": s["n"], "胜率%": round(s["win"], 1), "总盈亏$": round(s["tot"]),
                       "均笔$": round(s["avg"]), "回撤$": round(s["dd"]), "比值": round(s["ratio"], 2)}
                row.update(monthly_cols(rr, mm))
                row["正收益月"] = sum(1 for k in mm if row[k] > 0)
                rows.append(row)
    df = pd.DataFrame(rows)
    cols = list(df.columns)
    print("\n【总盈亏 Top 10】")
    _SHOW(df.sort_values("总盈亏$", ascending=False)[cols], 10)
    print("\n【比值 Top 10】")
    _SHOW(df.sort_values("比值", ascending=False)[cols], 10)
    print("\n【所有月份都为正 且 总盈亏 Top 10】")
    pos = df[df["正收益月"] == len(mm)]
    _SHOW(pos.sort_values("总盈亏$", ascending=False)[cols] if len(pos) else None, 10)


# ══════════════════════════════════════════════════════════
# ③ payoff —— 倍率分布 / 买卖网格 / 最惨日
# ══════════════════════════════════════════════════════════
def cmd_payoff(days, legs, args):
    head("③ 买方赔率分布 / 买卖网格 / 尾部风险")
    i0, iE1, iE2 = idx("09:30"), idx("11:00"), idx("15:55")
    print("\n【① 当日「组合最高倍数」分布（相对 09:30 入场成本）—— 决定止盈该设多少】")
    for x in [1.00, 1.10, 1.20, 1.30, 1.50, 1.75, 2.00, 2.50, 3.00, 4.00]:
        hit = 0
        for d in days:
            t = d["df"]["total"].to_numpy(float)
            if t[i0] > 0 and float(t[i0:].max()) / t[i0] >= x:
                hit += 1
        print(f"   最高倍数 ≥ {x:.2f}×  →  {100*hit/len(days):5.1f}%   ({hit}/{len(days)} 天)")
    print("   止盈 +X% 需要倍数 ≥ (1+X)。+200% 要 3.00×、+175% 要 2.75×。")

    print("\n【② 买方网格：平仓时刻 × 止盈（止损 −40%）】")
    rows = []
    for tm in A.TMAX_GRID:
        for tp in A.TP_GRID:
            s, _ = run_cfg(days, legs[0], "09:30", tm, tp, 0.4)
            if not s["n"]:
                continue
            rows.append({"平仓": tm, "止盈": tp_s(tp), "笔数": s["n"], "胜率%": round(s["win"], 1),
                         "总盈亏$": round(s["tot"]), "回撤$": round(s["dd"]),
                         "比值": round(s["ratio"], 2)})
    _SHOW(pd.DataFrame(rows).sort_values("总盈亏$", ascending=False), 10)

    print("\n【③ 卖方（短跨 = 卖 c20+p20）网格：平仓时刻 × 止盈】")
    rows = []
    for tm in A.TMAX_GRID:
        for tp in [0.3, 0.5, 0.7, 0.9, 99.0]:
            p = [sim(d, i0, idx(tm), [("c20", -1), ("p20", -1)], 2, tp, 99)[0] for d in days]
            s = stats(p)
            rows.append({"平仓": tm, "止盈": "不止盈" if tp >= 90 else f"赚回{tp*100:.0f}%平",
                         "笔数": s["n"], "胜率%": round(s["win"], 1), "总盈亏$": round(s["tot"]),
                         "回撤$": round(s["dd"]), "比值": round(s["ratio"], 2),
                         "最差单笔$": round(min(p))})
    _SHOW(pd.DataFrame(rows).sort_values("总盈亏$", ascending=False), 8)

    print("\n【④ 最惨的 6 天】（短跨持到收盘 vs 双买持到收盘）")
    rows = []
    for d in sorted(days, key=lambda x: sim(x, i0, iE2, [("c20", -1), ("p20", -1)], 2)[0])[:6]:
        rows.append({"日期": d["date"], "星期": d["weekday"],
                     "开盘→收盘%": round((d["mu_close"] / d["mu_open"] - 1) * 100, 2),
                     "日内振幅%": round(d["mu_range"], 2),
                     "短跨$": round(sim(d, i0, iE2, [("c20", -1), ("p20", -1)], 2)[0]),
                     "双买$": round(sim(d, i0, iE2, [("c20", 1), ("p20", 1)], 2)[0])})
    _SHOW(pd.DataFrame(rows), 6)


# ══════════════════════════════════════════════════════════
# ④ monthly —— 候选分月 + 样本外
# ══════════════════════════════════════════════════════════
CANDS = [
    ("09:30 · ±20 · 11:00 · 175/40（报告默认）", "sym20", "11:00", 1.75, 0.4, None, False),
    ("09:30 · ±20 · 11:00 · 不止盈/40", "sym20", "11:00", 99.0, 0.4, None, False),
    ("09:30 · ±20 · 13:00 · 200/40", "sym20", "13:00", 2.0, 0.4, None, False),
    ("09:30 · ±20 · 持到收盘 · 200/40", "sym20", "15:55", 2.0, 0.4, None, False),
    ("09:30 · 远C+近P · 13:00 · 200/40", "c30p20", "13:00", 2.0, 0.4, None, False),
    ("09:30 · 远C+近P · 13:00 · 200/50", "c30p20", "13:00", 2.0, 0.5, None, False),
    ("09:30 · ±20 · 11:00 · 175/40 · 仅跌", "sym20", "11:00", 1.75, 0.4, None, True),
    ("09:30 · ±20 · 11:00 · 175/40 · 开盘VIX≥15", "sym20", "11:00", 1.75, 0.4, 15.0, False),
    ("09:45 · ±20 · 11:00 · 175/40", "sym20", "11:00", 1.75, 0.4, None, False),
]


def cmd_monthly(days, legs, args):
    head("④ 候选配置分月体检（正收益月越多、最差月越高越稳）")
    lgm = {l[0]: l for l in legs}
    mm = sorted({d["date"][:7] for d in days})
    rows = []
    for name, key, tm, tp, sl, vo, dn in CANDS:
        lg = lgm.get(key)
        if lg is None:
            continue
        s, rr = run_cfg(days, lg, "09:30", tm, tp, sl, vo, dn)
        row = {"配置": name, "笔数": s["n"], "胜率%": round(s["win"], 1),
               "总盈亏$": round(s["tot"]), "回撤$": round(s["dd"]), "比值": round(s["ratio"], 2)}
        row.update(monthly_cols(rr, mm))
        row["正收益月"] = sum(1 for k in mm if row[k] > 0)
        row["最差月"] = min(row[k] for k in mm)
        rows.append(row)
    _SHOW(pd.DataFrame(rows).sort_values("比值", ascending=False), 20)

    # 样本外
    if len(mm) >= 3:
        tr, te = mm[:2], mm[2:]
        print(f"\n【样本外检验】用 {tr[0]}~{tr[1]} 挑「比值最大」的参数，再看 {'/'.join(te)}")
        out = []
        for key in ("sym20", "c30p20"):
            lg = lgm.get(key)
            if lg is None:
                continue
            best = None
            for tm in A.TMAX_GRID:
                EM = exit_matrix(days, lg[1], lg[2], "09:30", tm)
                if EM is None:
                    continue
                for ai, tp in enumerate(A.TP_GRID):
                    for bi, sl in enumerate(A.SL_GRID):
                        for vo in [None, 15.0, 15.5, 16.0]:
                            for dn in (False, True):
                                rr = []
                                for k, d in enumerate(days):
                                    e = EM[k]
                                    if not e:
                                        continue
                                    if vo is not None and not (d.get("vo") == d.get("vo")
                                                               and d.get("vo") is not None
                                                               and d["vo"] >= vo):
                                        continue
                                    if dn and not down_ok(d, "09:30"):
                                        continue
                                    rr.append((d["date"], float(e["M"][ai, bi])))
                                a = [x[1] for x in rr if x[0][:7] in tr]
                                if len(a) < 8:
                                    continue
                                mtr = stats(a)
                                if best is None or mtr["ratio"] > best[0]:
                                    best = (mtr["ratio"], tm, tp, sl, vo, dn, rr, mtr)
            if not best:
                continue
            _, tm, tp, sl, vo, dn, rr, mtr = best
            ate = stats([x[1] for x in rr if x[0][:7] in te])
            out.append({"腿型": lg[3], "平仓": tm, "止盈": tp_s(tp), "止损": sl_s(sl),
                        "过滤": ("开盘VIX≥%s" % vo if vo else "不过滤") + ("/仅跌" if dn else ""),
                        "训练盈亏$": round(mtr["tot"]), "训练比值": round(mtr["ratio"], 2),
                        "测试笔数": ate["n"], "测试盈亏$": round(ate["tot"]),
                        "测试比值": round(ate["ratio"], 2)})
        _SHOW(pd.DataFrame(out), 5)


# ══════════════════════════════════════════════════════════
# ⑤ vix —— 覆盖体检 + 开盘 VIX 对比
# ══════════════════════════════════════════════════════════
def cmd_vix(days, legs, args):
    head("⑤ VIX 数据覆盖体检 + 「开盘那一刻的 VIX」对比")
    m = pd.read_excel(A.VIX_FILE, sheet_name="VIX_5min")
    m["d"] = m["时间"].astype(str).str[:10]
    day = pd.read_excel(A.VIX_FILE, sheet_name="VIX_日K")
    day["d"] = day["日期"].astype(str).str[:10]
    cnt = m.groupby("d").size()
    print(f"日K ：{day['d'].min()} ~ {day['d'].max()}  {len(day)} 天")
    print(f"5min：{m['d'].min()} ~ {m['d'].max()}  {len(cnt)} 天 / {len(m):,} 根")
    print("\n【5min 按月覆盖】")
    for k in sorted(cnt.groupby(cnt.index.str[:7]).groups):
        s = cnt.groupby(cnt.index.str[:7]).get_group(k)
        print(f"  {k}: {len(s):>3} 天  每天 {s.min()}~{s.max()} 根"
              + ("   完整" if s.min() >= 150 else "   ← 不完整"))
    miss = sorted(set(day["d"]) - set(cnt.index))
    print(f"\n日K 有、5min 缺：{len(miss)} 天")
    for k in sorted({d[:7] for d in miss}):
        q = [d for d in miss if d[:7] == k]
        print(f"  {k}: {len(q):>2} 天  {' '.join(x[8:] for x in q)}")

    nf = [d["date"] for d in days if not (d.get("vix_raw") == d.get("vix_raw"))]
    print(f"\n当前样本 {len(days)} 天里没有盘中 VIX 的：{len(nf)} 天  {nf}")
    print("  （这些天在报告/本脚本里已自动回退为 T-1 收盘）")

    print("\n【开盘 VIX vs T-1 收盘 VIX】（用共同样本，避免缺数据干扰）")
    common = [d for d in days if d.get("vix_raw") == d.get("vix_raw")]
    if not common:
        return
    print(f"  （共同样本 {len(common)} 天，配置 = 09:30 双买 · 11:00 · +175%/−40%）")
    print(f"{'门槛':>8}{'T-1_笔':>8}{'T-1_胜率':>9}{'T-1_盈亏$':>11}"
          f"{'开盘_笔':>9}{'开盘_胜率':>10}{'开盘_盈亏$':>12}{'开盘_回撤$':>11}")
    for x in [14.5, 15.0, 15.5, 16.0, 17.0]:
        a = stats([sim(d, idx("09:30"), idx("11:00"), [("c20", 1), ("p20", 1)], 2, 1.75, 0.4)[0]
                   for d in common if d["vix_prev"] == d["vix_prev"] and d["vix_prev"] >= x])
        b = stats([sim(d, idx("09:30"), idx("11:00"), [("c20", 1), ("p20", 1)], 2, 1.75, 0.4)[0]
                   for d in common if d["vix_raw"] >= x])
        print(f"  ≥{x:<6}{a['n']:>8}{a['win']:>9.1f}{round(a['tot']):>11}"
              f"{b['n']:>9}{b['win']:>10.1f}{round(b['tot']):>12}{round(b['dd']):>11}")


# ══════════════════════════════════════════════════════════
# ⑥ ideas —— 换思路找路
# ══════════════════════════════════════════════════════════
def or_levels(d, wend):
    v = d["df"]["mu"].to_numpy(float)
    seg = v[idx("09:30"):idx(wend) + 1]
    return float(seg.max()), float(seg.min())


def or_break(d, wend, scan_end="11:30"):
    hi, lo = or_levels(d, wend)
    v = d["df"]["mu"].to_numpy(float)
    for k in range(idx(wend) + 1, idx(scan_end) + 1):
        if v[k] > hi:
            return k, "up"
        if v[k] < lo:
            return k, "down"
    return None


def cmd_ideas(days, legs, args):
    head("⑥ 换思路：择时入场 / 卖波动率 / 价差 / 过滤")
    i0930, i0945, i1000, i1100 = (idx(x) for x in ("09:30", "09:45", "10:00", "11:00"))
    i1300, i1555 = idx("13:00"), idx("15:55")
    STRAD = [("c20", 1), ("p20", 1)]
    SHORT = [("c20", -1), ("p20", -1)]
    CONDOR = [("c20", -1), ("p20", -1), ("c30", 1), ("p30", 1)]

    print("\n【A · 钱是怎么亏的】")
    rows = []
    for tm, i in [("09:30", i0930), ("09:45", i0945), ("10:00", i1000), ("11:00", i1100)]:
        cs = [float(d["df"]["c20"].iloc[i] + d["df"]["p20"].iloc[i]) for d in days]
        rows.append({"入场": tm, "平均成本$": round(np.mean(cs) * A.MULT),
                     "成本/股价%": round(np.mean([c / d["mu_open"] * 100 for c, d in zip(cs, days)]), 2)})
    _SHOW(pd.DataFrame(rows), 4)
    for name, (a, b) in {"09:30→11:00": (i0930, i1100), "11:00→13:00": (i1100, i1300),
                         "13:00→15:55": (i1300, i1555)}.items():
        ch = [d["df"]["total"].to_numpy(float)[b] / d["df"]["total"].to_numpy(float)[a] - 1
              for d in days if d["df"]["total"].to_numpy(float)[a] > 0]
        print(f"   {name}: 均值 {np.mean(ch)*100:+.1f}%  中位 {np.median(ch)*100:+.1f}%")

    res = []

    def add(tag, pnls, note=""):
        s = stats(pnls)
        res.append({"方案": tag, "笔数": s["n"], "胜率%": round(s["win"], 1),
                    "总盈亏$": round(s["tot"]), "均笔$": round(s["avg"]),
                    "回撤$": round(s["dd"]), "比值": round(s["ratio"], 2), "备注": note})

    add("基准：09:30 双买 · 13:00 · 200/40",
        [sim(d, i0930, i1300, STRAD, 2, 2.0, 0.4)[0] for d in days])
    add("报告默认：09:30 双买 · 11:00 · 175/40",
        [sim(d, i0930, i1100, STRAD, 2, 1.75, 0.4)[0] for d in days])
    for wend in ("09:45", "10:00"):
        p = []
        for d in days:
            b = or_break(d, wend)
            p.append(sim(d, b[0], i1300, STRAD, 2, 2.0, 0.4)[0] if b else None)
        add(f"OR{wend} 突破再双买 · 13:00", p, "突破时 IV 已放大")
        p2 = []
        for d in days:
            b = or_break(d, wend)
            if b:
                pos = [("c20", 1)] if b[1] == "up" else [("p20", 1)]
                p2.append(sim(d, b[0], i1300, pos, 1, 2.0, 0.4)[0])
            else:
                p2.append(None)
        add(f"OR{wend} 突破 → 顺势单腿 · 13:00", p2)
    for nm, pos, nl in [("短跨", SHORT, 2), ("铁鹰", CONDOR, 4)]:
        add(f"{nm} · 09:30→收盘 · 不止盈损", [sim(d, i0930, i1555, pos, nl)[0] for d in days])
        add(f"{nm} · 09:30→13:00 · TP60/SL100",
            [sim(d, i0930, i1300, pos, nl, 0.6, 1.0)[0] for d in days])
    add("Call 垂直（买c20卖c30）· 09:30 · 13:00",
        [sim(d, i0930, i1300, [("c20", 1), ("c30", -1)], 2)[0] for d in days])
    add("只买 c20 · 09:30 · 13:00 · 200/40",
        [sim(d, i0930, i1300, [("c20", 1)], 1, 2.0, 0.4)[0] for d in days])
    add("只买 p20 · 09:30 · 13:00 · 200/40",
        [sim(d, i0930, i1300, [("p20", 1)], 1, 2.0, 0.4)[0] for d in days])
    p = []
    for d in days:
        b = or_break(d, "09:45")
        if b:
            pos = [("c20", 1), ("c30", -1)] if b[1] == "up" else [("p20", 1), ("p30", -1)]
            p.append(sim(d, b[0], i1300, pos, 2)[0])
        else:
            p.append(None)
    add("OR09:45 突破 → 顺势垂直价差 · 13:00", p)

    # 跳空 / 开盘区间过滤
    gaps, ors = {}, {}
    for d in days:
        pc = d["prev_close"]
        gaps[d["date"]] = abs(d["mu_open"] - pc) / pc * 100 if pc == pc else np.nan
        hi, lo = or_levels(d, "10:00")
        ors[d["date"]] = (hi - lo) / d["mu_open"] * 100
    for g in (1.0, 1.5):
        add(f"09:30 双买 · 13:00 · 200/40 · |跳空| ≥ {g}%",
            [sim(d, i0930, i1300, STRAD, 2, 2.0, 0.4)[0]
             if (gaps[d["date"]] == gaps[d["date"]] and gaps[d["date"]] >= g) else None for d in days])
    for g in (0.5, 1.0):
        add(f"短跨 · 09:30→收盘 · |跳空| < {g}%",
            [sim(d, i0930, i1555, SHORT, 2)[0]
             if (gaps[d["date"]] == gaps[d["date"]] and gaps[d["date"]] < g) else None for d in days])
    for o in (1.0, 2.0):
        add(f"09:30 双买 · 13:00 · 200/40 · OR30 ≥ {o}%",
            [sim(d, i0930, i1300, STRAD, 2, 2.0, 0.4)[0] if ors[d["date"]] >= o else None for d in days])

    print("\n【B · 全部方案排名（按总盈亏）】")
    _SHOW(pd.DataFrame(res).sort_values("总盈亏$", ascending=False), 30)
    print("\n【C · 按比值 Top 10】")
    _SHOW(pd.DataFrame(res).sort_values("比值", ascending=False), 10)


# ══════════════════════════════════════════════════════════
# ⑦ regime —— 按跳空切换买卖
# ══════════════════════════════════════════════════════════
def cmd_regime(days, legs, args):
    head("⑦ 按开盘跳空 |gap| 切换买卖方向（多头=双买13:00平；空头=短跨持到收盘）")
    i0, i1300, i1555 = idx("09:30"), idx("13:00"), idx("15:55")
    for d in days:
        pc = d["prev_close"]
        d["gap_abs"] = abs(d["mu_open"] - pc) / pc * 100 if pc == pc else np.nan
        d["L"] = sim(d, i0, i1300, [("c20", 1), ("p20", 1)], 2, 2.0, 0.4)[0]
        d["S"] = sim(d, i0, i1555, [("c20", -1), ("p20", -1)], 2)[0]
    rows = []
    for thr in np.arange(0.2, 2.05, 0.1):
        dl = [d for d in days if d["gap_abs"] >= thr]
        ds = [d for d in days if d["gap_abs"] < thr]
        L, S = stats([d["L"] for d in dl]), stats([d["S"] for d in ds])
        C = stats([d["L"] for d in dl] + [d["S"] for d in ds])
        rows.append({"阈值%": round(float(thr), 1), "多_笔": L["n"], "多_盈亏$": round(L["tot"]),
                     "空_笔": S["n"], "空_盈亏$": round(S["tot"]),
                     "合并盈亏$": round(C["tot"]), "合并回撤$": round(C["dd"]),
                     "合并比值": round(C["ratio"], 2)})
    _SHOW(pd.DataFrame(rows), 25)
    g_rows = rows

    print("\n【对照：同样思路改用 T-1 收盘 VIX 做分界】")
    rows = []
    for thr in np.arange(14.0, 18.6, 0.5):
        dl = [d for d in days if d["vix_prev"] == d["vix_prev"] and d["vix_prev"] >= thr]
        ds = [d for d in days if not (d["vix_prev"] == d["vix_prev"] and d["vix_prev"] >= thr)]
        if not dl or not ds:
            continue
        C = stats([d["L"] for d in dl] + [d["S"] for d in ds])
        rows.append({"VIX门槛": round(float(thr), 1), "多_笔": len(dl),
                     "多_盈亏$": round(sum(d["L"] for d in dl)), "空_笔": len(ds),
                     "空_盈亏$": round(sum(d["S"] for d in ds)),
                     "合并盈亏$": round(C["tot"]), "合并回撤$": round(C["dd"]),
                     "合并比值": round(C["ratio"], 2)})
    _SHOW(pd.DataFrame(rows), 12)

    best = max(g_rows, key=lambda r: r["合并盈亏$"]) if g_rows else None
    print("\n【空头尾部风险与加止损效果（阈值 2.0% 的空头子集）】")
    ds = [d for d in days if d["gap_abs"] < 2.0]
    rows = []
    for sl in (0.5, 1.0, 2.0, 3.0, 99.0):
        p = [sim(d, i0, i1555, [("c20", -1), ("p20", -1)], 2, 99, sl)[0] for d in ds]
        s = stats(p)
        rows.append({"止损(倍权利金)": "不止损" if sl >= 90 else f"{sl:.1f}×", "笔数": s["n"],
                     "胜率%": round(s["win"], 1), "总盈亏$": round(s["tot"]),
                     "回撤$": round(s["dd"]), "最差单笔$": round(min(p))})
    _SHOW(pd.DataFrame(rows), 6)
    if best:
        print(f"\n（提示：阈值 {best['阈值%']}% 是这批数据里挑出来的，不等于可外推。）")


# ══════════════════════════════════════════════════════════
# ⑧ sweep —— 全参数扫描 + 结构改进 + 分月
# ══════════════════════════════════════════════════════════
def cmd_sweep(days, legs, args):
    head("⑧ 全参数扫描：4 腿型 × 平仓截止 × 止盈 × 止损 × 开盘VIX × 仅下跌日")
    mm = sorted({d["date"][:7] for d in days})
    MIN_N = args.min_n

    print("\n【① 变体对照】行权价锚点 vs 买入时刻（对称±20 · 13:00 · 200/40）")
    print("   A/B = 用 09:30 档的行权价 + 09:30 或 09:45 买入；C = 09:45 档（行权价按 09:45 股价）")
    rows = []
    for name, anch, entry in [("A 09:30锚点 · 09:30买", "0930", "09:30"),
                              ("B 09:30锚点 · 09:45买（旧默认，已取消）", "0930", "09:45"),
                              ("C 09:45锚点 · 09:45买", "0945", "09:45")]:
        dd, ll = panel(anch, set(mm))
        if not dd:
            continue
        s, _ = run_cfg(dd, ll[0], entry, "13:00", 2.0, 0.4)
        rows.append({"变体": name, "笔数": s["n"], "胜率%": round(s["win"], 1),
                     "总盈亏$": round(s["tot"]), "回撤$": round(s["dd"]), "比值": round(s["ratio"], 2)})
    _SHOW(pd.DataFrame(rows), 5)

    print("\n【② 四优化目标最优解（每个都带分月）】")
    res = []
    for lg in legs:
        for tm in A.TMAX_GRID:
            EM = exit_matrix(days, lg[1], lg[2], "09:30", tm)
            if EM is None:
                continue
            for ai, tp in enumerate(A.TP_GRID):
                for bi, sl in enumerate(A.SL_GRID):
                    for vo in [None, 15.0, 15.5, 16.0]:
                        for dn in (False, True):
                            p, rr = [], []
                            for k, d in enumerate(days):
                                e = EM[k]
                                if not e:
                                    continue
                                if vo is not None and not (d.get("vo") == d.get("vo")
                                                           and d.get("vo") is not None
                                                           and d["vo"] >= vo):
                                    continue
                                if dn and not down_ok(d, "09:30"):
                                    continue
                                p.append(float(e["M"][ai, bi]))
                                rr.append((d["date"], float(e["M"][ai, bi])))
                            if len(p) < MIN_N:
                                continue
                            s = stats(p)
                            res.append({"腿型": lg[3], "平仓": tm, "止盈": tp_s(tp), "止损": sl_s(sl),
                                        "过滤": ("开盘VIX≥%s" % vo if vo else "不过滤")
                                                + ("/仅跌" if dn else ""), **s, "rr": rr})
    if not res:
        print("   有效组合为 0（样本太少或门槛太高）")
        return
    df = pd.DataFrame([{k: v for k, v in r.items() if k != "rr"} for r in res])
    for obj, col in [("总盈亏最大", "tot"), ("平均每笔最大", "avg"),
                     ("盈亏/回撤比最大", "ratio"), ("胜率最高", "win")]:
        idx_best = df[col].idxmax()
        r = res[idx_best]
        row = {"目标": obj, "腿型": r["腿型"], "平仓": r["平仓"], "止盈": r["止盈"],
               "止损": r["止损"], "过滤": r["过滤"], "笔数": r["n"],
               "胜率%": round(r["win"], 1), "总盈亏$": round(r["tot"]),
               "回撤$": round(r["dd"]), "比值": round(r["ratio"], 2)}
        row.update(monthly_cols(r["rr"], mm))
        row["正收益月"] = sum(1 for k in mm if row[k] > 0)
        print()
        _SHOW(pd.DataFrame([row]), 1)

    print(f"\n【③ 结构改进逐项（腿型固定 {legs[0][3]}）】")
    lg = legs[0]
    print("\n  时间止损阶梯（+200%/−40%/不过滤）")
    rows = []
    for tm in A.TMAX_GRID:
        s, _ = run_cfg(days, lg, "09:30", tm, 2.0, 0.4)
        rows.append({"平仓": tm, "笔数": s["n"], "胜率%": round(s["win"], 1),
                     "总盈亏$": round(s["tot"]), "回撤$": round(s["dd"]), "比值": round(s["ratio"], 2)})
    _SHOW(pd.DataFrame(rows), 8)
    print("\n  止损阶梯（11:00 平 / +175%）")
    rows = []
    for sl in A.SL_GRID:
        s, _ = run_cfg(days, lg, "09:30", "11:00", 1.75, sl)
        rows.append({"止损": sl_s(sl), "笔数": s["n"], "胜率%": round(s["win"], 1),
                     "总盈亏$": round(s["tot"]), "回撤$": round(s["dd"]), "比值": round(s["ratio"], 2)})
    _SHOW(pd.DataFrame(rows), 8)
    print("\n  腿型对比（11:00 平 / +175%/−40%）")
    rows = []
    for l in legs:
        s, _ = run_cfg(days, l, "09:30", "11:00", 1.75, 0.4)
        rows.append({"腿型": l[3], "笔数": s["n"], "胜率%": round(s["win"], 1),
                     "总盈亏$": round(s["tot"]), "回撤$": round(s["dd"]), "比值": round(s["ratio"], 2)})
    _SHOW(pd.DataFrame(rows), 6)
    print("\n  仅下跌日 / 开盘VIX 过滤（11:00 平 / +175%/−40%）")
    rows = []
    for name, dn, vo in [("不过滤", False, None), ("仅下跌日", True, None),
                         ("开盘VIX≥15", False, 15.0), ("开盘VIX≥15.5", False, 15.5),
                         ("开盘VIX≥16", False, 16.0), ("仅跌 + 开盘VIX≥15", True, 15.0)]:
        s, _ = run_cfg(days, lg, "09:30", "11:00", 1.75, 0.4, vo, dn)
        rows.append({"过滤": name, "笔数": s["n"], "胜率%": round(s["win"], 1),
                     "总盈亏$": round(s["tot"]), "回撤$": round(s["dd"]), "比值": round(s["ratio"], 2)})
    _SHOW(pd.DataFrame(rows), 8)


# ══════════════════════════════════════════════════════════
CU = {
    "comm": cmd_comm, "defaults": cmd_defaults, "payoff": cmd_payoff, "monthly": cmd_monthly,
    "vix": cmd_vix, "ideas": cmd_ideas, "regime": cmd_regime, "sweep": cmd_sweep,
}
QUICK = ["sweep", "ideas", "regime", "payoff", "vix"]


def main():
    ap = argparse.ArgumentParser(description="MU 末日期权 · 研究与诊断合集",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="子命令：" + " / ".join(list(CU) + ["all"]))
    ap.add_argument("cmd", nargs="?", default="", help="子命令（留空列出全部）")
    ap.add_argument("--anchor", choices=["0930", "0945"], default="0930")
    ap.add_argument("--months", default="", help="只保留这些月份，如 2026-09,2026-10")
    ap.add_argument("--min-n", type=int, default=12, help="样本门槛（默认 12）")
    args = ap.parse_args()

    if not args.cmd:
        print(__doc__)
        return
    if args.cmd == "all":
        todo = QUICK
    elif args.cmd in CU:
        todo = [args.cmd]
    else:
        print(f"未知子命令：{args.cmd}\n可选：{', '.join(list(CU) + ['all'])}")
        return

    months = {m.strip() for m in args.months.split(",") if m.strip()} or None
    days, legs = panel(args.anchor, months)
    print(f"样本：{days[0]['date']} ~ {days[-1]['date']}（{len(days)} 天）"
          f" · 锚点 {A.ANCHOR_LAB[args.anchor]} · 腿型 {len(legs)} 种"
          + (f" · 月份过滤 {'/'.join(sorted(months))}" if months else ""))
    for c in todo:
        CU[c](days, legs, args)
    print("\n完成。")


if __name__ == "__main__":
    main()
