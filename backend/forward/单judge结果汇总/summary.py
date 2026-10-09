# -*- coding: utf-8 -*-
"""
匹配结果分类与汇总
====================================================================================

消费「属性匹配」阶段的产物（attribute_match_report_*.json），内部将其重排为统一的
comparisons 后，按既定汇总规则产出：

  1. 单属性 4 类结果：属性一致 / 属性不一致 / 无提及 / 无具体值
  2. 单信号最终分类：已落实 / 不一致 / 未落实 / 部分落实（新四类判定规则）
  3. 报告与下钻：按类型聚合统计 + 可下钻到 信号→属性→命中需求与两侧取值
  4. 完整性兜底：额外消费「身份识别」阶段产物（input/rejected/ 下的
     rejected_name_*.json / rejected_direction_*.json / rejected_bus_label_bit_*.json），
     把其中被拒绝（未通过身份识别）的全量信号与属性匹配产物做差集，凡身份识别阶段被拒绝
     却未在属性匹配产物出现的信号，强制补一条『未落实（未识别）』，确保未通过身份识别
     的信号不漏。

输入（默认 ./input，可用参数覆盖）：
  attribute_match_report_pub.json / attribute_match_report_sub.json   —— 主输入（属性匹配阶段产物）
  rejected_name_{pub,sub}.json / rejected_direction_{pub,sub}.json / rejected_bus_label_bit_{pub,sub}.json
    —— 兜底用（身份识别阶段产物，位于 ./input/rejected/）

输出（默认 ./outputs，每次运行在其中生成独立时间戳子文件夹）：
  <设备名>EoICD到软件高层需求的落实检查_<时间戳>/
    report_{pub,sub}.json        —— 结构化结果（含下钻）
    summary_{pub,sub}.docx      —— 人读汇总（Word 文档；未装 python-docx 时回退 .txt）
    summary_{pub,sub}.xlsx      —— 全量明细展开 Excel 附件（含『统计页』『汇总页』与按问题类型分类的多张明细 Sheet）
  设备名前缀（文件夹名 XXX 部分）默认从 ./input 下名称含『高层需求规范』的 .docx 自动识别，
  也可用 --device 显式指定；时间戳格式 YYYYMMDD_HHMMSS。

用法：
  python summary.py
  python summary.py --device 空气管理系统控制器控制通道
  python summary.py --match ./input/attribute_match_report_pub.json --out ./outputs
"""

import argparse
import json
import os
import sys
from pathlib import Path

# --- 复用集成项目公共包 common/：自动向上定位到含 common/ 的根目录 ---
_p = Path(__file__).resolve()
while not (_p / "common").is_dir() and _p != _p.parent:
    _p = _p.parent
if str(_p) not in sys.path:
    sys.path.insert(0, str(_p))

from common import safe_load_json  # noqa: E402


def _safe_path(path):
    """目标被占用（如 Word 正在打开该 docx/xlsx）时，退化为 _v2/_v3 文件名，
    避免 PermissionError 中断；若可写或不存在，则原样返回。"""
    if not os.path.exists(path):
        return path
    try:
        with open(path, "a+b"):
            pass
        return path
    except PermissionError:
        base, ext = os.path.splitext(path)
        i = 2
        while True:
            cand = "%s_v%d%s" % (base, i, ext)
            if not os.path.exists(cand):
                return cand
            i += 1


def _run_timestamp():
    """返回本次运行的紧凑时间戳（YYYYMMDD_HHMMSS）。"""
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _detect_device_name(raw_dir):
    """从 input 下名称含『高层需求规范』的 .docx 自动识别设备名（文件夹名 XXX 前缀）。
    取文件名去掉已知后缀（控制软件高层需求规范 / 软件高层需求规范 / 高层需求规范 / .docx）
    后的部分；找不到则返回空串（文件夹名不加前缀）。"""
    if not os.path.isdir(raw_dir):
        return ""
    for fn in os.listdir(raw_dir):
        if fn.lower().endswith(".docx") and "高层需求规范" in fn:
            name = fn[:-5]  # 去掉 .docx
            for suffix in ("控制软件高层需求规范", "软件高层需求规范", "高层需求规范"):
                if name.endswith(suffix):
                    name = name[: -len(suffix)]
                    break
            return name
    return ""

try:
    from docx import Document
    from docx.shared import Cm, RGBColor
    _HAS_DOCX = True
except Exception:
    _HAS_DOCX = False

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    _HAS_XLSX = True
except Exception:
    _HAS_XLSX = False


# 报告展示用配色（docx / xlsx 共用）
# 属性比对结果类型 → 颜色
if _HAS_DOCX:
    RESULT_COLORS = {
        "不一致": RGBColor(0xC0, 0x00, 0x00),   # 红：缺陷
        "无提及": RGBColor(0xBF, 0x8F, 0x00),   # 琥珀：覆盖率问题
        "无具体值": RGBColor(0x2E, 0x75, 0xB6), # 蓝：未判定
        "一致": RGBColor(0x00, 0x70, 0x00),     # 绿：一致
    }
else:
    RESULT_COLORS = {}
# 信号判定分类 → Excel 填充色 / 字体色（纯 hex 字符串，无 docx 依赖）
CLASS_FILL = {
    "不一致": "FFC7CE",   # 红底
    "未落实": "FFEB9C",   # 黄底
    "部分落实": "BDD7EE", # 蓝底
    "已落实": "C6EFCE",   # 绿底
}
CLASS_FONT = {
    "不一致": "9C0006",
    "未落实": "9C6500",
    "部分落实": "1F4E78",
    "已落实": "006100",
}

# ---------------------------------------------------------------------------
# 单属性结果映射（属性匹配 → 4 类）
# ---------------------------------------------------------------------------
# 上游「属性匹配」阶段输出的 result 取值（经 normalize 重排为统一 comparisons 后）：
#   一致 / 不一致 / 未提及 / 无具体值
# 本表将上游取值翻译为单属性分类；当前上游仅产出上述 4 类。
# 未知取值（含字段缺失/None）不报错：归并为「无具体值」，并由 map_single_result
# 在条目上打 unexpected_result=True 可见标记（不建新分类桶）。
SINGLE_MAP = {
    "一致": "属性一致",
    "不一致": "属性不一致",
    "未提及": "无提及",
    "无具体值": "无具体值",
}

# 「无提及」——参与需求覆盖率统计（coverage_breakdown）；同时在分类中作为
#   判据：所有保留属性均为无提及 → 未落实。注意：这仅表示"属性级"全程未提及，
#   信号在"信号级"通常已识别到 HLR 需求（id_matched_hlrs 非空），属于"已识别、
#   未承接"，并非"信号级身份识别失败"。
NOT_MENTIONED = "无提及"


def map_single_result(entry):
    """将单条 comparison 的 result 翻译为单属性分类。

    已知取值（一致/不一致/未提及/无具体值）直接翻译；未知取值（含字段缺失/
    None）不报错、不建新分类桶，统一归为「无具体值」，并在该条目上打
    unexpected_result=True 可见标记，供下游 JSON 核查上游格式变化。
    """
    r = entry.get("result")
    if r in SINGLE_MAP:
        entry["unexpected_result"] = False
        return SINGLE_MAP[r]
    # 未知取值：归并到「无具体值」并标记，避免脚本中断、也不掩盖漂移
    entry["unexpected_result"] = True
    return "无具体值"


