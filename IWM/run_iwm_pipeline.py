# -*- coding: utf-8 -*-
"""
IWM 一键数据更新入口脚本

执行顺序：
  1. 更新 IWM 行情数据（日K + 1min/2min/5min 分时）
  2. 更新 IWM ±0.5 末日期权数据
  3. 更新 IWM ±1.0 末日期权数据

用法：
  python run_iwm_pipeline.py
"""

from __future__ import annotations

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
    status = "OK  " if ok else "FAIL"
    print(f"\n[{status}] {step.name}  耗时 {elapsed:.0f}s")
    return ok, elapsed


STEPS: list[Step] = [
    Step(
        name   = "IWM 行情数据（日K + 分时）",
        cwd    = os.path.join(ROOT_DIR, "1-iwm日K"),
        script = "update_iwm_market_data.py",
    ),
    Step(
        name   = "IWM ±0.5 末日期权数据",
        cwd    = os.path.join(ROOT_DIR, "2-iwm末日期权-offset0.5"),
        script = "update_iwm_0dte_options_offset05.py",
    ),
    Step(
        name   = "IWM ±1.0 末日期权数据",
        cwd    = os.path.join(ROOT_DIR, "3-iwm末日期权-offset1"),
        script = "update_iwm_0dte_options_offset1.py",
    ),
]


def main():
    print("IWM 数据更新 Pipeline 启动")
    print(f"根目录：{ROOT_DIR}\n")

    total_start = time.time()
    results: list[tuple[str, bool, float]] = []

    for step in STEPS:
        ok, elapsed = _run_step(step)
        results.append((step.name, ok, elapsed))

    total_elapsed = time.time() - total_start

    print("\n" + "=" * 72)
    print("Pipeline 完成汇总")
    print("=" * 72)
    for name, ok, elapsed in results:
        status = "OK  " if ok else "FAIL"
        print(f"  [{status}] {name:<30}  {elapsed:.0f}s")
    print(f"\n  总耗时：{total_elapsed:.0f}s")

    failed = [name for name, ok, _ in results if not ok]
    if failed:
        print(f"\n  以下步骤失败，请检查日志：")
        for name in failed:
            print(f"    - {name}")
        sys.exit(1)
    else:
        print("\n  所有步骤均已完成。")


if __name__ == "__main__":
    main()
