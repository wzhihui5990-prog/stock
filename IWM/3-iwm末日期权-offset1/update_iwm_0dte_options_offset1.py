# -*- coding: utf-8 -*-
"""
IWM 末日期权（0DTE）历史数据批量拉取（增量追加模式）
行权价偏移：±1.0 美元（相对T-1收盘价取整后 ±1）
数据来源：Polygon.io 免费 API

输出文件：data/iwm_0dte_options_offset1.xlsx
  Sheet《摘要》    ：每个到期日的合约信息 + T2/T1 日K数据
  Sheet《Call_1min》：Call 合约在到期日的1分钟分时数据
  Sheet《Put_1min》 ：Put  合约在到期日的1分钟分时数据
"""

import os, time, requests, pandas as pd
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

API_KEY       = "NuxofB1uXuHOGR4jRmby3pBT1FXQURZg"   # Polygon.io API Key
SYMBOL        = "IWM"
STRIKE_OFFSET = 1.0      # ±1.0 美元
RATE_LIMIT    = 3        # Polygon 免费账号保守限速
START_DATE    = date(2026, 2, 13)
END_DATE      = None #None     # None = 拉到今天
OUTPUT_DIR    = "data"
OUTPUT_FILE   = os.path.join(OUTPUT_DIR, "iwm_0dte_options_offset1.xlsx")

os.makedirs(OUTPUT_DIR, exist_ok=True)
BASE_URL = "https://api.polygon.io"


# ────────────────────────────────────────────────
# 限速器
# ────────────────────────────────────────────────
class RateLimiter:
    def __init__(self, calls_per_min):
        self.interval = 60.0 / calls_per_min
        self._last    = 0.0

    def wait(self):
        elapsed = time.time() - self._last
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last = time.time()

limiter = RateLimiter(RATE_LIMIT)


# ────────────────────────────────────────────────
# HTTP 请求
# ────────────────────────────────────────────────
def api_get(url, params=None, retries=3):
    if params is None:
        params = {}
    params["apiKey"] = API_KEY
    for attempt in range(retries):
        limiter.wait()
        try:
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 429:
                print("  触发限速，等待65秒...")
                time.sleep(65)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                print(f"  请求失败: {url} => {e}")
                return {}
    return {}


# ────────────────────────────────────────────────
# 获取 IWM 日 K
# ────────────────────────────────────────────────
def get_iwm_daily_bars(start, end):
    url  = f"{BASE_URL}/v2/aggs/ticker/{SYMBOL}/range/1/day/{start}/{end}"
    data = api_get(url, {"adjusted": "true", "sort": "asc", "limit": 300})
    days = []
    for r in data.get("results", []):
        d = datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        days.append({"date": d, "close": round(r.get("c", 0), 2)})
    return days


# ────────────────────────────────────────────────
# 构建 Polygon 期权 Ticker
# IWM 行权价精度为 $0.5 或 $1，ticker 中 strike*1000 补零8位
# 示例：行权价 201.0 → 00201000
# ────────────────────────────────────────────────
def build_ticker(expiration: str, option_type: str, strike: float) -> str:
    exp_str    = datetime.strptime(expiration, "%Y-%m-%d").strftime("%y%m%d")
    cp         = "C" if option_type == "call" else "P"
    strike_int = int(round(strike * 1000))
    return f"O:{SYMBOL}{exp_str}{cp}{strike_int:08d}"


# ────────────────────────────────────────────────
# 拉取日 K 数据
# ────────────────────────────────────────────────
def fetch_daily_bar(ticker: str, day: str):
    url     = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{day}/{day}"
    data    = api_get(url, {"adjusted": "true", "sort": "asc", "limit": 5})
    results = data.get("results", [])
    if not results:
        return None
    r = results[0]
    return {
        "开盘价": round(r.get("o", 0), 4),
        "最高价": round(r.get("h", 0), 4),
        "最低价": round(r.get("l", 0), 4),
        "收盘价": round(r.get("c", 0), 4),
        "成交量": int(r.get("v", 0)),
    }


