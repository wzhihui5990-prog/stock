# -*- coding: utf-8 -*-
"""
VIX 恐慌指数数据拉取脚本（增量追加模式）
- 日 K 线：开盘价、收盘价、最高价、最低价
- 5分钟 K 线
数据来源：Yahoo Finance（^VIX）
结果写入 Excel 文件（data/vix_data.xlsx）
"""

import os
import sys
import time
import argparse
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, timezone

# GBK 控制台不能输出 ⚠ 等符号，统一转义避免 UnicodeEncodeError
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

# ────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────
SYMBOL     = "^VIX"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "vix_data.xlsx")

END_DATE   = datetime.today()
START_DATE = datetime(2026, 2, 13)   # 与 QQQ 数据同步起始日

CHUNK_DAYS = 7

# 直连 Yahoo chart API（yfinance 被限流时的备用通道）
# ⚠ 2026-10 起 Yahoo 要求【带 Cookie + crumb】才能访问 chart API，
#    否则一律 403 Forbidden（这就是之前「VIX 拿不到数据」的原因）。
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
YAHOO_COOKIE_URL = "https://finance.yahoo.com/quote/%5EVIX"

# CBOE 官方日 K 历史 CSV（1990 年至今），Yahoo 挂掉时的备用源，
# 只提供日 K，没有 5 分钟。
CBOE_DAILY_CSV = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
REQUEST_INTERVAL = 0.5   # 每个请求之间的间隔（秒），降低被限流概率

_YAHOO = None            # (requests.Session, crumb) 缓存


