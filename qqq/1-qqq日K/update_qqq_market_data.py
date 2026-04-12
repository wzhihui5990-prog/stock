"""
美股 QQQ 数据爬取脚本（增量追加模式）
- 每日 K 线：开盘价、收盘价、最高价、最低价、成交量
- 每日分时线（2分钟粒度）
- 每5分钟 K 线
首次运行：从 START_DATE 开始全量下载
后续运行：自动读取已有 Excel，从最后一条数据的日期续拉新数据并追加
结果写入 Excel 文件（data/qqq_market_data.xlsx）
"""

import os
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

# ────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────
SYMBOLS = ["QQQ"]          # 可扩展多只股票，如 ["QQQ", "SPY", "AAPL"]
OUTPUT_DIR = "data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "qqq_market_data.xlsx")

# yfinance: 2m 最多拉近60天；5m 最多60天；日线不限
END_DATE   = datetime.today()
START_DATE = datetime(2026, 2, 13)   # 从2月13日开始拉取

# 分段大小（Yahoo 限制：1m 最多30天，2m/5m 最多60天，每次拉7天）
CHUNK_DAYS = 7

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ────────────────────────────────────────────────
# 通用：下载单段并格式化
# ────────────────────────────────────────────────
def _fetch_one(symbol: str, interval: str, start, end) -> pd.DataFrame:
    df = yf.download(
        symbol,
        start=start.strftime("%Y-%m-%d"),
        end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
        interval=interval,
        auto_adjust=True,
        progress=False,
    )
    return df

# ────────────────────────────────────────────────
# 分段下载（专为 1m 准备），然后合并
# ────────────────────────────────────────────────
def download_chunked(symbol: str, interval: str, start, end, chunk_days: int) -> pd.DataFrame:
    """将区间拆成 chunk_days 天的小段逐段下载并合并。"""
    frames = []
    cur = start
    while cur < end:
        seg_end = min(cur + timedelta(days=chunk_days), end)
        print(f"  下载 {symbol} [{interval}] {cur.date()} ~ {seg_end.date()} ...")
        df = _fetch_one(symbol, interval, cur, seg_end)
        if not df.empty:
            frames.append(df)
        cur = seg_end + timedelta(days=1)
    if not frames:
        print(f"  ⚠  {symbol} [{interval}] 无数据返回")
        return pd.DataFrame()
    return pd.concat(frames)

