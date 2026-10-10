# -*- coding: utf-8 -*-
"""
MU（美光科技）行情数据拉取（增量追加模式）
- 日 K 线：开盘价、最高价、最低价、收盘价、成交量
- 1 分钟分时线
- 5 分钟 K 线

数据来源：Polygon.io（免费档 5 次/分钟）
注意：免费档数据延迟约 1 个交易日 —— 「最近一个交易日」的分钟数据拿不到（403），
      日 K 不受影响。脚本会自动跳过拿不到的日期并给出提示。

输出：data/mu_market_data.xlsx
  Sheet《MU_日K》    ：日期 / 开盘价 / 最高价 / 最低价 / 收盘价 / 成交量
  Sheet《MU_分时1min》：时间 / 开盘价 / 最高价 / 最低价 / 收盘价 / 成交量
  Sheet《MU_5min》   ：时间 / 开盘价 / 最高价 / 最低价 / 收盘价 / 成交量
"""

import os
import sys
import time
import argparse
import requests
import pandas as pd
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

# 让输出实时可见；并对 GBK 控制台/重定向做容错（避免 emoji 导致 UnicodeEncodeError）
try:
    sys.stdout.reconfigure(line_buffering=True, errors="replace")
except Exception:
    pass

EASTERN = ZoneInfo("America/New_York")

# ────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────
API_KEY      = "NuxofB1uXuHOGR4jRmby3pBT1FXQURZg"
SYMBOL       = "MU"
START_DATE   = date(2026, 7, 1)      # MU 周一/周三 0DTE 从 7 月才有，故只拉 7 月起
END_DATE     = None                  # None = 今天
RATE_LIMIT   = 5                     # Polygon 免费档：5 次/分钟

OUTPUT_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUTPUT_FILE  = os.path.join(OUTPUT_DIR, "mu_market_data.xlsx")
BASE_URL     = "https://api.polygon.io"

CHUNK_DAYS   = 25                    # 分钟数据分段天数

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ────────────────────────────────────────────────
# 限速 / 请求
# ────────────────────────────────────────────────
class RateLimiter:
    def __init__(self, calls_per_min):
        self.interval = 60.0 / calls_per_min
        self._last = 0.0

    def wait(self):
        elapsed = time.time() - self._last
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last = time.time()


limiter = RateLimiter(RATE_LIMIT)


def _get(url, params=None, retries=3):
    """返回 dict；403 返回 {'_403': True}；失败返回 {}"""
    p = dict(params or {})
    if "apiKey" not in p:
        p["apiKey"] = API_KEY
    for attempt in range(retries):
        limiter.wait()
        try:
            r = requests.get(url, params=p, timeout=30)
        except requests.RequestException as e:
            if attempt == retries - 1:
                print(f"    请求失败: {e}")
                return {}
            continue
        if r.status_code == 429:
            print("    触发限速，等待 65 秒 ...")
            time.sleep(65)
            continue
        if r.status_code == 403:
            return {"_403": True}
        if r.status_code >= 500:
            time.sleep(5)
            continue
        if r.status_code != 200:
            print(f"    HTTP {r.status_code}: {r.text[:150]}")
            return {}
        try:
            return r.json()
        except ValueError:
            return {}
    return {}


def paginate(url, params=None):
    """自动翻页，返回全部 results。若 403 则返回 None 以便上层区分。"""
    out = []
    js = _get(url, params)
    if js.get("_403"):
        return None
    while True:
        if not js:
            break
        out.extend(js.get("results", []))
        nxt = js.get("next_url")
        if not nxt:
            break
        js = _get(nxt)
        if js.get("_403"):
            break
    return out


# ────────────────────────────────────────────────
# 拉取
# ────────────────────────────────────────────────
def fetch_daily(start, end):
    url = f"{BASE_URL}/v2/aggs/ticker/{SYMBOL}/range/1/day/{start}/{end}"
    res = paginate(url, {"adjusted": "true", "sort": "asc", "limit": 50000})
    if not res:
        print("  ⚠  日K 无数据返回")
        return pd.DataFrame()
    rows = []
    for r in res:
        d = datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).astimezone(EASTERN)
        rows.append({
            "日期":   d.strftime("%Y-%m-%d"),
            "开盘价": round(r.get("o", 0), 2),
            "最高价": round(r.get("h", 0), 2),
            "最低价": round(r.get("l", 0), 2),
            "收盘价": round(r.get("c", 0), 2),
            "成交量": int(r.get("v", 0)),
        })
    df = pd.DataFrame(rows)
    return df.drop_duplicates(subset=["日期"]).sort_values("日期").reset_index(drop=True)


def _minute_rows(res):
    """Polygon 分钟数据含盘前盘后（04:00~19:59 ET），仅保留常规时段 09:30~16:00。"""
    rows = []
    for r in res:
        dt_et = datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).astimezone(EASTERN)
        hhmm = dt_et.strftime("%H:%M")
        if not ("09:30" <= hhmm <= "16:00"):
            continue
        rows.append({
            "时间":   dt_et.strftime("%Y-%m-%d %H:%M"),
            "开盘价": round(r.get("o", 0), 4),
            "最高价": round(r.get("h", 0), 4),
            "最低价": round(r.get("l", 0), 4),
            "收盘价": round(r.get("c", 0), 4),
            "成交量": int(r.get("v", 0)),
        })
    return rows