def yahoo_session():
    """取得带 Cookie + crumb 的 Yahoo 会话（只建一次）。"""
    global _YAHOO
    if _YAHOO is not None:
        return _YAHOO
    s = requests.Session()
    s.headers.update(HTTP_HEADERS)
    crumb = ""
    try:
        s.get(YAHOO_COOKIE_URL, timeout=20)          # 拿 A1/A3 cookie
        r = s.get(YAHOO_CRUMB_URL, timeout=20)
        crumb = (r.text or "").strip()
        if crumb:
            print(f"  ·  Yahoo 会话就绪（crumb={crumb[:8]}…）")
        else:
            print("  ⚠  Yahoo crumb 为空，chart API 可能仍返回 403")
    except Exception as e:
        print(f"  ⚠  Yahoo 会话建立失败：{e}")
    _YAHOO = (s, crumb)
    return _YAHOO

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ────────────────────────────────────────────────
# 下载
# ────────────────────────────────────────────────
def _fetch_direct(interval, start, end):
    """直接调用 Yahoo chart API，返回与 yfinance 一致的 DataFrame 结构。"""
    try:
        s, crumb = yahoo_session()
        params = {
            "period1":  int(start.replace(tzinfo=timezone.utc).timestamp()),
            "period2":  int((end + timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp()),
            "interval": interval,
            "includePrePost": "true",
            "events":   "div,splits",
        }
        if crumb:
            params["crumb"] = crumb
        resp = s.get(
            YAHOO_CHART_URL.format(symbol=SYMBOL),
            params=params,
            headers=HTTP_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        result = (resp.json().get("chart") or {}).get("result")
        if not result:
            return pd.DataFrame()
        ts = result[0].get("timestamp") or []
        if not ts:
            return pd.DataFrame()
        quote = result[0]["indicators"]["quote"][0]
        df = pd.DataFrame(
            {
                "Open":   quote.get("open"),
                "High":   quote.get("high"),
                "Low":    quote.get("low"),
                "Close":  quote.get("close"),
                "Volume": quote.get("volume"),
            },
            index=pd.to_datetime(ts, unit="s", utc=True),
        )
        df.index.name = "Datetime"
        return df.dropna(how="all")
    except Exception as e:
        print(f"  ⚠  直连 API [{interval}] 获取失败：{e}")
        return pd.DataFrame()


def _fetch_cboe_daily():
    """CBOE 官方日 K CSV（只日K，无 5 分钟）。Yahoo 完全挂掉时的备用。"""
    try:
        r = requests.get(CBOE_DAILY_CSV, headers=HTTP_HEADERS, timeout=30)
        r.raise_for_status()
        from io import StringIO
        d = pd.read_csv(StringIO(r.text))
        d["DATE"] = pd.to_datetime(d["DATE"], format="%m/%d/%Y")
        d = d.rename(columns={"DATE": "Datetime", "OPEN": "Open", "HIGH": "High",
                              "LOW": "Low", "CLOSE": "Close"})
        d["Volume"] = None
        d = d.set_index("Datetime")[ ["Open", "High", "Low", "Close", "Volume"] ]
        print(f"  \u2714  CBOE 日K 备用源取到 {len(d)} 条（最新 {d.index[-1].date()}）")
        return d
    except Exception as e:
        print(f"  \u26a0  CBOE 日K 备用源失败：{e}")
        return pd.DataFrame()


_USE_YFINANCE = True   # 首次被限流后，本次运行余下请求全部走直连通道


def _fetch_one(interval, start, end):
    global _USE_YFINANCE
    if _USE_YFINANCE:
        df = yf.download(
            SYMBOL,
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            interval=interval,
            auto_adjust=True,
            progress=False,
        )
        if df is not None and not df.empty:
            return df
        print(f"  ⚠  yfinance [{interval}] 无数据（可能被限流），改用直连 API")
        _USE_YFINANCE = False
    return _fetch_direct(interval, start, end)


def download_chunked(interval, start, end, chunk_days):
    frames = []
    cur = start
    while cur < end:
        seg_end = min(cur + timedelta(days=chunk_days), end)
        print(f"  下载 VIX [{interval}] {cur.date()} ~ {seg_end.date()} ...")
        df = _fetch_one(interval, cur, seg_end)
        if not df.empty:
            frames.append(df)
        time.sleep(REQUEST_INTERVAL)
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
    # keep="last"：重拉/补缺口时让【新数据覆盖旧数据】。
    # 否则今天那根还没收盘的日K（以及盘中最后一根5min柱）会一直卡在表里不被修正。
    combined = (combined.drop_duplicates(subset=[time_col], keep="last")
                        .sort_values(time_col).reset_index(drop=True))
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
    ap = argparse.ArgumentParser(description="VIX 数据拉取（默认增量追加）")
    ap.add_argument("--from", dest="from_date", default="", metavar="YYYY-MM-DD",
                    help="从该日期重新拉取并与现有数据合并（用来补中间的缺口）")
    args = ap.parse_args()
    force_from = (datetime.strptime(args.from_date, "%Y-%m-%d") if args.from_date else None)

    all_sheets = {}
    old_counts = {}

    print("=== VIX 恐慌指数 ===")
    if force_from:
        print(f"  ⚠ 强制从 {force_from.date()} 重新拉取并合并（补缺口模式）")

    # ── 1. 日 K 线 ──
    sheet_day = "VIX_日K"
    old_day = read_existing(sheet_day)
    old_counts[sheet_day] = len(old_day)
    last_day = get_last_date(old_day, "日期")
    start_day = force_from or ((last_day + timedelta(days=1)) if last_day else START_DATE)
    if start_day.date() > END_DATE.date():
        print("  日K 已是最新，无需更新")
        new_day = pd.DataFrame()
    else:
        print(f"  增量范围 [{start_day.date()} ~ {END_DATE.date()}]")
        raw = _fetch_one("1d", start_day, END_DATE)
        new_day = format_df(raw, date_only=True)
        if new_day.empty:
            print("  \u26a0  Yahoo 日K 无数据 → 改用 CBOE 官方 CSV 日K 备用源")
            raw = _fetch_cboe_daily()
            new_day = format_df(raw, date_only=True)
    all_sheets[sheet_day] = merge_df(old_day, new_day, "日期")

    # ── 2. 5分钟 K 线 ──
    sheet_5m = "VIX_5min"
    old_5m = read_existing(sheet_5m)
    old_counts[sheet_5m] = len(old_5m)
    last_5m = get_last_date(old_5m, "时间")
    start_5m = force_from or ((last_5m + timedelta(days=1)) if last_5m else START_DATE)
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
        print("  \u26a0  若本来应该有新数据，说明这一次一个源都没拉到："
              "日K 会依次试 Yahoo → CBOE CSV，5分钟只有 Yahoo。")
        print("     请检查网络/代理，或稍后重跑（Yahoo 限流一般几分钟后恢复）。")


if __name__ == "__main__":
    main()