# ---------------------------------------------------------------------------
# 单信号最终分类（新四类判定规则）
# ---------------------------------------------------------------------------
def classify_signal(single_results):
    """单条 ICD（信号）最终分类。

    输入 single_results 为按信号聚合后的单属性结果列表，取值 ∈
    {属性一致, 属性不一致, 无提及, 无具体值}（当前上游仅产出这四类）。

    判定（按优先级）：
      1. 不一致：至少有一个属性为「属性不一致」（最高优先，确定缺陷）。
      2. 已落实：所有属性均为「属性一致」。
      3. 未落实：所有属性均为「无提及」——信号已在信号级识别到 HLR 需求，
          但其全部属性在对应 HLR 中均未提及（"已识别、未承接"）；不等于
          "信号级身份识别失败"（id_matched_hlrs 通常非空）。
      4. 部分落实（信号属性未落实）：至少有一个属性为「无提及」或「无具体值」，
         但至少有一个属性通过了身份识别（即不是全部「无提及」），且无「属性不一致」。
    """
    considered = list(single_results)
    if not considered:
        return "未落实"

    # 1) 不一致 —— 最高优先
    if "属性不一致" in considered:
        return "不一致"

    # 2) 已落实 —— 所有属性均为「属性一致」
    if all(s == "属性一致" for s in considered):
        return "已落实"

    # 至少一条通过了身份识别（即不是全部「无提及」）
    has_carry = any(s != "无提及" for s in considered)
    # 存在未落实项（无提及 / 无具体值）
    has_gap = any(s in ("无提及", "无具体值") for s in considered)

    # 3) 未落实 —— 所有属性均为「无提及」
    if not has_carry:
        return "未落实"

    # 4) 部分落实 —— 有未落实项，且至少有一条通过了身份识别，且无不一致
    if has_gap:
        return "部分落实"


def _short_single_result(single_result):
    """属性一致→一致 / 属性不一致→不一致 / 无提及→未提及 / 无具体值→无具体值。"""
    return (single_result or "").replace("属性", "")


def build_attr_summary(attrs):
    """具体的属性比对结果串：XX属性不一致；XX属性无具体值；XX属性未提及；XX属性一致。"""
    parts = []
    for a in attrs:
        cls = a.get("attribute_class")
        if not cls:
            continue
        parts.append("%s属性%s" % (cls, _short_single_result(a.get("single_result"))))
    return "；".join(parts)


def _val(v):
    """取值兜底：None 渲染为『（无）』，避免文案出现 None。"""
    return "（无）" if v is None else str(v)


def build_analysis(attrs):
    """分析内容：逐属性的具体比对过程。

    顺序：先 EoICD，再 SWHLR（按用户要求 5）。
    SWHLR 未提及的不写『（缺失于XXX）』（按用户要求 4）。
    标点：每个属性分句不以句号结尾，整段以单个句号收尾，避免『。；』类错误
    （按用户要求 3）。
    """
    clauses = []
    for a in attrs:
        cls = a.get("attribute_class")
        if not cls:
            continue
        sr = a.get("single_result")
        hlr_val = _val(a.get("hlr_value"))
        eoicd_val = _val(a.get("eoicd_value"))
        if sr == "属性一致":
            clauses.append("EoICD中%s值为%s，SWHLR中%s值为%s，故一致"
                           % (cls, eoicd_val, cls, hlr_val))
        elif sr == "属性不一致":
            clauses.append("EoICD中%s值为%s，SWHLR中%s值为%s，存在不一致"
                           % (cls, eoicd_val, cls, hlr_val))
        elif sr == "无具体值":
            clauses.append("EoICD中%s值为%s，SWHLR中%s无对应可比值，无具体值"
                           % (cls, eoicd_val, cls))
        elif sr == "无提及":
            # 仅写『而SWHLR中未提及』，不再附（缺失于XXX）
            clauses.append("EoICD中%s值为%s，而SWHLR中未提及"
                           % (cls, eoicd_val))
        else:
            clauses.append("%s：%s" % (cls, _short_single_result(sr)))
    text = "；".join(clauses)
    if text:
        text += "。"
    return text


def load_json(p):
    """加载 JSON 文件。

    统一走 common.safe_load_json：对纯 JSON 行为完全一致，并额外支持 // 注释，
    避免汇总模块与其它子模块各写一份读取逻辑。
    """
    return safe_load_json(p)


def load_cfg(path):
    cfg = {
        "priority": "不一致 ＞ 未落实 ＞ 部分落实 ＞ 已落实",
        "indent_json": 2,
    }
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        if "summary" in user:
            cfg.update(user["summary"])
    return cfg


# ---------------------------------------------------------------------------
# 输入归一化：属性匹配产物(attribute_match_report_*) → comparisons
# ---------------------------------------------------------------------------
# 上游「属性匹配」阶段输出 attribute_match_report_{pub,sub}.json，结构为
# matched / mismatch / null_value / not_mentioned 四组分列；run_direction 消费的是
# 统一的 comparisons 数组（每条 result ∈ {一致, 不一致, 未提及, 无具体值}）。
# 本函数把前者重排为后者，使 summary.py 可直接吃原始产物。
RAW_RESULT_MAP = {
    "matched": "一致",
    "mismatch": "不一致",
    "null_value": "无具体值",   # 当前数据为空
}


def _hlr_evidence(identity):
    """把 hlr_identity（dict）压成展示串。"""
    if not isinstance(identity, dict):
        return None
    parts = [str(identity.get(k)) for k in ("name", "bit", "bus", "label")
             if identity.get(k) is not None]
    return "@".join(parts) if parts else None


