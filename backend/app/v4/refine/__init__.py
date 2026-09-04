# -*- coding: utf-8 -*-
"""RPDU 精化后处理：仅做"过滤无关 ICD Block + 完整 ICD 补采 + 同义词补采"前置处理。

后续 Step 4-6（多模型并发 + 共识 + 5星 + re_review + degradation）由
pipeline 主干接管，不在此包内生成报告或共识产物。

入口：`run_pipeline_refined_stage`（pipeline.py: refine=True 分支调用）
辅助：`filter_matched_blocks` / `hlr_signal_names` / `hlr_synonym_english_terms`
"""
from app.v4.refine.block_filter import (
    filter_matched_blocks,
    hlr_signal_names,
    hlr_synonym_english_terms,
)
from app.v4.refine.runner import run_pipeline_refined_stage

__all__ = [
    "filter_matched_blocks",
    "hlr_signal_names",
    "hlr_synonym_english_terms",
    "run_pipeline_refined_stage",
]