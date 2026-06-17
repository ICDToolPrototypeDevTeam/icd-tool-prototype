"""
mock_llm.py —— 结构化 mock LLM（不联网、可复现）。

设计原则：
- 不修改 prompts/*.md / skills/*.md 文本资产
- 接收 Agent / Task 传过来的 messages，输出与 CrewAI Task.output_pydantic 严格对齐的 JSON 字符串
- role 区分当前调用场景（minimax_generation / deepseek_generation / minimax_scoring / deepseek_scoring / deepseek_comparison）
- 不依赖网络，可在 USE_MOCK_LLM=1 时让端到端流程完整跑通

实现：继承 crewai.BaseLLM（Pydantic 模型），实现抽象方法 call()。
"""

from __future__ import annotations

import json
from typing import Any

from crewai import BaseLLM  # type: ignore


# ============================================================================
# Mock 数据生成器：与原 Issue #4 stub 数据保持一致，便于 demo 连续性
# ============================================================================


def _generation_mock_data(chunk_id: str, model_name: str) -> dict[str, Any]:
    """返回 generation Task 应输出的 JSON 结构（与 GenerationOutput 对齐）。"""
    if model_name == "MiniMax":
        entries = _entries_interface_level(chunk_id, model_name)
        summary = (
            f"MiniMax 候选：接口级条目化方式，每个信号独立成条，描述精确，"
            f"适合精确追溯场景。来源 chunk={chunk_id}。"
        )
    else:
        entries = _entries_function_level(chunk_id, model_name)
        summary = (
            f"DeepSeek 候选：功能级条目化方式，同一接口多个信号合并描述，"
            f"表述简洁，适合快速理解场景。来源 chunk={chunk_id}。"
        )
    return {
        "candidate_id": f"{chunk_id}@{model_name.lower()}",
        "chunk_id": chunk_id,
        "model_name": model_name,
        "entries": entries,
        "summary": summary,
    }


def _entries_interface_level(chunk_id: str, model_name: str) -> list[dict[str, Any]]:
    return [
        {
            "entry_id": f"{chunk_id}@REQ-001",
            "description": "系统应每10ms采集并报告发动机转速信号（EngineSpeed），"
                           "信号数据类型为uint16。",
            "interface_name": "Engine_Status_Report",
            "signal_name": "EngineSpeed",
            "source": "EoICD Word 主文件接口定义",
        },
        {
            "entry_id": f"{chunk_id}@REQ-002",
            "description": "系统应每10ms采集并报告发动机扭矩信号（EngineTorque），"
                           "信号数据类型为uint16。",
            "interface_name": "Engine_Status_Report",
            "signal_name": "EngineTorque",
            "source": "EoICD Word 主文件接口定义",
        },
        {
            "entry_id": f"{chunk_id}@REQ-003",
            "description": "系统应每100ms采集并报告车速信号（VehicleSpeed），"
                           "信号数据类型为uint16，供车身稳定系统使用。",
            "interface_name": "Vehicle_Speed_Report",
            "signal_name": "VehicleSpeed",
            "source": "EoICD Word 主文件接口定义",
        },
        {
            "entry_id": f"{chunk_id}@REQ-004",
            "description": "系统应接收制动踏板位置信号（BrakePedalPosition），"
                           "信号数据类型为uint8，精度不低于8位。",
            "interface_name": "Brake_Request",
            "signal_name": "BrakePedalPosition",
            "source": "EoICD Word 主文件接口定义",
        },
        {
            "entry_id": f"{chunk_id}@REQ-005",
            "description": "系统应接收目标挡位指令（TargetGear），"
                           "信号数据类型为uint8，换挡策略由整车控制器决定。",
            "interface_name": "Gear_Command",
            "signal_name": "TargetGear",
            "source": "EoICD Word 主文件接口定义",
        },
    ]


def _entries_function_level(chunk_id: str, model_name: str) -> list[dict[str, Any]]:
    return [
        {
            "entry_id": f"{chunk_id}@REQ-001",
            "description": "系统应实时监控发动机运行状态，包括转速和扭矩，"
                           "转速和扭矩信号均应每10ms更新一次，数据类型为uint16。",
            "interface_name": "Engine_Status_Report",
            "signal_name": "EngineSpeed, EngineTorque",
            "source": "EoICD Word 主文件接口定义",
        },
        {
            "entry_id": f"{chunk_id}@REQ-002",
            "description": "系统应实时监控车速信息，车速信号应每100ms更新一次，"
                           "数据类型为uint16，供车身稳定系统使用。",
            "interface_name": "Vehicle_Speed_Report",
            "signal_name": "VehicleSpeed",
            "source": "EoICD Word 主文件接口定义",
        },
        {
            "entry_id": f"{chunk_id}@REQ-003",
            "description": "制动系统应接收并处理制动踏板位置信号，"
                           "信号精度不低于8位，响应时间应满足实时性要求。",
            "interface_name": "Brake_Request",
            "signal_name": "BrakePedalPosition",
            "source": "EoICD Word 主文件接口定义",
        },
        {
            "entry_id": f"{chunk_id}@REQ-004",
            "description": "动力系统应接收并执行整车控制器下发的目标挡位指令，"
                           "挡位信号数据类型为uint8，指令来源为整车控制器。",
            "interface_name": "Gear_Command",
            "signal_name": "TargetGear",
            "source": "EoICD Word 主文件接口定义",
        },
    ]