def normalize_attribute_match(raw):
    """将 attribute_match_report_* 重排为 {meta, comparisons}。

    每条 comparison 额外透传 eoicd_signal_full_name（信号全名）与
    signal_id_matched_hlrs（not_mentioned 信号级匹配到的需求编号），
    供 run_direction 生成"明细展开"表格的「信号FullName」「匹配的需求编号」列。
    """
    comparisons = []

    # 1) matched → 一致
    for e in raw.get("matched", []):
        comparisons.append({
            "eoicd_cluster_id": e.get("eoicd_cluster_id"),
            "result": RAW_RESULT_MAP["matched"],
            "attribute_class": e.get("attribute"),
            "matched_hlr_req_id": e.get("req_id"),
            "hlr_value": e.get("hlr_value"),
            "hlr_evidence": _hlr_evidence(e.get("hlr_identity")),
            "hlr_identity": e.get("hlr_identity"),   # 透传地址四元组，供多judge KEY_FIELDS比对
            "eoicd_path": e.get("eoicd_signal_short_name"),
            "eoicd_signal_full_name": e.get("eoicd_signal_name"),
            "eoicd_value": e.get("eoicd_value"),
            "detail": "",
            "rule_result": e.get("rule"),
        })

    # 2) mismatch → 不一致
    for e in raw.get("mismatch", []):
        comparisons.append({
            "eoicd_cluster_id": e.get("eoicd_cluster_id"),
            "result": RAW_RESULT_MAP["mismatch"],
            "attribute_class": e.get("attribute"),
            "matched_hlr_req_id": e.get("req_id"),
            "hlr_value": e.get("hlr_value"),
            "hlr_evidence": _hlr_evidence(e.get("hlr_identity")),
            "hlr_identity": e.get("hlr_identity"),   # 透传地址四元组，供多judge KEY_FIELDS比对
            "eoicd_path": e.get("eoicd_signal_short_name"),
            "eoicd_signal_full_name": e.get("eoicd_signal_name"),
            "eoicd_value": e.get("eoicd_value"),
            "detail": "",
            "rule_result": e.get("rule"),
        })

    # 3) null_value → 无具体值（占位；当前数据为空）
    for e in raw.get("null_value", []):
        comparisons.append({
            "eoicd_cluster_id": e.get("eoicd_cluster_id"),
            "result": RAW_RESULT_MAP["null_value"],
            "attribute_class": e.get("attribute"),
            "matched_hlr_req_id": e.get("req_id"),
            "hlr_value": e.get("hlr_value"),
            "hlr_evidence": _hlr_evidence(e.get("hlr_identity")),
            "hlr_identity": e.get("hlr_identity"),   # 透传地址四元组，供多judge KEY_FIELDS比对
            "eoicd_path": e.get("eoicd_signal_short_name"),
            "eoicd_signal_full_name": e.get("eoicd_signal_name"),
            "eoicd_value": e.get("eoicd_value"),
            "detail": "EoICD 属性值为空，无具体值",
            "rule_result": e.get("rule"),
        })

    # 4) not_mentioned → 逐(信号, 属性) 展一条「未提及」
    #    raw 的 not_mentioned 按 (信号, 属性, 缺失HLR) 计（一个属性可能同时缺失于
    #    多个 HLR）。若逐缺失 HLR 展开，会让"无提及"按 HLR 数重复计，放大单属性
    #    分布与覆盖率口径。故此处按 (信号, 属性) 去重，每个属性只计一条；同时用
    #    missing_in_hlrs 字段保留该属性"缺失于哪些 HLR"的全量追溯（不计入计数口径）。
    for sig in raw.get("not_mentioned", {}).get("not_mentioned_by_signal", []):
        cid = sig.get("eoicd_cluster_id")
        short = sig.get("eoicd_signal_short_name")
        full = sig.get("eoicd_signal_name")
        id_matched = sig.get("id_matched_hlrs")
        for attr in sig.get("not_mentioned_attributes", []):
            miss_list = attr.get("missing_in_hlrs") or []
            if miss_list:
                missing_hlrs = [
                    {"req_id": m.get("req_id"),
                     "hlr_evidence": _hlr_evidence(m.get("hlr_identity"))}
                    for m in miss_list
                ]
                first = miss_list[0]
                comparisons.append({
                    "eoicd_cluster_id": cid,
                    "result": "未提及",
                    "attribute_class": attr.get("attribute"),
                    "matched_hlr_req_id": first.get("req_id"),
                    "hlr_value": None,
                    "hlr_evidence": _hlr_evidence(first.get("hlr_identity")),
                    "eoicd_path": short,
                    "eoicd_signal_full_name": full,
                    "eoicd_value": attr.get("eoicd_value"),
                    "detail": "",
                    "rule_result": None,
                    "missing_in_hlrs": missing_hlrs,
                    "signal_id_matched_hlrs": id_matched,
                })
            else:
                # missing_in_hlrs 为空：该属性无"缺失于具体哪个 HLR"的记录，
                # 本质是未被任何需求承接。不借用信号级 id_matched_hlrs 填 req_id，
                # 否则下钻会误导成"命中了某需求"。信号级匹配编号仍透传备用。
                comparisons.append({
                    "eoicd_cluster_id": cid,
                    "result": "未提及",
                    "attribute_class": attr.get("attribute"),
                    "matched_hlr_req_id": None,
                    "hlr_value": None,
                    "hlr_evidence": None,
                    "eoicd_path": short,
                    "eoicd_signal_full_name": full,
                    "eoicd_value": attr.get("eoicd_value"),
                    "detail": "",
                    "rule_result": None,
                    "missing_in_hlrs": [],
                    "signal_id_matched_hlrs": id_matched,
                })

    return {"meta": raw.get("meta", {}), "comparisons": comparisons}


def run_direction(attr_report, cfg):
    indent = cfg.get("indent_json", 2)

    # 按信号聚合单属性结果
    by_cid = {}
    for c in attr_report.get("comparisons", []):
        cid = c.get("eoicd_cluster_id")
        by_cid.setdefault(cid, []).append(c)

    signals = []
    single_dist = {"属性一致": 0, "属性不一致": 0, "无提及": 0, "无具体值": 0}
    final_dist = {"已落实": 0, "不一致": 0, "未落实": 0, "部分落实": 0}
    # 需求覆盖率：EoICD 有该属性但 HLR 未提及
    coverage = {"not_mentioned_total": 0, "not_mentioned_by_class": {}}
    # 透明度：不在已知 4 类内的 result（已归并为「无具体值」）条目计数
    unexpected_count = 0

    for cid, entries in by_cid.items():
        single = []
        attrs = []
        for e in entries:
            sr = map_single_result(e)
            single.append(sr)
            single_dist[sr] = single_dist.get(sr, 0) + 1
            if e.get("unexpected_result"):
                unexpected_count += 1
            cls = e.get("attribute_class")
            if sr == NOT_MENTIONED:
                coverage["not_mentioned_total"] += 1
                coverage["not_mentioned_by_class"][cls] = \
                    coverage["not_mentioned_by_class"].get(cls, 0) + 1
            attrs.append({
                "attribute_class": cls,
                "single_result": sr,
                "matched_hlr_req_id": e.get("matched_hlr_req_id"),
                "hlr_value": e.get("hlr_value"),
                "hlr_evidence": e.get("hlr_evidence"),
                "hlr_identity": e.get("hlr_identity"),   # 地址四元组(bus/label/name/bit/direction)
                "eoicd_path": e.get("eoicd_path"),
                "eoicd_value": e.get("eoicd_value"),
                "detail": e.get("detail"),
                "rule_result": e.get("rule_result"),
                "missing_in_hlrs": e.get("missing_in_hlrs"),
                "unexpected_result": e.get("unexpected_result"),
                "signal_id_matched_hlrs": e.get("signal_id_matched_hlrs"),
            })
        final = classify_signal(single)
        final_dist[final] = final_dist.get(final, 0) + 1
        # 信号级汇总字段（供"明细展开"表格使用）
        full_name = next((e.get("eoicd_signal_full_name") for e in entries
                          if e.get("eoicd_signal_full_name")), cid)
        req_ids = set()
        for a in attrs:
            if a.get("matched_hlr_req_id"):
                req_ids.add(a["matched_hlr_req_id"])
            for r in (a.get("signal_id_matched_hlrs") or []):
                if r:
                    req_ids.add(r)
        # 信号级身份地址四元组：取首个含 hlr_identity 的属性（优先有匹配需求的）
        sig_identity = next((a.get("hlr_identity") for a in attrs
                             if a.get("hlr_identity") and a.get("matched_hlr_req_id")), None)
        if sig_identity is None:
            sig_identity = next((a.get("hlr_identity") for a in attrs
                                 if a.get("hlr_identity")), None)

        # 信号级「需求+接口」配对（#5 修复）：把每个匹配到的 req_id 与其接口地址
        # （bus|label|bit|direction 四元组，即本域的 interface 标识）绑定，使 matched_hlr
        # 细到 req_id+interface_idx，能区分「同一 req_id 匹配到不同接口」的情形。
        #   - 属性级匹配：req_id 取自 matched_hlr_req_id，接口取自该属性 hlr_identity；
        #   - 信号级 id 匹配(signal_id_matched_hlrs)：无逐接口信息时回退到信号级 sig_identity，
        #     仍未知则记为 ""（未知接口）。
        def _iface_str(identity):
            if not isinstance(identity, dict):
                return ""
            return "|".join(str(identity.get(k, "") or "").strip().lower()
                            for k in ("bus", "label", "bit", "direction"))

        matched_hlrs = []
        _seen_pairs = set()
        for a in attrs:
            rid = a.get("matched_hlr_req_id")
            if rid:
                key = (str(rid), _iface_str(a.get("hlr_identity")))
                if key not in _seen_pairs:
                    _seen_pairs.add(key)
                    matched_hlrs.append([key[0], key[1]])
        for a in attrs:
            for item in (a.get("signal_id_matched_hlrs") or []):
                if isinstance(item, dict):
                    req = item.get("req_id")
                    iface = _iface_str(item.get("hlr_identity")) or _iface_str(sig_identity)
                else:
                    req = item
                    iface = _iface_str(sig_identity)
                if req:
                    key = (str(req), iface or "")
                    if key not in _seen_pairs:
                        _seen_pairs.add(key)
                        matched_hlrs.append([key[0], key[1]])

        # name 语义匹配结论：信号是否经身份识别匹配到某 HLR（matched_req_ids 非空即代表 name 语义命中）
        name_match_status = bool(req_ids)
        # 未落实子分类：
        #   已识别未承接 —— 识别到 HLR（matched_req_ids 非空）但属性全未提及；
        #   未识别       —— 未通过身份识别（matched_req_ids 为空）。
        # 该字段供 docx 将「未落实」拆为两个子小节、Excel 同列展示子类型使用。
        sub_clf = None
        if final == "未落实":
            sub_clf = "未识别" if not req_ids else "已识别未承接"
        signals.append({
            "eoicd_cluster_id": cid,
            "signal_full_name": full_name,
            "matched_req_ids": sorted(req_ids),
            "matched_hlrs": matched_hlrs,   # #5 修复：信号级 (req_id, 接口地址) 配对列表
            "final_classification": final,
            "sub_classification": sub_clf,
            "name_match_status": name_match_status,   # 身份层 KEY_FIELD：name 是否语义命中某 HLR
            "hlr_identity": sig_identity,             # 身份层 KEY_FIELD：bus/label/bit/direction/name 地址四元组
            "judged_attr_count": len(single),
            "attr_summary": build_attr_summary(attrs),
            "analysis": build_analysis(attrs),
            "attributes": attrs,
        })

    amet = attr_report.get("meta", {}) or {}
    out = {
        "meta": {
            "direction": amet.get("direction"),
            "direction_cn": amet.get("direction_cn")
            or {"pub": "发送", "sub": "接收"}.get(amet.get("direction"), amet.get("direction")),
            "total_signals": len(signals),
            "classification_distribution": final_dist,
            "single_attr_distribution": single_dist,
            "coverage_breakdown": coverage,
            "unexpected_result_count": unexpected_count,
            "priority": cfg.get("priority"),
        },
        "signals": signals,
    }
    return out


