# -*- coding: utf-8 -*-
"""
MU（美光科技）末日期权 开盘 ±20 / ±30 双买 —— 历史数据批量拉取（增量追加模式）

策略对应：T（到期日）在「锚点时刻」的股价 ± N 美元确定行权价，
         同时买入 Call 与 Put 各 1 张。
         锚点时刻默认 09:30（开盘价）；用 --anchor 09:45 可改用 09:45 的价格做锚点。
         注意：换锚点 = 换行权价 = 换合约，必须重新拉取，无法复用。

与 QQQ 版的差异（MU 特有）：
  1. MU 的 0DTE 只有「周一 / 周三 / 周五」有到期（QQQ 每天都有）
     → 到期日直接取 Polygon 合约表里 MU 真实存在的到期日，不假设星期
  2. MU 行权价是「$5 一档」
     → 基准行权价 = round(锚点价 / 5) * 5

数据来源：Polygon.io（免费档 5 次/分钟，数据延迟约 1 个交易日）
输出（每个偏移一个文件，与 QQQ 结构一致）：
  data/mu_0dte_options_open_offset20.xlsx          锚点 09:30
  data/mu_0dte_options_open_offset30.xlsx          锚点 09:30
  data/mu_0dte_options_open_a0945_offset20.xlsx    锚点 09:45（--anchor 09:45）
  data/mu_0dte_options_open_a0945_offset30.xlsx    锚点 09:45（--anchor 09:45）
     Sheet《摘要》    ：每个到期日一行（合约信息 + T2/T1 日K）
     Sheet《Call_1min》：Call 腿 T 日 1 分钟数据
     Sheet《Put_1min》 ：Put  腿 T 日 1 分钟数据
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
OFFSETS      = [20, 30]          # 行权价偏移（美元）：±20 / ±30
STRIKE_STEP  = 5                 # MU 行权价档位（美元）
ANCHOR_DEFAULT = "09:30"         # 默认锚点时刻（09:30 = 开盘价）
START_DATE   = date(2026, 7, 1)   # MU 周一/周三 0DTE 从 7 月才有，故只拉 7 月起
RATE_LIMIT   = 5                 # Polygon 免费档：5 次/分钟
CHECKPOINT_EVERY = 10            # 每处理 N 个到期日落盘一次

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.path.join(BASE_DIR, "data")
MARKET_FILE  = os.path.join(BASE_DIR, "..", "1-mu日K", "data", "mu_market_data.xlsx")
BASE_URL     = "https://api.polygon.io"

os.makedirs(DATA_DIR, exist_ok=True)


def output_file(offset, anchor=ANCHOR_DEFAULT):
    """锚点 09:30 沿用旧文件名（向后兼容）；其他锚点加时间后缀，互不覆盖。"""
    tag = "" if anchor == ANCHOR_DEFAULT else f"_a{anchor.replace(':', '')}"
    return os.path.join(DATA_DIR, f"mu_0dte_options_open{tag}_offset{offset}.xlsx")


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
    p = dict(params or {})
    if "apiKey" not in p:
        p["apiKey"] = API_KEY
    for attempt in range(retries):
        limiter.wait()
        try:
            r = requests.get(url, params=p, timeout=30)
        except requests.RequestException as e:
            if attempt == retries - 1:
                print(f"      请求失败: {e}")
                return {}
            continue
        if r.status_code == 429:
            print("      触发限速，等待 65 秒 ...")
            time.sleep(65)
            continue
        if r.status_code == 403:
            return {"_403": True}
        if r.status_code >= 500:
            time.sleep(5)
            continue
        if r.status_code != 200:
            return {}
        try:
            return r.json()
        except ValueError:
            return {}
    return {}


def paginate(url, params=None):
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
# 交易日 / 到期日
# ────────────────────────────────────────────────
def load_market():
    """读取 MU 日K，返回 (交易日列表, 开盘价映射)"""
    if not os.path.exists(MARKET_FILE):
        raise SystemExit(f"未找到行情文件：{MARKET_FILE}\n请先运行 1-mu日K/update_mu_market_data.py")
    df = pd.read_excel(MARKET_FILE, sheet_name="MU_日K", dtype=str)
    df = df.dropna(subset=["日期"]).sort_values("日期").reset_index(drop=True)
    days = [{"date": str(r["日期"])[:10],
             "open": float(r["开盘价"]),
             "close": float(r["收盘价"])} for _, r in df.iterrows()]
    return days


MARKET_1MIN_SHEET = "MU_分时1min"


def load_anchor_prices(anchor):
    """返回 {交易日: 锚点价}。

    anchor == "09:30" → 返回 None（调用方直接用日K 开盘价，省一次读盘）
    其他时刻        → 从《MU_分时1min》取该分钟的收盘价
    """
    if anchor == ANCHOR_DEFAULT:
        return None
    if not os.path.exists(MARKET_FILE):
        raise SystemExit(f"未找到行情文件：{MARKET_FILE}\n请先运行 1-mu日K/update_mu_market_data.py")
    df = pd.read_excel(MARKET_FILE, sheet_name=MARKET_1MIN_SHEET, dtype=str)
    df = df.dropna(subset=["时间"])
    df["_d"] = df["时间"].astype(str).str[:10]
    df["_t"] = df["时间"].astype(str).str[-5:]
    df["_c"] = pd.to_numeric(df["收盘价"], errors="coerce")
    sub = df[(df["_t"] == anchor) & df["_c"].notna()]
    out = dict(zip(sub["_d"], sub["_c"].astype(float)))
    print(f"  锚点 {anchor}：从《{MARKET_1MIN_SHEET}》取到 {len(out)} 个交易日")
    return out


def get_expiries(start, end):
    """从 Polygon 合约表取 MU 真实存在的到期日（按月分页，避免遗漏）。"""
    exps = set()
    cur = date(start.year, start.month, 1)
    while cur <= end:
        nxt = date(cur.year + (1 if cur.month == 12 else 0),
                   1 if cur.month == 12 else cur.month + 1, 1)
        lo = max(cur, start)
        hi = min(nxt - timedelta(days=1), end)
        if lo <= hi:
            res = paginate(f"{BASE_URL}/v3/reference/options/contracts", {
                "underlying_ticker": SYMBOL,
                "contract_type": "call",
                "expired": "true",
                "expiration_date.gte": lo.isoformat(),
                "expiration_date.lte": hi.isoformat(),
                "limit": 1000,
            })
            if res:
                for r in res:
                    exps.add(r["expiration_date"])
        cur = nxt
    return sorted(exps)


# ────────────────────────────────────────────────
# 合约行情
# ────────────────────────────────────────────────
def build_ticker(expiration, option_type, strike):
    exp_str = datetime.strptime(expiration, "%Y-%m-%d").strftime("%y%m%d")
    cp = "C" if option_type == "call" else "P"
    return f"O:{SYMBOL}{exp_str}{cp}{int(round(strike * 1000)):08d}"


def fetch_daily(ticker, start, end):
    """区间日K；返回 {日期: {开,高,低,收,量}}。一次调用即可覆盖合约生命周期。"""
    res = paginate(f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}",
                   {"adjusted": "true", "sort": "asc", "limit": 50000})
    if not res:
        return {}
    out = {}
    for r in res:
        d = datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).astimezone(EASTERN)
        out[d.strftime("%Y-%m-%d")] = {
            "开盘价": round(r.get("o", 0), 4),
            "最高价": round(r.get("h", 0), 4),
            "最低价": round(r.get("l", 0), 4),
            "收盘价": round(r.get("c", 0), 4),
            "成交量": int(r.get("v", 0)),
        }
    return out


def fetch_1min(ticker, day):
    res = paginate(f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/minute/{day}/{day}",
                   {"adjusted": "true", "sort": "asc", "limit": 50000})
    if not res:
        return []
    rows = []
    for r in res:
        dt_et = datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).astimezone(EASTERN)
        hhmm = dt_et.strftime("%H:%M")
        if not ("09:30" <= hhmm <= "16:00"):
            continue
        rows.append({
            "到期日":     day,
            "时间(美东)": dt_et.strftime("%Y-%m-%d %H:%M"),
            "开盘价":     round(r.get("o", 0), 4),
            "最高价":     round(r.get("h", 0), 4),
            "最低价":     round(r.get("l", 0), 4),
            "收盘价":     round(r.get("c", 0), 4),
            "成交量":     int(r.get("v", 0)),
        })
    return rows


# ────────────────────────────────────────────────
# Excel
# ────────────────────────────────────────────────
def read_existing(path, sheet):
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_excel(path, sheet_name=sheet, dtype=str).dropna(how="all").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def write_excel(path, sheets):
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, df in sheets.items():
            if df is None or df.empty:
                continue
            df.to_excel(writer, sheet_name=name[:31], index=False)
            ws = writer.sheets[name[:31]]
            for col_cells in ws.columns:
                w = max(len(str(c.value)) if c.value is not None else 0 for c in col_cells)
                ws.column_dimensions[col_cells[0].column_letter].width = min(w + 4, 40)


def merge(old, new, keys):
    if old.empty:
        return new
    if new.empty:
        return old
    combined = pd.concat([old, new], ignore_index=True)
    return combined.drop_duplicates(subset=keys).sort_values(keys).reset_index(drop=True)


def _checkpoint(path, acc_sum, acc_call, acc_put):
    """把累计结果合并进 Excel 并落盘（支持中断续拉）。"""
    s = merge(read_existing(path, "摘要"), pd.DataFrame(acc_sum), ["到期日(T1)"]) if acc_sum else read_existing(path, "摘要")
    c = merge(read_existing(path, "Call_1min"), pd.DataFrame(acc_call), ["到期日", "时间(美东)"]) if acc_call else read_existing(path, "Call_1min")
    p = merge(read_existing(path, "Put_1min"), pd.DataFrame(acc_put), ["到期日", "时间(美东)"]) if acc_put else read_existing(path, "Put_1min")
    write_excel(path, {"摘要": s, "Call_1min": c, "Put_1min": p})
    return s, c, p


# ────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="只处理区间内前 N 个到期日（调试/快速试跑）")
    ap.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD")
    ap.add_argument("--anchor", default=ANCHOR_DEFAULT,
                    help=f"锚点时刻 HH:MM（默认 {ANCHOR_DEFAULT} = 开盘价）")
    ap.add_argument("--offset", type=int, default=None,
                    help=f"只拉某个偏移 {OFFSETS}，默认两个都拉")
    args = ap.parse_args()

    if len(args.anchor) != 5 or args.anchor[2] != ":":
        raise SystemExit(f"--anchor 格式应为 HH:MM，收到 {args.anchor!r}")
    if args.offset is not None and args.offset not in OFFSETS:
        raise SystemExit(f"--offset 只能是 {OFFSETS} 之一，收到 {args.offset}")

    start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else START_DATE

    market = load_market()
    trading_days = [d["date"] for d in market]
    open_map = {d["date"]: d["open"] for d in market}
    close_map = {d["date"]: d["close"] for d in market}
    last_avail = trading_days[-1]
    print(f"=== MU 0DTE 期权数据  锚点 {args.anchor}  起始 {start}  "
          f"最新可用交易日 {last_avail} ===\n")

    anchor_map = load_anchor_prices(args.anchor) or open_map
    missing = [d for d in trading_days if d not in anchor_map]
    if missing:
        print(f"  ⚠ 锚点 {args.anchor} 缺开盘价的交易日 {len(missing)} 个（会跳过）："
              f"{missing[:5]}{' ...' if len(missing) > 5 else ''}")

    offsets = [args.offset] if args.offset else OFFSETS

    print("获取 MU 到期日列表 ...")
    exp_file = os.path.join(DATA_DIR, "mu_expiries.txt")
    end_d = datetime.strptime(last_avail, "%Y-%m-%d").date()

    cached = []
    if os.path.exists(exp_file):
        with open(exp_file, encoding="utf-8") as f:
            cached = [ln.strip() for ln in f if ln.strip()]
        print(f"  缓存中已有 {len(cached)} 个到期日")

    # 只补充发现「缓存最大日期之后」的到期日，避免每次都重新全量发现
    disc_from = max(start, datetime.strptime(max(cached), "%Y-%m-%d").date()) if cached else start
    if disc_from <= end_d:
        print(f"  增量发现 {disc_from} ~ {end_d} ...")
        found = get_expiries(disc_from, end_d)
        all_exps = sorted(set(cached) | set(found))
        with open(exp_file, "w", encoding="utf-8") as f:
            f.write("\n".join(all_exps))
        print(f"  新发现 {len(all_exps) - len(cached)} 个；合计 {len(all_exps)} 个（已缓存）")
    else:
        all_exps = cached
        print("  已是最新，无需重新发现")

    exps = [e for e in all_exps if start.isoformat() <= e <= last_avail]
    print(f"  区间内 {len(exps)} 个到期日（真实到期日，含周一/三/五）\n")

    # 目标：到期日 T <= 最新可用交易日；且 T 必须是交易日
    exps = [e for e in exps if e in set(trading_days)]
    if args.limit:
        exps = exps[:args.limit]
        print(f"  --limit 生效，仅处理区间内前 {len(exps)} 个: {exps[0]} ~ {exps[-1]}\n")

    for offset in offsets:
        path = output_file(offset, args.anchor)
        old_sum = read_existing(path, "摘要")
        done = set(old_sum["到期日(T1)"].astype(str).str[:10]) if not old_sum.empty else set()
        todo = [e for e in exps if e not in done]
        print(f"── 偏移 ±{offset}  已有 {len(done)} 天，待处理 {len(todo)} 天 ──")
        if not todo:
            print("   已是最新，跳过\n")
            continue

        new_sum, new_call, new_put = [], [], []
        for i, t1 in enumerate(todo, 1):
            idx = trading_days.index(t1)
            if idx == 0:
                continue
            t2 = trading_days[idx - 1]
            mu_open = open_map[t1]
            anchor_px = anchor_map.get(t1)
            if anchor_px is None or anchor_px != anchor_px:      # None 或 NaN
                print(f"  T1={t1}  跳过：锚点 {args.anchor} 无价格")
                continue

            base = round(anchor_px / STRIKE_STEP) * STRIKE_STEP
            call_strike = base + offset
            put_strike = base - offset
            call_ticker = build_ticker(t1, "call", call_strike)
            put_ticker = build_ticker(t1, "put", put_strike)

            print(f"  T1={t1}  锚点[{args.anchor}]=${anchor_px:.2f}  基准={base}  "
                  f"Call=${call_strike}  Put=${put_strike}")

            # 日K：区间 [T1-4天, T1] 即可覆盖 T2 与 T1
            d_lo = (datetime.strptime(t1, "%Y-%m-%d").date() - timedelta(days=5)).isoformat()
            call_d = fetch_daily(call_ticker, d_lo, t1)
            put_d = fetch_daily(put_ticker, d_lo, t1)
            call_1m = fetch_1min(call_ticker, t1)
            put_1m = fetch_1min(put_ticker, t1)

            cd1, cd2 = call_d.get(t1, {}), call_d.get(t2, {})
            pd1, pd2 = put_d.get(t1, {}), put_d.get(t2, {})

            print(f"      Call T1开盘={cd1.get('开盘价')} 收盘={cd1.get('收盘价')} 1min={len(call_1m)}条 | "
                  f"Put T1开盘={pd1.get('开盘价')} 收盘={pd1.get('收盘价')} 1min={len(put_1m)}条")

            new_sum.append({
                "到期日(T1)": t1, "基准日(T2)": t2,
                "锚点时间": args.anchor, "锚点价": round(float(anchor_px), 4),
                "MU_T1开盘": mu_open, "MU_T1收盘": close_map[t1], "MU_T2收盘": close_map[t2],
                "基准行权价": base,
                "Call合约": call_ticker, "Call行权价": call_strike,
                "Call_T2收盘": cd2.get("收盘价"),
                "Call_T1开盘": cd1.get("开盘价"), "Call_T1最高": cd1.get("最高价"),
                "Call_T1最低": cd1.get("最低价"), "Call_T1收盘": cd1.get("收盘价"),
                "Call_T1成交量": cd1.get("成交量"),
                "Put合约": put_ticker, "Put行权价": put_strike,
                "Put_T2收盘": pd2.get("收盘价"),
                "Put_T1开盘": pd1.get("开盘价"), "Put_T1最高": pd1.get("最高价"),
                "Put_T1最低": pd1.get("最低价"), "Put_T1收盘": pd1.get("收盘价"),
                "Put_T1成交量": pd1.get("成交量"),
                "Call_1min条数": len(call_1m), "Put_1min条数": len(put_1m),
            })
            new_call.extend(call_1m)
            new_put.extend(put_1m)

            if i % CHECKPOINT_EVERY == 0:
                s, c, p = _checkpoint(path, new_sum, new_call, new_put)
                print(f"      💾 检查点：摘要 {len(s)} 行 / Call {len(c)} 条 / Put {len(p)} 条")
                new_sum, new_call, new_put = [], [], []

        sum_final, call_final, put_final = _checkpoint(path, new_sum, new_call, new_put)
        print(f"   ✅ {os.path.basename(path)}: 摘要 {len(sum_final)} 行, "
              f"Call_1min {len(call_final)} 条, Put_1min {len(put_final)} 条\n")

    print("完成。")


if __name__ == "__main__":
    main()
