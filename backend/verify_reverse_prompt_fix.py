# -*- coding: utf-8 -*-
"""Temporary verification script: real-API re-test of needs_review misjudgment fix.

Expected final semantics:
  A) 内部路由引用 Label / 未断言接口属性 → covered
  B) 匹配到但 HLR 完全未提及的无关 Block → needs_review
  C) 多 Block 只写其一 → covered（不判"需求缺失"）
  D) HLR 断言接口属性但 ICD 信息不足以验证 → needs_review

Run from backend/: python verify_reverse_prompt_fix.py
"""

from __future__ import annotations

import sys

from app.v4.config import load_dotenv  # noqa: F401  (ensure .env is loaded)
from app.v4.comparison.multi_judge import _judge_with_provider_sync, _load_reverse_prompt
from app.v4.comparison.review_agent import review_judgments
from app.v4.models import MultiJudgeResult, ReverseCase

CALIBRATED_AIRSPEED_PROFILE = {
    "block_key": "L206_AIR_SPEED_FCM1_R1",
    "signal_family": "Calibrated_Airspeed",
    "label": "206",
    "direction": "receive",
    "bus_types": ["A429"],
    "channel_count": 1,
    "merged_attributes": {
        "DataFormatType": "BNR",
        "Units": "knots",
        "FuncRngMin": 0,
        "FuncRngMax": 512,
        "LSBResolution": 0.125,
        "Period": "50ms",
    },
    "sub_signals": [
        {"dp_name": "AIR_SPEED_FCM1_R1", "bit_offset": 11, "size": 15, "dtype": "BNR"},
        {"dp_name": "AIR_SPEED_FCM1_R1_SSM", "bit_offset": 9, "size": 2, "dtype": "SSM"},
    ],
}

DIS_SYS1_PROFILE = {
    "block_key": "L145_DIS_00_SYS1",
    "signal_family": "Discrete_Output_Sys1",
    "label": "145",
    "direction": "send",
    "bus_types": ["A429"],
    "channel_count": 1,
    "merged_attributes": {
        "DataFormatType": "UnsignedInteger",
        "ParameterSize": "19 Bits",
    },
    "sub_signals": [
        {"dp_name": "DIS_00_SYS1", "bit_offset": 11, "size": 19, "dtype": "UnsignedInteger"},
    ],
}

