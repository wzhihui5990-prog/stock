# -*- coding: utf-8 -*-
"""
QQQ 开盘垂直看涨价差 — 参数扫描

5 轴扫描：
  buy_offset × sell_offset × upper_pct × lower_pct × close_time
  约束：1 ≤ buy_offset < sell_offset ≤ 5
  触发：QQQ 当日涨幅 ≥ upper_pct%(止盈) 或 跌幅 ≥ lower_pct%(止损)
输出：CSV 完整结果 + Top 30 控制台展示 + HTML 报告
"""
import os, json, itertools
import pandas as pd

ROOT = os.path.dirname(__file__)
QQQ_FILE = os.path.join(ROOT, "..", "1-qqq日K", "data", "qqq_market_data.xlsx")
OPT_DIR  = os.path.join(ROOT, "..", "4-qqq末日期权日K-当天开盘上下2和上下3股价的期权合同", "data")
OPT_FILES = {n: os.path.join(OPT_DIR, f"qqq_0dte_options_open_offset{n}.xlsx") for n in (1, 2, 3, 4, 5)}
OUT_CSV  = os.path.join(ROOT, "data", "vertical_call_param_optimization.csv")
OUT_HTML = os.path.join(ROOT, "data", "qqq_vertical_call_optimization.html")

COMMISSION = 1.7    # 每张合约
MONITOR_START = "09:30"

# 扫描范围
BUY_OFFSETS  = [1, 2, 3, 4]
SELL_OFFSETS = [2, 3, 4, 5]
UPPER_PCTS   = [round(0.1 + 0.1*i, 2) for i in range(20)]   # 0.10% ~ 2.00%
LOWER_PCTS   = [round(0.1 + 0.1*i, 2) for i in range(15)]   # 0.10% ~ 1.50%
CLOSE_TIMES  = ["10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30"]

TOP_N = 30


def _get_qqq_day(t1, qqq_1m, qqq_2m, qqq_5m):
    for df in [qqq_1m, qqq_2m, qqq_5m]:
        day = df[df["时间"].astype(str).str.startswith(t1)].copy()
        if not day.empty:
            day["t"] = day["时间"].astype(str).str[-5:]
            return day
    return pd.DataFrame()


def load_qqq_per_day():
    print("  加载 QQQ 分时...", end=" ", flush=True)
    qqq_1m = pd.read_excel(QQQ_FILE, sheet_name="QQQ_分时1min")
    qqq_2m = pd.read_excel(QQQ_FILE, sheet_name="QQQ_分时2min")
    qqq_5m = pd.read_excel(QQQ_FILE, sheet_name="QQQ_5min")
    # 提取全部日期
    all_dates = set()
    for d in [qqq_1m, qqq_2m, qqq_5m]:
        all_dates.update(d["时间"].astype(str).str[:10].unique())
    out = {}
    for d in all_dates:
        day = _get_qqq_day(d, qqq_1m, qqq_2m, qqq_5m)
        if day.empty: continue
        bars = [(r["t"], float(r["收盘价"])) for _, r in day.iterrows()]
        bars.sort(key=lambda x: x[0])
        out[d] = bars
    print(f"{len(out)} 天")
    return out


def load_call_per_day(offset):
    print(f"  加载 +{offset} ...", end=" ", flush=True)
    df = pd.read_excel(OPT_FILES[offset], sheet_name="Call_1min")
    df["date"] = df["到期日"].astype(str).str[:10]
    df["t"]    = df["时间(美东)"].astype(str).str[-5:]
    out = {}
    for d, grp in df.groupby("date"):
        bars = []
        for _, r in grp.iterrows():
            bars.append((r["t"], float(r["开盘价"]), float(r["收盘价"])))
        bars.sort(key=lambda x: x[0])
        out[d] = bars
    print(f"{len(out)} 天")
    return out


def backtest(buy_per_day, sell_per_day, qqq_per_day, dates,
             upper_pct, lower_pct, close_time, commission_total):
    """QQQ 涨跌幅触发，返回 (累计盈亏$, 胜场, 交易日数, 触发数)."""
    total_pnl = 0.0
    wins, trig_cnt, n_days = 0, 0, 0

    for d in dates:
        bb = buy_per_day.get(d); ss = sell_per_day.get(d); qq = qqq_per_day.get(d)
        if not bb or not ss or not qq:
            continue
        bo = next((x for x in bb if x[0] == "09:30"), bb[0])
        so = next((x for x in ss if x[0] == "09:30"), ss[0])
        qo = next((x for x in qq if x[0] == "09:30"), qq[0])
        buy_cost, sell_recv, qqq_open = bo[1], so[1], qo[1]
        if buy_cost <= 0 or sell_recv <= 0 or qqq_open <= 0:
            continue
        net_p = buy_cost - sell_recv
        if net_p <= 0:
            continue

        trig_time = None
        for t, p in qq:
            if t < MONITOR_START or t > close_time: continue
            pct = (p - qqq_open) / qqq_open * 100
            if pct >= upper_pct: trig_time = t; break
            if pct <= -lower_pct: trig_time = t; break

        sell_t = trig_time or close_time
        b_last = next((x for x in reversed(bb) if x[0] <= sell_t), None)
        s_last = next((x for x in reversed(ss) if x[0] <= sell_t), None)
        if not b_last or not s_last: continue
        trig_spread = b_last[2] - s_last[2]
        if trig_time: trig_cnt += 1

        pnl = trig_spread - net_p - commission_total
        total_pnl += pnl
        n_days += 1
        if pnl > 0: wins += 1

    return round(total_pnl * 100, 2), wins, n_days, trig_cnt


