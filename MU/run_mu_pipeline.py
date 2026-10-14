# -*- coding: utf-8 -*-
"""
MU（美光科技）一键任务入口脚本。

默认行为：只更新数据（VIX + MU 行情 + 0DTE 开盘 ±20/±30 期权数据）。
可选参数：
  --with-report   更新数据后，额外执行分析 + 回测并生成 HTML 报告

用法示例：
  python run_mu_pipeline.py
  python run_mu_pipeline.py --with-report

注意：期权拉取受 Polygon 免费档 5 次/分钟限速，全量约需 1 小时；
      脚本每 10 个到期日落盘一次，中断后可重复执行自动续拉。
      VIX 一步走 Yahoo（需 Cookie+crumb）+ CBOE 日K备用源；
      偶发的 ConnectionReset 属网络抖动，重跑即可，不会破坏已有数据。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


@dataclass
class Step:
    name: str
    cwd: str
    script: str


def _run_step(step: Step) -> tuple[bool, float]:
    script_path = os.path.join(step.cwd, step.script)
    if not os.path.exists(script_path):
        print(f"[SKIP] {step.name}: 脚本不存在 -> {script_path}")
        return False, 0.0

    print("\n" + "=" * 72)
    print(f"[RUN ] {step.name}")
    print(f"       {script_path}")
    print("=" * 72)

    start = time.time()
    try:
        proc = subprocess.run([sys.executable, step.script], cwd=step.cwd, check=False)
    except Exception as exc:
        print(f"[FAIL] {step.name}: 启动异常 -> {exc}")
        return False, time.time() - start

    elapsed = time.time() - start
    ok = proc.returncode == 0
    print(f"[{' OK ' if ok else 'FAIL'}] {step.name} ({elapsed:.1f}s)")
    return ok, elapsed


def build_steps(with_report: bool) -> list[Step]:
    steps: list[Step] = [
        # VIX 放第一位：它最短（~7s）且是唯一有失败风险的一步（Yahoo 403/限流），
        # 先跑能尽早看到「今天 VIX 到底拿没拿到」，不用等行情拉完。
        # （功能上三步互不依赖，报告始终在最后。）
        Step("更新 VIX 数据", os.path.join(ROOT_DIR, "..", "VIX"), "update_vix_data.py"),
        Step("更新 MU 行情数据", os.path.join(ROOT_DIR, "1-mu日K"), "update_mu_market_data.py"),
        Step("更新 0DTE 开盘期权数据（±20 / ±30）",
             os.path.join(ROOT_DIR, "2-mu末日期权开盘+-20和+-30的双买合同"),
             "update_mu_0dte_options_open_offset20_30.py"),
    ]
    if with_report:
        steps.append(Step("分析 + 回测 + 生成报告",
                          os.path.join(ROOT_DIR, "2-mu末日期权开盘+-20和+-30的双买合同"),
                          "analyze_mu_straddle.py"))
    return steps


def main() -> int:
    ap = argparse.ArgumentParser(description="MU 一键任务入口")
    ap.add_argument("--with-report", action="store_true", help="更新数据后执行分析/回测并生成 HTML 报告")
    args = ap.parse_args()

    steps = build_steps(args.with_report)

    print("=" * 72)
    print(f"MU 流水线：共 {len(steps)} 个步骤")
    for i, s in enumerate(steps, 1):
        print(f"  {i}. {s.name}")
    print("=" * 72)

    failed: list[str] = []
    t0 = time.time()
    for s in steps:
        ok, _ = _run_step(s)
        if not ok:
            failed.append(s.name)

    total = time.time() - t0
    print("\n" + "=" * 72)
    print(f"完成，总耗时 {total:.1f}s（{total / 60:.1f} 分钟）")
    if failed:
        print(f"失败步骤 {len(failed)} 个：")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("全部步骤成功。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