# ────────────────────────────────────────────────
# 拉取1分钟分时数据
# ────────────────────────────────────────────────
def fetch_1min_bars(ticker: str, day: str) -> list:
    url  = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/minute/{day}/{day}"
    data = api_get(url, {"adjusted": "true", "sort": "asc", "limit": 1000})
    rows = []
    for r in data.get("results", []):
        dt_et = (datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc)
                 .astimezone(EASTERN).strftime("%Y-%m-%d %H:%M"))
        rows.append({
            "到期日":     day,
            "时间(美东)": dt_et,
            "开盘价":     round(r.get("o", 0), 4),
            "最高价":     round(r.get("h", 0), 4),
            "最低价":     round(r.get("l", 0), 4),
            "收盘价":     round(r.get("c", 0), 4),
            "成交量":     int(r.get("v", 0)),
        })
    return rows


# ────────────────────────────────────────────────
# 处理单个交易日
# ────────────────────────────────────────────────
def process_day(t2_date: str, t2_close: float, t1_date: str) -> dict:
    base_strike  = round(t2_close)
    call_strike  = base_strike + STRIKE_OFFSET
    put_strike   = base_strike - STRIKE_OFFSET
    call_ticker  = build_ticker(t1_date, "call", call_strike)
    put_ticker   = build_ticker(t1_date, "put",  put_strike)

    print(f"  T2={t2_date} IWM收盘=${t2_close}  "
          f"Call=${call_strike} {call_ticker}  Put=${put_strike} {put_ticker}")

    call_t2 = fetch_daily_bar(call_ticker, t2_date)
    put_t2  = fetch_daily_bar(put_ticker,  t2_date)
    call_t1 = fetch_daily_bar(call_ticker, t1_date)
    put_t1  = fetch_daily_bar(put_ticker,  t1_date)
    call_1m = fetch_1min_bars(call_ticker, t1_date)
    put_1m  = fetch_1min_bars(put_ticker,  t1_date)

    print(f"    Call: T2收盘={'$'+str(call_t2['收盘价']) if call_t2 else '无'}"
          f"  T1收盘={'$'+str(call_t1['收盘价']) if call_t1 else '无'}"
          f"  1min={len(call_1m)}条")
    print(f"    Put : T2收盘={'$'+str(put_t2['收盘价'])  if put_t2  else '无'}"
          f"  T1收盘={'$'+str(put_t1['收盘价'])  if put_t1  else '无'}"
          f"  1min={len(put_1m)}条")

    summary = {
        "到期日(T1)":    t1_date,
        "基准日(T2)":    t2_date,
        "IWM_T2收盘":    t2_close,
        "Call合约":      call_ticker,
        "Call行权价":    call_strike,
        "Call_T2收盘":   call_t2["收盘价"] if call_t2 else None,
        "Call_T1开盘":   call_t1["开盘价"] if call_t1 else None,
        "Call_T1最高":   call_t1["最高价"] if call_t1 else None,
        "Call_T1最低":   call_t1["最低价"] if call_t1 else None,
        "Call_T1收盘":   call_t1["收盘价"] if call_t1 else None,
        "Call_T1成交量": call_t1["成交量"] if call_t1 else None,
        "Put合约":       put_ticker,
        "Put行权价":     put_strike,
        "Put_T2收盘":    put_t2["收盘价"]  if put_t2  else None,
        "Put_T1开盘":    put_t1["开盘价"]  if put_t1  else None,
        "Put_T1最高":    put_t1["最高价"]  if put_t1  else None,
        "Put_T1最低":    put_t1["最低价"]  if put_t1  else None,
        "Put_T1收盘":    put_t1["收盘价"]  if put_t1  else None,
        "Put_T1成交量":  put_t1["成交量"]  if put_t1  else None,
        "Call_1min条数": len(call_1m),
        "Put_1min条数":  len(put_1m),
    }
    return {"summary": summary, "call_1m": call_1m, "put_1m": put_1m}


# ────────────────────────────────────────────────
# 读取已有 sheet
# ────────────────────────────────────────────────
def read_existing(sheet_name: str) -> pd.DataFrame:
    if not os.path.exists(OUTPUT_FILE):
        return pd.DataFrame()
    try:
        df = pd.read_excel(OUTPUT_FILE, sheet_name=sheet_name, dtype=str)
        return df.dropna(how="all").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


