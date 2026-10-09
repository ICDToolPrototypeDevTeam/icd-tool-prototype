"""
attr_mismatch_result_feeder.py — 属性匹配：AI 复核结果反哺

功能：
  读取 AI 分批复核结果，按 (pair_id) 定位回原始 mismatch 数据：
  - "一致" → 脚本误判，升级到 matched
  - "不一致" → 确认 mismatch

输入：
  - mismatch_pairs: 原始 mismatch 列表（来自 attr_script_matcher）
  - ai_results: AI 复核结果列表（来自 attr_mismatch_ai_matcher）

输出：
  每个原始 mismatch 项附加 ai_result 字段
"""

from __future__ import annotations


def feed_ai_results(mismatch_pairs: list[dict], ai_results: list[dict]) -> list[dict]:
    """
    将 AI 复核结果反哺回 mismatch 数据。

    返回：每个 mismatch 项附加 ai_result 字段
    """
    # 建立 AI 结果映射: pair_id -> "一致"/"不一致"
    ai_map = {}
    for r in ai_results:
        ai_map[r["pair_id"]] = r.get("ai_result", "不一致")

    fed_results = []

    for i, item in enumerate(mismatch_pairs, 1):
        pair_id = f"attr_mismatch_{i:05d}"
        ai_result = ai_map.get(pair_id, "不一致")

        fed_item = dict(item)
        fed_item["ai_result"] = ai_result
        fed_item["pair_id"] = pair_id
        fed_results.append(fed_item)

    upgraded = sum(1 for r in fed_results if r["ai_result"] == "一致")
    confirmed = sum(1 for r in fed_results if r["ai_result"] == "不一致")
    print(f"      AI 复核结果: {upgraded} 项升级 matched, {confirmed} 项确认 mismatch")

    return fed_results
