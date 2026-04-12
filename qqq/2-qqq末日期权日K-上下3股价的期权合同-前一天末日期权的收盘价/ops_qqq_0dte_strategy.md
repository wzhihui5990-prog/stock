# QQQ 末日期权双买策略 — 运维手册

## 1. 策略说明

**策略类型**：0DTE（当日到期）双买期权（Long Straddle 变体）

**交易逻辑**：
- **T-1（建仓日）**：在 QQQ 末日期权到期日前一天收盘前，以收盘价同时买入：
  - Call：行权价 = round(T-1 收盘价 + N)
  - Put ：行权价 = round(T-1 收盘价 − N)
  - N 可配置为 3 或 4（对应两套数据）
- **T（到期日）**：美东 09:30 开始监控 QQQ 分钟价格
  - 若相对 T-1 收盘价涨幅 ≥ 上涨触发阈值，立即卖出双腿
  - 若相对 T-1 收盘价跌幅 ≥ 下跌触发阈值，立即卖出双腿
  - 若到平仓时间（默认 10:30，可调）仍未触发，以当时价格止损平仓

**手续费**：每张合约 $1.7，共 4 张次（买入 2 张 + 卖出 2 张），折合权利金 $0.068

**最优参数（参数扫描结论）**：
| 参数 | 推荐值 |
|------|--------|
| 行权价偏移 N | ±3 |
| 上涨触发阈值 | +2.00% |
| 下跌触发阈值 | −1.25% |
| 平仓时间 | 10:30 |
| 累计盈亏（39日） | +$1,063.8 |

---

## 2. 目录结构

```
D:\wzh\stock\qqq\
├── 1-qqq日K\
│   ├── update_qqq_market_data.py                  # QQQ 股价数据拉取（yfinance）
│   ├── build_qqq_market_chart.py             # QQQ 股价图表生成
│   └── data\
│       └── qqq_market_data.xlsx             # QQQ 股价数据（日K/1min/2min/5min）
│
├── 2-qqq末日期权日K-上下3股价的期权合同-前一天末日期权的收盘价\
│   ├── update_qqq_0dte_options_offset3.py # 期权数据拉取（Polygon.io，±3）
│   ├── build_qqq_0dte_strategy_report.py          # 回测 + HTML 报告生成
│   ├── optimize_qqq_0dte_params.py          # 参数扫描（暴力枚举最优阈值）
│   ├── ops_qqq_0dte_strategy.md      # 本文档
│   └── data\
│       ├── qqq_0dte_options_offset3.xlsx           # ±3 期权数据（摘要/Call_1min/Put_1min）
│       ├── qqq_0dte_strategy_report.html    # 回测可视化报告
│       └── qqq_0dte_param_optimization.csv      # 参数扫描完整结果
│
└── 3-qqq末日期权日K-上下4股价的期权合同-前一天末日期权的收盘价\
    ├── update_qqq_0dte_options_offset4.py # 期权数据拉取（Polygon.io，±4）
    └── data\
        └── qqq_0dte_options_offset4.xlsx           # ±4 期权数据
```

---

## 3. 数据文件说明

### qqq_market_data.xlsx（股价数据）

| Sheet | 内容 | 粒度 |
|-------|------|------|
| QQQ_日K | 日K线 | 每日 |
| QQQ_分时1min | 1分钟K线 | 1min |
| QQQ_分时2min | 2分钟K线 | 2min |
| QQQ_5min | 5分钟K线 | 5min |

关键列：`时间`、`开盘价`、`最高价`、`最低价`、`收盘价`、`成交量`

### qqq_0dte_options_offset3.xlsx / qqq_0dte_options_offset4.xlsx（期权数据）

| Sheet | 内容 |
|-------|------|
| 摘要 | 每个到期日的合约基本信息 + T-1 收盘价 |
| Call_1min | Call 合约 1min K线 |
| Put_1min | Put 合约 1min K线 |

摘要关键列：`到期日(T1)`、`基准日(T2)`、`QQQ_T2收盘`、`Call合约`、`Put合约`、`Call_T2收盘`、`Put_T2收盘`

期权 K 线关键列：`到期日`、`时间(美东)`、`收盘价`

> 数据来源：Polygon.io 免费 API，限速 3 次/分钟，增量追加模式

---

## 4. 运行流程

### 4.0 一键入口（推荐）

在 `qqq` 根目录执行：

```powershell
Set-Location "D:\wzh\stock\qqq"
& "C:\Users\wzh\AppData\Local\Programs\Python\Python313\python.exe" run_qqq_pipeline.py
```

可选参数：
- `--with-reports`：数据更新后，额外生成图表与策略报告
- `--with-optimize`：数据更新后，额外执行参数优化（耗时较长）

示例：

```powershell
& "C:\Users\wzh\AppData\Local\Programs\Python\Python313\python.exe" run_qqq_pipeline.py --with-reports
```

### 4.1 更新数据（每周/每次交易日后）

