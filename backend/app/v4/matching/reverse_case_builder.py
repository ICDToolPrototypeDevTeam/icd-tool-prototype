# -*- coding: utf-8 -*-
"""Reverse case builder: converts reverse match results into agent-judgeable cases.

Script responsibility: MATCHING only — package HLR + matched EoICD blocks into
structured cases for the AI judge.
"""

from __future__ import annotations

from app.v4.config import REVERSE_KEY_ATTRS
from app.v4.matching.signal_profiler import SignalProfile, ICDBlock
from app.v4.models import (
    HLRCoverageResult,
    ReverseCase,
    ReverseMatchOutput,
)


def _serialize_block(block: ICDBlock) -> dict:
    """Serialize an ICDBlock into a judge-friendly dict.

    Block-level only: signal_family, direction, bus_types, merged attributes.
    Per-channel detail is intentionally omitted — merged_attributes covers the
    common attributes across all channels.
    """
    merged_attrs = {}
    for k, v in block.attributes.items():
        if k in REVERSE_KEY_ATTRS:
            merged_attrs[k] = v["value"] + (f" {v['unit']}" if v.get("unit") else "")

    result = {
        "block_key": block.block_key,
        "signal_family": block.signal_family,
        "label": block.label,
        "direction": block.direction,
        "bus_types": sorted(block.bus_types),
        "channel_count": block.channel_count,
        "merged_attributes": merged_attrs,
    }
    if block.sub_signals:
        result["sub_signals"] = block.sub_signals
    return result


def build_reverse_cases(
    match_output: ReverseMatchOutput,
    block_index: dict[str, ICDBlock],
) -> list[ReverseCase]:
    """Convert reverse match results into agent-judgeable ReverseCases.

    "已匹配" and "待确定" cases are both sent to the AI judge. The match_type
    in the evidence tells the AI how confident the match is.
    "无匹配" cases skip AI — they are marked for human review in the report directly.
    """
    cases: list[ReverseCase] = []
    case_num = 0

    for result in match_output.results:
        if result.match_type not in ("已匹配", "待确定"):
            continue

        case_num += 1
        case_id = f"REV-{case_num:04d}"

        matched: list[dict] = []
        for key in result.matched_profile_keys:
            if key in block_index:
                matched.append(_serialize_block(block_index[key]))

        hlr_req = {
            "hlr_id": result.hlr_id,
            "content": result.hlr_content,
            "rationale": result.hlr_rationale,
            "signal_category": result.signal_category,
        }

        cases.append(ReverseCase(
            case_id=case_id,
            hlr_requirement=hlr_req,
            matched_profiles=matched,
            match_evidence=result.match_evidence,
        ))

    return cases
