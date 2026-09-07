# -*- coding: utf-8 -*-
"""RPDU 精化前置处理入口：仅做"过滤无关 ICD Block + 精确补采 + 同义词补采"。

不调 LLM、不生成共识/报告；返回 (new_match_result, new_cases) 后由 pipeline 主干
继续走原 Step 4-6（多模型并发 + drain + degradation + 5星共识 + re_review）。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.v4.matching.entry_filter import should_keep
from app.v4.matching.reverse_case_builder import build_reverse_cases
from app.v4.matching.signal_profiler import build_blocks, build_profiles
from app.v4.models import EoICDOutput, ReverseCase, ReverseMatchOutput
from app.v4.refine.block_filter import filter_matched_blocks


def run_pipeline_refined_stage(
    eoicd_out: EoICDOutput,
    hlr_labels: dict,
    match_result: ReverseMatchOutput,
    output_dir: Path,
) -> tuple[ReverseMatchOutput, list[ReverseCase]]:
    """过滤无关 + 精确补采 + 同义词补采 → 覆盖写盘 → 重建 cases。

    参数：
      eoicd_out:  EoICDOutput（含完整 requirements，用于重建完整 block 索引）
      hlr_labels: {hlr_id: label} 或 HLRLabelOutput 顶层 dict
      match_result: 匹配层输出（ReverseMatchOutput）
      output_dir:   输出目录（reverse_matches.json 会被覆盖）

    返回：(new_match_result, new_cases)，供 pipeline 继续 Step 4-6 使用。
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Step A: 重建完整 ICD Block 索引（供过滤与补采使用）
    eoicd_kept = [req for req in eoicd_out.requirements if should_keep(req)]
    profiles = build_profiles(eoicd_kept)
    blocks = build_blocks(profiles)
    block_index = {b.block_key: b for b in blocks}

    # Step B: 过滤无关 + 精确补采 + 同义词补采
    match_data = json.loads(match_result.model_dump_json(indent=2, ensure_ascii=False))
    match_data = filter_matched_blocks(match_data, hlr_labels, block_index=block_index)
    new_match_result = ReverseMatchOutput(**match_data)

    # 覆盖写盘：reverse_matches.json 被过滤+补采后内容替换
    match_path = out / "reverse_matches.json"
    match_path.write_text(
        new_match_result.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Step C: 用过滤后的匹配重建 cases
    new_cases = build_reverse_cases(new_match_result, block_index)

    return new_match_result, new_cases