**步骤 1 — 更新 QQQ 股价：**
```powershell
Set-Location "D:\wzh\stock\qqq\1-qqq日K"
& "C:\Users\wzh\AppData\Local\Programs\Python\Python313\python.exe" update_qqq_market_data.py
```

**步骤 2 — 更新 ±3 期权数据：**
```powershell
Set-Location "D:\wzh\stock\qqq\2-qqq末日期权日K-上下3股价的期权合同-前一天末日期权的收盘价"
& "C:\Users\wzh\AppData\Local\Programs\Python\Python313\python.exe" update_qqq_0dte_options_offset3.py
```

**步骤 3 — 更新 ±4 期权数据：**
```powershell
Set-Location "D:\wzh\stock\qqq\3-qqq末日期权日K-上下4股价的期权合同-前一天末日期权的收盘价"
& "C:\Users\wzh\AppData\Local\Programs\Python\Python313\python.exe" update_qqq_0dte_options_offset4.py
```

> 三个脚本均为增量模式：自动读取已有 Excel，从最后一条记录日期往后续拉，不重复下载历史数据。

### 4.2 生成回测报告

```powershell
Set-Location "D:\wzh\stock\qqq\2-qqq末日期权日K-上下3股价的期权合同-前一天末日期权的收盘价"
& "C:\Users\wzh\AppData\Local\Programs\Python\Python313\python.exe" build_qqq_0dte_strategy_report.py
```

输出：`data/qqq_0dte_strategy_report.html`，直接用浏览器打开即可。

### 4.3 运行参数优化（可选，新增大量数据后重跑）

```powershell
Set-Location "D:\wzh\stock\qqq\2-qqq末日期权日K-上下3股价的期权合同-前一天末日期权的收盘价"
& "C:\Users\wzh\AppData\Local\Programs\Python\Python313\python.exe" optimize_qqq_0dte_params.py
```

- 扫描范围：上涨触发 0.5%～5.0%（步长 0.25）× 下跌触发同上 × 平仓时间 11 档（10:00～15:00）
- 共约 7942 组，约 1～2 分钟
- 输出：`data/qqq_0dte_param_optimization.csv`，终端打印 Top 20

---

## 5. 回测报告使用说明（qqq_0dte_strategy_report.html）

### 顶部控制栏

| 控件 | 说明 |
|------|------|
| ±3 / ±4 行权价 | 切换数据集，不重新拉数据，直接在浏览器内重算 |
| 上涨触发 % | QQQ 相对 T-1 收盘涨幅达到此值时触发卖出 |
| 下跌触发 % | QQQ 相对 T-1 收盘跌幅达到此值时触发卖出 |
| 平仓时间 | 未触发时的强制止损时间（范围 09:35～15:00） |
| 手续费 $/张 | 单张合约手续费，共计算 4 张次 |
| ▶ 重新计算 | 应用上述参数，实时重算所有日期的触发点和盈亏 |

### 统计卡片

交易天数、胜率、盈利/亏损天数、触发次数、累计盈亏、总投入成本、平均每日盈亏

### 累计盈亏曲线

鼠标悬停显示当日盈亏 + 截止该日累计盈亏的浮窗提示

### 每日明细表

点击任意行展开该日 3 张 K 线图（QQQ / Call / Put），图中标注：
- 黄色竖线：触发卖出时刻
- 灰色横线：T-1 收盘价基准线
- 蓝色横线：T 当日开盘价
- 红/绿横虚线：上涨/下跌触发阈值线

---

## 6. 关键配置项（build_qqq_0dte_strategy_report.py）

```python
TRIGGER_PCT   = 1.5     # 默认触发阈值（HTML 初始值，可在页面调整）
COMMISSION    = 1.7     # 每张合约手续费（美元）
MONITOR_START = "09:30" # 监控开始时间（固定，不可通过页面改）
MONITOR_END   = "12:00" # 默认平仓时间（可通过页面调整）
```

---

## 7. 依赖环境

- **Python**：3.13（`C:\Users\wzh\AppData\Local\Programs\Python\Python313\python.exe`）
- **核心包**：`pandas`、`openpyxl`、`yfinance`、`requests`
- **数据 API**：
  - 股价：Yahoo Finance（yfinance，免费，无需 Key）
  - 期权：Polygon.io（免费 Key，限速 3 次/分钟）

安装依赖：
```powershell
pip install pandas openpyxl yfinance requests
```

---

## 8. 常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| 期权数据某天空白 | Polygon 免费层当日数据未入库 | 隔天重跑 fetch 脚本 |
| K 线显示"5min"粒度 | 该日 1min/2min 数据缺失，自动降级 | 检查 qqq_market_data.xlsx 对应日期 |
| 页面全黑 | HTML 内 `</style>` 标签缺失 | 重新运行 build_qqq_0dte_strategy_report.py |
| 重新计算无反应 | JS 变量声明顺序错误（TDZ） | 重新运行 build_qqq_0dte_strategy_report.py |
| 触发时间显示异常 | 平仓时间输入超出 09:35～15:00 | 检查页面平仓时间输入框 |
