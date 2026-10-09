"""
attr_mismatch_cropper.py — 属性匹配：mismatch 数据裁剪

功能：
  对脚本预匹配标记为 mismatch 的数据进行裁剪，去掉无关信息，
  只保留供 AI 复核所需的最小信息集。

裁剪原则：
  - 保留身份信息（cluster_id, signal_name, req_id）
  - 保留待比对的属性名和双方值
  - 去掉已知的匹配结果（脚本已判定 mismatch）
  - 为每对生成唯一 pair_id

输入：mismatch_pairs 列表（来自 attr_script_matcher）
输出：cropped_pairs 列表，供 AI 分批处理
"""

from __future__ import annotations


def crop_mismatch_input(mismatch_pairs: list[dict], direction: str) -> list[dict]:
    """
    裁剪 mismatch 数据，生成 AI 复核输入。

    返回：
      [
        {
          "pair_id": "attr_mismatch_00001",
          "eoicd_cluster_id": "...",
          "eoicd_signal_name": "...",
          "eoicd_signal_short_name": "...",
          "req_id": "...",
          "attribute": "DP.BitOffsetWithinDS",
          "hlr_attr_name": "BitOffsetWithinDS",
          "eoicd_value": 15,
          "hlr_value": "15-16",
        },
        ...
      ]
    """
    cropped = []

    for i, item in enumerate(mismatch_pairs, 1):
        cropped.append({
            "pair_id": f"attr_mismatch_{i:05d}",
            "eoicd_cluster_id": item.get("eoicd_cluster_id", ""),
            "eoicd_signal_name": item.get("eoicd_signal_name", ""),
            "eoicd_signal_short_name": item.get("eoicd_signal_short_name", ""),
            "req_id": item.get("req_id", ""),
            "attribute": item.get("attribute", ""),
            "hlr_attr_name": item.get("hlr_attr_name", ""),
            "eoicd_value": item.get("eoicd_value"),
            "hlr_value": item.get("hlr_value"),
            "rule": item.get("rule", "default"),
        })

    print(f"      裁剪完成: {len(cropped)} 对 mismatch 待 AI 复核")
    return cropped
