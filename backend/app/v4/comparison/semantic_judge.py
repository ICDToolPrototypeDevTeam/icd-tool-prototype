# -*- coding: utf-8 -*-
"""Semantic judge: calls LLM via abstraction layer to judge each case."""

from __future__ import annotations

import json
import sys
import time

from app.v4.llm import get_llm
from app.v4.models import ComparisonCase, JudgmentResult, ReverseCase, ReverseJudgmentResult
from app.v4.prompts import load_prompt


def _build_user_prompt(case: ComparisonCase) -> str:
    """Serialize a ComparisonCase as the user prompt."""
    parts = []
    parts.append("## EoICD 需求")
    parts.append(f"- ID: {case.eoicd_requirement.get('ird_id', 'N/A')}")
    parts.append(f"- 总线类型: {case.eoicd_requirement.get('bus_type', 'N/A')}")
    parts.append(f"- 描述: {case.eoicd_requirement.get('description', 'N/A')}")
    parts.append(f"- 来源: {case.eoicd_requirement.get('source', 'N/A')}")

    # Show signal context if available (profile-level aggregation)
    signal_ctx = case.eoicd_requirement.get("signal_context")
    if signal_ctx:
        parts.append("")
        parts.append("### 信号上下文（同信号的其他属性）")
        parts.append(f"- 信号画像: {signal_ctx.get('profile_key', 'N/A')}")
        parts.append(f"- Label号: {signal_ctx.get('label_value', 'N/A')}")
        parts.append(f"- 方向: {signal_ctx.get('direction', 'N/A')}")
        peer_attrs = signal_ctx.get("peer_attributes", {})
        if peer_attrs:
            parts.append("- 同信号其他属性:")
            for attr_name, attr_val in peer_attrs.items():
                parts.append(f"  - {attr_name} = {attr_val}")
        parts.append("")

    parts.append("")

    if case.candidates:
        parts.append("## 候选软件高层需求（Top-K）")
        for i, cand in enumerate(case.candidates, 1):
            parts.append(f"### 候选 {i}: {cand.hlr_id} (得分: {cand.score})")
            parts.append(f"内容: {cand.hlr_content}")
            if getattr(cand, 'rationale', ''):
                parts.append(f"基本原理: {cand.rationale}")
            parts.append(f"匹配字段: {', '.join(cand.matched_fields)}")
            parts.append("")
    else:
        parts.append("## 候选软件高层需求")
        parts.append("（无候选 — 匹配层未检索到任何相关 HLR）")
        parts.append("")
        parts.append("注意：此条 EoICD 需求未检索到候选 HLR。请判断：")
        parts.append("- 若 EoICD 描述的是 ICD 配置细节（位偏移、满量程值、数据格式等），")
        parts.append("  这些通常不在软件高层需求中定义 → 标记为 needs_review")
        parts.append("- 若是功能性需求但确实未召回 → 标记为 missing, confidence=0.5")
        parts.append("")

    parts.append("## 匹配证据")
    top_score = case.match_evidence.get("top_score", 0)
    parts.append(f"综合得分: {top_score}/100")
    parts.append("")
    if case.candidates:
        parts.append("各维度命中情况：")
        for cand in case.candidates[:1]:  # show top candidate detail
            for mf in cand.matched_fields:
                parts.append(f"  - {mf}")
    parts.append("")
    parts.append("请判断覆盖性并输出 JSON。")

    return "\n".join(parts)


def judge_cases(
    cases: list[ComparisonCase],
) -> list[JudgmentResult]:
    """Judge each ComparisonCase via LLM.

    Returns one JudgmentResult per case.
    Uses DEEPSEEK_API_KEY/BASE_URL/MODEL from environment.
    Set USE_MOCK_LLM=1 for offline development.
    """
    llm = get_llm("deepseek")
    system_prompt = load_prompt("forward_judge")
    total = len(cases)
    results: list[JudgmentResult] = []

    for idx, case in enumerate(cases):
        user_prompt = _build_user_prompt(case)
        result = _call_judge_api(
            llm=llm,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            case_id=case.case_id,
        )
        results.append(result)

        status = result.coverage_status
        conf = result.confidence
        print(
            f"  [judge] {case.case_id} ({idx + 1}/{total}) "
            f"{status} confidence={conf:.2f}",
            file=sys.stderr,
        )

        if idx < total - 1:
            time.sleep(0.3)

    return results