def _iter_json_array_values(fp, array_key):
    """从 {..., "<array_key>": [ ... ]} 流中逐个产出数组元素，内存友好。

    仅解析目标数组，不把整个（可能数十 MB 的）文件一次性读入内存。适用于
    rejected_{stage}_{direction}.json 这类顶层为对象、内部含大数组的产物。
    """
    dec = json.JSONDecoder()
    target = '"%s"' % array_key
    # 1) 定位 "<array_key>": [ 之后的数组起始
    tail = ""
    buf = ""
    started = False
    while not started:
        chunk = fp.read(1 << 20)
        if not chunk:
            return
        buf = tail + chunk
        pos = buf.find(target)
        if pos == -1:
            tail = buf[-(len(target) + 8):]
            continue
        after = buf.find("[", pos)
        if after == -1:
            tail = buf[pos:]
            continue
        buf = buf[after + 1:]
        started = True
    # 2) 逐个 raw_decode 数组元素
    #    采用整数偏移 idx 在 buf 上游走，仅在跨越分块边界时对 buf[idx:] 做一次压缩，
    #    避免每解析一个元素就对剩余缓冲反复切片（原写法对大数组为 O(n^2)，是 54 分钟
    #    级耗时的根因）。修正后为 O(n)，输出完全一致。
    idx = 0
    n = len(buf)
    while True:
        while idx < n and buf[idx] in " \t\r\n,":
            idx += 1
        if idx >= n:
            chunk = fp.read(1 << 20)
            if not chunk:
                return
            buf = buf[idx:] + chunk
            idx = 0
            n = len(buf)
            continue
        if buf[idx] == "]":
            return
        try:
            obj, end = dec.raw_decode(buf, idx)
        except json.JSONDecodeError:
            chunk = fp.read(1 << 20)
            if not chunk:
                return
            buf = buf[idx:] + chunk
            idx = 0
            n = len(buf)
            continue
        yield obj
        idx = end


# 身份识别拒绝原因 → 具体未通过的参数（信号名称/方向/总线/位/标签）
_REASON_TO_PARAM = {
    "name mismatch (AI)": "name",
    "direction mismatch": "direction",
    "bus mismatch": "bus",
    "bit mismatch": "bit",
    "label mismatch": "label",
}
_PARAM_CN = {"name": "信号名称(name)", "direction": "方向(direction)",
             "bus": "总线(bus)", "bit": "位(bit)", "label": "标签(label)"}
_PARAM_ORDER = ["name", "direction", "bus", "bit", "label"]
_PARAM_EOICD_KEY = {"name": "eoicd_signal_short_name", "direction": "eoicd_direction",
                    "bus": "eoicd_bus", "bit": "eoicd_bit", "label": "eoicd_label"}
_PARAM_HLR_KEY = {"bus": "hlr_bus", "bit": "hlr_bit", "direction": "hlr_direction"}


def _hlr_value_of(param, obj):
    """取 HLR 侧期望取值：标签优先 hlr_label_raw（缺失回退 extracted），
    信号名称取 hlr_name，其余取对应键。"""
    if param == "label":
        hv = obj.get("hlr_label_raw")
        if not hv or hv == "None":
            hv = obj.get("hlr_label_extracted")
        return hv
    if param == "name":
        return obj.get("hlr_name")
    return obj.get(_PARAM_HLR_KEY.get(param))


def load_identity_rejections(direction, rejected_dir):
    """读取身份识别阶段产物（rejected 文件夹）并按信号聚合拒绝信息。

    身份识别阶段分 name / direction / bus_label_bit 三个子阶段，各自输出
    rejected_{stage}_{direction}.json，结构为 {meta, rejections:[...]}，每条 rejection
    含 eoicd_cluster_id / hlr_req_id / reason 等。这里把三个文件按 eoicd_cluster_id
    聚合，返回 {cid: {"hlrs": set(hlr_req_id), "reasons": set(reason)}}。
    任一文件缺失则跳过该文件（不报错）；全部缺失返回空 dict。
    """
    agg = {}
    for stage in ("name", "direction", "bus_label_bit"):
        fn = os.path.join(rejected_dir, "rejected_%s_%s.json" % (stage, direction))
        if not os.path.isfile(fn):
            print("[提示] 未找到身份识别产物 %s，跳过该子阶段" % fn)
            continue
        with open(fn, encoding="utf-8") as f:
            for obj in _iter_json_array_values(f, "rejections"):
                cid = obj.get("eoicd_cluster_id")
                if not cid:
                    continue
                d = agg.setdefault(cid, {"hlrs": set(), "reasons": set(), "name": None, "params": {}})
                h = obj.get("hlr_req_id")
                if h:
                    d["hlrs"].add(h)
                r = obj.get("reason")
                if r:
                    d["reasons"].add(r)
                    param = _REASON_TO_PARAM.get(r)
                    if param:
                        p = d["params"].get(param)
                        if p is None:
                            p = {"eoicd": set(), "hlr": set()}
                            d["params"][param] = p
                        ev = obj.get(_PARAM_EOICD_KEY.get(param))
                        if ev not in (None, ""):
                            p["eoicd"].add(str(ev))
                        hv = _hlr_value_of(param, obj)
                        if hv not in (None, ""):
                            p["hlr"].add(str(hv))
                full = obj.get("eoicd_signal_name")
                short = obj.get("eoicd_signal_short_name")
                # 「有全名就用全名、否则短名」：全名优先，且可覆盖已存的短名
                if full:
                    d["name"] = full
                elif short and not d["name"]:
                    d["name"] = short
    return agg


