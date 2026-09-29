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


_STATE_ATTRS = ("OneState", "ZeroState")


def _field_state_map(block: ICDBlock) -> dict[str, dict[str, str]]:
    """Per-field state values from DP-ref rows: {dp_ref_name: {attr: value}}."""
    states: dict[str, dict[str, str]] = {}
    for prof in block.profiles:
        for entry in prof.entries:
            if (
                entry.is_dp_ref
                and entry.dp_ref_name
                and entry.attribute_name in _STATE_ATTRS
            ):
                states.setdefault(entry.dp_ref_name, {})[
                    entry.attribute_name
                ] = str(entry.attribute_value)
    return states


def _serialize_block(block: ICDBlock, include_protocol_fields: bool = True) -> dict:
    """Serialize an ICDBlock into a judge-friendly dict.

    Block-level only: signal_family, direction, bus_types, merged attributes.
    Per-channel detail is intentionally omitted — merged_attributes covers the
    common attributes across all channels.

    When the block carries per-field OneState/ZeroState rows and every such
    field appears in ``sub_signals``, the states move from the block-level
    merged attributes onto the matching sub-signal rows (``one_state`` /
    ``zero_state``), so the judge sees each state attached to its own field
    instead of a same-value block-level pair.

    ``include_protocol_fields`` attaches the same-word SSM bit definitions
    (``word_protocol_fields``) once per label per case — see build_reverse_cases.
    """
    merged_attrs = {}
    for k, v in block.attributes.items():
        if k in REVERSE_KEY_ATTRS:
            merged_attrs[k] = v["value"] + (f" {v['unit']}" if v.get("unit") else "")

    sub_signals = block.sub_signals
    if sub_signals:
        states = _field_state_map(block)
        field_names = {ss.get("dp_name") for ss in sub_signals}
        if states and all(name in field_names for name in states):
            merged_attrs = {
                k: v for k, v in merged_attrs.items() if k not in _STATE_ATTRS
            }
            sub_signals = [
                dict(
                    ss,
                    one_state=states.get(ss.get("dp_name"), {}).get("OneState"),
                    zero_state=states.get(ss.get("dp_name"), {}).get("ZeroState"),
                )
                for ss in sub_signals
            ]

    result = {
        "block_key": block.block_key,
        "signal_family": block.signal_family,
        "label": block.label,
        "direction": block.direction,
        "bus_types": sorted(block.bus_types),
        "channel_count": block.channel_count,
        "merged_attributes": merged_attrs,
    }
    if sub_signals:
        result["sub_signals"] = sub_signals
    if include_protocol_fields and block.word_protocol_fields:
        result["word_protocol_fields"] = block.word_protocol_fields
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
        enriched_labels: set[str] = set()
        for key in result.matched_profile_keys:
            if key not in block_index:
                continue
            block = block_index[key]
            # SSM 位定义附注每个 label 只附一次，避免同一 word 的多个
            # Block 重复携带相同附注、撑大裁判输入。
            attach = block.label is None or block.label not in enriched_labels
            matched.append(_serialize_block(block, include_protocol_fields=attach))
            if attach and block.label:
                enriched_labels.add(block.label)

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
