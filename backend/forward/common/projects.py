# -*- coding: utf-8 -*-
"""
common.projects — 全项目统一的「系统 / 项目」配置（单一数据源）
================================================================================
背景：
  改造前，项目配置分散在两处且必须手动同步，新增一个系统时漏改任一处就会运行失败：
    1. run_integration.py                    的 PROJECT_EOICD_NAMES（只用到 pub/sub 文件名）
    2. EoICD侧数据处理/run_data_processing.py 的 PROJECTS（还需 raw_dir / 定位配置等）
  现在统一由本模块提供，两个脚本都从此处读取；**新增系统只需改这一个文件**。

字段说明：
    label          系统中文名（用于 --help 提示）
    pub / sub      EoICD Publisher / Subscriber 表文件名（集成脚本按此重命名输入文件）
    raw_dir        默认输入目录（相对 EoICD侧数据处理/；集成运行时会被 workspace 覆盖）
    output_dir     默认输出目录（同上）
    located        定位后文件名模板，{side} 会替换为 Publisher / Subscriber
    locate_config  定位过滤配置（位于 EoICD侧数据处理/config/ 下）
    skip_locate    是否跳过定位步骤。约定：所有系统均启用定位（False），不再跳过；
                  若某系统确无定位过滤条件，也应提供"全保留"定位配置（空白名单/省略 sheet）而非跳过。
    sides          该项目实际存在的信号侧列表，默认 ["publisher", "subscriber"]。
                  仅 Publisher 侧的项目（如 fgmc）须显式设为 ["publisher"]，
                  下游数据处理/匹配据此跳过 subscriber 侧。
"""
from __future__ import annotations

PROJECTS: dict[str, dict] = {
    # 空气管理系统控制器（AMS）：3 层链路 HLR → ERD → EoICD
    "ams": {
        "label": "空气管理系统 AMS（3 层链路 HLR→ERD→EoICD）",
        "raw_dir": "input/ams",
        "output_dir": "data/processed",
        "pub": "AMS_EoICD_Publisher_Table.xlsx",
        "sub": "AMS_EoICD_Subscriber_Table.xlsx",
        "located": "AMS_EoICD_{side}_Located.xlsx",
        "locate_config": "eoicd_locate.yaml",
        "skip_locate": False,
    },
    # 配电装置（RPDU / ATA24EPS）：4 层链路 HLR → ERD → SRD → EoICD
    "eps": {
        "label": "配电装置 EPS / ATA24EPS（4 层链路 HLR→ERD→SRD→EoICD）",
        "raw_dir": "input/eps",
        "output_dir": "data/processed_eps",
        "pub": "ATA24EPS_EoICD_Publisher_Table.xlsx",
        "sub": "ATA24EPS_EoICD_Subscriber_Table.xlsx",
        "located": "ATA24EPS_EoICD_{side}_Located.xlsx",
        "locate_config": "eoicd_locate.yaml",
        "skip_locate": False,  # EPS 也走定位步骤（白名单见 eoicd_locate.yaml 并集，含 HF_EMPC_EPS 等全部 EPS 设备）
    },
    # 燃油测量管理计算机控制器（FGMC）：仅 Publisher 侧（无 Subscriber 表）
    #   sides 显式声明只存在 publisher 侧，下游数据处理/匹配据此跳过 subscriber。
    "fgmc": {
        "label": "燃油测量管理计算机 FGMC（仅 Publisher 侧）",
        "raw_dir": "input/fgmc",
        "output_dir": "data/processed_fgmc",
        "pub": "FGMC_EoICD_Publisher_Table.xlsx",
        "located": "FGMC_EoICD_{side}_Located.xlsx",
        "locate_config": "eoicd_locate.yaml",
        "skip_locate": False,
        "sides": ["publisher"],
    },
    # 液压系统控制单元（HSCU）：Publisher + Subscriber 双侧（与 ams/eps 同结构）
    "hscu": {
        "label": "液压系统控制单元 HSCU（Publisher + Subscriber）",
        "raw_dir": "input/hscu",
        "output_dir": "data/processed_hscu",
        "pub": "HSCU_EoICD_Publisher_Table.xlsx",
        "sub": "HSCU_EoICD_Subscriber_Table.xlsx",
        "located": "HSCU_EoICD_{side}_Located.xlsx",
        "locate_config": "eoicd_locate.yaml",
        "skip_locate": False,
    },
}


def project_ids() -> list[str]:
    """返回所有已登记的系统标识（用于 --project 的 choices）。"""
    return sorted(PROJECTS.keys())


def get_project(project: str) -> dict:
    """按标识取配置；不存在时给出清晰报错，并提示去哪里新增。"""
    try:
        return PROJECTS[project]
    except KeyError:
        raise KeyError(
            f"未知的系统标识 {project!r}。已登记的系统：{', '.join(project_ids())}。"
            f"如需新增，请在 common/projects.py 的 PROJECTS 中登记（只需改这一处）。"
        ) from None


def describe() -> str:
    """生成 --help 中展示的可选值说明。"""
    return "; ".join(f"{k}={v['label']}" for k, v in sorted(PROJECTS.items()))
