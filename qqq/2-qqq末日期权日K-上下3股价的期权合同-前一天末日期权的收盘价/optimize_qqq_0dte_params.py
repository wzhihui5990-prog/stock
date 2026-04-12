# -*- coding: utf-8 -*-
"""
参数扫描：枚举 upper_pct / lower_pct / close_time 三轴，分别对 ±3 / ±4 数据做回测
输出：累计盈亏最高的 Top 20 组合
"""
import os
import itertools
import pandas as pd

# ────────────────────────────────────────────────
QQQ_FILE   = os.path.join(os.path.dirname(__file__), "..", "1-qqq日K", "data", "qqq_market_data.xlsx")
OPT_FILE_3 = os.path.join(os.path.dirname(__file__), "data", "qqq_0dte_options_offset3.xlsx")
OPT_FILE_4 = os.path.join(os.path.dirname(__file__), "..", "3-qqq末日期权日K-上下4股价的期权合同-前一天末日期权的收盘价", "data", "qqq_0dte_options_offset4.xlsx")
COMMISSION = 1.7
MONITOR_START = "09:30"
# ────────────────────────────────────────────────

# 扫描范围
UPPER_PCTS   = [round(x * 0.25, 2) for x in range(2, 21)]   # 0.5 ~ 5.0，步长 0.25
LOWER_PCTS   = [round(x * 0.25, 2) for x in range(2, 21)]
CLOSE_TIMES  = ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
                "13:00", "13:30", "14:00", "14:30", "15:00"]
STRIKE_FILES = [("±3", OPT_FILE_3), ("±4", OPT_FILE_4)]

TOP_N = 20


def load_data(opt_file):
    summary  = pd.read_excel(opt_file, sheet_name="摘要")
    call_1m  = pd.read_excel(opt_file, sheet_name="Call_1min")
    put_1m   = pd.read_excel(opt_file, sheet_name="Put_1min")
    qqq_1m   = pd.read_excel(QQQ_FILE, sheet_name="QQQ_分时1min")
    qqq_2m   = pd.read_excel(QQQ_FILE, sheet_name="QQQ_分时2min")
    qqq_5m   = pd.read_excel(QQQ_FILE, sheet_name="QQQ_5min")
    return summary, call_1m, put_1m, qqq_1m, qqq_2m, qqq_5m


def _get_qqq_day(t1, qqq_1m, qqq_2m, qqq_5m):
    for df in [qqq_1m, qqq_2m, qqq_5m]:
        day = df[df["时间"].astype(str).str.startswith(t1)].copy()
        if not day.empty:
            day["time_only"] = day["时间"].astype(str).str[-5:]
            return day
    return pd.DataFrame()


def build_daily_records(summary, call_1m, put_1m, qqq_1m, qqq_2m, qqq_5m):
    """预处理每个交易日的数据，只做一次 IO，后续参数扫描不再读文件"""
    records = []
    call_1m = call_1m.copy(); put_1m = put_1m.copy()
    call_1m["time_only"] = call_1m["时间(美东)"].astype(str).str[-5:]
    put_1m["time_only"]  = put_1m["时间(美东)"].astype(str).str[-5:]

    for _, row in summary.iterrows():
        t1 = str(row["到期日(T1)"])[:10]
        call_cost = row.get("Call_T2收盘")
        put_cost  = row.get("Put_T2收盘")
        if pd.isna(call_cost) or pd.isna(put_cost):
            continue
        call_cost = float(call_cost); put_cost = float(put_cost)
        total_cost = call_cost + put_cost

        qqq_day = _get_qqq_day(t1, qqq_1m, qqq_2m, qqq_5m)
        if qqq_day.empty:
            continue

        qqq_t2_close = float(row["QQQ_T2收盘"])

        # 只取 9:30 之后的数据
        qqq_monitor = qqq_day[qqq_day["time_only"] >= MONITOR_START][["time_only", "收盘价"]].values.tolist()

        c1m = call_1m[call_1m["到期日"].astype(str).str[:10] == t1][["time_only", "收盘价"]].values.tolist()
        p1m = put_1m[put_1m["到期日"].astype(str).str[:10]  == t1][["time_only", "收盘价"]].values.tolist()

        records.append({
            "t1": t1,
            "total_cost": total_cost,
            "qqq_t2_close": qqq_t2_close,
            "qqq": qqq_monitor,   # list of [time_only, close]
            "call": c1m,
            "put":  p1m,
        })
    return records


