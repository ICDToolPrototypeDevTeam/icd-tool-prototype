"""
crew/difference_analyzer.py —— comparison crew pipeline 入口。

仅使用 DeepSeek comparison agent，把最终最优 EoICD 条目化需求
（MergedRequirementResult）与软件高层需求做差异分析。
"""

from __future__ import annotations

from app.crew.crews import build_comparison_crew
from app.models import DifferenceItem, MergedRequirementResult, ParsedSoftwareRequirements


def analyze_differences(
    merged: MergedRequirementResult,
    software_requirements: ParsedSoftwareRequirements,
) -> list[DifferenceItem]:
    """调用 comparison crew，返回结构化差异项列表。"""
    crew = build_comparison_crew(merged, software_requirements)
    result = crew.kickoff()

    differences: list[DifferenceItem] = []
    for t in result.tasks_output:
        if t.pydantic is None:
            raise RuntimeError(
                f"Comparison Task 未返回 Pydantic 输出：{t.description[:80]!r}"
            )
        for entry in t.pydantic.differences:
            differences.append(
                DifferenceItem(
                    difference_id=entry.difference_id,
                    difference_requirement_id=entry.difference_requirement_id,
                    difference_eoicd_entry_id=entry.difference_eoicd_entry_id,
                    difference_type=entry.difference_type,
                    eoicd_requirement_text=entry.eoicd_requirement_text,
                    software_requirement_text=entry.software_requirement_text,
                    description=entry.description,
                    suggested_action=entry.suggested_action,
                )
            )

    return differences