def apply_identity_safeguard(out, rejections, cfg):
    """完整性兜底：以身份识别阶段产物（rejected 文件）为基准，补全在属性匹配
    阶段"消失"的信号。

    身份识别阶段按 name / direction / bus_label_bit 三个子阶段对 EoICD 信号做匹配，
    未通过任一阶段的信号会落入 rejected_{stage}_{direction}.json 的 rejections 列表
    （每条含 eoicd_cluster_id / hlr_req_id / reason）。这些信号即『未通过身份识别』的信号，
    必须始终在汇总中可见，不能静默漏掉。

    思路：取 rejected 聚合结果里全部 eoicd_cluster_id，与 out['signals'] 已含的
    cluster_id 做差集。凡『身份识别阶段被拒绝、却未出现在属性匹配产物』的信号，
    强制补一条『未落实』记录，子分类定为『未识别』（未通过身份识别，无承接目标）。
    其被拒绝时比对过的高层需求编号（hlr_req_id）保留用于追溯。
    若 rejections 为空（文件缺失），则跳过本兜底，不影响既有输出。
    """
    if not rejections:
        return
    existing = {s["eoicd_cluster_id"] for s in out.get("signals", [])}
    added = 0
    for cid, info in rejections.items():
        if cid in existing:
            continue
        # 曾比对过但未通过身份识别的需求编号：仅作追溯写入 analysis，
        # 不进入 matched_req_ids（未识别 = 从未真正匹配到任何需求）。
        compared = sorted(info.get("hlrs", set()))
        reasons = sorted(info.get("reasons", set()))
        name = info.get("name") or cid
        # 从聚合结果提取『具体哪些参数未通过身份识别』（总线/位/标签）及双方取值
        params = info.get("params", {})
        failed = [pk for pk in _PARAM_ORDER if pk in params]

        param_clauses = []
        hlr_parts = []
        for pk in failed:
            evs = sorted(params[pk].get("eoicd", set()))
            ev = "/".join(evs) if evs else "?"
            param_clauses.append("%s=%s 不匹配" % (_PARAM_CN[pk], ev))
            hv = sorted(params[pk].get("hlr", set()))
            if hv:
                shown = "、".join(hv[:5])
                more = " 等%d个" % len(hv) if len(hv) > 5 else ""
                hlr_parts.append("%s 期望 %s%s" % (_PARAM_CN[pk], shown, more))
        hlr_detail = "；".join(hlr_parts)

        if param_clauses:
            attr_summary = "未通过身份识别（%s）" % "；".join(param_clauses)
        else:
            attr_summary = "未通过身份识别（%s）" % ("、".join(reasons) if reasons else "未匹配")

        failed_cn = [_PARAM_CN[pk] for pk in failed]
        if compared:
            head = "、".join(compared[:5])
            tail = " 等" if len(compared) > 5 else ""
            trace = "（身份识别阶段曾与 %s%s 等比对但未通过" % (head, tail)
            if hlr_detail:
                trace += "：%s" % hlr_detail
            trace += "）"
            analysis = ("该信号在身份识别阶段被拒绝，未通过身份识别（%s），无法承接，归为未落实。"
                        % ("、".join(failed_cn) if failed_cn else "未匹配")) + trace
        else:
            analysis = ("该信号在身份识别阶段未匹配到任何高层需求（未通过身份识别），"
                        "无法承接，归为未落实。")
            if hlr_detail:
                analysis += "（%s）" % hlr_detail
        out["signals"].append({
            "eoicd_cluster_id": cid,
            "signal_full_name": name,
            "matched_req_ids": [],
            "final_classification": "未落实",
            "sub_classification": "未识别",
            "judged_attr_count": 0,
            "attr_summary": attr_summary,
            "analysis": analysis,
            "attributes": [],
            "identity_safeguard": True,
        })
        existing.add(cid)
        added += 1
    if added:
        out["meta"]["total_signals"] = len(out["signals"])
        out["meta"]["classification_distribution"]["未落实"] = \
            out["meta"]["classification_distribution"].get("未落实", 0) + added
        out["meta"]["safeguard_added"] = out["meta"].get("safeguard_added", 0) + added
        print("[完整性兜底] 补充 %d 个身份识别阶段被拒绝（未通过身份识别）的信号（需求中没有任何信号信息）" % added)


def sorted_signals(out):
    """按（信号分类顺序, cluster_id）排序，并附连续全局序号，供 docx/xlsx 共用。"""
    order = {"不一致": 0, "未落实": 1, "部分落实": 2, "已落实": 3}
    sigs = sorted(out["signals"],
                  key=lambda s: (order.get(s["final_classification"], 9),
                                 s["eoicd_cluster_id"]))
    return [(i, s) for i, s in enumerate(sigs, 1)]


def _color_for_attr_segment(seg):
    """根据属性比对结果分段末缀判定颜色 key（不一致/无提及/无具体值/一致）。"""
    if seg.endswith("不一致"):
        return "不一致"
    if seg.endswith("无提及"):
        return "无提及"
    if seg.endswith("无具体值"):
        return "无具体值"
    return "一致"


def _fill_colored_attr_summary(cell, attr_summary):
    """把『具体的属性比对结果』按属性结果类型着色渲染（docx 单元格）。

    仅对真实属性结果分段（末缀为 不一致/无提及/无具体值/一致）着色；
    兜底文本（如完整性兜底补录的『未通过身份识别（无属性比对数据）』）末缀不匹配，
    用默认黑色，避免误染绿（绿=一致）。
    """
    cell.text = ""
    para = cell.paragraphs[0]
    if not _HAS_DOCX or not RESULT_COLORS:
        para.add_run(attr_summary or "")
        return
    parts = [p for p in (attr_summary or "").split("；") if p]
    if not parts:
        para.add_run(attr_summary or "")
        return
    for i, p in enumerate(parts):
        key = _color_for_attr_segment(p)
        run = para.add_run(p)
        if not (key == "一致" and not p.endswith("一致")):
            run.font.color.rgb = RESULT_COLORS[key]
        if i < len(parts) - 1:
            para.add_run("；")


def _fill_classified_cell(cell, text, classification):
    """把信号『判定结果』按分类着色渲染（docx 单元格）。"""
    cell.text = ""
    para = cell.paragraphs[0]
    run = para.add_run(text or "")
    if _HAS_DOCX and RESULT_COLORS:
        # 直接用与 Excel 一致的语义：不一致红、未落实黄、部分落实蓝、已落实绿
        key = {"不一致": "不一致", "未落实": "无提及",
               "部分落实": "无具体值", "已落实": "一致"}.get(classification)
        if key:
            run.font.color.rgb = RESULT_COLORS[key]


