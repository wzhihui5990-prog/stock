# Stock 数据爬取与策略分析

基于 Python 的 QQQ 数据抓取、期权数据整理、策略回测与参数优化工具集。

## 当前目录结构

```
stock/
├── qqq/
│   ├── 1-qqq日K/
│   │   ├── update_qqq_market_data.py
│   │   ├── build_qqq_market_chart.py
│   │   └── data/
│   │       ├── qqq_market_data.xlsx
│   │       └── qqq_market_chart.html
│   ├── 2-qqq末日期权日K-上下3股价的期权合同-前一天末日期权的收盘价/
│   │   ├── update_qqq_0dte_options_offset3.py
│   │   ├── build_qqq_0dte_strategy_report.py
│   │   ├── optimize_qqq_0dte_params.py
│   │   └── data/
│   │       ├── qqq_0dte_options_offset3.xlsx
│   │       ├── qqq_0dte_strategy_report.html
│   │       └── qqq_0dte_param_optimization.csv
│   └── 3-qqq末日期权日K-上下4股价的期权合同-前一天末日期权的收盘价/
│       ├── update_qqq_0dte_options_offset4.py
│       └── data/
│           └── qqq_0dte_options_offset4.xlsx
└── README.md
```

## 脚本职责

1. 市场数据
- update_qqq_market_data.py：增量拉取 QQQ 日K、1min、2min、5min 到 qqq_market_data.xlsx
- build_qqq_market_chart.py：读取 qqq_market_data.xlsx 生成 qqq_market_chart.html

2. 期权数据
- update_qqq_0dte_options_offset3.py：拉取 ±3 行权价偏移 0DTE 数据
- update_qqq_0dte_options_offset4.py：拉取 ±4 行权价偏移 0DTE 数据

3. 策略分析
- build_qqq_0dte_strategy_report.py：回测并生成可视化报告 qqq_0dte_strategy_report.html
- optimize_qqq_0dte_params.py：参数网格扫描并输出 qqq_0dte_param_optimization.csv

## 运行顺序

1. 更新市场数据
```bash
python qqq/1-qqq日K/update_qqq_market_data.py
```

2. 更新期权数据
```bash
python qqq/2-qqq末日期权日K-上下3股价的期权合同-前一天末日期权的收盘价/update_qqq_0dte_options_offset3.py
python qqq/3-qqq末日期权日K-上下4股价的期权合同-前一天末日期权的收盘价/update_qqq_0dte_options_offset4.py
```

3. 生成策略报告
```bash
python qqq/2-qqq末日期权日K-上下3股价的期权合同-前一天末日期权的收盘价/build_qqq_0dte_strategy_report.py
```

4. 参数优化（可选）
```bash
python qqq/2-qqq末日期权日K-上下3股价的期权合同-前一天末日期权的收盘价/optimize_qqq_0dte_params.py
```

## 环境

- Python 3.10+
- 依赖：

```bash
pip install yfinance requests pandas openpyxl
```