def main():
    print("=" * 70)
    print("  QQQ 开盘垂直看涨价差 — 参数扫描")
    print("=" * 70)
    valid_combos = [(b, s) for b in BUY_OFFSETS for s in SELL_OFFSETS if s > b]
    n_param = len(valid_combos) * len(UPPER_PCTS) * len(LOWER_PCTS) * len(CLOSE_TIMES)
    print(f"  价差组合: {len(valid_combos)}  上涨触发: {len(UPPER_PCTS)}  下跌触发: {len(LOWER_PCTS)}  平仓时间: {len(CLOSE_TIMES)}")
    print(f"  共 {n_param} 组参数")
    print(f"  手续费: ${COMMISSION}/张 × 4 = ${COMMISSION*4}")
    print()

    print("加载数据...")
    qqq_per_day = load_qqq_per_day()
    per_day = {n: load_call_per_day(n) for n in (1, 2, 3, 4, 5)}

    all_dates = sorted(set(qqq_per_day.keys()) & set().union(*[set(d.keys()) for d in per_day.values()]))
    commission_total = COMMISSION * 4 / 100

    rows = []
    done = 0
    for b, s in valid_combos:
        bd, sd = per_day[b], per_day[s]
        common = [d for d in all_dates if d in bd and d in sd]
        for up, lo, ct in itertools.product(UPPER_PCTS, LOWER_PCTS, CLOSE_TIMES):
            pnl, wins, n_days, trig = backtest(bd, sd, qqq_per_day, common, up, lo, ct, commission_total)
            wr = round(wins / n_days * 100, 1) if n_days else 0
            rows.append({
                "买腿偏移": b, "卖腿偏移": s,
                "组合": f"+{b}/+{s}",
                "上涨触发%": up, "下跌触发%": lo,
                "平仓时间": ct,
                "累计盈亏$": pnl, "交易天数": n_days,
                "胜率%": wr, "触发次数": trig,
                "日均盈亏$": round(pnl / n_days, 2) if n_days else 0,
            })
            done += 1
            if done % 1000 == 0:
                print(f"  进度 {done}/{n_param}")
        print(f"  完成 {b}/{s}")

    df = pd.DataFrame(rows).sort_values("累计盈亏$", ascending=False).reset_index(drop=True)
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n✅ CSV 已保存: {OUT_CSV}")

    # 控制台 Top
    top = df.head(TOP_N).copy()
    top.index += 1
    print(f"\n{'='*70}")
    print(f"  Top {TOP_N} 最优参数（按累计盈亏降序）")
    print(f"{'='*70}")
    print(top.to_string())

    # 各组合的 Top1
    print(f"\n  各组合最优参数:")
    for b, s in valid_combos:
        sub = df[(df["买腿偏移"] == b) & (df["卖腿偏移"] == s)]
        if len(sub):
            r = sub.iloc[0]
            print(f"    +{b}/+{s}: 涨{r['上涨触发%']}%止盈  跌{r['下跌触发%']}%止损  {r['平仓时间']}  → ${r['累计盈亏$']}  胜率{r['胜率%']}%")

    # 生成 HTML 报告
    generate_html(df, valid_combos)


def _safe_id(k):
    return k.replace("/", "_").replace("+", "p")