def write_txt_summary(out, path):
    meta = out["meta"]
    lines = []
    lines.append("===== 匹配结果汇总（%s）=====" % (meta.get("direction")))
    lines.append("信号总数：%d" % meta.get("total_signals"))
    lines.append("")
    lines.append("【信号级最终分类】")
    for k in ("不一致", "未落实", "部分落实", "已落实"):
        lines.append("  %s：%d" % (k, meta["classification_distribution"].get(k, 0)))
    lines.append("  口径：'已落实'要求信号全部保留属性均为「属性一致」；只要存在任一"
                 "「无提及」或「无具体值」属性即归'部分落实'/'未落实'，不计'已落实'。"
                 "故已落实=0 通常反映 EoICD→HLR 覆盖率不足，而非工具漏判。")
    lines.append("")
    lines.append("【单属性结果】")
    for k in ("属性一致", "属性不一致", "无提及", "无具体值"):
        lines.append("  %s：%d" % (k, meta["single_attr_distribution"].get(k, 0)))
    if meta.get("unexpected_result_count"):
        lines.append("【意外取值】共 %d 条 result 不在已知 4 类，已归并为「无具体值」"
                     % meta["unexpected_result_count"])
        lines.append("  对应条目已标记 unexpected_result=true（见各信号 attributes 下钻）")
    lines.append("")
    cov = meta.get("coverage_breakdown", {}) or {}
    if cov.get("not_mentioned_total"):
        lines.append("【需求覆盖率：EoICD 有该属性但 HLR 未提及，共 %d 条】"
                     % cov["not_mentioned_total"])
        top = sorted(cov.get("not_mentioned_by_class", {}).items(), key=lambda x: -x[1])
        for k, v in top:
            lines.append("  %s：%d" % (k, v))
        lines.append("")
    lines.append("【明细展开：各信号比对结果（全部 %d 个信号）】" % meta["total_signals"])
    order = {"不一致": 0, "未落实": 1, "部分落实": 2, "已落实": 3}
    sorted_sigs = sorted(out["signals"],
                         key=lambda s: (order.get(s["final_classification"], 9),
                                        s["eoicd_cluster_id"]))
    for i, s in enumerate(sorted_sigs, 1):
        lines.append("")
        lines.append("── 序号 %d ──" % i)
        lines.append("信号FullName   : %s" % s.get("signal_full_name"))
        lines.append("匹配需求编号   : %s" % ("；".join(s.get("matched_req_ids") or []) or "（无）"))
        lines.append("判定结果       : %s" % s.get("final_classification"))
        lines.append("属性比对结果   : %s" % s.get("attr_summary"))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# 未落实子分类 -> 展示名（docx 子小节标题与 Excel 工作表名共用，对应上一轮【未落实】父标题修改）
_SUB_DISPLAY = {
    "已识别未承接": "需求中有信号信息但未提及任何属性",
    "未识别": "需求中没有任何信号信息",
}


def write_docx_summary(out, path):
    """将汇总结果输出为 Word（.docx）文档：含标题、分布表格、下钻抽样。"""
    meta = out["meta"]
    doc = Document()

    # 明细表较宽，采用横向排版以保证可读性
    try:
        from docx.enum.section import WD_ORIENT
        sec = doc.sections[0]
        sec.orientation = WD_ORIENT.LANDSCAPE
        sec.page_width, sec.page_height = Cm(29.7), Cm(21.0)
    except Exception:
        pass

    doc.add_heading("匹配结果汇总（%s）"
                    % (meta.get("direction")), level=0)
    doc.add_paragraph("信号总数：%d" % meta.get("total_signals"))

    # 信号级最终分类
    doc.add_heading("【信号级最终分类】", level=1)
    t = doc.add_table(rows=1, cols=2)
    t.style = "Light Grid Accent 1"
    t.rows[0].cells[0].text = "分类"
    t.rows[0].cells[1].text = "数量"
    for k in ("不一致", "未落实", "部分落实", "已落实"):
        r = t.add_row().cells
        r[0].text, r[1].text = k, str(meta["classification_distribution"].get(k, 0))

    doc.add_paragraph(
        "口径说明：'已落实'要求信号的全部保留属性均为「属性一致」；只要存在任一"
        "「无提及」或「无具体值」属性即归为'部分落实'/'未落实'，不计'已落实'。"
        "故某方向已落实=0 通常反映 EoICD→HLR 覆盖率不足，而非工具漏判。")

    # 单属性结果
    doc.add_heading("【单属性结果】", level=1)
    t2 = doc.add_table(rows=1, cols=3)
    t2.style = "Light Grid Accent 1"
    h2 = t2.rows[0].cells
    h2[0].text, h2[1].text, h2[2].text = "结果", "数量", "说明"
    _sd = meta.get("single_attr_distribution", {})
    for k, desc in (("属性一致", "两侧取值核对一致"),
                    ("属性不一致", "两侧取值核对不一致（缺陷）"),
                    ("无提及", "EoICD 有该属性，HLR 未提要求（覆盖率问题）"),
                    ("无具体值", "尚未判定（取值无法解析/HLR 未写可比值）")):
        r = t2.add_row().cells
        r[0].text, r[1].text, r[2].text = k, str(_sd.get(k, 0)), desc

    if meta.get("unexpected_result_count"):
        doc.add_paragraph(
            "⚠ 意外取值：共 %d 条 result 不在已知 4 类，已归并为「无具体值」"
            "（对应条目标记 unexpected_result=true，见下钻 attributes）。"
            % meta["unexpected_result_count"])

    # 需求覆盖率
    cov = meta.get("coverage_breakdown", {}) or {}
    if cov.get("not_mentioned_total"):
        doc.add_heading("【需求覆盖率：EoICD 有该属性但 HLR 未提及】", level=1)
        doc.add_paragraph("合计 %d 条。各属性类未提及数（按数量降序，全部列出）："
                          % cov["not_mentioned_total"])
        t4 = doc.add_table(rows=1, cols=2)
        t4.style = "Light Grid Accent 1"
        t4.rows[0].cells[0].text = "属性类"
        t4.rows[0].cells[1].text = "未提及数"
        for k, v in sorted(cov.get("not_mentioned_by_class", {}).items(),
                           key=lambda x: -x[1]):
            r = t4.add_row().cells
            r[0].text, r[1].text = str(k), str(v)

    # 明细展开：按信号问题类型分小节列出，便于在数量多时快速区分
    doc.add_heading("【明细展开：各信号比对结果（按问题类型分节）】", level=1)
    doc.add_paragraph("以下按信号判定结果分为四节列出全部 %d 个信号；"
                      "各节内『序号』为全局连续编号，与 Excel 附件的序号一致"
                      "（本表不含『分析内容』列，该列仅保留于 Excel 附件；"
                      "其中『需求中没有任何信号信息』小节仅包含序号/信号名/判定结果）。"
                      % meta["total_signals"])

    ordered = sorted_signals(out)
    headers = ["序号", "信号FullName", "匹配的需求编号", "信号的判定结果",
               "具体的属性比对结果"]
    widths = [Cm(1.0), Cm(5.0), Cm(3.0), Cm(2.0), Cm(8.0)]

    def emit_section(cls, title, level, sub_filter=None, unrec=False):
        """按 cls 输出一个明细小节；sub_filter 非空时仅在未落实内按子分类筛选。
        unrec=True 时（未识别小节）直接删除『匹配的需求编号』与『具体的属性比对结果』
        两列，仅保留 序号/信号FullName/信号的判定结果。"""
        if sub_filter is None:
            group = [(i, s) for i, s in ordered if s["final_classification"] == cls]
        else:
            group = [(i, s) for i, s in ordered
                     if s["final_classification"] == cls and sub_filter(s)]
        if not group:
            return
        doc.add_heading("%s（%d 个）" % (title, len(group)), level=level)
        if unrec:
            cols = ["序号", "信号FullName", "信号的判定结果"]
            ws_ = [Cm(1.0), Cm(5.0), Cm(2.0)]
            table = doc.add_table(rows=1, cols=len(cols))
            table.style = "Light Grid Accent 1"
            hdr = table.rows[0].cells
            for i, h in enumerate(cols):
                hdr[i].text = h
                hdr[i].width = ws_[i]
            for idx, s in group:
                row = table.add_row().cells
                row[0].text = str(idx)
                row[1].text = str(s.get("signal_full_name") or "")
                _fill_classified_cell(row[2], str(s.get("final_classification") or ""), cls)
                for i, w in enumerate(ws_):
                    row[i].width = w
        else:
            table = doc.add_table(rows=1, cols=len(headers))
            table.style = "Light Grid Accent 1"
            hdr = table.rows[0].cells
            for i, h in enumerate(headers):
                hdr[i].text = h
                hdr[i].width = widths[i]
            for idx, s in group:
                row = table.add_row().cells
                row[0].text = str(idx)
                row[1].text = str(s.get("signal_full_name") or "")
                row[2].text = "；".join(s.get("matched_req_ids") or [])
                _fill_classified_cell(row[3], str(s.get("final_classification") or ""), cls)
                _fill_colored_attr_summary(row[4], str(s.get("attr_summary") or ""))
                for i, w in enumerate(widths):
                    row[i].width = w

    # 不一致 / 部分落实 / 已落实 三节
    emit_section("不一致", "【不一致】缺陷，需重点核查", 2)
    emit_section("部分落实", "【部分落实】有属性未落实/无具体值", 2)
    emit_section("已落实", "【已落实】全部保留属性一致", 2)

    # 未落实 拆为两个子小节：已识别未承接 / 未通过身份识别（未识别）
    wf = [(i, s) for i, s in ordered if s["final_classification"] == "未落实"]
    if wf:
        n_recog = sum(1 for _, s in wf if s.get("sub_classification") == "已识别未承接")
        n_unrec = sum(1 for _, s in wf if s.get("sub_classification") == "未识别")
        doc.add_heading("【未落实】（共 %d 个；需求中有信号信息但未提及任何属性 %d / 需求中没有任何信号信息 %d）"
                        % (len(wf), n_recog, n_unrec), level=2)
        emit_section("未落实", _SUB_DISPLAY["已识别未承接"], 3,
                     sub_filter=lambda s: s.get("sub_classification") == "已识别未承接")
        emit_section("未落实", _SUB_DISPLAY["未识别"], 3,
                     sub_filter=lambda s: s.get("sub_classification") == "未识别",
                     unrec=True)

    doc.save(path)


