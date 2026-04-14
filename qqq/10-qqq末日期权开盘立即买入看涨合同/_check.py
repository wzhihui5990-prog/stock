import pandas as pd, os
opt3 = os.path.join(os.path.dirname(__file__), "..", "2-qqq末日期权日K-上下3股价的期权合同-前一天末日期权的收盘价", "data", "qqq_0dte_options_offset3.xlsx")
summary = pd.read_excel(opt3, sheet_name="摘要")
qqq_file = os.path.join(os.path.dirname(__file__), "..", "1-qqq日K", "data", "qqq_market_data.xlsx")
qqq_1m = pd.read_excel(qqq_file, sheet_name="QQQ_分时1min")
call_1m = pd.read_excel(opt3, sheet_name="Call_1min")
call_1m["time_only"] = call_1m["时间(美东)"].astype(str).str[-5:]

print("=== 摘要数据（全部） ===")
for _, r in summary.iterrows():
    t1 = str(r["到期日(T1)"])[:10]
    t2_close = r["QQQ_T2收盘"]
    call_contract = r["Call合约"]
    # 解析行权价
    strike_str = call_contract.split("C")[-1] if "C" in call_contract else "?"
    strike = int(strike_str) / 1000 if strike_str.isdigit() else "?"
    
    # 找QQQ开盘价
    qqq_day = qqq_1m[qqq_1m["时间"].astype(str).str.startswith(t1)]
    qqq_open = float(qqq_day.iloc[0]["收盘价"]) if not qqq_day.empty else None
    
    # 找Call开盘价
    c_day = call_1m[(call_1m["到期日"].astype(str).str[:10] == t1) & (call_1m["time_only"] == "09:30")]
    call_open = float(c_day.iloc[0]["开盘价"]) if not c_day.empty else None
    
    gap = round(qqq_open - t2_close, 2) if qqq_open else None
    itm = round(qqq_open - strike, 2) if qqq_open and isinstance(strike, float) else None
    
    print(f"{t1}  T-1收盘=${t2_close}  T开盘=${qqq_open}  跳空={gap}  行权价=${strike}  ITM={itm}  Call开盘价=${call_open}  合约={call_contract}")
