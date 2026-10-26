# -*- coding: utf-8 -*-
"""Reverse case builder: converts reverse match results into agent-judgeable cases.

Script responsibility: MATCHING only — package HLR + matched EoICD blocks into
structured cases for the AI judge.
"""

from __future__ import annotations

import re

from app.v4.config import REVERSE_KEY_ATTRS
from app.v4.matching.signal_profiler import SignalProfile, ICDBlock
from app.v4.models import (
    HLRCoverageResult,
    ReverseCase,
    ReverseMatchOutput,
)


_STATE_ATTRS = ("OneState", "ZeroState")


def _extract_word_name(signal_name: str, label: str | None) -> str:
    """Return the A429 word segment carrying ``label`` from a signal path.

    Two EoICD path shapes occur:

    - hierarchical (mostly Publisher): ``...Msg.L272_ACS_Status_Word1.PACK_OVHT``
      — the label-bearing segment is the word above the leaf;
    - flat (some Subscriber rows): ``...pi429_A1_272_1.L272_1_A1_...`` —
      the leaf carries label + optional word index + channel; the word
      identity is the ``L272_1`` prefix (channel suffix excluded), so
      A1/A2/B1/B2/S1/S2 instances of one word stay together.

    Returns "" for non-A429 paths or paths that do not carry ``label``.
    """
    if not signal_name or not label:
        return ""
    prefix = f"L{label}"
    segments = [s.strip() for s in signal_name.split(".") if s.strip()]
    for idx, segment in enumerate(segments):
        upper = segment.upper()
        if upper != prefix.upper() and not upper.startswith(prefix.upper() + "_"):
            continue
        if idx < len(segments) - 1:
            # Hierarchical path: the parent segment is the word itself.
            return segment
        # Flat path: strip the channel/layer suffix after the optional
        # numeric word index (e.g. L272_1_A1_... -> L272_1).
        m = re.match(
            rf"^{re.escape(prefix)}(?:_\d+)?(?=_|$)", segment, re.IGNORECASE
        )
        return m.group(0) if m else segment
    return ""


def _block_provenance(block: ICDBlock) -> tuple[list[str], list[str]]:
    """Aggregate table sources and A429 words from every entry in the block."""
    sources: set[str] = set()
    words: set[str] = set()
    for prof in block.profiles:
        for entry in prof.entries:
            if entry.source:
                sources.add(entry.source)
            word = _extract_word_name(entry.signal_name, block.label)
            if word:
                words.add(word)
    return sorted(sources), sorted(words)


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


def _field_coded_map(block: ICDBlock) -> dict[str, str]:
    """Per-field CodedSet from DP-ref rows: {dp_ref_name: coded_set}.

    A multi-sub-signal word carries one CodedSet per field (e.g. SN "1=OK"
    and validity "1=Invalid;0=Valid"). ``build_profiles`` merges them into
    one block-level string, which loses the field binding, so the judge
    cannot tell which value belongs to which sub-signal. Collect each
    field's own definition here and attach it to the sub-signal row.
    Distinct values for one field are joined in first-seen order (same
    convention as the block-level merge).
    """
    coded: dict[str, str] = {}
    for prof in block.profiles:
        for entry in prof.entries:
            if not (
                entry.is_dp_ref
                and entry.dp_ref_name
                and entry.attribute_name == "CodedSet"
            ):
                continue
            val = (
                str(entry.attribute_value)
                if entry.attribute_value is not None
                else ""
            )
            if not val:
                continue
            cur = coded.get(entry.dp_ref_name, "")
            if val not in cur.split("/"):
                coded[entry.dp_ref_name] = f"{cur}/{val}" if cur else val
    return coded


def _serialize_block(block: ICDBlock, include_protocol_fields: bool = True) -> dict:
    """Serialize an ICDBlock into a judge-friendly dict.

    Block-level only: signal_family, direction, bus_types, merged attributes.
    Per-channel detail is intentionally omitted — merged_attributes covers the
    common attributes across all channels.

    When the block carries per-field OneState/ZeroState rows and every such
    field appears in ``sub_signals``, the states move from the block-level
    merged attributes onto the matching sub-signal rows (``one_state`` /
    ``zero_state``), so the judge sees each state attached to its own field
    instead of a same-value block-level pair. Each sub-signal row likewise
    carries its own ``coded_set`` when the DP-ref row defines one; the
    block-level CodedSet remains only as the merged reference value.

    ``include_protocol_fields`` attaches the same-word SSM bit definitions
    (``word_protocol_fields``) once per label per case — see build_reverse_cases.
    """
    merged_attrs = {}
    for k, v in block.attributes.items():
        if k in REVERSE_KEY_ATTRS:
            merged_attrs[k] = v["value"] + (f" {v['unit']}" if v.get("unit") else "")

    sub_signals = [dict(ss) for ss in block.sub_signals]
    if sub_signals:
        coded_map = _field_coded_map(block)
        for ss in sub_signals:
            coded = coded_map.get(ss.get("dp_name", ""))
            if coded:
                ss["coded_set"] = coded
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
    sources, words = _block_provenance(block)
    if sources:
        result["sources"] = sources
    if words:
        result["words"] = words
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
