# -*- coding: utf-8 -*-
"""
VIX 恐慌指数数据拉取脚本（增量追加模式）
- 日 K 线：开盘价、收盘价、最高价、最低价
- 5分钟 K 线
数据来源：Yahoo Finance（^VIX）
结果写入 Excel 文件（data/vix_data.xlsx）
"""

import os
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

# ────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────
SYMBOL     = "^VIX"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "vix_data.xlsx")

END_DATE   = datetime.today()
START_DATE = datetime(2026, 2, 13)   # 与 QQQ 数据同步起始日

CHUNK_DAYS = 7

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ────────────────────────────────────────────────
# 下载
# ────────────────────────────────────────────────
def _fetch_one(interval, start, end):
    df = yf.download(
        SYMBOL,
        start=start.strftime("%Y-%m-%d"),
        end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
        interval=interval,
        auto_adjust=True,
        progress=False,
    )
    return df


def download_chunked(interval, start, end, chunk_days):
    frames = []
    cur = start
    while cur < end:
        seg_end = min(cur + timedelta(days=chunk_days), end)
        print(f"  下载 VIX [{interval}] {cur.date()} ~ {seg_end.date()} ...")
        df = _fetch_one(interval, cur, seg_end)
        if not df.empty:
            frames.append(df)
        cur = seg_end + timedelta(days=1)
    if not frames:
        print(f"  ⚠  VIX [{interval}] 无数据返回")
        return pd.DataFrame()
    return pd.concat(frames)


# ────────────────────────────────────────────────
# 格式化
# ────────────────────────────────────────────────
def format_df(df, date_only=False):
    if df.empty:
        return df
    df = df.copy()

    # 先转换时区（在 reset_index 之前，保留 DatetimeIndex 的 tz 信息）
    if hasattr(df.index, 'tz') and df.index.tz is not None:
        df.index = df.index.tz_convert("America/New_York")

    df.index.name = "Datetime"
    df = df.reset_index()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]

    # 清理列名中可能残留的 ticker 后缀
    df.columns = [c.replace(f"_{SYMBOL}", "").replace("_^VIX", "").strip() for c in df.columns]

    keep = ["Datetime", "Open", "High", "Low", "Close", "Volume"]
    existing = [c for c in keep if c in df.columns]
    df = df[existing].copy()

    df = df.drop_duplicates(subset=["Datetime"]).sort_values("Datetime").reset_index(drop=True)

    fmt = "%Y-%m-%d" if date_only else "%Y-%m-%d %H:%M"
    df["Datetime"] = pd.to_datetime(df["Datetime"]).dt.strftime(fmt)

    for col in ["Open", "High", "Low", "Close"]:
        if col in df.columns:
            df[col] = df[col].round(2)

    time_col = "日期" if date_only else "时间"
    df.rename(columns={
        "Datetime": time_col,
        "Open":     "开盘价",
        "High":     "最高价",
        "Low":      "最低价",
        "Close":    "收盘价",
        "Volume":   "成交量",
    }, inplace=True)

    return df


# ────────────────────────────────────────────────
# 读取已有数据 / 增量合并 / 写入
# ────────────────────────────────────────────────
def read_existing(sheet_name):
    if not os.path.exists(OUTPUT_FILE):
        return pd.DataFrame()
    try:
        df = pd.read_excel(OUTPUT_FILE, sheet_name=sheet_name, dtype=str)
        return df.dropna(how="all").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def get_last_date(df, time_col):
    if df.empty or time_col not in df.columns:
        return None
    try:
        last = pd.to_datetime(df[time_col]).max()
        return last.to_pydatetime().replace(tzinfo=None)
    except Exception:
        return None


def merge_df(old, new, time_col):
    if old.empty:
        return new
    if new.empty:
        return old
    combined = pd.concat([old, new], ignore_index=True)
    combined = combined.drop_duplicates(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
    return combined


def write_excel(sheets):
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            if df is None or df.empty:
                continue
            safe_name = sheet_name[:31]
            df.to_excel(writer, sheet_name=safe_name, index=False)
            ws = writer.sheets[safe_name]
            for col_cells in ws.columns:
                max_len = max(
                    len(str(cell.value)) if cell.value is not None else 0
                    for cell in col_cells
                )
                ws.column_dimensions[col_cells[0].column_letter].width = max_len + 4
    print(f"\n✅ 数据已写入：{os.path.abspath(OUTPUT_FILE)}")


# ────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────
def main():
    all_sheets = {}
    old_counts = {}

    print("=== VIX 恐慌指数 ===")

    # ── 1. 日 K 线 ──
    sheet_day = "VIX_日K"
    old_day = read_existing(sheet_day)
    old_counts[sheet_day] = len(old_day)
    last_day = get_last_date(old_day, "日期")
    start_day = (last_day + timedelta(days=1)) if last_day else START_DATE
    if start_day.date() > END_DATE.date():
        print("  日K 已是最新，无需更新")
        new_day = pd.DataFrame()
    else:
        print(f"  增量范围 [{start_day.date()} ~ {END_DATE.date()}]")
        raw = _fetch_one("1d", start_day, END_DATE)
        new_day = format_df(raw, date_only=True)
    all_sheets[sheet_day] = merge_df(old_day, new_day, "日期")

    # ── 2. 5分钟 K 线 ──
    sheet_5m = "VIX_5min"
    old_5m = read_existing(sheet_5m)
    old_counts[sheet_5m] = len(old_5m)
    last_5m = get_last_date(old_5m, "时间")
    start_5m = (last_5m + timedelta(days=1)) if last_5m else START_DATE
    if start_5m.date() > END_DATE.date():
        print("  5min 已是最新，无需更新")
        new_5m = pd.DataFrame()
    else:
        print(f"  增量范围 [{start_5m.date()} ~ {END_DATE.date()}]")
        raw_5m = download_chunked("5m", start_5m, END_DATE, CHUNK_DAYS)
        new_5m = format_df(raw_5m)
    all_sheets[sheet_5m] = merge_df(old_5m, new_5m, "时间")

    print()
    write_excel(all_sheets)

    # 打印摘要
    print("\n── 更新摘要 ──")
    any_updated = False
    for name, df in all_sheets.items():
        new_total = len(df) if df is not None else 0
        old_total = old_counts.get(name, 0)
        added = new_total - old_total
        if added > 0:
            print(f"  ✅ {name:<20} 新增 {added:>5} 条  (共 {new_total} 条)")
            any_updated = True
        else:
            print(f"  ─  {name:<20} 无新数据       (共 {new_total} 条)")
    if not any_updated:
        print("  所有数据均已是最新，无新增。")


if __name__ == "__main__":
    main()
