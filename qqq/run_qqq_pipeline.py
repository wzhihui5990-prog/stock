# -*- coding: utf-8 -*-
"""
QQQ 一键任务入口脚本。

默认行为：只更新数据（市场数据 + ±3期权 + ±4期权 + 开盘±2期权 + 开盘±3期权）。
可选参数：
  --with-reports   更新数据后，额外生成图表与策略报告（文件夹1/2/10/11/12）
  --with-optimize  更新数据后，额外执行参数优化（文件夹2/10/11/12，耗时较长）

用法示例：
  python run_qqq_pipeline.py
  python run_qqq_pipeline.py --with-reports
  python run_qqq_pipeline.py --with-reports --with-optimize
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
    """执行单个步骤，返回 (是否成功, 耗时秒)。"""
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
        proc = subprocess.run(
            [sys.executable, step.script],
            cwd=step.cwd,
            check=False,
        )
    except Exception as exc:
        elapsed = time.time() - start
        print(f"[FAIL] {step.name}: 启动异常 -> {exc}")
        return False, elapsed

    elapsed = time.time() - start
    ok = proc.returncode == 0
    if ok:
        print(f"[ OK ] {step.name} ({elapsed:.1f}s)")
    else:
        print(f"[FAIL] {step.name} (exit={proc.returncode}, {elapsed:.1f}s)")
    return ok, elapsed


def build_steps(with_reports: bool, with_optimize: bool) -> list[Step]:
    steps: list[Step] = [
        Step(
            name="更新 QQQ 市场数据",
            cwd=os.path.join(ROOT_DIR, "1-qqq日K"),
            script="update_qqq_market_data.py",
        ),
        Step(
            name="更新 0DTE 期权数据（±3）",
            cwd=os.path.join(
                ROOT_DIR,
                "2-qqq末日期权日K-上下3股价的期权合同-前一天末日期权的收盘价",
            ),
            script="update_qqq_0dte_options_offset3.py",
        ),
        Step(
            name="更新 0DTE 期权数据（±4）",
            cwd=os.path.join(
                ROOT_DIR,
                "3-qqq末日期权日K-上下4股价的期权合同-前一天末日期权的收盘价",
            ),
            script="update_qqq_0dte_options_offset4.py",
        ),
        Step(
            name="更新 0DTE 开盘期权数据（±2）",
            cwd=os.path.join(
                ROOT_DIR,
                "4-qqq末日期权日K-当天开盘上下2和上下3股价的期权合同",
            ),
            script="update_qqq_0dte_options_open_offset2.py",
        ),
        Step(
            name="更新 0DTE 开盘期权数据（±3）",
            cwd=os.path.join(
                ROOT_DIR,
                "4-qqq末日期权日K-当天开盘上下2和上下3股价的期权合同",
            ),
            script="update_qqq_0dte_options_open_offset3.py",
        ),
    ]

    if with_reports:
        steps.extend(
            [
                Step(
                    name="生成 QQQ 市场图表",
                    cwd=os.path.join(ROOT_DIR, "1-qqq日K"),
                    script="build_qqq_market_chart.py",
                ),
                Step(
                    name="生成 0DTE 策略报告",
                    cwd=os.path.join(
                        ROOT_DIR,
                        "2-qqq末日期权日K-上下3股价的期权合同-前一天末日期权的收盘价",
                    ),
                    script="build_qqq_0dte_strategy_report.py",
                ),
                Step(
                    name="生成开盘买 Call 策略报告（文件夹10）",
                    cwd=os.path.join(ROOT_DIR, "10-qqq末日期权开盘立即买入看涨合同"),
                    script="build_call_open_strategy.py",
                ),
                Step(
                    name="生成前日收盘买 Call 策略报告（文件夹11）",
                    cwd=os.path.join(ROOT_DIR, "11-qqq末日期权前一天收盘价买入看涨合同"),
                    script="build_call_t1close_strategy.py",
                ),
                Step(
                    name="生成前日收盘买 Put 策略报告（文件夹12）",
                    cwd=os.path.join(ROOT_DIR, "12-qqq末日期权前一天收盘价买入看跌合同"),
                    script="build_put_t1close_strategy.py",
                ),
                Step(
                    name="生成开盘双买策略报告（文件夹10-1）",
                    cwd=os.path.join(ROOT_DIR, "10-1-qqq末日期权开盘立即买入看涨看跌双买合同"),
                    script="build_straddle_open_strategy.py",
                ),
            ]
        )

    if with_optimize:
        steps.extend(
            [
                Step(
                    name="执行 0DTE 参数优化",
                    cwd=os.path.join(
                        ROOT_DIR,
                        "2-qqq末日期权日K-上下3股价的期权合同-前一天末日期权的收盘价",
                    ),
                    script="optimize_qqq_0dte_params.py",
                ),
                Step(
                    name="执行开盘买 Call 参数优化（文件夹10）",
                    cwd=os.path.join(ROOT_DIR, "10-qqq末日期权开盘立即买入看涨合同"),
                    script="optimize_call_open_params.py",
                ),
                Step(
                    name="执行前日收盘买 Call 参数优化（文件夹11）",
                    cwd=os.path.join(ROOT_DIR, "11-qqq末日期权前一天收盘价买入看涨合同"),
                    script="optimize_call_t1close_params.py",
                ),
                Step(
                    name="执行前日收盘买 Put 参数优化（文件夹12）",
                    cwd=os.path.join(ROOT_DIR, "12-qqq末日期权前一天收盘价买入看跌合同"),
                    script="optimize_put_t1close_params.py",
                ),
                Step(
                    name="执行开盘双买参数优化（文件夹10-1）",
                    cwd=os.path.join(ROOT_DIR, "10-1-qqq末日期权开盘立即买入看涨看跌双买合同"),
                    script="optimize_straddle_open_params.py",
                ),
            ]
        )

    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description="QQQ 一键更新任务")
    parser.add_argument(
        "--with-reports",
        action="store_true",
        help="更新数据后，额外生成图表与策略报告",
    )
    parser.add_argument(
        "--with-optimize",
        action="store_true",
        help="更新数据后，额外执行参数优化（耗时较长）",
    )
    args = parser.parse_args()

    steps = build_steps(args.with_reports, args.with_optimize)
    print(f"工作目录: {ROOT_DIR}")
    print(f"Python:   {sys.executable}")
    print(f"任务数:   {len(steps)}")

    all_ok = True
    total_start = time.time()
    details: list[tuple[str, bool, float]] = []

    for step in steps:
        ok, elapsed = _run_step(step)
        details.append((step.name, ok, elapsed))
        if not ok:
            all_ok = False
            # 一旦失败立即停止，避免级联错误
            print("\n检测到失败，后续步骤已停止。")
            break

    total_elapsed = time.time() - total_start

    print("\n" + "-" * 72)
    print("执行汇总")
    print("-" * 72)
    for name, ok, elapsed in details:
        status = "OK" if ok else "FAIL"
        print(f"[{status:4}] {name} ({elapsed:.1f}s)")
    print(f"总耗时: {total_elapsed:.1f}s")

    if all_ok:
        print("\n全部任务执行完成。")
        return 0

    print("\n存在失败任务，请根据日志排查。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