# ────────────────────────────────────────────────
# 写入 Excel
# ────────────────────────────────────────────────
def write_excel(df_summary, df_call_1m, df_put_1m):
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        for sheet_name, df in [("摘要", df_summary),
                                ("Call_1min", df_call_1m),
                                ("Put_1min",  df_put_1m)]:
            if df is None or df.empty:
                continue
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            ws = writer.sheets[sheet_name]
            for col_cells in ws.columns:
                w = max(len(str(c.value)) if c.value else 0 for c in col_cells)
                ws.column_dimensions[col_cells[0].column_letter].width = min(w + 3, 45)
    print(f"\n已写入: {os.path.abspath(OUTPUT_FILE)}")


# ────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────
def main():
    today    = date.today()
    end_date = END_DATE if END_DATE is not None else today

    old_summary = read_existing("摘要")
    old_call_1m = read_existing("Call_1min")
    old_put_1m  = read_existing("Put_1min")

    existing_dates = set()
    if not old_summary.empty and "到期日(T1)" in old_summary.columns:
        existing_dates = set(old_summary["到期日(T1)"].dropna().tolist())
    print(f"已有数据: {len(existing_dates)} 天")

    fetch_start = START_DATE - timedelta(days=10)
    print(f"\n获取 {SYMBOL} 交易日列表（{fetch_start} ~ {end_date}）...")
    iwm_days = get_iwm_daily_bars(fetch_start, end_date)
    if len(iwm_days) < 2:
        print("获取交易日失败，请检查 API Key 或网络")
        return
    print(f"  共 {len(iwm_days)} 个交易日")

    pairs_to_process = []
    for i in range(len(iwm_days) - 1):
        t2      = iwm_days[i]
        t1_date = iwm_days[i + 1]["date"]
        t1_d    = datetime.strptime(t1_date, "%Y-%m-%d").date()
        if t1_d >= START_DATE and t1_date not in existing_dates and t1_d <= end_date:
            pairs_to_process.append((t2, t1_date))

    if not pairs_to_process:
        print("\n所有日期已是最新，无需更新。")
        return

    est_mins = max(1, len(pairs_to_process) * 6 // RATE_LIMIT)
    print(f"\n需处理 {len(pairs_to_process)} 天，预计约 {est_mins} 分钟\n")

    new_summaries, new_call_1m, new_put_1m, updated_dates = [], [], [], []
    for t2, t1_date in pairs_to_process:
        print(f"── {t1_date} ──")
        result = process_day(t2["date"], t2["close"], t1_date)
        new_summaries.append(result["summary"])
        new_call_1m.extend(result["call_1m"])
        new_put_1m.extend(result["put_1m"])
        updated_dates.append(t1_date)

    def merge(old, new_rows, dedup_cols):
        new_df = pd.DataFrame(new_rows)
        if old.empty:
            return new_df
        if new_df.empty:
            return old
        combined = pd.concat([old, new_df], ignore_index=True)
        return (combined.drop_duplicates(subset=dedup_cols)
                        .sort_values(dedup_cols)
                        .reset_index(drop=True))

    df_summary_final = merge(old_summary, new_summaries, ["到期日(T1)"])
    df_call_1m_final = merge(old_call_1m, new_call_1m,   ["到期日", "时间(美东)"])
    df_put_1m_final  = merge(old_put_1m,  new_put_1m,    ["到期日", "时间(美东)"])

    write_excel(df_summary_final, df_call_1m_final, df_put_1m_final)

    print(f"\n── 更新摘要 ──")
    print(f"  新增 {len(updated_dates)} 天: {', '.join(updated_dates)}")
    print(f"  摘要总计: {len(df_summary_final)} 行")
    print(f"  Call_1min 总计: {len(df_call_1m_final)} 条")
    print(f"  Put_1min  总计: {len(df_put_1m_final)} 条")
    if new_summaries and all(
        r["Call_1min条数"] == 0 and r["Put_1min条数"] == 0
        for r in new_summaries
    ):
        print("\n  提示：1分钟数据均为空 — Polygon 免费账号不支持期权分钟级历史数据，"
              "需升级 Starter 套餐")


if __name__ == "__main__":
    main()
