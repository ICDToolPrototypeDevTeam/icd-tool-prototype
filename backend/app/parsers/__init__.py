"""
parsers/ 模块统一入口。

提供 parse_inputs 函数，构建统一分析输入包。
"""

from pathlib import Path

from app.models import UnifiedInputPackage
from app.parsers.eoicd_parser import parse_eoicd_word
from app.parsers.software_req_parser import parse_software_requirement


def parse_inputs(
    eoicd_word_path: Path,
    eoicd_excel_paths: list[Path],
    sw_req_path: Path,
    job_id: str,
) -> UnifiedInputPackage:
    """
    解析所有输入文件，构建统一分析输入包。

    Args:
        eoicd_word_path: EoICD Word 主文件路径
        eoicd_excel_paths: EoICD Excel 附件路径列表
        sw_req_path: 软件高层需求文件路径
        job_id: 任务标识

    Returns:
        UnifiedInputPackage 统一分析输入包
    """
    # 解析 EoICD 主文件
    eoicd = parse_eoicd_word(eoicd_word_path)

    # 解析软件高层需求
    software_requirements = parse_software_requirement(sw_req_path)

    return UnifiedInputPackage(
        eoicd=eoicd,
        software_requirements=software_requirements,
        job_id=job_id,
    )