CASES = [
    ReverseCase(
        case_id="VERIFY-A",
        hlr_requirement={
            "hlr_id": "FSF29-022645",
            "content": (
                "HSCU 应将 AIR_SPEED_FCM1_R1 的值及其 Valid 状态传递并赋给 "
                "FCM13 对应的输入信号。所引用 Label L206 "
                "（亦称：L206_AIR_SPEED_FCM1_R1）。"
            ),
            "signal_category": "bus",
        },
        matched_profiles=[CALIBRATED_AIRSPEED_PROFILE],
        match_evidence={
            "match_type": "已匹配",
            "hlr_labels": ["L206"],
            "matched_block_count": 1,
            "top_scores": [
                {"block_key": "L206_AIR_SPEED_FCM1_R1", "total": 82,
                 "signal_family": "Calibrated_Airspeed", "channel_count": 1}
            ],
        },
    ),
    ReverseCase(
        case_id="VERIFY-B",
        hlr_requirement={
            "hlr_id": "FSF29-023194",
            "content": (
                "HSCU 应采集液压泵压力传感器电压信号，并进行滤波与量程转换，"
                "用于液压系统压力监控。"
            ),
            "signal_category": "analog",
        },
        matched_profiles=[DIS_SYS1_PROFILE],
        match_evidence={
            "match_type": "待确定",
            "hlr_labels": [],
            "matched_block_count": 1,
            "top_scores": [
                {"block_key": "L145_DIS_00_SYS1", "total": 45,
                 "signal_family": "Discrete_Output_Sys1", "channel_count": 1}
            ],
        },
    ),
    ReverseCase(
        case_id="VERIFY-C",
        hlr_requirement={
            "hlr_id": "FSF29-022600",
            "content": (
                "HSCU 应将 DIS_00_SYS1 的门控状态写入输出寄存器，"
                "所引用 Label L145（亦称：L145_DIS_00_SYS1）。"
            ),
            "signal_category": "discrete",
        },
        matched_profiles=[
            DIS_SYS1_PROFILE,
            {
                "block_key": "L146_DIS_00_SYS2",
                "signal_family": "Discrete_Output_Sys2",
                "label": "146",
                "direction": "send",
                "bus_types": ["A429"],
                "channel_count": 1,
                "merged_attributes": {
                    "DataFormatType": "UnsignedInteger",
                    "ParameterSize": "19 Bits",
                },
                "sub_signals": [
                    {"dp_name": "DIS_00_SYS2", "bit_offset": 11, "size": 19,
                     "dtype": "UnsignedInteger"},
                ],
            },
        ],
        match_evidence={
            "match_type": "已匹配",
            "hlr_labels": ["L145"],
            "matched_block_count": 2,
            "top_scores": [
                {"block_key": "L145_DIS_00_SYS1", "total": 88,
                 "signal_family": "Discrete_Output_Sys1", "channel_count": 1},
                {"block_key": "L146_DIS_00_SYS2", "total": 60,
                 "signal_family": "Discrete_Output_Sys2", "channel_count": 1},
            ],
        },
    ),
    ReverseCase(
        case_id="VERIFY-D",
        hlr_requirement={
            "hlr_id": "FSF29-022601",
            "content": (
                "HSCU 应将 DIS_00_SYS1 的 bit0 置 1 表示门控关闭，"
                "所引用 Label L145（亦称：L145_DIS_00_SYS1）。"
            ),
            "signal_category": "discrete",
        },
        matched_profiles=[
            {
                # 该 Block 画像缺少 OneState/ZeroState 与 bit0 子信号定义，
                # HLR 明确断言了 bit0=1 的状态语义，ICD 信息不足以验证 → 应判 needs_review
                "block_key": "L145_DIS_00_SYS1",
                "signal_family": "Discrete_Output_Sys1",
                "label": "145",
                "direction": "send",
                "bus_types": ["A429"],
                "channel_count": 1,
                "merged_attributes": {
                    "DataFormatType": "UnsignedInteger",
                    "ParameterSize": "19 Bits",
                },
                "sub_signals": [],
            }
        ],
        match_evidence={
            "match_type": "待确定",
            "hlr_labels": ["L145"],
            "matched_block_count": 1,
            "top_scores": [
                {"block_key": "L145_DIS_00_SYS1", "total": 52,
                 "signal_family": "Discrete_Output_Sys1", "channel_count": 1}
            ],
        },
    ),
]

PROVIDERS = ["deepseek", "minimax", "qwen"]


def run_case(case: ReverseCase, system_prompt: str) -> MultiJudgeResult:
    judgments: dict[str, dict] = {}
    for provider in PROVIDERS:
        j = _judge_with_provider_sync(case, provider, system_prompt)
        judgments[provider] = j
        print(
            f"  [{provider}] {j['coverage_status']} "
            f"(conf={j.get('confidence', 0):.2f})\n"
            f"    analysis: {j.get('analysis', '')[:400]}\n"
        )
    return MultiJudgeResult(case_id=case.case_id, judgments=judgments)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    only = set(a.upper() for a in sys.argv[1:])
    system_prompt = _load_reverse_prompt()
    results = []
    for case in CASES:
        if only and case.case_id.upper() not in only:
            continue
        print(f"=== {case.case_id} ===")
        mr = run_case(case, system_prompt)
        results.append(mr)
        print()

    print("=== Consensus review ===")
    consensus = review_judgments(results)
    for r in consensus.results:
        print(
            f"  {r.case_id}: stars={r.star_rating} agreement={r.agreement_level} "
            f"final={r.final_coverage_status} conf={r.confidence:.2f}"
        )
        print(f"    final_analysis: {r.final_analysis[:400]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