def fetch_minutes(start, end, span_min):
    """分段拉取分钟数据；某段 403 时自动按天重试，跳过拿不到的日期。"""
    rows = []
    cur = start
    while cur <= end:
        seg_end = min(cur + timedelta(days=CHUNK_DAYS), end)
        url = (f"{BASE_URL}/v2/aggs/ticker/{SYMBOL}/range/{span_min}/minute/"
               f"{cur.isoformat()}/{seg_end.isoformat()}")
        print(f"    拉取 MU {span_min}min {cur} ~ {seg_end} ...")
        res = paginate(url, {"adjusted": "true", "sort": "asc", "limit": 50000})
        if res is None:
            print("      ⚠  整段不可用（免费档延迟），改为逐日重试")
            d = cur
            while d <= seg_end:
                u2 = (f"{BASE_URL}/v2/aggs/ticker/{SYMBOL}/range/{span_min}/minute/"
                      f"{d.isoformat()}/{d.isoformat()}")
                r2 = paginate(u2, {"adjusted": "true", "sort": "asc", "limit": 50000})
                if r2:
                    rows.extend(_minute_rows(r2))
                elif r2 is None:
                    print(f"        {d} 无权限（最近交易日），跳过")
                d += timedelta(days=1)
        elif res:
            rows.extend(_minute_rows(res))
        cur = seg_end + timedelta(days=1)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.drop_duplicates(subset=["时间"]).sort_values("时间").reset_index(drop=True)


# ────────────────────────────────────────────────
# Excel 读写
# ────────────────────────────────────────────────
def read_existing(sheet_name):
    if not os.path.exists(OUTPUT_FILE):
        return pd.DataFrame()
    try:
        return pd.read_excel(OUTPUT_FILE, sheet_name=sheet_name, dtype=str).dropna(how="all").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def merge(old, new, key_cols):
    if old.empty:
        return new
    if new.empty:
        return old
    combined = pd.concat([old, new], ignore_index=True)
    return combined.drop_duplicates(subset=key_cols).sort_values(key_cols).reset_index(drop=True)


def write_excel(sheets):
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        for name, df in sheets.items():
            if df is None or df.empty:
                continue
            df.to_excel(writer, sheet_name=name[:31], index=False)
            ws = writer.sheets[name[:31]]
            for col_cells in ws.columns:
                w = max(len(str(c.value)) if c.value is not None else 0 for c in col_cells)
                ws.column_dimensions[col_cells[0].column_letter].width = min(w + 4, 40)
    print(f"\n✅ 已写入: {os.path.abspath(OUTPUT_FILE)}")


# ────────────────────────────────────────────────
# 增量区间计算
# ────────────────────────────────────────────────
def get_last_date(df, col):
    """取已有数据的最后日期（date 对象）；无数据返回 None。"""
    if df.empty or col not in df.columns:
        return None
    try:
        s = df[col].astype(str).str[:10]
        d = pd.to_datetime(s, errors="coerce").max()
        return None if pd.isna(d) else d.date()
    except Exception:
        return None


def incremental_start(old, col, default):
    """已有数据 → 从「最后日期 + 1 天」续拉；否则从默认起始日开始。"""
    last = get_last_date(old, col)
    return (last + timedelta(days=1)) if last else default


# ────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD（仅在无已有数据时生效）")
    ap.add_argument("--full", action="store_true", help="忽略已有数据，从起始日全量重拉")
    ap.add_argument("--days-only", action="store_true", help="只拉日K，跳过分钟数据")
    args = ap.parse_args()

    default_start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else START_DATE
    end = END_DATE or date.today()

    print(f"=== MU 行情数据  默认起始 {default_start} ~ {end} ===\n")

    sheets = {}

    # ── 1. 日K ──
    old_d = read_existing("MU_日K")
    s_d = default_start if args.full else incremental_start(old_d, "日期", default_start)
    print(f"[1/3] 日K  已有 {len(old_d)} 条 → 增量区间 {s_d} ~ {end}")
    if s_d > end:
        print("      已是最新，无需更新")
        new_d = pd.DataFrame()
    else:
        new_d = fetch_daily(s_d, end)
        print(f"      拉取 {len(new_d)} 条")
    sheets["MU_日K"] = merge(old_d, new_d, ["日期"])

    if not args.days_only:
        # ── 2. 1分钟 ──
        old_1 = read_existing("MU_分时1min")
        s_1 = default_start if args.full else incremental_start(old_1, "时间", default_start)
        print(f"\n[2/3] 1 分钟分时  已有 {len(old_1)} 条 → 增量区间 {s_1} ~ {end}")
        if s_1 > end:
            print("      已是最新，无需更新")
            new_1 = pd.DataFrame()
        else:
            new_1 = fetch_minutes(s_1, end, 1)
            print(f"      拉取 {len(new_1)} 条")
        sheets["MU_分时1min"] = merge(old_1, new_1, ["时间"])

        # ── 3. 5分钟 ──
        old_5 = read_existing("MU_5min")
        s_5 = default_start if args.full else incremental_start(old_5, "时间", default_start)
        print(f"\n[3/3] 5 分钟K  已有 {len(old_5)} 条 → 增量区间 {s_5} ~ {end}")
        if s_5 > end:
            print("      已是最新，无需更新")
            new_5 = pd.DataFrame()
        else:
            new_5 = fetch_minutes(s_5, end, 5)
            print(f"      拉取 {len(new_5)} 条")
        sheets["MU_5min"] = merge(old_5, new_5, ["时间"])

    # 保留旧表（未参与更新的也写回）
    for name in ["MU_日K", "MU_分时1min", "MU_5min"]:
        if name in sheets:
            continue
        old = read_existing(name)
        if not old.empty:
            sheets[name] = old

    write_excel(sheets)

    print("\n── 摘要 ──")
    for name, df in sheets.items():
        print(f"  {name:<12} 共 {len(df):>6} 条")


if __name__ == "__main__":
    main()
