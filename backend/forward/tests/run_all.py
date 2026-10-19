"""
run_all.py — 一键跑所有自测（不触发任何真实 LLM 调用）

用法：
  python tests/run_all.py
  python -m tests.run_all
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
_MULTI_DIR = _PROJECT / "multi_judge协同"
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))
if str(_MULTI_DIR) not in sys.path:
    sys.path.insert(0, str(_MULTI_DIR))

# 三个 test 模块
from tests import test_aggregate, test_ai_maps, test_arbitrator, test_runner_review_route  # noqa: E402


def main() -> int:
    """依次跑 7 个模块的测试；任一失败打印 traceback 并返回非 0。

    前 4 个（aggregate / ai_maps / arbitrator / runner_review_route）：
    从生产文件迁出 / 新增大自测；
    后 3 个（summary / report / excel）：新增的最小 _self_test（在生产文件内）。
    """
    failures: list[tuple[str, Exception]] = []
    # 前 4：迁出到 tests/ 的大自测 + 新增 ai_maps 自测（① ③ 修复）
    for name, mod in (
        ("aggregate", test_aggregate),
        ("ai_maps", test_ai_maps),
        ("arbitrator", test_arbitrator),
        ("runner_review_route", test_runner_review_route),
    ):
        print(f"\n{'=' * 60}\n> tests.{name}.run_all()\n{'=' * 60}")
        try:
            mod.run_all()
        except Exception as e:
            failures.append((name, e))
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()

    # 后 3：生产文件内新增的最小 _self_test（P2.2 增量防回归）
    from multi_judge协同 import multi_judge_summary, multi_judge_report, gen_excel_report
    for name, mod in (
        ("summary._self_test", multi_judge_summary),
        ("report._self_test", multi_judge_report),
        ("excel._self_test", gen_excel_report),
    ):
        print(f"\n{'=' * 60}\n> {name}()\n{'=' * 60}")
        try:
            mod._self_test()
        except Exception as e:
            failures.append((name, e))
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    if failures:
        print(f"[FAIL] {len(failures)} 个测试模块失败：")
        for n, e in failures:
            print(f"  - {n}: {e}")
        return 1
    print("[OK] 所有自测通过（aggregate / ai_maps / arbitrator / runner_review_route / summary / report / excel）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
