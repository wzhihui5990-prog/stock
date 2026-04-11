"""
QQQ 期权链数据爬取脚本（基于 yfinance）

可获取：
  - 当前所有到期日的完整期权链（Call + Put）
  - 每个合约：行权价、最新成交价、买卖价、成交量、未平仓量、隐含波动率（IV）
  - 当前底层股价、期权到期前天数（DTE）

注意：yfinance 只提供当日快照；历史期权价格需付费API（Polygon.io/CBOE）
"""

import os
import yfinance as yf
import pandas as pd
from datetime import datetime, date

# ────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────
SYMBOL      = "QQQ"
OUTPUT_DIR  = "data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, f"{SYMBOL}_options_{date.today()}.xlsx")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ────────────────────────────────────────────────
# 爬取
# ────────────────────────────────────────────────
def fetch_options(symbol: str) -> dict[str, pd.DataFrame]:
    ticker = yf.Ticker(symbol)

    # 当前股价
    info       = ticker.fast_info
    spot_price = round(info.last_price, 2)
    today      = date.today()
    print(f"\n{symbol} 当前价格：${spot_price}")

    # 所有可用到期日
    expirations = ticker.options
    print(f"可用到期日数量：{len(expirations)} 个")
    print(f"最近到期：{expirations[0]}  最远到期：{expirations[-1]}\n")

    all_calls = []
    all_puts  = []

    for exp in expirations:
        exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
        dte      = (exp_date - today).days          # Days To Expiration

        try:
            chain = ticker.option_chain(exp)
        except Exception as e:
            print(f"  ⚠ {exp} 获取失败: {e}")
            continue

        print(f"  {exp} (DTE={dte:>4})  calls={len(chain.calls):>4}  puts={len(chain.puts):>4}")

        for df, opt_type in [(chain.calls, "Call"), (chain.puts, "Put")]:
            if df.empty:
                continue
            df = df.copy()
            df.insert(0, "OptionType", opt_type)
            df.insert(1, "Expiration", exp)
            df.insert(2, "DTE",        dte)
            df.insert(3, "SpotPrice",  spot_price)
            # 计算价内/价外
            if opt_type == "Call":
                df.insert(4, "Moneyness", df["strike"].apply(
                    lambda k: "ITM" if k < spot_price else ("ATM" if k == spot_price else "OTM")
                ))
            else:
                df.insert(4, "Moneyness", df["strike"].apply(
                    lambda k: "ITM" if k > spot_price else ("ATM" if k == spot_price else "OTM")
                ))
            if opt_type == "Call":
                all_calls.append(df)
            else:
                all_puts.append(df)

    def merge_and_clean(frames: list) -> pd.DataFrame:
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        # 保留关键列（顺序友好）
        priority_cols = [
            "OptionType", "Expiration", "DTE", "SpotPrice", "Moneyness",
            "strike", "lastPrice", "bid", "ask", "change", "percentChange",
            "volume", "openInterest", "impliedVolatility",
            "inTheMoney", "contractSymbol", "lastTradeDate",
        ]
        ordered = [c for c in priority_cols if c in df.columns]
        rest    = [c for c in df.columns if c not in ordered]
        df = df[ordered + rest]

        # 格式化 IV 为百分比
        if "impliedVolatility" in df.columns:
            df["impliedVolatility"] = (df["impliedVolatility"] * 100).round(2)

        # 格式化 lastTradeDate
        if "lastTradeDate" in df.columns:
            df["lastTradeDate"] = pd.to_datetime(df["lastTradeDate"]).dt.strftime("%Y-%m-%d %H:%M")

        # 数值精度
        for col in ["lastPrice", "bid", "ask", "change"]:
            if col in df.columns:
                df[col] = df[col].round(2)
        if "percentChange" in df.columns:
            df["percentChange"] = df["percentChange"].round(2)

        # 列名中文化
        df.rename(columns={
            "OptionType":        "期权类型",
            "Expiration":        "到期日",
            "DTE":               "剩余天数",
            "SpotPrice":         "标的现价",
            "Moneyness":         "价内外",
            "strike":            "行权价",
            "lastPrice":         "最新成交价",
            "bid":               "买价",
            "ask":               "卖价",
            "change":            "涨跌额",
            "percentChange":     "涨跌幅(%)",
            "volume":            "成交量",
            "openInterest":      "未平仓量",
            "impliedVolatility": "隐含波动率(%)",
            "inTheMoney":        "是否价内",
            "contractSymbol":    "合约代码",
            "lastTradeDate":     "最后成交时间",
        }, inplace=True)

        return df

    calls_df = merge_and_clean(all_calls)
    puts_df  = merge_and_clean(all_puts)

    # 汇总：Put-Call 持仓比（PCR）按到期日
    pcr_rows = []
    for exp in expirations:
        c_oi = calls_df.loc[calls_df["到期日"] == exp, "未平仓量"].sum() if not calls_df.empty else 0
        p_oi = puts_df.loc[puts_df["到期日"] == exp, "未平仓量"].sum()   if not puts_df.empty  else 0
        c_vol= calls_df.loc[calls_df["到期日"] == exp, "成交量"].sum()   if not calls_df.empty else 0
        p_vol= puts_df.loc[puts_df["到期日"] == exp, "成交量"].sum()     if not puts_df.empty  else 0
        dte  = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        pcr_rows.append({
            "到期日":          exp,
            "剩余天数":        dte,
            "认购未平仓量":    int(c_oi),
            "认沽未平仓量":    int(p_oi),
            "PCR持仓比":      round(p_oi / c_oi, 3) if c_oi else None,
            "认购成交量":      int(c_vol),
            "认沽成交量":      int(p_vol),
            "PCR成交比":      round(p_vol / c_vol, 3) if c_vol else None,
        })
    pcr_df = pd.DataFrame(pcr_rows)

    return {
        f"{symbol}_Call":    calls_df,
        f"{symbol}_Put":     puts_df,
        f"{symbol}_PCR汇总": pcr_df,
    }

# ────────────────────────────────────────────────
# 写入 Excel
# ────────────────────────────────────────────────
def write_excel(sheets: dict, filepath: str):
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            if df is None or df.empty:
                continue
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
            ws = writer.sheets[sheet_name[:31]]
            for col_cells in ws.columns:
                max_len = max(
                    (len(str(c.value)) if c.value is not None else 0) for c in col_cells
                )
                ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 3, 40)
    print(f"\n✅ 期权数据已写入：{os.path.abspath(filepath)}")

# ────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────
def main():
    print(f"爬取时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    sheets = fetch_options(SYMBOL)

    write_excel(sheets, OUTPUT_FILE)

    print("\n── 数据摘要 ──")
    for name, df in sheets.items():
        print(f"  {name:<25} {len(df):>6} 条")

if __name__ == "__main__":
    main()