def backtest_params(records, upper_pct, lower_pct, close_time, commission):
    """用给定参数对预处理好的 records 做一次完整回测，返回累计盈亏（美元）"""
    commission_total = commission * 4 / 100
    total_pnl = 0.0

    for rec in records:
        t2 = rec["qqq_t2_close"]
        total_cost = rec["total_cost"]

        # 找触发时间
        trig_time = None
        for t, price in rec["qqq"]:
            if t > close_time:
                break
            pct = (price - t2) / t2 * 100
            if pct >= upper_pct:
                trig_time = t; break
            if pct <= -lower_pct:
                trig_time = t; break

        sell_time = trig_time or close_time

        # 获取期权价格（精确 or 最近一根）
        def get_price(arr, st):
            best = None
            for t, c in arr:
                if t <= st:
                    best = c
                else:
                    break
            return best or 0.0

        call_sell = get_price(rec["call"], sell_time)
        put_sell  = get_price(rec["put"],  sell_time)
        pnl = (call_sell + put_sell) - total_cost - commission_total
        total_pnl += pnl

    return round(total_pnl * 100, 2)  # 转换为美元


def main():
    print("加载数据...")
    results_all = []

    old_opt_file_4 = os.path.join(os.path.dirname(__file__), "..", "3-qqq末日期权日K-上下4股价的期权合同-前一天末日期权的收盘价", "data", "QQQ_0DTE_4.xlsx")
    strike_files = []
    for label, opt_file in STRIKE_FILES:
        if label == "±4" and (not os.path.exists(opt_file)) and os.path.exists(old_opt_file_4):
            print("提示：检测到旧命名 QQQ_0DTE_4.xlsx，临时使用旧文件。")
            strike_files.append((label, old_opt_file_4))
        else:
            strike_files.append((label, opt_file))

    for label, opt_file in strike_files:
        print(f"  处理 {label}...")
        summary, call_1m, put_1m, qqq_1m, qqq_2m, qqq_5m = load_data(opt_file)
        records = build_daily_records(summary, call_1m, put_1m, qqq_1m, qqq_2m, qqq_5m)
        n_days = len(records)
        print(f"    共 {n_days} 个交易日")

        combos = list(itertools.product(UPPER_PCTS, LOWER_PCTS, CLOSE_TIMES))
        total = len(combos)
        print(f"    扫描 {total} 组参数组合...")

        for i, (up, lo, ct) in enumerate(combos):
            if (i + 1) % 5000 == 0:
                print(f"      {i+1}/{total}...")
            pnl = backtest_params(records, up, lo, ct, COMMISSION)
            wins = 0
            # 计算胜率（可选，稍慢）
            results_all.append({
                "行权价": label,
                "上涨触发%": up,
                "下跌触发%": lo,
                "平仓时间": ct,
                "累计盈亏$": pnl,
                "交易天数": n_days,
                "日均盈亏$": round(pnl / n_days, 2) if n_days else 0,
            })

    df = pd.DataFrame(results_all)
    df_sorted = df.sort_values("累计盈亏$", ascending=False)

    print(f"\n{'='*70}")
    print(f"  参数扫描完成，共 {len(df)} 组")
    print(f"  Top {TOP_N} 最优参数组合（按累计盈亏降序）：")
    print(f"{'='*70}")
    top = df_sorted.head(TOP_N).reset_index(drop=True)
    top.index += 1
    print(top.to_string())

    print(f"\n{'='*70}")
    print("  [±3 专项 Top 10]")
    print(df_sorted[df_sorted["行权价"] == "±3"].head(10).reset_index(drop=True).to_string())
    print(f"\n  [±4 专项 Top 10]")
    print(df_sorted[df_sorted["行权价"] == "±4"].head(10).reset_index(drop=True).to_string())

    # 保存完整结果
    out = os.path.join(os.path.dirname(__file__), "data", "qqq_0dte_param_optimization.csv")
    df_sorted.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n完整结果已保存到: {out}")


if __name__ == "__main__":
    main()
