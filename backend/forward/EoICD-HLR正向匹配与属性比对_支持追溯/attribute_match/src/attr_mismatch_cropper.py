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
  - ★2026-10-28 补充 AI 判定所需的上下文：
      - hlr_identity：HLR 侧接口身份（bit + 中文接口名）——AI 需要知道"HLR 值里
        说的是哪个信号"，否则无法判断是表述差异还是真冲突
      - eoicd_bit：EoICD 侧信号的 bit 区间
      - req_text：需求原文（语义判定的依据）

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
          "hlr_identity": {...},   # 2026-10-28 新增：HLR 侧接口身份（bit/中文名）
          "eoicd_bit": "22-22",    # 2026-10-28 新增：EoICD 侧 bit 区间
          "req_text": "...",       # 2026-10-28 新增：需求原文
        },
        ...
      ]
    """
    cropped = []

    for i, item in enumerate(mismatch_pairs, 1):
        hlr_identity = item.get("hlr_identity") or {}
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
            # ── AI 判定上下文（2026-10-28）──
            # hlr_identity 只取判判定必需的字段（bit + 中文接口名 + 方向），
            # 避免把整个身份字典塞进 prompt 撑大体积。
            "hlr_bit": hlr_identity.get("bit"),
            "hlr_signal_name": hlr_identity.get("name"),
            "eoicd_bit": item.get("eoicd_bit", ""),
            "req_text": item.get("req_text", ""),
        })

    print(f"      裁剪完成: {len(cropped)} 对 mismatch 待 AI 复核")
    return cropped
