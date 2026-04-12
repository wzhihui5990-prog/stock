**`1-qqq日K` 的功能：**

这个文件夹实现了 **QQQ（纳斯达克100 ETF）行情数据获取 + 可视化图表生成** 的完整流程，分两步：

**第一步：update_qqq_market_data.py — 数据爬取（增量追加模式）**
- 使用 `yfinance` 从 Yahoo Finance 拉取 QQQ 行情数据
- 首次运行从 `START_DATE`（2026-02-13）开始全量下载；后续运行自动检测已有数据的最后日期，只下载新增部分并追加，历史数据永久保留
- 抓取四种粒度，保存至 `data/qqq_market_data.xlsx` 的对应 Sheet：

  | Sheet | 粒度 | Yahoo 保留限制 |
  |---|---|---|
  | QQQ_日K | 日线 | 不限 |
  | QQQ_分时1min | 1 分钟 | 最近 30 天 |
  | QQQ_分时2min | 2 分钟 | 最近 60 天 |
  | QQQ_5min | 5 分钟 | 最近 60 天 |

- 运行结束后打印更新摘要，显示各 Sheet 新增条数

**第二步：build_qqq_market_chart.py — 可视化生成**
- 读取 `qqq_market_data.xlsx`，计算技术指标：MA5/MA10/MA20、MACD（DIF/DEA/柱状图）
- 生成纯 HTML 交互图表，输出到 `data/qqq_market_chart.html`，含四个标签页：
  - **日K线**：完整历史日线 + 均线 + 成交量 + MACD
  - **1分钟 / 2分钟 / 5分钟**：按日期切换的盘中分时图

**运行方式（需先安装依赖）：**
```bash
pip install yfinance pandas openpyxl
```
```bash
# 在 1-qqq日K 目录下执行
python update_qqq_market_data.py       # 拉取/追加数据
python build_qqq_market_chart.py       # 生成图表
```
然后用浏览器打开 `data/qqq_market_chart.html` 查看。

**Python 路径说明（若 python 命令不可用）：**
```bash
C:\Users\wzh\AppData\Local\Programs\Python\Python313\python.exe update_qqq_market_data.py
```