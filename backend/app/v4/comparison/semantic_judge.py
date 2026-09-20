# -*- coding: utf-8 -*-
"""Semantic judge: calls LLM via abstraction layer to judge each case."""

from __future__ import annotations

import json
import re
import time

from app.v4.matching.hlr_classifier import (
    extract_bit_fields,
    extract_bit_range_assertions,
    extract_bit_value_assertions,
)
from app.v4.models import ReverseCase, ReverseJudgmentResult

# 调用参数指纹：既用于实际调用，也编入缓存 key（llm_cache.compute_key）。
# 改这里 ⇒ key 变化 ⇒ 旧缓存自动失效。
REVERSE_JUDGE_PARAMS = {"temperature": 0.1, "max_tokens": 8192}


def _to_float(v) -> float | None:
    """Parse a display string like '-512' / '12 Bits' / '1000 ms' to float."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def _append_bnr_sign_derivation(parts: list[str], merged: dict) -> None:
    """If the merged data-field attrs prove a signed BNR field (negative
    FullScaleRngMin), state the arithmetic consequence: the field is two's
    complement and its top physical bit is the sign bit.

    Pure derivation from the block's own numbers — a 12-bit field with
    LSB 0.25 unsigned can only span 0..1023.75, so a range min of -512
    forces signedness; the sign bit of two's complement is the MSB.
    """
    if not merged:
        return
    if str(merged.get("DataFormatType", "")).strip() != "BNR":
        return
    rng_min = _to_float(merged.get("FullScaleRngMin"))
    offset = _to_float(merged.get("BitOffsetWithinDS"))
    size = _to_float(merged.get("ParameterSize"))
    if rng_min is None or rng_min >= 0 or offset is None or size is None:
        return
    top_phys = int(offset + size)
    top_off = int(top_phys - 1)
    parts.append(
        f"- [推导] 数据字段 offset={int(offset)}, size={int(size)} ⇒ 覆盖物理第"
        f"{int(offset) + 1}~{top_phys}位；FullScaleRngMin={merged.get('FullScaleRngMin')} < 0 "
        f"(无符号 {int(size)} 位×LsbRes 只能取非负值) ⇒ 该字段为二进制补码有符号数，"
        f"最高位(0基 offset={top_off}，物理第{top_phys}位)为符号位，已包含在 "
        f"ParameterSize={merged.get('ParameterSize')} 之内。"
    )


def _dedupe_coded_fields(
    matched_profiles: list[dict] | None, offset: int, size: int
) -> list[tuple[str, str]]:
    """Collect (field name, CodedSet) for protocol fields whose bit span
    equals ``offset``/``size``, deduped by first occurrence (no set
    iteration — order must stay deterministic for prompt/cache stability)."""
    seen: list[tuple[str, str]] = []
    for blk in matched_profiles or []:
        for pf in blk.get("word_protocol_fields", []) or []:
            attrs = pf.get("attrs", {}) or {}
            if _to_float(attrs.get("BitOffsetWithinDS")) != offset:
                continue
            if _to_float(attrs.get("ParameterSize")) != size:
                continue
            coded = str(attrs.get("CodedSet", "") or "").strip()
            if not coded:
                continue
            entry = (str(pf.get("name", "")), coded)
            if entry not in seen:
                seen.append(entry)
    return seen


def _append_bit_assembly_derivation(
    parts: list[str], hlr_content: str, matched_profiles: list[dict] | None
) -> None:
    """Spell out the deterministic bit-pair → code-value assembly for HLR
    assertions like "第9和10位分别设置为“1”和“0”" or "bit8=0，bit9=0".

    Judging this needs the chain bit-number → weight → assembled value →
    ICD CodedSet mapping. Left to each model's own mental arithmetic the
    chain is unstable (the same input has flipped verdicts across runs), so
    it is computed here from the word's own convention (larger bit number =
    more significant within the ARINC 429 word) and injected as data. The
    field's CodedSet definition is quoted only when it is unique across
    matched blocks — heterogeneous definitions cannot be summarized by
    joining them without ambiguity, and each block's own CodedSet is already
    visible in the matched evidence. A pure no-op when no qualifying
    assertion is present, keeping prompts of other cases byte-identical
    (and their cached judgments valid).
    """
    if not hlr_content:
        return
    for a in extract_bit_value_assertions(hlr_content):
        positions, values = a["positions"], a["values"]
        pair = dict(zip(positions, values))
        lo = min(positions)
        size = max(positions) - lo + 1
        terms = []
        assembled = 0
        for pos in sorted(positions):
            value = int(pair[pos])
            weight = 2 ** (pos - lo)
            terms.append(f"{value}×{weight}")
            assembled += value * weight
        # 中文"第N位"是 1 基物理位（offset=N-1）；英文 bitN 与 ICD 偏移同基准。
        offset = lo - 1 if a["convention"] == "cn-physical" else lo
        line = (
            f"- [推导] 位赋值组合：{a['text']} ⇒ 按位号约定（ARINC 429 字内位号"
            f"越大越高位）组装编码值 = {' + '.join(terms)} = {assembled}"
        )
        entries = _dedupe_coded_fields(matched_profiles, offset, size)
        if entries:
            line += (
                f"；该位置对应 ICD 字段 {entries[0][0]}"
                f"（offset={offset}, size={size}）"
            )
            codedsets = [entries[0][1]]
            for _, coded in entries[1:]:
                if coded not in codedsets:
                    codedsets.append(coded)
            if len(codedsets) == 1:
                line += f"，其取值定义：{codedsets[0]}"
        line += f"。比对时请直接采用编码值 {assembled}，勿再按书写顺序自行组装位对。"
        parts.append(line)


def _append_bit_range_derivation(parts: list[str], hlr_content: str) -> None:
    """Spell out the normalized 0-based span for "bitN至bitM" range assertions.

    English bitN shares the ICD offset base (bitN = 0-based offset N =
    physical position N+1), but the anchor note only teaches the Chinese
    "第N位" physical→0-based conversion. Left to their own arithmetic the
    judges have applied that -1 to English range forms too — a range
    identical to the ICD field ("bit17至bit28" vs offset=17, size=12) was
    flipped to inconsistent by all three providers; another case showed the
    reverse over-conversion on the ICD side. Computed here once and injected
    as data. A pure no-op when no range assertion is present, keeping other
    prompts byte-identical (and their cached judgments valid).
    """
    if not hlr_content:
        return
    for a in extract_bit_range_assertions(hlr_content):
        lo, hi = a["lo"], a["hi"]
        parts.append(
            f"- [推导] 位段声明：{a['text']} ⇒ 英文 bitN 与 ICD "
            f"BitOffsetWithinDS 同一基准（bitN 即 0 基 offset N，对应物理第 N+1 位）"
            f"⇒ 归一化位段 = offset {lo}~{hi}"
            f"（物理第{lo + 1}~{hi + 1}位，共{hi - lo + 1}位）；"
            f"比对时请直接采用该归一化位段，勿再自行做 ±1 换算。"
        )


def _extract_json(text: str) -> str:
    """Extract JSON object from text, with basic repair for truncated responses."""
    # Strip MiniMax/DeepSeek <think> reasoning blocks
    import re
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    text = text.strip()
    # Remove markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    else:
        # text 不以 ``` 开头（minimax re-review 场景）：```json fence 前可能有
        # 大段 markdown 分析。先在 text 内部搜索 ```json fence，提取其中的 {...}；
        # 若没有 fence，再退到找首个 { 的位置。
        fence_match = re.search(
            r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```',
            text, flags=re.DOTALL,
        )
        if fence_match:
            text = fence_match.group(1).strip()
        else:
            brace_idx = text.find("{")
            if brace_idx > 0:
                text = text[brace_idx:]
    # Repair truncated JSON: close unterminated strings and missing braces
    if text and text[0] == "{":
        # Count unescaped quotes — if odd, the last string is unterminated
        in_string = False
        escape = False
        for i, ch in enumerate(text):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
        if in_string:
            text = text + '"'
        # Close missing braces
        open_braces = text.count("{") - text.count("}")
        text = text + "}" * open_braces
    return text




def _build_reverse_user_prompt(case: ReverseCase) -> str:
    """Serialize a ReverseCase as the user prompt for ICD-centric reverse judgment.

    Structure: ICD Block (benchmark) FIRST → HLR (implementation) SECOND.
    The AI judges whether HLR correctly implements what ICD defines.
    """
    parts = []
    hlr = case.hlr_requirement
    anchor_note_added = False

    # ── ICD Block (benchmark) ──
    if case.matched_profiles:
        parts.append("## EoICD 信号块（ICD 基准定义）")
        parts.append("以下 ICD Block 是接口定义的权威来源。请以此为准，检查 HLR 中的落实情况。")
        parts.append("")
        for i, blk in enumerate(case.matched_profiles, 1):
            parts.append(f"### ICD Block {i}: {blk.get('signal_family', 'N/A')}")
            parts.append(f"- Block Key: {blk.get('block_key', 'N/A')}")
            parts.append(f"- Label号: {blk.get('label', 'N/A')}")
            parts.append(f"- 方向: {blk.get('direction', 'N/A')}")
            parts.append(f"- 总线类型: {', '.join(blk.get('bus_types', []))}")
            parts.append(f"- 通道变体数: {blk.get('channel_count', 0)}")

            # Merged attributes (block level, common across all channels)
            merged = blk.get("merged_attributes", {})
            if merged:
                parts.append("- 信号级公共属性:")
                for attr_name, attr_val in merged.items():
                    parts.append(f"  - {attr_name} = {attr_val}")
                _append_bnr_sign_derivation(parts, merged)

            # Sub-signals (bit-level layout within one A429 word)
            sub_signals = blk.get("sub_signals", [])
            if sub_signals:
                parts.append("- 字内子信号明细:")
                for ss in sub_signals:
                    state_suffix = ""
                    if (
                        ss.get("one_state") is not None
                        or ss.get("zero_state") is not None
                    ):
                        state_suffix = (
                            f"（OneState={ss.get('one_state', '—')} / "
                            f"ZeroState={ss.get('zero_state', '—')}）"
                        )
                    parts.append(
                        f"  - {ss.get('dp_name', '?')}: "
                        f"bit{ss.get('bit_offset', '?')}, "
                        f"{ss.get('size', '?')}bit, "
                        f"{ss.get('dtype', '?')}{state_suffix}"
                    )

            # Same-word protocol field definitions — context only, not a
            # matching object. Anchors expose the word's bit layout
            # (LABEL/SDI/SSM/PARITY offsets+size, SDI CodedSet) so the judge
            # can verify HLR assertions about protocol bits (e.g. "将第9和10位
            # 设置为1/0" → SDI=1) against ICD data alone.
            proto_fields = blk.get("word_protocol_fields", [])
            if proto_fields:
                parts.append("- 同 word 协议字段（ICD 位偏移锚点，仅作比对上下文）:")
                for pf in proto_fields:
                    name = pf.get("name", "?")
                    attrs = pf.get("attrs", {})
                    parts.append(
                        f"  - {name}: "
                        + ", ".join(f"{k} = {v}" for k, v in attrs.items())
                    )
                if not anchor_note_added:
                    # 基制说明由上方 ICD 锚点数据直接支撑（如 LABEL offset=0 →
                    # 物理第1~8位、SDI offset=8 → 物理第9~10位），非外部规则。
                    parts.append(
                        "  位偏移约定：BitOffsetWithinDS 为 0 基偏移，offset=N 对应"
                        "物理位第 N+1 位；HLR 正文的“第 N 位”/“第N到M位”是物理位号，"
                        "判定时请换算为 0 基后与 ICD 位偏移比对。"
                    )
                    anchor_note_added = True

            parts.append("")
    else:
        parts.append("## EoICD 信号块（ICD 基准定义）")
        parts.append("（无匹配 — 匹配层未在 EoICD 中找到对应该 HLR 的信号定义）")
        parts.append("")

    # ── HLR (implementation to check) ──
    parts.append("## 软件高层需求 (HLR) — 待检查的软件实现")
    parts.append(f"- ID: {hlr.get('hlr_id', 'N/A')}")
    parts.append(f"- 内容: {hlr.get('content', 'N/A')}")
    hlr_rationale = hlr.get('rationale', '')
    if hlr_rationale:
        parts.append(f"- 基本原理: {hlr_rationale}")

    # HLR 正文位声明结构化呈现（物理位号 → 0 基 offset/size），让裁判直接
    # 比对本行与 ICD 锚点，避免各家对“第 N 位”自行换算时错位（如把第31位
    # 误当 PARITY 的物理32位）。
    bit_decls = extract_bit_fields(hlr.get('content', ''))
    if bit_decls:
        parts.append("- HLR 位声明（物理位号→0基换算，由正文提取，仅供比对）:")
        for bf in bit_decls:
            parts.append(
                f"  - {bf.get('text', '?')} → offset={bf.get('offset')}, size={bf.get('size')}"
            )
    _append_bit_assembly_derivation(
        parts, hlr.get('content', ''), case.matched_profiles
    )
    _append_bit_range_derivation(parts, hlr.get('content', ''))
    parts.append("")

    # ── Match evidence ──
    parts.append("## 匹配证据")
    evidence = case.match_evidence
    mt = evidence.get('match_type', 'N/A')
    parts.append(f"- 匹配类型: {mt}")
    if mt == "待确定":
        parts.append("- ⚠ 此匹配置信度较低（部分维度命中或分数偏低），请谨慎判断，confidence 适当下调")
        parts.append("- 若能确认 ICD 要求已在 HLR 中落实或不一致，正常判断即可")
    parts.append(f"- HLR Labels: {evidence.get('hlr_labels', [])}")
    parts.append(f"- 匹配 Block 数: {evidence.get('matched_block_count', 0)}")

    top_scores = evidence.get("top_scores", [])
    if top_scores:
        parts.append("- 最高分匹配:")
        for ts in top_scores:
            parts.append(f"  - {ts.get('block_key', '?')}: {ts.get('total', 0)}分 "
                         f"(信号族={ts.get('signal_family', '?')}, "
                         f"通道={ts.get('channel_count', 0)})")

    parts.append("")
    mt = evidence.get("match_type", "")
    if mt == "待确定":
        parts.append("请以 ICD 信号块为基准，判断其定义的接口要求是否在 HLR 中得到了落实。")
        parts.append("注意：此匹配的可靠度较低，HLR 可能仅笼统引用了 Label 号而未描述具体信号，请据此调整 confidence。")
    else:
        parts.append("请以 ICD 信号块为基准，判断其定义的接口要求是否在 HLR 中得到了正确落实，并输出 JSON。")

    return "\n".join(parts)


def _call_reverse_judge_api(
    llm,
    system_prompt: str,
    user_prompt: str,
    case: ReverseCase,
    max_retries: int = 2,
) -> ReverseJudgmentResult:
    """Call LLM and parse JSON response for reverse judgment.

    Populates source data (hlr_id, hlr_content, matched_profiles) on the result
    so the report is self-contained and traceable.
    """
    case_id = case.case_id
    hlr = case.hlr_requirement
    report_evidence = {k: v for k, v in case.match_evidence.items() if k != "top_scores"}
    source_fields = dict(
        hlr_id=hlr.get("hlr_id", ""),
        hlr_content=hlr.get("content", ""),
        signal_category=hlr.get("signal_category", ""),
        matched_profiles_summary=[b.get("block_key", "") for b in case.matched_profiles],
        match_evidence=report_evidence,
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(max_retries + 1):
        try:
            response = llm.chat(messages=messages, temperature=0.1, max_tokens=8192)
            content = _extract_json(response["content"])
            data = json.loads(content)
            return ReverseJudgmentResult(
                case_id=case_id,
                coverage_status=data.get("coverage_status", "needs_review"),
                difference_type=data.get("difference_type", "需确认"),
                missing_points=data.get("missing_points", []),
                inconsistent_points=data.get("inconsistent_points", []),
                analysis=data.get("analysis", ""),
                suggested_action=data.get("suggested_action", ""),
                confidence=float(data.get("confidence", 0.5)),
                **source_fields,
            )
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            if attempt < max_retries:
                time.sleep(1.0)
                continue
            return ReverseJudgmentResult(
                case_id=case_id,
                coverage_status="error",
                analysis=f"JSON parse error after retries: {e}",
                confidence=0.0,
                **source_fields,
            )
        except Exception as e:
            if attempt < max_retries:
                time.sleep(2.0)
                continue
            return ReverseJudgmentResult(
                case_id=case_id,
                coverage_status="error",
                analysis=f"API error: {e}",
                confidence=0.0,
                **source_fields,
            )

    return ReverseJudgmentResult(
        case_id=case_id,
        coverage_status="error",
        analysis="Max retries exceeded",
        confidence=0.0,
        **source_fields,
    )
