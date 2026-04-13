# IWM 末日期权双买策略 — 运维手册

## 1. 策略说明

**策略类型**：0DTE（当日到期）双买期权（Long Straddle 变体）

**交易逻辑**：
- **T-1（建仓日）**：在 IWM 末日期权到期日前一天收盘前，以收盘价同时买入：
  - Call：行权价 = ceil(T-1 收盘价)（向上取整）或 round(T-1 收盘价) + 1
  - Put ：行权价 = floor(T-1 收盘价)（向下取整）或 round(T-1 收盘价) − 1
  - 可切换 ceil/floor 模式 或 ±1 模式
- **T（到期日）**：美东 09:30 开始监控 IWM 分钟价格
  - 若相对 T-1 收盘价涨幅 ≥ 上涨触发阈值，立即卖出双腿
  - 若相对 T-1 收盘价跌幅 ≥ 下跌触发阈值，立即卖出双腿
  - 若到平仓时间（默认 12:00，可调）仍未触发，以当时价格止损平仓

**手续费**：每张合约 $1.7，共 4 张次（买入 2 张 + 卖出 2 张），折合权利金 $0.068

---

## 2. 目录结构

```
D:\wzh\stock\IWM\
├── 1-iwm日K\
│   ├── update_iwm_market_data.py            # IWM 股价数据拉取（yfinance）
│   ├── build_iwm_market_chart.py            # IWM 股价图表生成
│   ├── iwm.md                               # 说明文档
│   └── data\
│       └── iwm_market_data.xlsx             # IWM 股价数据（日K/1min/2min/5min）
│
├── 2-iwm末日期权-offset0.5\
│   ├── update_iwm_0dte_options_offset05.py  # 期权数据拉取（Polygon.io，ceil/floor）
│   ├── build_iwm_0dte_strategy_report.py    # 回测 + HTML 报告生成
│   ├── optimize_iwm_0dte_params.py          # 参数扫描（暴力枚举最优阈值）
│   ├── ops_iwm_0dte_strategy.md             # 本文档
│   └── data\
│       ├── iwm_0dte_options_offset05.xlsx   # ceil/floor 期权数据（摘要/Call_1min/Put_1min）
│       ├── iwm_0dte_strategy_report.html    # 回测可视化报告
│       └── iwm_0dte_param_optimization.csv  # 参数扫描完整结果
│
├── 3-iwm末日期权-offset1\
│   ├── update_iwm_0dte_options_offset1.py   # 期权数据拉取（Polygon.io，±1）
│   └── data\
│       └── iwm_0dte_options_offset1.xlsx    # ±1 期权数据
│
└── run_iwm_pipeline.py                      # 一键运行管道
```

---

## 3. 数据文件说明

### iwm_market_data.xlsx（股价数据）

| Sheet | 内容 | 粒度 |
|-------|------|------|
| IWM_日K | 日K线 | 每日 |
| IWM_分时1min | 1分钟K线 | 1min |
| IWM_分时2min | 2分钟K线 | 2min |
| IWM_5min | 5分钟K线 | 5min |

关键列：`时间`、`开盘价`、`最高价`、`最低价`、`收盘价`、`成交量`

### iwm_0dte_options_offset05.xlsx / iwm_0dte_options_offset1.xlsx（期权数据）

| Sheet | 内容 |
|-------|------|
| 摘要 | 每个到期日的合约基本信息 + T-1 收盘价 |
| Call_1min | Call 合约 1min K线 |
| Put_1min | Put 合约 1min K线 |

摘要关键列：`到期日(T1)`、`基准日(T2)`、`IWM_T2收盘`、`Call合约`、`Put合约`、`Call_T2收盘`、`Put_T2收盘`

期权 K 线关键列：`到期日`、`时间(美东)`、`收盘价`

> 数据来源：Polygon.io 免费 API，限速 3 次/分钟，增量追加模式

---

## 4. 运行流程

### 4.0 一键入口（推荐）

在 `IWM` 根目录执行：

```powershell
Set-Location "D:\wzh\stock\IWM"
& "d:\python3.10\python.exe" run_iwm_pipeline.py
```

### 4.1 更新数据（每周/每次交易日后）

