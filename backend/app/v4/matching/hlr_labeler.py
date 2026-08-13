# -*- coding: utf-8 -*-
"""HLR AI pre-labeling: one-time batch labeling of HLR requirements via DeepSeek API."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from app.v4.llm.factory import get_llm
from app.v4.models import HLRLabel, HLRLabelOutput, HLRRequirement

SYSTEM_PROMPT = """你是一个航空/车辆接口控制文档（ICD）的领域专家。你的任务是对一条软件高层需求（HLR）提取结构化标签，用于后续的匹配和检索。

对每条 HLR 需求正文，提取以下标签，输出严格 JSON：

{
  "bus_types": ["涉及的通信总线/协议类型"],
  "labels": ["涉及的A429 Label号，如L32、L34"],
  "devices": ["涉及的设备/组件/子系统名称"],
  "signal_keywords": ["涉及的信号/参数/数据项关键词，中英文均可"],
  "attr_categories": ["涉及的属性类别"],
  "direction_keywords": ["表示数据流向的实际动词，如发送、接收、采集、写入等"]
}

字段说明：
- bus_types: 从 A429, A664, A825, Analog, Discrete 中选择（可多选）。其中 CAN 总线统一归为 A825，AFDX 统一归为 A664，ARINC429 统一归为 A429。未明确提到总线名称但涉及通信的，根据上下文推断后使用上述标准名称。
- labels: 提取 A429 Label 标识，如 "L32", "L34"。不要编造不存在的 Label。
- devices: 提取设备/组件/子系统，如 "风扇", "RFAN", "FCM", "ADC"。设备名称保持原文形式，中文原文保留中文，英文保留英文。
- signal_keywords: 提取信号/参数/数据项关键词语，中英文都可以。如 "速度", "SPEED", "RPM", "温度", "状态"。
- attr_categories: 从以下类别中选择：时序, 大小, 数据布局, 位宽, 数据格式, 范围, 编码/状态, Label号, SDI, SSM, 硬件/配置, 通信协议。只选择明确涉及的类别。
- direction_keywords: 从原文中提取表示数据流向的实际动词，如 "发送", "写入", "接收", "采集", "解析" 等。

重要：
- 只输出 JSON，不要输出任何解释文字。
- 只提取原文中确实存在或可合理推断的标签，不要凭空编造。
- 如果某字段没有对应的内容，输出空列表 []。"""


def _build_label_prompt(hlr: HLRRequirement) -> str:
    return f"需求ID: {hlr.requirement_id}\n需求正文: {hlr.content}"


def _call_label_api(
    llm,
    hlr: HLRRequirement,
    max_retries: int = 2,
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> HLRLabel:
    """Call DeepSeek API via factory `llm` to label one HLR requirement.

    Goes through `app.v4.llm.factory.get_llm` so retries / URL construction /
    response unwrapping live in exactly one place. Outer retry loop catches:
      - JSON parse errors (model returned garbage)
      - any other exception (network, factory `ValueError` when missing key)
    Factory internal retry is disabled (max_retries=0) to avoid 3x3 retry storm.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_label_prompt(hlr)},
    ]

    for attempt in range(max_retries + 1):
        try:
            response = llm.chat(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                max_retries=0,
            )
            content = (response["content"] or "").strip()

            # Strip markdown fences that some models wrap JSON in
            if content.startswith("```"):
                lines = content.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                content = "\n".join(lines)

            data = json.loads(content)
            return _build_label_from_api(hlr, data)

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            if attempt < max_retries:
                time.sleep(1.0)
                continue
            print(f"  [label] {hlr.requirement_id}: JSON parse error — {e}", file=sys.stderr)
        except Exception as e:
            if attempt < max_retries:
                time.sleep(2.0)
                continue
            print(f"  [label] {hlr.requirement_id}: API error — {e}", file=sys.stderr)

    return _fallback_label(hlr)


def _build_label_from_api(hlr: HLRRequirement, data: dict) -> HLRLabel:
    """Build HLRLabel from AI response, with validation."""
    bus_types = data.get("bus_types", []) or []
    labels = data.get("labels", []) or []
    devices = data.get("devices", []) or []
    signal_keywords = data.get("signal_keywords", []) or []
    attr_categories = data.get("attr_categories", []) or []
    direction_keywords = data.get("direction_keywords", []) or []

    # Build enriched_text (content only; structured labels handled by 6 dims)
    enriched = hlr.content

    return HLRLabel(
        hlr_id=hlr.requirement_id,
        bus_types=bus_types,
        labels=labels,
        devices=devices,
        signal_keywords=signal_keywords,
        attr_categories=attr_categories,
        direction_keywords=direction_keywords,
        enriched_text=enriched,
    )


def _fallback_label(hlr: HLRRequirement) -> HLRLabel:
    """Fallback: empty labels, enriched_text = original content."""
    return HLRLabel(
        hlr_id=hlr.requirement_id,
        enriched_text=hlr.content,
    )


def label_hlrs(
    hlr_reqs: list[HLRRequirement],
    cache_path: Path | None = None,
) -> dict[str, HLRLabel]:
    """Label all HLR requirements via the LLM factory, with file-based caching.

    Args:
        hlr_reqs: Parsed HLR requirements.
        cache_path: Path to hlr_labels.json cache file.

    Returns:
        dict mapping hlr_id → HLRLabel.

    LLM client (api_key / base_url / model / mock flag) is read from env via
    `app.v4.llm.factory.get_llm`. Per-arg overrides removed in 2026-07-27
    because every other LLM site in V4 also goes through this single channel.
    """
    # Try cache first
    if cache_path and cache_path.exists():
        print(f"  [label] Loading cached labels from {cache_path}")
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            return {hlr_id: HLRLabel(**lbl) for hlr_id, lbl in data.get("labels", {}).items()}
        except Exception:
            print(f"  [label] Cache invalid, re-labeling...", file=sys.stderr)

    try:
        llm = get_llm("deepseek")
    except Exception as e:
        # factory raises ValueError when API key is missing and mock is off;
        # fall back to empty labels (callers can store them in cache).
        print(f"  [label] {type(e).__name__}: {e} — using fallback (empty labels)", file=sys.stderr)
        return {hlr.requirement_id: _fallback_label(hlr) for hlr in hlr_reqs}

    labels: dict[str, HLRLabel] = {}
    total = len(hlr_reqs)

    print(f"  [label] Labeling {total} HLRs via {llm.model}...")
    for idx, hlr in enumerate(hlr_reqs):
        lbl = _call_label_api(llm, hlr)
        labels[hlr.requirement_id] = lbl
        print(f"  [label] {idx + 1}/{total} {hlr.requirement_id} "
              f"bus={lbl.bus_types} devices={lbl.devices[:3]}...")
        if idx < total - 1:
            time.sleep(0.2)

    # Persist cache
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        output = HLRLabelOutput(
            total_labeled=len(labels),
            labels=labels,
        )
        cache_path.write_text(
            output.model_dump_json(indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  [label] Labels cached to {cache_path}")

    return labels
