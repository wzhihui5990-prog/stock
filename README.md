# Stock 数据爬取工具集

基于 Python 的美股数据抓取工具，当前聚焦于 QQQ（纳斯达克100 ETF）的行情与期权数据。

---

## 项目结构

```
stock/
├── qqq/
│   ├── fetch_qqq.py                  # QQQ 股价 K 线数据（日线、1分钟、5分钟）
│   ├── fetch_qqq_options.py          # QQQ 当日期权链快照
│   ├── fetch_qqq_options_history.py  # QQQ 历史期权数据（Polygon.io）
│   └── data/                         # 输出的 Excel 文件
└── README.md
```

---

## 脚本说明

### 1. `fetch_qqq.py` — QQQ 股价 K 线

抓取 QQQ 近一个月的盘中数据，包含：

- 每日 K 线（开盘、收盘、最高、最低、成交量）
- 每分钟分时线（近 8 天，受 Yahoo Finance 限制）
- 每 5 分钟 K 线

输出文件：`data/QQQ_data.xlsx`（多 Sheet）

**依赖：**
```
pip install yfinance pandas openpyxl
```

**运行：**
```bash
python qqq/fetch_qqq.py
```

---

### 2. `fetch_qqq_options.py` — 当日期权链快照

抓取 QQQ 所有到期日的完整期权链（Call + Put），包含：

- 行权价、最新成交价、买卖价差
- 成交量、未平仓量（OI）
- 隐含波动率（IV）
- 当前底层股价、到期剩余天数（DTE）

> 注意：yfinance 只提供当日快照，无法获取历史期权价格。

输出文件：`data/QQQ_options_YYYY-MM-DD.xlsx`

**依赖：**
```
pip install yfinance pandas openpyxl
```

**运行：**
```bash
python qqq/fetch_qqq_options.py
```

---

### 3. `fetch_qqq_options_history.py` — 历史期权数据

通过 [Polygon.io](https://polygon.io) 免费 API 抓取 QQQ 近月到期合约的历史 OHLCV 数据。

可配置参数（脚本顶部）：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `LOOKBACK_DAYS` | 15 | 拉取最近多少天 |
| `MAX_DTE` | 3 | 只爬 DTE ≤ 此值的近月合约 |
| `STRIKE_RANGE` | 0.5% | 只爬当前股价 ±0.5% 以内的行权价 |

> 免费账号限制：5 次/分钟，脚本已自动限速。

**使用前准备：**
1. 前往 [https://polygon.io](https://polygon.io) 注册免费账号，获取 API Key
2. 将脚本中 `API_KEY` 替换为你的实际 Key

输出文件：`data/QQQ_options_history_起始日_结束日.xlsx`

**依赖：**
```
pip install requests pandas openpyxl
```

**运行：**
```bash
python qqq/fetch_qqq_options_history.py
```

---

## 环境要求

- Python 3.10+
- 依赖库：

```bash
pip install yfinance requests pandas openpyxl
```