def write_xlsx_summary(out, path):
    """将全量明细展开输出为 Excel 附件：含『统计页』『汇总页』与多个『按问题类型分类』的 Sheet 页。

    工作表结构：
      1. 统计页  —— 信号级/属性级分类统计与覆盖率；
      2. 汇总页  —— 所有问题类型信号的全量明细展开（即原"明细展开"页，一个 Sheet 内列出全部信号）；
         6 列（序号/信号FullName/匹配的需求编号/判定结果/属性比对结果/分析内容）；
         未识别信号在该页『匹配的需求编号』『分析内容』留空，保留『属性比对结果』。
      3. 按问题类型分类的 Sheet 页 —— 每个信号分类（不一致 / 需求中有信号信息但未提及任何属性 /
         需求中没有任何信号信息 / 部分落实 / 已落实）单独成页，启用自动筛选、冻结首行与分类着色；
         未识别 Sheet 直接删除『匹配的需求编号』『分析内容』两列，仅 4 列
         （序号/信号FullName/判定结果/属性比对结果）；其余 Sheet 为 6 列（含分析内容）。
    """
    meta = out["meta"]
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont
    RESULT_HEX = {
        "不一致": "FFC00000",
        "无提及": "FFBF8F00",
        "无具体值": "FF2E75B6",
        "一致": "FF007000",
    }

    def _attr_rich_text(attr_summary):
        """将『具体的属性比对结果』按分段着色为富文本（兜底黑色，避免误染绿）。"""
        parts = [p for p in (attr_summary or "").split("；") if p]
        rt = CellRichText()
        for i, p in enumerate(parts):
            key = _color_for_attr_segment(p)
            color = "000000" if (key == "一致" and not p.endswith("一致")) else RESULT_HEX.get(key, "000000")
            rt.append(TextBlock(InlineFont(color=color), p))
            if i < len(parts) - 1:
                rt.append("；")
        return rt

    wb = Workbook()

    # 按信号问题类型分组（未落实按子类型拆 sheet）
    ordered = sorted_signals(out)

    def _sheet_key(s):
        cls = s["final_classification"]
        if cls == "未落实":
            return _SUB_DISPLAY.get(s.get("sub_classification") or "未识别", "未识别")
        return cls

    groups = {}
    for idx, s in ordered:
        groups.setdefault(_sheet_key(s), []).append((idx, s))

    # 固定的问题类型顺序（汇总页与 Sheet 页均按此序）
    TYPE_ORDER = ["不一致", "需求中有信号信息但未提及任何属性", "需求中没有任何信号信息",
                  "部分落实", "已落实"]

    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    _cell_align = Alignment(vertical="top", wrap_text=True)

    # ---- Sheet 1: 统计页 ----
    ws0 = wb.active
    ws0.title = "统计页"
    ws0.append(["匹配结果汇总（%s）" % meta.get("direction")])
    ws0.append(["信号总数", meta.get("total_signals")])
    ws0.append([])
    ws0.append(["信号级最终分类", "数量"])
    for k in ("不一致", "部分落实", "已落实"):
        ws0.append([k, meta["classification_distribution"].get(k, 0)])
    # 未落实（合并两个子类型计数）
    wl_total = meta["classification_distribution"].get("未落实", 0)
    ws0.append(["未落实（合计）", wl_total])
    ws0.append([])
    ws0.append(["单属性结果", "数量", "说明"])
    _sd = meta.get("single_attr_distribution", {})
    for k, desc in (("属性一致", "两侧取值核对一致"),
                    ("属性不一致", "两侧取值核对不一致（缺陷）"),
                    ("无提及", "EoICD 有该属性，HLR 未提要求（覆盖率问题）"),
                    ("无具体值", "尚未判定（取值无法解析/HLR 未写可比值）")):
        ws0.append([k, _sd.get(k, 0), desc])
    # 覆盖率
    cov = meta.get("coverage_breakdown", {}) or {}
    if cov:
        ws0.append([])
        ws0.append(["需求覆盖率", "数值", "说明"])
        nm_total = cov.get("not_mentioned_total")
        if nm_total is not None:
            ws0.append(["『无提及』属性总条数", nm_total,
                        "EoICD 有属性但 HLR 未提要求的总条目数"])
    bold = Font(bold=True)
    for r in (1, 4, 7):
        for c in ws0[r]:
            c.font = bold
    ws0.column_dimensions["A"].width = 22
    ws0.column_dimensions["B"].width = 12
    ws0.column_dimensions["C"].width = 40

    # 明细展开通用表头（汇总页与按类型分类 Sheet 复用）
    headers = ["序号", "信号FullName", "匹配的需求编号", "信号的判定结果",
               "具体的属性比对结果", "分析内容"]

    # ---- 汇总页（所有信号全量明细展开，即原"明细展开"页）----
    ws_sum = wb.create_sheet("汇总页")
    ws_sum.append(headers)
    hdr_fill = PatternFill("solid", fgColor="4472C4")
    hdr_font = Font(bold=True, color="FFFFFF")
    for c in ws_sum[1]:
        c.fill = hdr_fill
        c.font = hdr_font
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for idx, s in ordered:
        cls = s["final_classification"]
        sub = s.get("sub_classification")
        # 未识别信号：『匹配的需求编号』与『分析内容』两列留空（不能整列删除，
        # 因为汇总页含全部信号类型），但保留『具体的属性比对结果』以承载失败参数追溯。
        is_unrec = (sub == "未识别")
        disp = cls if cls != "未落实" or not sub else "未落实（%s）" % _SUB_DISPLAY.get(sub, sub)
        fill = PatternFill("solid", fgColor=CLASS_FILL.get(cls, "FFFFFF"))
        font = Font(color=CLASS_FONT.get(cls, "000000"))
        attr_rt = _attr_rich_text(s.get("attr_summary") or "")
        ws_sum.append([
            idx,
            str(s.get("signal_full_name") or ""),
            "" if is_unrec else "；".join(s.get("matched_req_ids") or []),
            disp,
            None,
            "" if is_unrec else str(s.get("analysis") or ""),
        ])
        r = ws_sum.max_row
        ws_sum.cell(row=r, column=5).value = attr_rt
        ws_sum.cell(row=r, column=4).fill = fill
        ws_sum.cell(row=r, column=4).font = font
        for col in range(1, 7):
            cell = ws_sum.cell(row=r, column=col)
            cell.border = border
            cell.alignment = _cell_align
    last = ws_sum.max_row
    ws_sum.auto_filter.ref = "A1:F%d" % last
    ws_sum.freeze_panes = "A2"
    for col, w in zip("ABCDEF", (6, 46, 22, 16, 42, 72)):
        ws_sum.column_dimensions[col].width = w

    # ---- 按问题类型分类的 Sheet 页 ----
    for k in TYPE_ORDER:
        items = groups.get(k)
        if not items:
            continue
        # 未识别 Sheet 为纯未识别信号，直接删除『匹配的需求编号』『分析内容』两列，
        # 仅保留 序号/信号FullName/判定结果/属性比对结果（4 列）；其余 Sheet 为 6 列。
        unrec_sheet = (k == _SUB_DISPLAY.get("未识别"))
        ws = wb.create_sheet(k)
        if unrec_sheet:
            ws.append(["序号", "信号FullName", "信号的判定结果", "具体的属性比对结果"])
        else:
            ws.append(headers)
        hdr_fill = PatternFill("solid", fgColor="4472C4")
        hdr_font = Font(bold=True, color="FFFFFF")
        for c in ws[1]:
            c.fill = hdr_fill
            c.font = hdr_font
            c.alignment = Alignment(vertical="center", wrap_text=True)
        for idx, s in items:
            cls = s["final_classification"]
            sub = s.get("sub_classification")
            # 未落实 在判定结果列展示子类型，便于与 docx 子小节一致；其余类别原样
            disp = cls if cls != "未落实" or not sub else "未落实（%s）" % _SUB_DISPLAY.get(sub, sub)
            fill = PatternFill("solid", fgColor=CLASS_FILL.get(cls, "FFFFFF"))
            font = Font(color=CLASS_FONT.get(cls, "000000"))
            attr_rt = _attr_rich_text(s.get("attr_summary") or "")
            if unrec_sheet:
                ws.append([idx, str(s.get("signal_full_name") or ""), disp, None])
                r = ws.max_row
                ws.cell(row=r, column=4).value = attr_rt
                ws.cell(row=r, column=3).fill = fill
                ws.cell(row=r, column=3).font = font
                for col in range(1, 5):
                    cell = ws.cell(row=r, column=col)
                    cell.border = border
                    cell.alignment = _cell_align
            else:
                ws.append([
                    idx,
                    str(s.get("signal_full_name") or ""),
                    "；".join(s.get("matched_req_ids") or []),
                    disp,
                    None,  # 占位，下面用单元格对象显式写入富文本以保留着色
                    str(s.get("analysis") or ""),
                ])
                r = ws.max_row
                ws.cell(row=r, column=5).value = attr_rt
                ws.cell(row=r, column=4).fill = fill
                ws.cell(row=r, column=4).font = font
                for col in range(1, 7):
                    cell = ws.cell(row=r, column=col)
                    cell.border = border
                    cell.alignment = _cell_align

        last = ws.max_row
        if unrec_sheet:
            ws.auto_filter.ref = "A1:D%d" % last
            ws.freeze_panes = "A2"
            for col, w in zip("ABCD", (6, 46, 16, 42)):
                ws.column_dimensions[col].width = w
        else:
            ws.auto_filter.ref = "A1:F%d" % last
            ws.freeze_panes = "A2"
            for col, w in zip("ABCDEF", (6, 46, 22, 16, 42, 72)):
                ws.column_dimensions[col].width = w

    wb.save(path)


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    default_data = os.path.join(base, "input")
    default_raw = default_data  # 输入文件直接位于 input/ 下（无 raw 子层）
    default_out = os.path.join(base, "outputs")
    parser = argparse.ArgumentParser(description="匹配结果分类与汇总")
    parser.add_argument("--match", action="append", default=[],
                        help="属性匹配报告(attribute_match_report_*.json)，可多次指定；"
                             "默认自动找 ./input/attribute_match_report_{pub,sub}.json")
    parser.add_argument("--config", default=os.path.join(base, "config_summary.json"))
    parser.add_argument("--out", default=default_out,
                        help="输出根目录；每次运行在其中生成 '<设备名>EoICD到软件高层需求的落实检查_<时间戳>' 子文件夹")
    parser.add_argument("--device", default=None,
                        help="设备名前缀（文件夹名 XXX 部分）；默认从 input 下 '高层需求规范' docx 自动识别")
    args = parser.parse_args()

    cfg = load_cfg(args.config)
    os.makedirs(args.out, exist_ok=True)

    # 本次运行落到独立时间戳文件夹，避免多次运行互相覆盖，也顺带规避 Excel 占用锁
    device = args.device if args.device is not None else _detect_device_name(default_raw)
    folder_name = "%sEoICD到软件高层需求的落实检查_%s" % (device, _run_timestamp())
    run_dir = os.path.join(args.out, folder_name)
    os.makedirs(run_dir, exist_ok=True)
    print("本次输出目录:", run_dir)

    matches = args.match or [os.path.join(default_raw, "attribute_match_report_pub.json"),
                             os.path.join(default_raw, "attribute_match_report_sub.json")]

    summary = {}
    for mp in matches:
        if not os.path.isfile(mp):
            print("[跳过] 缺属性匹配报告: %s" % mp)
            continue
        raw = load_json(mp)
        attr_report = normalize_attribute_match(raw)
        out = run_direction(attr_report, cfg)
        direction = (attr_report.get("meta", {}).get("direction")
                     or os.path.basename(mp).replace("attribute_match_report_", "").replace(".json", ""))
        # 完整性兜底：消费同方向身份识别阶段产物（rejected 文件夹），
        # 补全属性匹配阶段"消失"的信号（未通过身份识别）
        rejected_dir = os.path.join(default_raw, "rejected")
        rejections = load_identity_rejections(direction, rejected_dir)
        if rejections:
            apply_identity_safeguard(out, rejections, cfg)
        else:
            print("[提示] 未找到身份识别产物（rejected 文件夹为空或缺失），跳过完整性兜底")
        jpath = os.path.join(run_dir, "report_%s.json" % direction)
        with open(jpath, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=cfg.get("indent_json", 2))
        summary[direction] = out["meta"]
        print("已生成:", jpath)
        if _HAS_DOCX:
            dpath = os.path.join(run_dir, "summary_%s.docx" % direction)
            dpath = _safe_path(dpath)
            write_docx_summary(out, dpath)
            print("已生成:", dpath)
        else:
            tpath = os.path.join(run_dir, "summary_%s.txt" % direction)
            write_txt_summary(out, tpath)
            print("已生成:", tpath, "（未安装 python-docx，回退 txt）")
        if _HAS_XLSX:
            xpath = os.path.join(run_dir, "summary_%s.xlsx" % direction)
            xpath = _safe_path(xpath)
            write_xlsx_summary(out, xpath)
            print("已生成:", xpath)

    print("=== 匹配结果汇总完成 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