def _call_judge_api(
    llm,
    system_prompt: str,
    user_prompt: str,
    case_id: str,
    max_retries: int = 2,
) -> JudgmentResult:
    """Call LLM and parse JSON response for forward judgment."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(max_retries + 1):
        temperature = 0.1 if attempt == 0 else 0.1 + attempt * 0.1
        try:
            response = llm.chat(messages=messages, temperature=temperature)
            content = _extract_json(response["content"])
            data = json.loads(content)
            return JudgmentResult(
                case_id=case_id,
                coverage_status=data.get("coverage_status", "needs_review"),
                matched_hlr_ids=data.get("matched_hlr_ids", []),
                difference_type=data.get("difference_type", "需确认"),
                missing_points=data.get("missing_points", []),
                inconsistent_points=data.get("inconsistent_points", []),
                analysis=data.get("analysis", ""),
                suggested_action=data.get("suggested_action", ""),
                confidence=float(data.get("confidence", 0.5)),
            )
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            if attempt < max_retries:
                time.sleep(1.0)
                continue
            return JudgmentResult(
                case_id=case_id,
                coverage_status="needs_review",
                analysis=f"JSON parse error after retries: {e}",
                confidence=0.0,
            )
        except Exception as e:
            if attempt < max_retries:
                time.sleep(2.0)
                continue
            return JudgmentResult(
                case_id=case_id,
                coverage_status="needs_review",
                analysis=f"API error: {e}",
                confidence=0.0,
            )

    return JudgmentResult(
        case_id=case_id,
        coverage_status="needs_review",
        analysis="Max retries exceeded",
        confidence=0.0,
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

            # Sub-signals (bit-level layout within one A429 word)
            sub_signals = blk.get("sub_signals", [])
            if sub_signals:
                parts.append("- 字内子信号明细:")
                for ss in sub_signals:
                    parts.append(
                        f"  - {ss.get('dp_name', '?')}: "
                        f"bit{ss.get('bit_offset', '?')}, "
                        f"{ss.get('size', '?')}bit, "
                        f"{ss.get('dtype', '?')}"
                    )

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
    parts.append("")

    # ── Match evidence ──
    parts.append("## 匹配证据")
    evidence = case.match_evidence
    mt = evidence.get('match_type', 'N/A')
    parts.append(f"- 匹配类型: {mt}")
    if mt == "待确定":
        parts.append("- ⚠ 此匹配置信度较低（部分维度命中或分数偏低），请谨慎判断")
        parts.append("- 若 HLR 内容与 ICD 信号块确实无关（如仅提及 Label 号但无具体信号描述），标记为 needs_review")
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


def judge_reverse_cases(
    cases: list[ReverseCase],
) -> list[ReverseJudgmentResult]:
    """Judge each ReverseCase via LLM.

    Returns one ReverseJudgmentResult per case with source data embedded.
    """
    llm = get_llm("deepseek")
    system_prompt = load_prompt("reverse_judge")
    total = len(cases)
    results: list[ReverseJudgmentResult] = []

    for idx, case in enumerate(cases):
        user_prompt = _build_reverse_user_prompt(case)
        result = _call_reverse_judge_api(
            llm=llm,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            case=case,
        )
        results.append(result)

        status = result.coverage_status
        conf = result.confidence
        print(
            f"  [rev-judge] {case.case_id} ({idx + 1}/{total}) "
            f"{status} confidence={conf:.2f}",
            file=sys.stderr,
        )

        if idx < total - 1:
            time.sleep(0.3)

    return results


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
                coverage_status="needs_review",
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
                coverage_status="needs_review",
                analysis=f"API error: {e}",
                confidence=0.0,
                **source_fields,
            )

    return ReverseJudgmentResult(
        case_id=case_id,
        coverage_status="unmatched",
        analysis="Max retries exceeded",
        confidence=0.0,
        **source_fields,
    )