**步骤 1 — 更新 IWM 股价：**
```powershell
Set-Location "D:\wzh\stock\IWM\1-iwm日K"
& "d:\python3.10\python.exe" update_iwm_market_data.py
```

**步骤 2 — 更新 ceil/floor 期权数据：**
```powershell
Set-Location "D:\wzh\stock\IWM\2-iwm末日期权-offset0.5"
& "d:\python3.10\python.exe" update_iwm_0dte_options_offset05.py
```

**步骤 3 — 更新 ±1 期权数据：**
```powershell
Set-Location "D:\wzh\stock\IWM\3-iwm末日期权-offset1"
& "d:\python3.10\python.exe" update_iwm_0dte_options_offset1.py
```

> 三个脚本均为增量模式：自动读取已有 Excel，从最后一条记录日期往后续拉，不重复下载历史数据。

### 4.2 生成回测报告

```powershell
Set-Location "D:\wzh\stock\IWM\2-iwm末日期权-offset0.5"
& "d:\python3.10\python.exe" build_iwm_0dte_strategy_report.py
```

输出：`data/iwm_0dte_strategy_report.html`，直接用浏览器打开即可。

### 4.3 生成行情图表

```powershell
Set-Location "D:\wzh\stock\IWM\1-iwm日K"
& "d:\python3.10\python.exe" build_iwm_market_chart.py
```

输出：`data/iwm_market_chart.html`

### 4.4 运行参数优化（可选，新增大量数据后重跑）

```powershell
Set-Location "D:\wzh\stock\IWM\2-iwm末日期权-offset0.5"
& "d:\python3.10\python.exe" optimize_iwm_0dte_params.py
```

- 扫描范围：上涨触发 0.5%～5.0%（步长 0.25）× 下跌触发同上 × 平仓时间 11 档（10:00～15:00）
- 共约 7942 组，约 1～2 分钟
- 输出：`data/iwm_0dte_param_optimization.csv`，终端打印 Top 20

---

## 5. 回测报告使用说明（iwm_0dte_strategy_report.html）

### 顶部控制栏

| 控件 | 说明 |
|------|------|
| ceil/floor / ±1 行权价 | 切换数据集，不重新拉数据，直接在浏览器内重算 |
| 上涨触发 % | IWM 相对 T-1 收盘涨幅达到此值时触发卖出 |
| 下跌触发 % | IWM 相对 T-1 收盘跌幅达到此值时触发卖出 |
| 平仓时间 | 未触发时的强制止损时间（范围 09:35～15:00） |
| 手续费 $/张 | 单张合约手续费，共计算 4 张次 |
| ▶ 重新计算 | 应用上述参数，实时重算所有日期的触发点和盈亏 |

### 统计卡片

交易天数、胜率、盈利/亏损天数、触发次数、累计盈亏、总投入成本、平均每日盈亏

### 累计盈亏曲线

鼠标悬停显示当日盈亏 + 截止该日累计盈亏的浮窗提示

### 每日明细表

点击任意行展开该日 3 张 K 线图（IWM / Call / Put），图中标注：
- 黄色竖线：触发卖出时刻
- 灰色横线：T-1 收盘价基准线
- 蓝色横线：T 当日开盘价
- 红/绿横虚线：上涨/下跌触发阈值线

---

## 6. 关键配置项（build_iwm_0dte_strategy_report.py）

```python
TRIGGER_PCT   = 1.5     # 默认触发阈值（HTML 初始值，可在页面调整）
COMMISSION    = 1.7     # 每张合约手续费（美元）
MONITOR_START = "09:30" # 监控开始时间（固定，不可通过页面改）
MONITOR_END   = "12:00" # 默认平仓时间（可通过页面调整）
```

---

## 7. 依赖环境

- **Python**：3.10（`d:\python3.10\python.exe`）
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
| K 线显示"5min"粒度 | 该日 1min/2min 数据缺失，自动降级 | 检查 iwm_market_data.xlsx 对应日期 |
| 页面全黑 | HTML 内 `</style>` 标签缺失 | 重新运行 build_iwm_0dte_strategy_report.py |
| 重新计算无反应 | JS 变量声明顺序错误（TDZ） | 重新运行 build_iwm_0dte_strategy_report.py |
| 触发时间显示异常 | 平仓时间输入超出 09:35～15:00 | 检查页面平仓时间输入框 |
