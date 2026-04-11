"""
QQQ 历史期权数据爬取（过去一个月）
数据来源：Polygon.io 免费 API

使用前：
  1. 去 https://polygon.io 注册免费账号，获取 API Key
  2. 将下方 API_KEY 替换为你的实际 Key
  3. 运行：d:\python3.10\python.exe fetch_qqq_options_history.py

免费账号限制：5次/分钟，脚本已自动限速

爬取范围（可调整）：
  - 只爬近月到期合约（DTE <= MAX_DTE 天）
  - 只爬行权价在当前价格 ±STRIKE_RANGE% 以内的合约
  - 每个合约取过去 LOOKBACK_DAYS 天的每日 OHLCV
"""

import os
import time
import requests
import pandas as pd
from datetime import datetime, date, timedelta

# ────────────────────────────────────────────────
# ⚠  必填：替换为你的 Polygon.io API Key
# ────────────────────────────────────────────────
API_KEY = "NuxofB1uXuHOGR4jRmby3pBT1FXQURZg"

# ────────────────────────────────────────────────
# 配置（可调整）
# ────────────────────────────────────────────────
SYMBOL        = "QQQ"
LOOKBACK_DAYS = 15          # 拉取最近多少天的历史
MAX_DTE       = 3           # 只爬 DTE <= 此值的近月合约
STRIKE_RANGE  = 0.005       # 只爬当前股价 ±0.5% 以内的行权价
RATE_LIMIT    = 3           # 每分钟最多请求次数（免费账号实测约3次/分钟）

OUTPUT_DIR  = "data"
TODAY       = date.today()
START_DATE  = TODAY - timedelta(days=LOOKBACK_DAYS)
OUTPUT_FILE = os.path.join(
    OUTPUT_DIR,
    f"{SYMBOL}_options_history_{START_DATE}_{TODAY}.xlsx"
)

os.makedirs(OUTPUT_DIR, exist_ok=True)

BASE_URL = "https://api.polygon.io"

# ────────────────────────────────────────────────
# 限速器
# ────────────────────────────────────────────────
class RateLimiter:
    def __init__(self, calls_per_min: int):
        self.interval = 60.0 / calls_per_min
        self._last    = 0.0

    def wait(self):
        elapsed = time.time() - self._last
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last = time.time()

limiter = RateLimiter(RATE_LIMIT)

# ────────────────────────────────────────────────
# API 请求封装（含重试）
# ────────────────────────────────────────────────
def api_get(url: str, params: dict = None, retries: int = 3) -> dict:
    if params is None:
        params = {}
    params["apiKey"] = API_KEY
    for attempt in range(retries):
        limiter.wait()
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 429:
                print("  ⚠ 触发限速，等待60秒...")
                time.sleep(60)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                print(f"  ✗ 请求失败：{url} — {e}")
                return {}
    return {}

# ────────────────────────────────────────────────
# 第一步：获取当前 QQQ 股价
# ────────────────────────────────────────────────
def get_spot_price() -> float:
    url  = f"{BASE_URL}/v2/last/trade/{SYMBOL}"
    data = api_get(url)
    try:
        price = data["results"]["p"]
        print(f"{SYMBOL} 参考价格：${price:.2f}")
        return float(price)
    except Exception:
        # fallback：用前一交易日收盘价
        url2  = f"{BASE_URL}/v2/aggs/ticker/{SYMBOL}/prev"
        data2 = api_get(url2)
        try:
            price = data2["results"][0]["c"]
            print(f"{SYMBOL} 参考价格（前收盘）：${price:.2f}")
            return float(price)
        except Exception:
            raise RuntimeError("无法获取当前股价，请检查 API Key 是否有效")

# ────────────────────────────────────────────────
# 第二步：列出符合条件的期权合约
# ────────────────────────────────────────────────
def list_contracts(spot: float) -> list[dict]:
    strike_lo = round(spot * (1 - STRIKE_RANGE), 0)
    strike_hi = round(spot * (1 + STRIKE_RANGE), 0)
    exp_max   = (TODAY + timedelta(days=MAX_DTE)).isoformat()

    print(f"\n筛选条件：行权价 ${strike_lo:.0f}~${strike_hi:.0f}，"
          f"到期日 <= {exp_max}，过去 {LOOKBACK_DAYS} 天内有交易")

    url     = f"{BASE_URL}/v3/reference/options/contracts"
    params  = {
        "underlying_ticker": SYMBOL,
        "strike_price.gte":  strike_lo,
        "strike_price.lte":  strike_hi,
        "expiration_date.lte": exp_max,
        "expiration_date.gte": TODAY.isoformat(),  # 未过期
        "limit": 250,
        "sort":  "expiration_date",
        "order": "asc",
    }

    contracts = []
    while True:
        data = api_get(url, params)
        results = data.get("results", [])
        contracts.extend(results)
        next_url = data.get("next_url")
        if not next_url or not results:
            break
        # 翻页
        url    = next_url
        params = {}   # next_url 已包含所有参数，只需加 apiKey（api_get 自动加）

    print(f"找到符合条件的合约：{len(contracts)} 个")
    return contracts