# ────────────────────────────────────────────────
# 格式化 DataFrame（统一列名、时间、精度）
# ────────────────────────────────────────────────
def format_df(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()
    df.index.name = "Datetime"
    df = df.reset_index()

    # 统一列名（yfinance 新版 MultiIndex 兼容）
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = ["_".join(filter(None, col)).strip() for col in df.columns]
        df.columns = [c.replace(f"_{symbol}", "") for c in df.columns]

    # 只保留需要的列
    keep = ["Datetime", "Open", "High", "Low", "Close", "Volume"]
    existing = [c for c in keep if c in df.columns]
    df = df[existing].copy()

    # 去重（分段下载可能有重叠）
    df = df.drop_duplicates(subset=["Datetime"]).sort_values("Datetime").reset_index(drop=True)

    # 格式化时间
    df["Datetime"] = pd.to_datetime(df["Datetime"])
    try:
        df["Datetime"] = df["Datetime"].dt.tz_convert("America/New_York")
        df["Datetime"] = df["Datetime"].dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        df["Datetime"] = df["Datetime"].dt.strftime("%Y-%m-%d %H:%M")

    # 数值保留2位小数
    for col in ["Open", "High", "Low", "Close"]:
        if col in df.columns:
            df[col] = df[col].round(2)

    # 列名中文化
    df.rename(columns={
        "Datetime": "时间",
        "Open":     "开盘价",
        "High":     "最高价",
        "Low":      "最低价",
        "Close":    "收盘价",
        "Volume":   "成交量",
    }, inplace=True)

    return df

# ────────────────────────────────────────────────
# 通用：下载并格式化时间列
# ────────────────────────────────────────────────
def download(symbol: str, interval: str, start, end) -> pd.DataFrame:
    print(f"  下载 {symbol} [{interval}] {start.date()} ~ {end.date()} ...")
    df = _fetch_one(symbol, interval, start, end)
    if df.empty:
        print(f"  ⚠  {symbol} [{interval}] 无数据返回")
        return df
    return format_df(symbol, df)

# ────────────────────────────────────────────────
# 读取已有 Excel 中某个 sheet 的数据
# ────────────────────────────────────────────────
def read_existing(filepath: str, sheet_name: str) -> pd.DataFrame:
    if not os.path.exists(filepath):
        return pd.DataFrame()
    try:
        df = pd.read_excel(filepath, sheet_name=sheet_name, dtype=str)
        return df.dropna(how="all").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()

# ────────────────────────────────────────────────
# 获取已有数据的最后日期，作为增量起始点
# ────────────────────────────────────────────────
def get_last_date(df: pd.DataFrame, time_col: str) -> datetime | None:
    if df.empty or time_col not in df.columns:
        return None
    try:
        last = pd.to_datetime(df[time_col]).max()
        return last.to_pydatetime().replace(tzinfo=None)
    except Exception:
        return None

# ────────────────────────────────────────────────
# 合并旧数据与新数据（去重 + 排序）
# ────────────────────────────────────────────────
def merge_df(old: pd.DataFrame, new: pd.DataFrame, time_col: str) -> pd.DataFrame:
    if old.empty:
        return new
    if new.empty:
        return old
    combined = pd.concat([old, new], ignore_index=True)
    combined = combined.drop_duplicates(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
    return combined

# ────────────────────────────────────────────────
# 写入 Excel
# ────────────────────────────────────────────────
def write_excel(sheets: dict, filepath: str):
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            if df is None or df.empty:
                continue
            # Sheet 名最长 31 字符
            safe_name = sheet_name[:31]
            df.to_excel(writer, sheet_name=safe_name, index=False)

            # 自动调整列宽
            ws = writer.sheets[safe_name]
            for col_cells in ws.columns:
                max_len = max(
                    len(str(cell.value)) if cell.value is not None else 0
                    for cell in col_cells
                )
                ws.column_dimensions[col_cells[0].column_letter].width = max_len + 4

    print(f"\n✅ 数据已写入：{os.path.abspath(filepath)}")

# ────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────
def main():
    all_sheets = {}
    old_counts = {}  # 记录写入前各 sheet 的旧行数

    for symbol in SYMBOLS:
        print(f"=== {symbol} ===")

        # ── 1. 日 K 线（增量）──
        sheet_day = f"{symbol}_日K"
        old_day = read_existing(OUTPUT_FILE, sheet_day)
        old_counts[sheet_day] = len(old_day)
        last_day = get_last_date(old_day, "日期")
        start_day = (last_day + timedelta(days=1)) if last_day else START_DATE
        if start_day.date() > END_DATE.date():
            print(f"  日K 已是最新，无需更新")
            new_day = pd.DataFrame()
        else:
            print(f"  增量范围 [{start_day.date()} ~ {END_DATE.date()}]")
            new_day = download(symbol, "1d", start_day, END_DATE)
            if not new_day.empty:
                new_day["时间"] = new_day["时间"].str[:10]
                new_day.rename(columns={"时间": "日期"}, inplace=True)
        all_sheets[sheet_day] = merge_df(old_day, new_day, "日期")

        # ── 2. 1分钟分时线（增量，Yahoo 最多保留30天）──
        sheet_1m = f"{symbol}_分时1min"
        old_1m = read_existing(OUTPUT_FILE, sheet_1m)
        old_counts[sheet_1m] = len(old_1m)
        last_1m = get_last_date(old_1m, "时间")
        start_1m = (last_1m + timedelta(days=1)) if last_1m else START_DATE
        if start_1m.date() > END_DATE.date():
            print(f"  1min 已是最新，无需更新")
            new_1m = pd.DataFrame()
        else:
            print(f"  增量范围 [{start_1m.date()} ~ {END_DATE.date()}]")
            df_1m_raw = download_chunked(symbol, "1m", start_1m, END_DATE, CHUNK_DAYS)
            new_1m = format_df(symbol, df_1m_raw)
        all_sheets[sheet_1m] = merge_df(old_1m, new_1m, "时间")

        # ── 3. 2分钟分时线（增量）──
        sheet_2m = f"{symbol}_分时2min"
        old_2m = read_existing(OUTPUT_FILE, sheet_2m)
        old_counts[sheet_2m] = len(old_2m)
        last_2m = get_last_date(old_2m, "时间")
        start_2m = (last_2m + timedelta(days=1)) if last_2m else START_DATE
        if start_2m.date() > END_DATE.date():
            print(f"  2min 已是最新，无需更新")
            new_2m = pd.DataFrame()
        else:
            print(f"  增量范围 [{start_2m.date()} ~ {END_DATE.date()}]")
            df_2m_raw = download_chunked(symbol, "2m", start_2m, END_DATE, CHUNK_DAYS)
            new_2m = format_df(symbol, df_2m_raw)
        all_sheets[sheet_2m] = merge_df(old_2m, new_2m, "时间")

        # ── 4. 5分钟 K 线（增量）──
        sheet_5m = f"{symbol}_5min"
        old_5m = read_existing(OUTPUT_FILE, sheet_5m)
        old_counts[sheet_5m] = len(old_5m)
        last_5m = get_last_date(old_5m, "时间")
        start_5m = (last_5m + timedelta(days=1)) if last_5m else START_DATE
        if start_5m.date() > END_DATE.date():
            print(f"  5min 已是最新，无需更新")
            new_5m = pd.DataFrame()
        else:
            print(f"  增量范围 [{start_5m.date()} ~ {END_DATE.date()}]")
            new_5m = download(symbol, "5m", start_5m, END_DATE)
        all_sheets[sheet_5m] = merge_df(old_5m, new_5m, "时间")

        print()

    write_excel(all_sheets, OUTPUT_FILE)

    # 打印更新摘要
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
