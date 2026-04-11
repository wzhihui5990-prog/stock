#!/usr/bin/env python3
"""
将 QQQ_data.xlsx 转换为可视化 HTML 页面（K线 + MA + 成交量 + MACD）
运行（在 stock/ 目录下）：python qqq/generate_chart.py
输出：qqq/data/QQQ_chart.html
"""

import os
import json
import pandas as pd

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR    = os.path.join(SCRIPT_DIR, "data")
INPUT_FILE  = os.path.join(DATA_DIR, "QQQ_data.xlsx")
OUTPUT_FILE = os.path.join(DATA_DIR, "QQQ_chart.html")


# ─────────────────────────────────────────────────────────────────────────────
# 技术指标计算
# ─────────────────────────────────────────────────────────────────────────────

def calc_ma(closes, n):
    result = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        vals = [v for v in closes[i - n + 1:i + 1] if v is not None]
        if len(vals) == n:
            result[i] = round(sum(vals) / n, 3)
    return result


def calc_ema(values, n):
    result = [None] * len(values)
    k = 2.0 / (n + 1)
    prev = None
    for i, v in enumerate(values):
        if v is None:
            continue
        if prev is None:
            result[i] = round(v, 4)
        else:
            result[i] = round(v * k + prev * (1 - k), 4)
        prev = result[i]
    return result


def calc_macd(closes, fast=12, slow=26, signal=9):
    ema_fast    = calc_ema(closes, fast)
    ema_slow    = calc_ema(closes, slow)
    macd_line   = [round(f - s, 4) if f is not None and s is not None else None
                   for f, s in zip(ema_fast, ema_slow)]
    signal_line = calc_ema(macd_line, signal)
    histogram   = [round(m - s, 4) if m is not None and s is not None else None
                   for m, s in zip(macd_line, signal_line)]
    return macd_line, signal_line, histogram


# ─────────────────────────────────────────────────────────────────────────────
# 数据加载 & 转换
# ─────────────────────────────────────────────────────────────────────────────

def load_sheet(xls, name):
    try:
        df = pd.read_excel(xls, sheet_name=name)
        return df.dropna(how="all").reset_index(drop=True)
    except Exception as e:
        print(f"  ⚠  Sheet '{name}' 读取失败: {e}")
        return pd.DataFrame()


def df_to_kdata(df):
    if df.empty:
        return {
            "dates": [], "ohlcv": [], "volumes": [],
            "ma5": [], "ma10": [], "ma20": [],
            "macd": [], "signal": [], "histogram": [],
        }
    cols   = df.columns.tolist()
    dates  = df[cols[0]].astype(str).tolist()
    opens  = [float(v) for v in df[cols[1]]]
    highs  = [float(v) for v in df[cols[2]]]
    lows   = [float(v) for v in df[cols[3]]]
    closes = [float(v) for v in df[cols[4]]]
    vols   = ([int(v) if pd.notna(v) else 0 for v in df[cols[5]]]
               if len(cols) > 5 else [0] * len(df))

    # ECharts candlestick: [open, close, low, high]
    ohlcv  = [[round(o, 3), round(c, 3), round(l, 3), round(h, 3)]
               for o, c, l, h in zip(opens, closes, lows, highs)]
    clr    = [round(c, 3) for c in closes]
    ml, sl, hl = calc_macd(clr)

    return {
        "dates":     dates,
        "ohlcv":     ohlcv,
        "volumes":   vols,
        "ma5":       calc_ma(clr, 5),
        "ma10":      calc_ma(clr, 10),
        "ma20":      calc_ma(clr, 20),
        "macd":      ml,
        "signal":    sl,
        "histogram": hl,
    }


def intraday_by_date(df):
    if df.empty:
        return {}
    cols = df.columns.tolist()
    df   = df.copy()
    df["_date"] = df[cols[0]].astype(str).str[:10]
    result = {}
    for date_str, group in df.groupby("_date", sort=True):
        result[date_str] = df_to_kdata(group.drop(columns=["_date"]).reset_index(drop=True))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# HTML 模板
