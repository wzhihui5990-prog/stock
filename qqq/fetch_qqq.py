"""
美股 QQQ 近一个月盘中数据爬取脚本
- 每日 K 线：开盘价、收盘价、最高价、最低价、成交量
- 每日分时线（1分钟粒度）
- 每5分钟 K 线
结果写入 Excel 文件（data/QQQ_data.xlsx）
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
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "QQQ_data.xlsx")

# yfinance: 1m 每次最多8天；5m/日线可拉30天
END_DATE   = datetime.today()
START_DATE = END_DATE - timedelta(days=30)

# 1m 分段大小（Yahoo 限制 8 天/次）
CHUNK_DAYS_1M = 7

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
    print(f"数据范围：{START_DATE.date()} ~ {END_DATE.date()}\n")
    all_sheets = {}

    for symbol in SYMBOLS:
        print(f"=== {symbol} ===")

        # 1. 日 K 线
        df_day = download(symbol, "1d", START_DATE, END_DATE)
        if not df_day.empty:
            df_day["时间"] = df_day["时间"].str[:10]
            df_day.rename(columns={"时间": "日期"}, inplace=True)
        all_sheets[f"{symbol}_日K"] = df_day

        # 2. 1分钟分时线（每次最多8天，分段拉取并合并）
        df_1m_raw = download_chunked(symbol, "1m", START_DATE, END_DATE, CHUNK_DAYS_1M)
        df_1m = format_df(symbol, df_1m_raw)
        all_sheets[f"{symbol}_分时1min"] = df_1m

        # 3. 5分钟 K 线
        df_5m = download(symbol, "5m", START_DATE, END_DATE)
        all_sheets[f"{symbol}_5min"] = df_5m

        print()

    write_excel(all_sheets, OUTPUT_FILE)

    # 打印每张 sheet 的行数摘要
    print("\n── 数据摘要 ──")
    for name, df in all_sheets.items():
        rows = len(df) if df is not None else 0
        print(f"  {name:<25} {rows:>6} 条")

if __name__ == "__main__":
    main()