# ────────────────────────────────────────────────
# 第三步：获取单个合约的历史日线 OHLCV
# ────────────────────────────────────────────────
def fetch_contract_history(ticker: str) -> list[dict]:
    url = (
        f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day"
        f"/{START_DATE.isoformat()}/{TODAY.isoformat()}"
    )
    data = api_get(url, {"adjusted": "true", "sort": "asc", "limit": 50})
    rows = []
    for bar in data.get("results", []):
        rows.append({
            "日期":   datetime.utcfromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d"),
            "开盘价": round(bar.get("o", 0), 2),
            "最高价": round(bar.get("h", 0), 2),
            "最低价": round(bar.get("l", 0), 2),
            "收盘价": round(bar.get("c", 0), 2),
            "成交量": int(bar.get("v", 0)),
            "成交额": round(bar.get("vw", 0), 2),  # 成交量加权均价
        })
    return rows

# ────────────────────────────────────────────────
# 第四步：汇总所有合约历史
# ────────────────────────────────────────────────
def fetch_all_history(contracts: list[dict]) -> pd.DataFrame:
    total  = len(contracts)
    rows   = []
    empty  = 0

    for i, c in enumerate(contracts, 1):
        ticker     = c["ticker"]          # e.g. O:QQQ260417C00460000
        opt_type   = "认购(Call)" if c["contract_type"] == "call" else "认沽(Put)"
        strike     = c["strike_price"]
        expiration = c["expiration_date"]
        dte        = (datetime.strptime(expiration, "%Y-%m-%d").date() - TODAY).days

        print(f"  [{i:>3}/{total}] {ticker}  "
              f"{opt_type}  行权价=${strike}  到期={expiration}(DTE={dte})", end="")

        history = fetch_contract_history(ticker)
        if not history:
            print("  （无历史成交）")
            empty += 1
            continue

        print(f"  {len(history)} 天")
        for bar in history:
            rows.append({
                "合约代码":    ticker,
                "期权类型":    opt_type,
                "行权价":      strike,
                "到期日":      expiration,
                "剩余天数":    dte,
                **bar,
            })

    print(f"\n共获取 {len(rows)} 条历史记录，{empty} 个合约无历史成交")
    return pd.DataFrame(rows)

# ────────────────────────────────────────────────
# 写入 Excel
# ────────────────────────────────────────────────
def write_excel(df_call: pd.DataFrame, df_put: pd.DataFrame, filepath: str):
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        for sheet_name, df in [("认购期权_历史", df_call), ("认沽期权_历史", df_put)]:
            if df is None or df.empty:
                print(f"  ⚠ {sheet_name} 无数据，跳过")
                continue
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            ws = writer.sheets[sheet_name]
            for col_cells in ws.columns:
                max_len = max(
                    (len(str(c.value)) if c.value is not None else 0) for c in col_cells
                )
                ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 3, 40)
    print(f"\n✅ 历史期权数据已写入：{os.path.abspath(filepath)}")

# ────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────
def main():
    if API_KEY == "YOUR_POLYGON_API_KEY":
        print("❌ 请先在脚本顶部填入你的 Polygon.io API Key！")
        print("   注册地址：https://polygon.io（免费）")
        return

    # 直接指定两个合约（4.10 末日期权）
    target_date = "2026-04-01"
    contracts = [
        {"ticker": "O:QQQ260401C00582000", "contract_type": "call", "strike_price": 582, "expiration_date": target_date},
    ]

    print(f"爬取合约：{[c['ticker'] for c in contracts]}")
    print(f"历史日期：{START_DATE} ~ {TODAY}\n")

    df_all = fetch_all_history(contracts)

    if df_all.empty:
        print("无数据，退出")
        return

    df_call = df_all[df_all["期权类型"].str.startswith("认购")].reset_index(drop=True)
    df_put  = df_all[df_all["期权类型"].str.startswith("认沽")].reset_index(drop=True)

    write_excel(df_call, df_put, OUTPUT_FILE)

    print(f"\n── 数据摘要 ──")
    print(f"  认购期权_历史    {len(df_call):>6} 条")
    print(f"  认沽期权_历史    {len(df_put):>6} 条")

if __name__ == "__main__":
    main()