# ─────────────────────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>QQQ 行情分析</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0e1117; color:#d0d7de; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }

/* ── Header ── */
header {
  background:#161b22; border-bottom:1px solid #30363d;
  padding:14px 24px; display:flex; align-items:center; gap:28px; flex-wrap:wrap;
}
.ticker { font-size:26px; font-weight:700; color:#58a6ff; letter-spacing:3px; }
.price  { font-size:26px; font-weight:600; color:#e6edf3; }
.change { font-size:14px; margin-top:2px; }
.meta   { font-size:11px; color:#8b949e; margin-top:3px; }
.cards  { display:flex; gap:10px; margin-left:auto; flex-wrap:wrap; }
.card   {
  background:#1c2128; border:1px solid #30363d; border-radius:8px;
  padding:7px 14px; text-align:center; min-width:80px;
}
.card-label { font-size:11px; color:#8b949e; }
.card-value { font-size:14px; font-weight:600; color:#e6edf3; margin-top:1px; }

/* ── Tabs ── */
.tabs {
  display:flex; background:#161b22;
  padding:0 24px; border-bottom:1px solid #30363d; gap:4px;
}
.tab {
  padding:10px 22px; cursor:pointer; font-size:14px; color:#8b949e;
  border-bottom:2px solid transparent; transition:all .2s; user-select:none;
}
.tab:hover { color:#e6edf3; }
.tab.active { color:#58a6ff; border-bottom-color:#58a6ff; }

/* ── Toolbar ── */
.toolbar {
  display:flex; align-items:center; gap:14px;
  padding:9px 24px; background:#0e1117; border-bottom:1px solid #21262d;
}
.toolbar label { font-size:13px; color:#8b949e; }
.toolbar select {
  background:#1c2128; color:#e6edf3; border:1px solid #30363d;
  border-radius:6px; padding:5px 10px; font-size:13px; cursor:pointer;
}
.toolbar select:focus { outline:none; border-color:#58a6ff; }
.tip { font-size:12px; color:#8b949e; }
.hidden { display:none !important; }

/* ── Chart ── */
#chart-wrap { width:100%; height:calc(100vh - 170px); min-height:520px; }
</style>
</head>
<body>

<header>
  <div class="ticker">QQQ</div>
  <div>
    <div class="price">$LAST_CLOSE</div>
    <div class="change" style="color:CHANGE_COLOR">CHANGE_VAL (CHANGE_PCT%)</div>
    <div class="meta">数据截至：LAST_DATE &nbsp;·&nbsp; 纳斯达克100 ETF</div>
  </div>
  <div class="cards" id="stat-cards"></div>
</header>

<div class="tabs">
  <div class="tab active" data-tab="daily">日 K 线</div>
  <div class="tab" data-tab="m5">5 分钟</div>
  <div class="tab" data-tab="m1">1 分钟</div>
</div>

<div class="toolbar">
  <label id="date-label" class="hidden">选择日期：</label>
  <select id="date-select" class="hidden"></select>
  <span class="tip" id="tip-daily">近30天日线 &nbsp;·&nbsp; 下方滑条可缩放区间</span>
  <span class="tip hidden" id="tip-intraday">选择日期查看当天盘中走势</span>
</div>

<div id="chart-wrap"></div>

<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<script>
// ── Embedded Data ────────────────────────────────────────────────────────────
const DAILY  = DAILY_JSON;
const D5MIN  = D5MIN_JSON;
const D1MIN  = D1MIN_JSON;
const DATES5 = DATES5_JSON;
const DATES1 = DATES1_JSON;

// ── Init chart ───────────────────────────────────────────────────────────────
const chart = echarts.init(document.getElementById('chart-wrap'), 'dark');

// ── Helpers ──────────────────────────────────────────────────────────────────
const fmt2 = n => (n == null ? '-' : (+n).toFixed(2));
const fmtV = n => {
  if (n == null) return '-';
  if (n >= 1e6) return (n/1e6).toFixed(2)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'K';
  return String(n);
};

// ── Stats cards ──────────────────────────────────────────────────────────────
function setCards(data) {
  const el = document.getElementById('stat-cards');
  if (!data || !data.ohlcv || !data.ohlcv.length) { el.innerHTML = ''; return; }
  const last = data.ohlcv[data.ohlcv.length - 1];
  const [o, c, l, h] = last;
  const vol = data.volumes[data.volumes.length - 1];
  const chg = c - o, chgPct = (chg/o*100).toFixed(2);
  const col = chg >= 0 ? '#ef5350' : '#26a69a';
  const items = [
    ['开盘', fmt2(o)], ['收盘', fmt2(c), col],
    ['最高', fmt2(h), '#ef5350'], ['最低', fmt2(l), '#26a69a'],
    ['涨跌', (chg>=0?'+':'')+fmt2(chg)+' ('+chgPct+'%)', col],
    ['成交量', fmtV(vol)],
  ];
  el.innerHTML = items.map(([label, val, c2]) =>
    `<div class="card">
       <div class="card-label">${label}</div>
       <div class="card-value" style="color:${c2||'#e6edf3'}">${val}</div>
     </div>`
  ).join('');
}

// ── Build ECharts option ─────────────────────────────────────────────────────
function buildOption(data, isIntraday) {
  const { dates, ohlcv, volumes, ma5, ma10, ma20, macd, signal, histogram } = data;
  const UP = '#ef5350', DN = '#26a69a';
  const volColors   = ohlcv.map(v => v[1] >= v[0] ? UP : DN);
  const histColors  = histogram.map(v =>
    v == null ? 'transparent' : v >= 0 ? UP+'bb' : DN+'bb');

  const commonXAxis = (gridIdx, showLabel) => ({
    type: 'category', data: dates, gridIndex: gridIdx,
    axisLine: { lineStyle: { color: '#30363d' } },
    axisLabel: { show: showLabel, fontSize: 11, color: '#8b949e' },
    splitLine: { show: false }, boundaryGap: true,
  });

  return {
    animation: false,
    backgroundColor: '#0e1117',
    legend: {
      top: 6, left: 60,
      data: ['K线','MA5','MA10','MA20','MACD','Signal'],
      textStyle: { color: '#8b949e', fontSize: 12 },
      inactiveColor: '#3d444d',
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', crossStyle: { color: '#4a5568' } },
      backgroundColor: '#1c2128',
      borderColor: '#30363d',
      textStyle: { color: '#e6edf3', fontSize: 12 },
      formatter(params) {
        let s = `<b style="display:block;margin-bottom:5px">${params[0].axisValue}</b>`;
        params.forEach(p => {
          if (p.seriesName === 'K线' && Array.isArray(p.value)) {
            const [o,c,l,h] = p.value;
            const col = c >= o ? UP : DN;
            s += `<span style="color:${col}">▐</span> 开 ${fmt2(o)} &nbsp; 收 <b>${fmt2(c)}</b> &nbsp; 低 ${fmt2(l)} &nbsp; 高 ${fmt2(h)}<br>`;
          } else if (p.seriesName === '成交量') {
            s += `<span style="color:#8b949e">▐</span> 成交量 ${fmtV(p.value)}<br>`;
          } else if (p.seriesName === 'Histogram') {
            const col = (p.value||0) >= 0 ? UP : DN;
            s += `<span style="color:${col}">▐</span> Hist ${p.value != null ? p.value.toFixed(4) : '-'}<br>`;
          } else if (p.value != null) {
            s += `<span style="color:${p.color}">▐</span> ${p.seriesName}: ${
              typeof p.value === 'number' && p.value < 1 ? p.value.toFixed(4) : fmt2(p.value)
            }<br>`;
          }
        });
        return s;
      }
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [
      { left: 75, right: 12, top: 46,   height: '50%' },
      { left: 75, right: 12, top: '63%', height: '13%' },
      { left: 75, right: 12, top: '79%', height: '14%' },
    ],
    xAxis: [
      commonXAxis(0, false),
      commonXAxis(1, false),
      commonXAxis(2, true),
    ],
    yAxis: [
      {
        scale: true, gridIndex: 0, splitNumber: 6,
        axisLabel: { fontSize: 11, color: '#8b949e', formatter: v => '$'+v.toFixed(2) },
        splitLine: { lineStyle: { color: '#21262d' } }, axisLine: { show: false },
      },
      {
        scale: true, gridIndex: 1, splitNumber: 2,
        axisLabel: { fontSize: 10, color: '#8b949e', formatter: fmtV },
        splitLine: { lineStyle: { color: '#21262d' } }, axisLine: { show: false },
      },
      {
        scale: true, gridIndex: 2, splitNumber: 2,
        axisLabel: { fontSize: 10, color: '#8b949e', formatter: v => v.toFixed(3) },
        splitLine: { lineStyle: { color: '#21262d' } }, axisLine: { show: false },
      },
    ],
    dataZoom: [
      {
        type: 'inside', xAxisIndex: [0,1,2],
        start: isIntraday ? 0 : Math.max(0, 100 - Math.ceil(6000/dates.length)),
        end: 100,
      },
      {
        type: 'slider', xAxisIndex: [0,1,2],
        start: isIntraday ? 0 : Math.max(0, 100 - Math.ceil(6000/dates.length)),
        end: 100, height: 22, bottom: 2,
        handleStyle: { color: '#58a6ff' },
        borderColor: '#30363d', backgroundColor: '#161b22',
        dataBackground: {
          lineStyle: { color: '#30363d' },
          areaStyle: { color: '#1c2128' },
        },
        selectedDataBackground: {
          lineStyle: { color: '#58a6ff66' },
          areaStyle: { color: '#1c3d6e33' },
        },
        textStyle: { color: '#8b949e' },
      },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick',
        xAxisIndex: 0, yAxisIndex: 0, data: ohlcv,
        itemStyle: { color: UP, color0: DN, borderColor: UP, borderColor0: DN },
        barMaxWidth: 24,
      },
      { name:'MA5',  type:'line', data:ma5,  xAxisIndex:0, yAxisIndex:0,
        lineStyle:{width:1}, symbol:'none', color:'#ffa726', smooth:false },
      { name:'MA10', type:'line', data:ma10, xAxisIndex:0, yAxisIndex:0,
        lineStyle:{width:1}, symbol:'none', color:'#ab47bc', smooth:false },
      { name:'MA20', type:'line', data:ma20, xAxisIndex:0, yAxisIndex:0,
        lineStyle:{width:1}, symbol:'none', color:'#29b6f6', smooth:false },
      {
        name:'成交量', type:'bar',
        xAxisIndex:1, yAxisIndex:1, data:volumes, barMaxWidth:24,
        itemStyle: { color: (p) => volColors[p.dataIndex] },
      },
      { name:'MACD',   type:'line', data:macd,    xAxisIndex:2, yAxisIndex:2,
        lineStyle:{width:1.5}, symbol:'none', color:'#58a6ff' },
      { name:'Signal', type:'line', data:signal,  xAxisIndex:2, yAxisIndex:2,
        lineStyle:{width:1.5}, symbol:'none', color:'#ff7043' },
      {
        name:'Histogram', type:'bar',
        xAxisIndex:2, yAxisIndex:2, data:histogram, barMaxWidth:24,
        itemStyle: { color: (p) => histColors[p.dataIndex] },
      },
    ],
  };
}

// ── Render ───────────────────────────────────────────────────────────────────
function render(data, isIntraday) {
  setCards(data);
  chart.setOption(buildOption(data, isIntraday), true);
}

// ── Date dropdown ─────────────────────────────────────────────────────────────
function populateDates(dates, selected) {
  const sel = document.getElementById('date-select');
  sel.innerHTML = [...dates].reverse().map(d =>
    `<option value="${d}"${d === selected ? ' selected' : ''}>${d}</option>`
  ).join('');
}

// ── Tab switch ────────────────────────────────────────────────────────────────
const elDateLabel  = document.getElementById('date-label');
const elDateSelect = document.getElementById('date-select');
const elTipDaily   = document.getElementById('tip-daily');
const elTipIntra   = document.getElementById('tip-intraday');

function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(el =>
    el.classList.toggle('active', el.dataset.tab === tab));

  if (tab === 'daily') {
    elDateLabel.classList.add('hidden');
    elDateSelect.classList.add('hidden');
    elTipDaily.classList.remove('hidden');
    elTipIntra.classList.add('hidden');
    render(DAILY, false);
    return;
  }

  elDateLabel.classList.remove('hidden');
  elDateSelect.classList.remove('hidden');
  elTipDaily.classList.add('hidden');
  elTipIntra.classList.remove('hidden');

  const dates   = tab === 'm5' ? DATES5 : DATES1;
  const dataMap = tab === 'm5' ? D5MIN  : D1MIN;

  if (!dates.length) {
    chart.clear();
    document.getElementById('stat-cards').innerHTML = '';
    return;
  }

  const latest = dates[dates.length - 1];
  populateDates(dates, latest);
  render(dataMap[latest], true);
  elDateSelect.onchange = () => render(dataMap[elDateSelect.value], true);
}

// ── Boot ──────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(el =>
  el.addEventListener('click', () => switchTab(el.dataset.tab)));
window.addEventListener('resize', () => chart.resize());
switchTab('daily');
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print(f"读取数据：{INPUT_FILE}")
    if not os.path.exists(INPUT_FILE):
        print(f"❌ 文件不存在：{INPUT_FILE}")
        return

    xls = pd.ExcelFile(INPUT_FILE)
    print(f"Sheet 列表：{xls.sheet_names}")

    df_daily = load_sheet(xls, "QQQ_日K")
    df_5min  = load_sheet(xls, "QQQ_5min")
    df_1min  = load_sheet(xls, "QQQ_分时1min")

    daily_data    = df_to_kdata(df_daily)
    d5min_by_date = intraday_by_date(df_5min)
    d1min_by_date = intraday_by_date(df_1min)

    # 统计摘要
    last_close = daily_data["ohlcv"][-1][1] if daily_data["ohlcv"] else 0
    prev_close = daily_data["ohlcv"][-2][1] if len(daily_data["ohlcv"]) > 1 else last_close
    change     = round(float(last_close) - float(prev_close), 2)
    change_pct = round(change / float(prev_close) * 100, 2) if prev_close else 0
    last_date  = daily_data["dates"][-1] if daily_data["dates"] else "N/A"

    change_color = "#ef5350" if change >= 0 else "#26a69a"
    change_sign  = "+" if change >= 0 else ""

    dates_5 = sorted(d5min_by_date.keys())
    dates_1 = sorted(d1min_by_date.keys())

    html = (HTML_TEMPLATE
        .replace("DAILY_JSON",   json.dumps(daily_data,    ensure_ascii=False))
        .replace("D5MIN_JSON",   json.dumps(d5min_by_date, ensure_ascii=False))
        .replace("D1MIN_JSON",   json.dumps(d1min_by_date, ensure_ascii=False))
        .replace("DATES5_JSON",  json.dumps(dates_5,       ensure_ascii=False))
        .replace("DATES1_JSON",  json.dumps(dates_1,       ensure_ascii=False))
        .replace("LAST_CLOSE",   str(last_close))
        .replace("CHANGE_COLOR", change_color)
        .replace("CHANGE_VAL",   f"{change_sign}{change}")
        .replace("CHANGE_PCT",   f"{change_sign}{change_pct}")
        .replace("LAST_DATE",    last_date)
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ 页面已生成：{os.path.abspath(OUTPUT_FILE)}")
    print(f"   日线数据：{len(daily_data['dates'])} 条")
    print(f"   5分钟数据：{len(d5min_by_date)} 天")
    print(f"   1分钟数据：{len(d1min_by_date)} 天")
    print(f"\n   用浏览器打开 QQQ_chart.html 即可查看。")


if __name__ == "__main__":
    main()