def _scoring_mock_data(role: str) -> dict[str, Any]:
    """scoring Task 应输出的 JSON 结构（与 ScoringOutput 对齐）。"""
    # 在同一 chunk 内，scoring agent 同时对 MiniMax 和 DeepSeek 候选评分
    return {
        "scores": [
            {
                "candidate_id": "chunk-001@minimax",
                "score": 82.0,
                "reasoning": "接口级条目化完整覆盖所有信号，追溯性强，但部分描述略显冗余。",
                "recommended_is_best": True,
            },
            {
                "candidate_id": "chunk-001@deepseek",
                "score": 78.0,
                "reasoning": "功能级条目化表述简洁，可读性好，但合并描述导致部分细节信息丢失。",
                "recommended_is_best": False,
            },
        ],
    }


def _comparison_mock_data() -> dict[str, Any]:
    return {
        "differences": [
            {
                "difference_id": "diff-1",
                "difference_type": "缺失",
                "requirement_text": "chunk-001@REQ-003：系统应每100ms采集并报告车速信号（VehicleSpeed）。",
                "software_requirement_text": "SRS-003：车速信息应每100ms更新一次，供车身稳定系统使用。",
                "description": "EoICD 条目化需求描述了接口定义和传输周期，但软件高层需求未明确信号名称和数据类型。",
                "suggested_action": "建议在软件高层需求中补充信号名称和数据类型定义，保持与 EoICD 一致。",
            },
            {
                "difference_id": "diff-2",
                "difference_type": "不一致",
                "requirement_text": "chunk-001@REQ-004：系统应接收制动踏板位置信号，精度不低于8位。",
                "software_requirement_text": "SRS-004：制动系统应接收制动踏板位置信号，信号精度不低于8位。",
                "description": "两者精度要求一致，但 EoICD 强调了系统侧接收，软件需求强调了制动系统侧处理，视角不同。",
                "suggested_action": "建议统一需求描述视角，明确信号流向和职责边界。",
            },
            {
                "difference_id": "diff-3",
                "difference_type": "需确认",
                "requirement_text": "chunk-001@REQ-005：系统应接收目标挡位指令，挡位信号数据类型为uint8。",
                "software_requirement_text": "SRS-005：换挡策略由整车控制器决定，TCU应接收目标挡位指令并执行。",
                "description": "EoICD 定义了 uint8 数据类型，但软件需求未明确数据类型；两者对挡位控制策略描述一致。",
                "suggested_action": "建议在软件高层需求中补充挡位信号数据类型定义，与 EoICD 保持一致。",
            },
            {
                "difference_id": "diff-4",
                "difference_type": "冗余",
                "requirement_text": "",
                "software_requirement_text": "SRS-006：变速箱应监控油温，油温信号应每1s更新一次。",
                "description": "软件高层需求中包含油温监控要求，但 EoICD 中未定义相关接口和信号。",
                "suggested_action": "建议补充 EoICD 接口定义，或说明油温监控为新增需求且尚未完成接口定义。",
            },
            {
                "difference_id": "diff-5",
                "difference_type": "缺失",
                "requirement_text": "",
                "software_requirement_text": "SRS-007：故障诊断应支持ISO 14229标准。",
                "description": "软件高层需求要求支持ISO 14229诊断标准，但 EoICD 中未定义相关诊断接口和服务。",
                "suggested_action": "建议在 EoICD 中补充诊断相关接口定义，或在软件需求中明确暂不包含诊断能力。",
            },
        ],
    }


# ============================================================================
# MockLLM 类
# ============================================================================


class MockLLM(BaseLLM):
    """结构化 mock LLM。

    继承 crewai.BaseLLM，实现抽象方法 call()。
    - 支持 role 区分场景
    - 输出与 Task.output_pydantic 严格对齐的 JSON 字符串
    """

    role: str = "generic"

    def call(
        self,
        messages,
        tools: list | None = None,
        callbacks: list | None = None,
        available_functions: dict | None = None,
        from_task=None,
        from_agent=None,
        response_model=None,
    ) -> str:
        """返回与当前 role 对应的 mock JSON 字符串。"""
        # 简单容错：支持 list[dict] / str / LLMMessage 对象
        if isinstance(messages, str):
            text = messages
        elif isinstance(messages, list) and messages:
            last = messages[-1]
            if isinstance(last, dict):
                text = last.get("content", "")
            elif hasattr(last, "content"):
                text = str(last.content)
            else:
                text = str(last)
        else:
            text = ""

        if self.role in ("minimax_generation", "deepseek_generation"):
            model_name = "MiniMax" if self.role == "minimax_generation" else "DeepSeek"
            chunk_id = self._extract_chunk_id(text) or "chunk-001"
            data = _generation_mock_data(chunk_id, model_name)
        elif self.role in ("minimax_scoring", "deepseek_scoring"):
            data = _scoring_mock_data(self.role)
        elif self.role == "deepseek_comparison":
            data = _comparison_mock_data()
        else:
            data = {}

        return json.dumps(data, ensure_ascii=False)

    def get_token_usage(self) -> dict[str, int]:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    @staticmethod
    def _extract_chunk_id(text: str) -> str | None:
        """从 messages 文本中尝试解析 chunk_id 标识。"""
        import re

        if not text:
            return None
        match = re.search(r"chunk[-_]\d+", text)
        return match.group(0) if match else None

    def __repr__(self) -> str:
        return f"MockLLM(model={self.model!r}, role={self.role!r})"
