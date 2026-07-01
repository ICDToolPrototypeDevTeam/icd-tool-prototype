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
                "difference_id": "1",
                "difference_requirement_id": "SRS-003",
                "difference_eoicd_entry_id": "REQ-003",
                "difference_type": "缺失",
                "eoicd_requirement_text": "系统应每100ms采集并报告车速信号（VehicleSpeed），数据类型为uint16。",
                "software_requirement_text": "SRS-003：车速信息应每100ms更新一次，供车身稳定系统使用。",
                "description": (
                    "属性 SignalName: SWHLR=(无) IRD=VehicleSpeed 仅IRD定义 - SWHLR 未明确信号名\n"
                    "属性 DataType: SWHLR=(无) IRD=uint16 仅IRD定义 - SWHLR 未明确数据类型\n"
                    "属性 UpdatePeriod: SWHLR=100ms IRD=100ms 一致 - 传输周期双方一致\n"
                    "属性 Consumer: SWHLR=车身稳定系统 IRD=(无) 仅SWHLR描述 - SWHLR 标了使用方\n"
                    "整体判定: 缺失\n"
                    "整体分析: EoICD 详细描述了 VehicleSpeed 信号的接口定义和数据类型，但 SWHLR 仅泛泛提到车速信息\n"
                    "整体建议: 在 SWHLR 中补充 VehicleSpeed 信号的名称、数据类型及传输周期定义"
                ),
                "suggested_action": "建议在软件高层需求 SRS-003 中补充信号名称 VehicleSpeed、数据类型 uint16 等具体定义。",
            },
            {
                "difference_id": "2",
                "difference_requirement_id": "SRS-004",
                "difference_eoicd_entry_id": "REQ-004",
                "difference_type": "不一致",
                "eoicd_requirement_text": "系统应接收制动踏板位置信号，精度不低于8位。",
                "software_requirement_text": "SRS-004：制动系统应接收制动踏板位置信号，信号精度不低于8位。",
                "description": (
                    "属性 SignalName: SWHLR=制动踏板位置信号 IRD=制动踏板位置信号 一致 - 信号名一致\n"
                    "属性 Precision: SWHLR=不低于8位 IRD=不低于8位 一致 - 精度要求一致\n"
                    "属性 Receiver: SWHLR=制动系统 IRD=系统 不一致 - 接收方视角不同（系统级 vs 子系统级）\n"
                    "属性 DataType: SWHLR=(无) IRD=(无) 仅IRD定义 - 双方都未明确\n"
                    "整体判定: 不一致\n"
                    "整体分析: 双方对精度要求一致，但接收方描述视角不同——EoICD 用系统级抽象，SWHLR 明确到制动系统\n"
                    "整体建议: 统一需求描述视角，建议 SWHLR 与 EoICD 对齐或明确层级关系"
                ),
                "suggested_action": "建议统一需求描述视角，明确信号接收方是系统级还是子系统级。",
            },
            {
                "difference_id": "3",
                "difference_requirement_id": "SRS-005",
                "difference_eoicd_entry_id": "REQ-005",
                "difference_type": "需确认",
                "eoicd_requirement_text": "系统应接收目标挡位指令，挡位信号数据类型为uint8。",
                "software_requirement_text": "SRS-005：换挡策略由整车控制器决定，TCU应接收目标挡位指令并执行。",
                "description": (
                    "属性 SignalName: SWHLR=目标挡位指令 IRD=目标挡位指令 一致 - 信号名一致\n"
                    "属性 DataType: SWHLR=(无) IRD=uint8 不一致 - EoICD 明确 uint8，SWHLR 未明确\n"
                    "属性 Strategy: SWHLR=由整车控制器决定 IRD=(无) 仅SWHLR描述 - SWHLR 补充了控制策略\n"
                    "属性 Executor: SWHLR=TCU IRD=(无) 仅SWHLR描述 - SWHLR 明确执行方\n"
                    "属性 Receiver: SWHLR=TCU IRD=系统 待确认 - 接收方在 SWHLR 是 TCU、在 EoICD 是系统，需澄清\n"
                    "整体判定: 需确认\n"
                    "整体分析: 双方对信号功能描述一致，但 SWHLR 未明确数据类型，接收方层级表述不一致\n"
                    "整体建议: 建议 SWHLR 补充数据类型定义，并与 EoICD 确认接收方层级"
                ),
                "suggested_action": "建议在 SWHLR 中补充挡位信号数据类型 uint8 定义，并与需求方确认接收方层级。",
            },
            {
                "difference_id": "4",
                "difference_requirement_id": "SRS-006",
                "difference_eoicd_entry_id": "",
                "difference_type": "冗余",
                "eoicd_requirement_text": "",
                "software_requirement_text": "SRS-006：变速箱应监控油温，油温信号应每1s更新一次。",
                "description": (
                    "属性 SignalName: SWHLR=变速箱油温 IRD=(无) 仅SWHLR描述 - SWHLR 提出新需求\n"
                    "属性 UpdatePeriod: SWHLR=1s IRD=(无) 仅SWHLR描述 - SWHLR 提出新需求\n"
                    "属性 Interface: SWHLR=(无) IRD=(无) 仅IRD定义 - EoICD 未定义油温接口\n"
                    "整体判定: 冗余\n"
                    "整体分析: SWHLR 提出变速箱油温监控需求，但 EoICD 中未定义任何油温相关接口或信号\n"
                    "整体建议: 补充 EoICD 中油温信号接口定义，或说明该需求暂不在 ICD 范围内"
                ),
                "suggested_action": "建议补充 EoICD 接口定义覆盖油温信号，或说明该需求为新增且尚未完成接口定义。",
            },
            {
                "difference_id": "5",
                "difference_requirement_id": "SRS-007",
                "difference_eoicd_entry_id": "",
                "difference_type": "缺失",
                "eoicd_requirement_text": "",
                "software_requirement_text": "SRS-007：故障诊断应支持ISO 14229标准。",
                "description": (
                    "属性 Protocol: SWHLR=ISO 14229 IRD=(无) 仅SWHLR描述 - SWHLR 提出新协议\n"
                    "属性 DiagnosticService: SWHLR=(无明确) IRD=(无) 仅IRD定义 - 双方均未定义诊断服务接口\n"
                    "整体判定: 缺失\n"
                    "整体分析: SWHLR 要求支持 ISO 14229 诊断标准，但 EoICD 中未定义任何诊断协议或服务接口\n"
                    "整体建议: 在 EoICD 中补充诊断相关接口（UDS 服务、诊断请求/响应消息定义），或在 SWHLR 中明确暂不包含诊断能力"
                ),
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