def generate_html(df, valid_combos):
    top = df.head(TOP_N).to_dict("records")
    by_combo = {f"+{b}/+{s}": df[(df["买腿偏移"] == b) & (df["卖腿偏移"] == s)].head(10).to_dict("records") for b, s in valid_combos}
    full = df.to_dict("records")

    tabs_html = "".join(f'<span class="tab" onclick="showCombo(\'{k}\',event)">{k}</span>' for k in by_combo.keys())
    sections_html = "".join(f'<div class="combo-section" id="combo-{_safe_id(k)}">{render_table(v)}</div>' for k, v in by_combo.items())
    combo_opts = "".join(f'<option>+{b}/+{s}</option>' for b, s in valid_combos)
    ct_set = sorted(set(r["平仓时间"] for r in full))
    ct_opts = "".join(f'<option>{t}</option>' for t in ct_set)

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>垂直看涨价差 — 参数优化</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI','Microsoft YaHei',sans-serif;background:#0a0e17;color:#e0e0e0;padding:20px}}
h1{{font-size:20px;color:#58a6ff;margin-bottom:10px}}
.sub{{font-size:12px;color:#8b949e;margin-bottom:20px}}
.section{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:15px;margin-bottom:15px}}
.section h2{{font-size:15px;color:#c9d1d9;margin-bottom:10px;border-left:3px solid #58a6ff;padding-left:8px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:#0d1117;color:#8b949e;padding:6px 8px;text-align:right;border-bottom:1px solid #30363d;position:sticky;top:0}}
th:first-child,td:first-child{{text-align:left}}
td{{padding:5px 8px;border-bottom:1px solid #21262d;text-align:right}}
tr:hover td{{background:#1c2333}}
.pos{{color:#3fb950;font-weight:bold}}
.neg{{color:#f85149;font-weight:bold}}
.tab{{display:inline-block;padding:5px 12px;margin-right:5px;background:#21262d;border-radius:4px;cursor:pointer;font-size:12px;color:#8b949e}}
.tab.active{{background:#1f6feb;color:#fff;font-weight:bold}}
.combo-section{{display:none}}
.combo-section.active{{display:block}}
.filter-bar{{margin:10px 0;font-size:12px;color:#8b949e}}
.filter-bar input,.filter-bar select{{background:#0d1117;border:1px solid #30363d;color:#e0e0e0;padding:3px 6px;border-radius:4px;margin:0 4px}}
</style></head><body>
<h1>QQQ 开盘垂直看涨价差 — 参数优化报告</h1>
<div class="sub">扫描 {len(df)} 组参数 · Top {TOP_N} + 各组合 Top10 + 全量交互查询</div>

<div class="section">
  <h2>Top {TOP_N} 全局最优</h2>
  {render_table(top)}
</div>

<div class="section">
  <h2>分组合 Top 10</h2>
  <div>{tabs_html}</div>
  {sections_html}
</div>

<div class="section">
  <h2>全量结果（{len(df)} 组）</h2>
  <div class="filter-bar">
    组合: <select id="f-combo" onchange="filterRows()">
      <option value="">全部</option>{combo_opts}
    </select>
    平仓时间: <select id="f-ct" onchange="filterRows()"><option value="">全部</option>{ct_opts}</select>
    最低累计盈亏$: <input type="number" id="f-pnl" value="" oninput="filterRows()" style="width:80px">
    显示前 N 行: <input type="number" id="f-n" value="200" oninput="filterRows()" style="width:60px">
  </div>
  <div id="full-table">{render_table(full[:200])}</div>
</div>

<script>
const FULL = {json.dumps(full, ensure_ascii=False)};
function showCombo(k, e) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  e.target.classList.add('active');
  document.querySelectorAll('.combo-section').forEach(s => s.classList.remove('active'));
  document.getElementById('combo-' + k.replace('/','_').replace('+','p')).classList.add('active');
}}
function fmt(rows) {{
  if (!rows.length) return '<p style="color:#8b949e;padding:20px">无匹配结果</p>';
  const cols = Object.keys(rows[0]);
  let h = '<table><thead><tr>' + cols.map(c => `<th>${{c}}</th>`).join('') + '</tr></thead><tbody>';
  for (const r of rows) {{
    h += '<tr>';
    for (const c of cols) {{
      const v = r[c];
      let cls = '';
      if (c === '累计盈亏$' || c === '日均盈亏$') cls = v > 0 ? 'pos' : v < 0 ? 'neg' : '';
      h += `<td class="${{cls}}">${{v}}</td>`;
    }}
    h += '</tr>';
  }}
  return h + '</tbody></table>';
}}
function filterRows() {{
  const c = document.getElementById('f-combo').value;
  const t = document.getElementById('f-ct').value;
  const p = parseFloat(document.getElementById('f-pnl').value);
  const n = parseInt(document.getElementById('f-n').value) || 200;
  let rows = FULL;
  if (c) rows = rows.filter(r => r['组合'] === c);
  if (t) rows = rows.filter(r => r['平仓时间'] === t);
  if (!isNaN(p)) rows = rows.filter(r => r['累计盈亏$'] >= p);
  document.getElementById('full-table').innerHTML = fmt(rows.slice(0, n));
}}
// 默认激活第一个 tab
document.querySelector('.tab').click();
</script>
</body></html>"""
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ HTML 已保存: {OUT_HTML}")


def render_table(rows):
    if not rows:
        return '<p style="color:#8b949e;padding:20px">无数据</p>'
    cols = list(rows[0].keys())
    out = '<table><thead><tr>' + ''.join(f'<th>{c}</th>' for c in cols) + '</tr></thead><tbody>'
    for r in rows:
        out += '<tr>'
        for c in cols:
            v = r[c]
            cls = ''
            if c in ("累计盈亏$", "日均盈亏$"):
                if isinstance(v, (int, float)):
                    cls = "pos" if v > 0 else "neg" if v < 0 else ''
            out += f'<td class="{cls}">{v}</td>'
        out += '</tr>'
    return out + '</tbody></table>'


if __name__ == "__main__":
    main()
