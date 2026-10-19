# -*- coding: utf-8 -*-
"""
multi_judge_aggregate.py — 多 judge 分片聚合（正向检查多智能体嫁接 · 阶段5聚合层）

把 N 个 judge（每个 judge = 一次完整正向检查运行）产出的「按 EoICD 信号 signal_full_name
对齐的分片」汇聚成最终报告。

设计对齐反向检查的多智能体机制（详见 正向检查多judge嫁接方案.md §11/§12/§13）：
  - 多数投票（反向 MultiJudgeResult 共识推导）
  - 分歧标记 field_disagreements（仅在 KEY_FIELDS 上算，反向同名机制）
  - 覆盖缺口 coverage_gap（反向 coverage_status：某 judge 未出结论，不当作冲突票）
  - 错误即合法判定（反向 make_error_judgment 思想）
  - agreement_level 显式归一化（3/3、2/3…）
  - 星级 star_rating（对齐反向 1~5★ 映射：full=5/4、majority+关键异议=2、split=1、单源/0存活=1）
  - 两维共识分开：身份共识(verdict+matched_hlr+name_match_status+地址四元组) 与 属性共识(各 attribute_class 的单属性结论)
  - 严格二分「事实不一致 inconsistent_attributes」与「judge 间分歧 field_disagreements」
  - 结构化属性 diff（field-level：attribute_class/eoicd_value/hlr_value/diff_type）
  - 0 存活 provider 强制压星 + 强制需复核（反向 zero_provider 规则）
  - judge 元数据可追溯（model/base_url/key_hash/frozen）

本模块不依赖任何 LLM/网络，可离线运行；`python multi_judge_aggregate.py` 自带合成数据自测。
LLM 仲裁层（Step5 仲裁者共识 + Step5.5 peer-aware 复查）在独立模块 multi_judge_arbitrator.py，
由 runner 在配置仲裁者后调用，叠加在本聚合结果之上。

用法（被 multi_judge_runner.py 调用）：
    from multi_judge_aggregate import build_shard_from_reports, aggregate, write_outputs
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from collections import Counter

# KEY_FIELDS（身份层，judge 结论骨架）—— 分歧即真分歧；事实不一致走 inconsistent_attributes。
# 与方案 §12.3 精确定义一致：
#   进 KEY_FIELDS：verdict + matched_hlr + name_match_status + 地址四元组(bus/label/bit/direction)
#                   + 每 attribute_class 的结论字段(single_result)。
#   不进 KEY_FIELDS：eoicd_val/hlr_val(属性值事实本身)、name 文本值、rationale/analysis、
#                   confidence（除非后续加权）。
KEY_FIELDS_IDENTITY = ("verdict", "matched_hlr", "name_match_status",
                       "bus", "label", "bit", "direction")
ADDRESS_KEYS = ("bus", "label", "bit", "direction")

# 单属性结论的「不一致」取值（任一 judge 命中即记为事实不一致）
_INCONSISTENT_RESULTS = ("不一致", "属性不一致")


# ---------------------------------------------------------------------------
# 1) 从单个 judge 的 stage-5 report_*.json 构建按 signal_full_name 对齐的分片
# ---------------------------------------------------------------------------

def _norm_matched_hlr(value) -> tuple:
    """把 matched_hlr 归一为排序后的 (req_id, iface) 元组序列，便于比较。

    支持两种结构（#5 修复）：
      - 富结构：每个元素为 [req_id, iface_str] / (req_id, iface_str)，iface_str 为
        归一化接口地址（bus|label|bit|direction），可区分「同一 req_id 匹配到不同接口」；
      - 兼容旧结构：元素为纯 req_id 字符串，接口记为 ""。
    """
    if value is None:
        return tuple()
    items = value if isinstance(value, (list, tuple, set)) else [value]
    out = []
    for v in items:
        if v is None:
            continue
        if isinstance(v, (list, tuple)) and len(v) == 2:
            out.append((str(v[0]), str(v[1] or "")))
        else:
            out.append((str(v), ""))
    return tuple(sorted(out))


def _collapse_matched(values) -> list:
    """把多个 judge 的 matched_hlr 值折叠成展示串列表（按 req_id 去冗余）。

    针对同一 req_id 的两种表示做去重：
      - 若某 req_id 同时存在「带接口」(req[iface]) 与「空接口」(req) 写法，
        空接口版是带接口版的子集（编号信息已含），丢弃空接口版；
      - 不同 req_id、或同一 req_id 的「不同非空接口」(#5 真实接口级分歧) 均保留。

    这样单元格只呈现带接口的一条，避免把同一 HLR 渲染成两条冗余。
    排序：按被引用的 judge 数降序、再按 req_id 升序，使最常见结论在前。
    """
    pairs: dict[str, set] = {}      # req -> {iface,...}（iface 空串=空接口）
    counts: dict[str, int] = {}
    for v in values:
        seen_reqs: set = set()
        for req, iface in _norm_matched_hlr(v):
            pairs.setdefault(req, set()).add(iface)
            seen_reqs.add(req)
        for r in seen_reqs:
            counts[r] = counts.get(r, 0) + 1
    out: list[str] = []
    emitted: set[str] = set()
    for req in sorted(pairs, key=lambda r: (-counts.get(r, 0), r)):
        non_empty = sorted(i for i in pairs[req] if i)
        if non_empty:
            for i in non_empty:
                tok = f"{req}[{i}]"
                if tok not in emitted:
                    emitted.add(tok)
                    out.append(tok)
        else:
            tok = req
            if tok not in emitted:
                emitted.add(tok)
                out.append(tok)
    return out


def _collapse_matched_ids(values) -> list:
    """多 HLR 修复·显示层用：只取 req_id，不带 [iface]。

    Word/Excel 报告的"匹配 HLR"列读此字段；接口描述符（bus/label/bit/direction）
    已在分析摘要的 `_match_desc`（`R12（a429 / l11 / ...）`）展示，此处不重复，
    避免列宽浪费 + 视觉冗余。同一 req_id 多次出现去重。
    排序与 `_collapse_matched` 一致：按被引用的 judge 数降序、再按 req_id 升序。
    """
    counts: dict[str, int] = {}
    for v in values:
        for req, _ in _norm_matched_hlr(v):
            counts[req] = counts.get(req, 0) + 1
    return sorted(counts, key=lambda r: (-counts[r], r))


def _display_matched_full(value) -> str:
    """把单个 judge 的 matched_hlr 富结构压成**带接口地址**的展示串：R12[1|0x10|8|pub],R99[2|0x33|2|sub]。

    #5 可见性修复：只在 CSV 侧显示 req_id 的话，两个 judge「同名 req 不同接口」的结论
    在 CSV 里会长得一模一样，复核人无法察觉接口级分歧，只能去翻 JSON。
    接口为空（旧 report 未提供接口信息）时退化为纯 req_id，保持向后兼容。
    同一 req_id 若既有带接口版又有空接口版，仅保留带接口版（见 _collapse_matched）。
    """
    return ",".join(_collapse_matched([value]))


def _norm_addr(identity: dict | None) -> dict:
    """把 hlr_identity 地址四元组归一为小写字符串 dict（缺失=空串）。"""
    d = identity if isinstance(identity, dict) else {}
    return {k: str(d.get(k, "") or "").strip().lower() for k in ADDRESS_KEYS}


def build_shard_from_reports(report_dir: Path) -> dict:
    """读取给定目录（judge 工作区的 <设备名>EoICD到软件高层需求的落实检查_<时间戳>/ 文件夹）
    下**平铺**的 report_{pub,sub}.json，返回 {signal_full_name: entry}。

    约定：调用方（见 runner._latest_summary_folder）负责定位到确切含 report 文件的文件夹，
    本函数只直接读 report_dir/report_{pub,sub}.json，不再下钻 sub 目录，
    以免把聚合层耦合到 summary.py 的产物目录命名约定（同 #6 的解耦原则）。
    任一方向的 report 缺失即跳过该方向；两方向均无 signal_full_name 时返回空 dict。
    报告需含顶层 "signals" 列表，每项含 signal_full_name / matched_hlrs / matched_req_ids /
    final_classification / name_match_status / hlr_identity / attributes。

    entry 形如：
      {
        "direction": "pub"|"sub",
        "matched_hlr": [[req_id, 接口地址], ...] or [req_id, ...],  # #5 富结构；旧 report 退化为纯 req_id
        "verdict": "已落实"|"不一致"|"未落实"|"部分落实",
        "sub_classification": "已识别未承接"|"未识别"|None,
        "name_match_status": bool,                     # 身份层 KEY_FIELD
        "hlr_identity": {bus,label,bit,direction,name},# 身份层 KEY_FIELD
        "attr_summary": "...",
        "analysis": "...",
        "confidence": None,                            # 正向管线当前不产出，留接口
        "rationale": None,
        "coverage": "ok",
        "attributes": [ {attribute_class, single_result, matched_hlr_req_id,
                         eoicd_value, hlr_value, hlr_identity}, ... ]
      }
    """
    shard: dict[str, dict] = {}
    if not report_dir.exists():
        return shard
    for direction in ("pub", "sub"):
        rp = report_dir / f"report_{direction}.json"
        if not rp.exists():
            continue
        try:
            with open(rp, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  [警告] 读取 {rp} 失败：{e}")
            continue
        for sig in data.get("signals", []):
            full = sig.get("signal_full_name")
            if not full:
                continue
            attrs = []
            for a in sig.get("attributes", []):
                cls = a.get("attribute_class")
                if not cls:
                    continue
                attrs.append({
                    "attribute_class": cls,
                    "single_result": a.get("single_result"),
                    "matched_hlr_req_id": a.get("matched_hlr_req_id"),
                    "eoicd_value": a.get("eoicd_value"),
                    "hlr_value": a.get("hlr_value"),
                    "hlr_identity": a.get("hlr_identity"),
                })
            # 优先使用富结构 matched_hlrs(req_id+接口)；兼容旧 report 仅有 req_id 列表的情形
            matched = sig.get("matched_hlrs")
            if not matched:
                matched = [[r, ""] for r in (sig.get("matched_req_ids") or [])]
            entry = {
                "direction": direction,
                "matched_hlr": matched,
                "verdict": sig.get("final_classification"),
                "sub_classification": sig.get("sub_classification"),
                "name_match_status": sig.get("name_match_status"),
                "hlr_identity": sig.get("hlr_identity"),
                "attr_summary": sig.get("attr_summary"),
                "analysis": sig.get("analysis"),
                "confidence": sig.get("confidence"),
                "rationale": sig.get("rationale"),
                "coverage": "ok",
                "attributes": attrs,
            }
            # 同一 full_name 在 pub/sub 两侧理论上不重复；若重复，保留有匹配结论的一侧
            if full in shard:
                prev = shard[full]
                if not prev.get("matched_hlr") and entry.get("matched_hlr"):
                    shard[full] = entry
            else:
                shard[full] = entry
    return shard


# ---------------------------------------------------------------------------
# 2) 跨 judge 聚合
# ---------------------------------------------------------------------------

def _identity_key(entry: dict):
    return (
        entry.get("verdict"),
        _norm_matched_hlr(entry.get("matched_hlr")),
        bool(entry.get("name_match_status")),
        tuple(_norm_addr(entry.get("hlr_identity")).values()),
    )


# 属性结论的「最差」优先序（多 HLR 修复 · 治本）—— 跨 (judge, HLR) 取事实保守口径。
# 严重度（2026-10-16 定稿，与信号级 verdict 序统一）：不一致 > 未落实 > 部分落实 > 已落实。
# 与单 judge `classify_signal`、报告展示序 `VERDICT_GROUPS` 三处保持同一顺序
# （此前本表把「部分落实」排在「未落实」之前，与另两处冲突，已按用户裁决对调）。
# 属性是事实核查，任何一处不匹配都让整体失败（judge 间投票、HLR 间取最差）。
_WORST_PRIORITY = [
    "不一致", "属性不一致",       # 事实不一致（最严重）
    "未落实",                    # HLR 无对应信号（疑似需求缺失，处置更根本）
    "部分落实",
    "已落实",
    "属性一致",
    "无提及",                    # 数据缺失（最轻）
]

# 信号级 verdict 严重度序（**单一来源**，2026-10-16 定稿）：
#   不一致 > 未落实 > 部分落实 > 已落实
# 用途：identity 计票**平票**时裁定 final_verdict（取最严重者，规则确定可复现）。
# 一致性要求：属性层 `_WORST_PRIORITY`（同序，属性专用词另列）、单 judge `classify_signal`
# 的判定优先级、报告展示序 `multi_judge_summary.VERDICT_GROUPS` 三处均与此保持一致。
# `待确认`（无有效源）与 `覆盖缺口` 不参与严重度比较，遇到时退回原取值。
_VERDICT_SEVERITY = ["不一致", "未落实", "部分落实", "已落实"]


def _worst_attr(verdicts) -> str | None:
    """从一组 single_result 取最差，verdicts 可含 None。空集返 None。"""
    vs = [v for v in verdicts if v is not None]
    if not vs:
        return None
    for p in _WORST_PRIORITY:
        if p in vs:
            return p
    return vs[0]  # 兜底：未知 verdict 取首


def _worst_hlr(per_hlr: dict) -> str | None:
    """per_hlr: {req_id: {judge: single_result}}。返驱动 verdict 的最差 HLR req_id。

    取该 HLR 跨 judge 的最差 verdict，再在 HLR 间取最差；并列时返最小 req_id（稳定排序）。
    """
    hlr_verdict = {req: _worst_attr(judge_results.values())
                   for req, judge_results in per_hlr.items()}
    valid = {r: v for r, v in hlr_verdict.items() if v is not None}
    if not valid:
        return next(iter(per_hlr), None)
    # 找最差的 priority 索引最小者
    def _idx(v):
        try:
            return _WORST_PRIORITY.index(v)
        except ValueError:
            return len(_WORST_PRIORITY)
    worst_v = min(valid.values(), key=_idx)
    candidates = sorted(r for r, v in valid.items() if v == worst_v)
    return candidates[0]


def _attr_consensus_per_hlr(valid_entries: list[dict]) -> dict:
    """多 HLR 修复 · 唯一真相源：按 (judge, attribute_class, req_id) 三键分组。

    返回结构（每个 attribute_class 一份）：
      {
        "per_hlr":          {req_id: {judge: single_result}},  # 三键展开，不抹除
        "by_judge":         {judge: 单值},                     # 跨 HLR 最差（向后兼容旧 results key）
        "by_judge_per_hlr": {judge: {req_id: single_result}},  # 用于 cross_hlr 检测
        "eoicd_by_req":     {req_id: eoicd_value},
        "hlr_by_req":       {req_id: hlr_value},
        "worst_req":        req_id,                            # 驱动 consensus 的最差 HLR
        "worst_eoicd":      ...（取自 worst_req）,
        "worst_hlr":        ...（取自 worst_req）,
        "consensus":        跨 (judge, HLR) 全集最差 verdict（事实保守口径）
      }

    修复前 `_attr_consensus_per_class` 按 (judge, attribute_class) 二键塌缩，
    同 cls 在 R12/R99 不同结论时后写覆盖前写 → R99 不一致被 R12 一致抹掉。
    本函数以 (judge, attribute_class, req_id) 三键保留所有原始判定，
    `consensus` 跨全集取最差（任一 HLR 不一致即整体不一致），
    `worst_req` 用于显示「驱动 verdict 的 HLR 侧取值」。

    valid_entries: 来自 shard 的 entry 列表（每条带 `_judge` 标记 + `attributes` 子列表）。
    """
    by_class: dict[str, dict] = {}
    for e in valid_entries:
        j = e.get("_judge")
        for a in e.get("attributes", []):
            cls = a.get("attribute_class")
            if not cls:
                continue
            req = a.get("matched_hlr_req_id") or "_singleton"
            slot = by_class.setdefault(cls, {
                "per_hlr": {},
                "by_judge": {},
                "by_judge_per_hlr": {},
                "eoicd_by_req": {},
                "hlr_by_req": {},
                "worst_req": None,
                "worst_eoicd": None,
                "worst_hlr": None,
                "consensus": None,
            })
            # 三键展开：不覆盖
            slot["per_hlr"].setdefault(req, {})[j] = a.get("single_result")
            slot["by_judge_per_hlr"].setdefault(j, {})[req] = a.get("single_result")
            slot["eoicd_by_req"][req] = a.get("eoicd_value")
            slot["hlr_by_req"][req] = a.get("hlr_value")

    # 派生字段
    for cls, slot in by_class.items():
        # by_judge：每个 judge 跨 HLR 取最差（= 该 judge 对该属性的整体结论）
        for j, per_req in slot["by_judge_per_hlr"].items():
            slot["by_judge"][j] = _worst_attr(per_req.values())
        # consensus：基于 by_judge（各 judge 的整体结论）——全相同取该值，否则「分歧」。
        # 这是原「judge 间是否分歧」语义的**精确推广**：by_judge 即原来的 per-judge single_result，
        # 故 field_disagreements / attr_consensus / _build_attr_seg 完全保持原语义。
        # 同一 judge 内跨 HLR 的差异由 by_judge 的「最差」吸收，事实不一致由 inconsistent_attributes 承载
        # （不再被更早写回的一致值抹除，即为本次多 HLR 修复的根因修复）。
        _bv = {v for v in slot["by_judge"].values() if v is not None}
        if len(_bv) == 1:
            slot["consensus"] = next(iter(_bv))
        elif not _bv:
            slot["consensus"] = None
        else:
            slot["consensus"] = "分歧"
        # worst_req + 展示取值（驱动 verdict 的最差 HLR）
        slot["worst_req"] = _worst_hlr(slot["per_hlr"])
        if slot["worst_req"]:
            slot["worst_eoicd"] = slot["eoicd_by_req"].get(slot["worst_req"])
            slot["worst_hlr"] = slot["hlr_by_req"].get(slot["worst_req"])
    return by_class


def _attr_consensus_per_class(valid_entries: list[dict]) -> dict:
    """向后兼容薄封装：从 `_attr_consensus_per_hlr` 派生旧 4 key 形态。

    返回 {attribute_class: {"results":{judge:single_result}, "eoicd_value":..,
                            "hlr_value":.., "consensus": <值或"分歧">}}。

    旧 key 语义保留：
      - results  = 跨 HLR 最差（= by_judge），等价于原「该 judge 对该属性的整体结论」
      - eoicd_value / hlr_value = 取自 worst_req（驱动 verdict 的最差 HLR）
      - consensus = 基于 by_judge（各 judge 整体结论）全相同取该值、否则「分歧」
                    （原语义的精确推广；多 HLR 时同一 judge 的跨 HLR 差异由 by_judge 最差吸收）
    """
    src = _attr_consensus_per_hlr(valid_entries)
    return {
        cls: {
            "results": slot["by_judge"],
            "eoicd_value": slot["worst_eoicd"],
            "hlr_value": slot["worst_hlr"],
            "consensus": slot["consensus"],
        }
        for cls, slot in src.items()
    }


# 方向归一：EoICD/HLR 侧对发送/接收的命名不统一（TX/RX、PUB/SUB、OUT/IN、发送/接收），
# 统一映射到中文「发送 / 接收」，供报告「属性比对范围」按信号方向拆分。
_TX_DIRS = {"TX", "PUB", "OUT", "SEND", "发送", "1"}
_RX_DIRS = {"RX", "SUB", "IN", "RECV", "接收", "0"}


def _signal_direction(valid: dict) -> str:
    """推断信号方向（发送/接收）。

    取「匹配到 HLR」的 judge 的方向信息：优先 hlr_identity.direction（HLR 侧），
    回退到分片条目的 direction（EoICD 侧）。TX/PUB/OUT→发送；RX/SUB/IN→接收。
    未匹配 HLR 的 judge 无方向可比，跳过。全部缺失则返回空串（报告侧归入「方向未标明」）。
    """
    for e in valid.values():
        if not e.get("matched_hlr"):
            continue
        for src in ((e.get("hlr_identity") or {}).get("direction"), e.get("direction")):
            if not src:
                continue
            s = str(src).upper()
            if s in _TX_DIRS:
                return "发送"
            if s in _RX_DIRS:
                return "接收"
    return ""


def _star_rating(agreement: str, has_key_disagreement: bool, n_valid: int, n_configured: int) -> int:
    """对齐反向星级映射：
        full            → 5（无分歧）/ 4（有分歧）
        majority+关键   → 2
        majority+非关键 → 3
        split/0存活     → 1
        single_source：
            n_configured==1（单 judge 正常模式，退化兼容）→ 5（不强制降级）
            n_configured>1 且仅剩 1 存活（降级）          → 1
    """
    if agreement == "full":
        return 4 if has_key_disagreement else 5
    if agreement == "majority":
        return 2 if has_key_disagreement else 3
    if agreement == "single_source":
        # 单 judge 配置是正常路径（不强制需复核）；多 judge 配置下只剩 1 存活属降级
        return 5 if n_configured == 1 else 1
    return 1  # split / no_consensus


def _consensus_label(star: int, agreement: str) -> str:
    """中文共识标签（对齐反向 consensus_word_generator._map_consensus_label）。

    5★ 完全共识 / 4★ 完全共识·字段异议 / 3★ 多数共识 / 2★ 多数共识·关键异议 /
    1★ 按共识类型细分（三方分歧 / 仅单源 / 全部失效）/ 其余（含 0★ 覆盖缺口）。
    CSV 与 Word 报告共用此函数，避免两处各维护一套标签。
    """
    if star == 5:
        return "完全共识"
    if star == 4:
        return "完全共识·字段异议"
    if star == 3:
        return "多数共识"
    if star == 2:
        return "多数共识·关键异议"
    if star == 1:
        return {
            "split": "三方分歧",
            "single_source": "仅单源",
            "no_consensus": "全部失效",
        }.get(agreement, "降级")
    return "覆盖缺口"


# ---------------------------------------------------------------------------
# 2.5) 路由判定层
# ---------------------------------------------------------------------------
# 反向有一个独立的路由判定层 `_needs_ai_for(证据强度) -> needs_ai`（forward_matcher.py:395-503），
# 逐条写清「什么证据强度该让 AI 插手」，可枚举、可审计。正向原先没有对等物——`need_review`
# 的条件散在 aggregate 主体里的一串 `or`，要调路由策略得改聚合逻辑。此处补齐同一形态：
# 规则表 + 逐条可关闭单条规则。

NEED_REVIEW_RULES: list[dict] = [
    {
        "name": "no_valid_source",
        "detail": "无有效 judge 结论（全部失效或覆盖缺口）",
        "test": lambda c: c["n_valid"] == 0,
    },
    {
        "name": "low_star_under_multijudge",
        "detail": "多 judge 配置下星级 ≤2（低置信）",
        "test": lambda c: (c["n_configured"] > 1 and c["star"] <= 2
                           and c["need_review_on_split"]),
    },
]


def needs_review(ctx: dict, overrides: dict | None = None) -> tuple[bool, list[dict]]:
    """路由判定：该信号是否应送第二裁决（AI 复核）。

    ctx 键：n_valid / star / n_configured / need_review_on_split。
    overrides: {规则名: False} 可逐条关闭（来自 rule.need_review_rules）。
    返回 (是否需复核, 命中的规则列表[{name, detail}])，命中列表即「为什么需复核」的审计依据。
    """
    overrides = overrides or {}
    hits: list[dict] = []
    for r in NEED_REVIEW_RULES:
        if overrides.get(r["name"]) is False:
            continue
        try:
            if r["test"](ctx):
                hits.append({"name": r["name"], "detail": r["detail"]})
        except Exception:
            # 规则异常不得影响主流程：按「不复核」处理，故障由 judge_errors 侧显式记账
            continue
    return len(hits) > 0, hits


def _fd_val_str(v):
    """把 field_disagreements 里单个 judge 的取值转成可展示字符串（布尔/列表/字典统一处理）。"""
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (list, tuple)):
        return ", ".join(_fd_val_str(x) for x in v) or "—"
    if v is None:
        return "—"
    return str(v).strip()


def derive_sub_classification(verdict: str, judges: dict) -> str | None:
    """派生「未落实」二级子类（**单一来源**：聚合层字段与摘要判定句共用同一实现）。

    规则（2026-10-16 定稿）：按各 judge 的**识别状态取多数派**——
      - 仅 `verdict == "未落实"` 时返回取值，其余判定返回 None；
      - 只统计**有效 judge**（`coverage == "ok"`；字段缺失时退化为全部，兼容单 judge / 旧数据）；
      - 「已识别」= `matched_hlr` 或 `name_match_status` 非空；
      - 已识别数 >= 未识别数 → 「已识别未承接」，否则 → 「未识别」；
        平票取「已识别未承接」（优先级：已识别未承接 > 未识别）；
        无有效 judge 时返回「未识别」（对齐原 any 口径的 `bool(n_valid) and ...`）。

    为什么是多数派而不是 any：聚合层其余字段（verdict / agreement / 星级）**都按多数表决**，
    子类若沿用「任一方识别到即可」（any），会成为全流程唯一的 any 例外，
    与「多 judge 交叉验证、少数派可能判错」的设计前提相悖。
    """
    if verdict != "未落实":
        return None
    valid = [v for v in judges.values() if v.get("coverage") == "ok"] or list(judges.values())
    n_ident = sum(1 for v in valid if (v.get("matched_hlr") or v.get("name_match_status")))
    n_not = len(valid) - n_ident
    return "已识别未承接" if (n_ident > 0 and n_ident >= n_not) else "未识别"


def build_analysis_summary(*, final_verdict, judges_with, attr_by_class,
                            inconsistent_attributes, field_disagreements,
                            divergence, n_judges):
    """构造「分析摘要」（一段话 · 纯规则 · 不调 LLM · 可审计）。

    输出为**连续一段话**（不含 \\n），用中文标点分层：
      - 判定句（+少数派/子类）与匹配子句用 `，` 连接；属性子句、补充句各另起一句（`。`）
      - 属性项之间用 `；`，末尾「无提及」汇总句是总结性补充、用 `，` 引出
      - 便于复制转发（字段定位=用户向业务结论，需可携带）

    两处复用：
      - aggregate() 聚合阶段（final_verdict=后端合成判定，_flipped 恒 False）；
      - 仲裁后重算（final_verdict 偏离 judge 多数判定时切「仲裁判定」口径，
        仅以「仲裁判定」为结论，不掺入原 judge 分布等过程细节）。
    """

    # 未落实二级子类（#12）：与聚合层共用 derive_sub_classification（单一来源，避免两处口径漂移）。
    sub_classification = derive_sub_classification(final_verdict, judges_with)

    def _match_desc(_jv):
        _ml = _jv.get("matched_hlr")
        _reqs = sorted({r for r, _ in _norm_matched_hlr(_ml)})
        _req_str = "、".join(_reqs) if _reqs else "—"
        _id = _jv.get("hlr_identity") or {}
        _addr_parts = []
        for k in ("bus", "label", "bit", "direction"):
            _v = _id.get(k)
            if not _v:
                continue
            _v = str(_v)
            if k == "bit":
                _v = f"bit范围{_v}"
            _addr_parts.append(_v)
        _addr = " / ".join(_addr_parts)
        return f"{_req_str}（{_addr}）" if _addr else _req_str

    def _build_attr_seg():
        """返回「属性比对」子句（不含句末 。）。

        属性项之间用 `；`；末尾「无提及」汇总句是总结性补充（非同级并列），用 `，` 引出。

        2026-10-19 改造 1：「属性一致」项不再省略为 `其余 N 项一致`，而是 cap=3 逐项列出
        类名（与 `_notm` L618-624 的「等 N 项」口径一致，避免摘要过长）。列名优先级：
        1 项 = `其余 X 一致`；2 项 = `其余 X 和 Y 一致`；
        3 项 = `其余 X、Y 和 Z 一致`；≥4 项 = `X、Y 和 Z 等 N 项一致`。

        2026-10-19 改造 2：「各 judge 结论分歧」不再只写短语，而是 cap=3 列出每个 judge
        的 single_result，按 results dict 插入序（即 judge_with 注册序，不为排版重排）。
        1 项 = `（judge_A=XX）`；2 项 = `（judge_A=XX；judge_B=YY）`；
        3 项 = `（judge_A=XX；judge_B=YY；judge_C=ZZ）`；
        ≥4 项 = `（judge_A=XX；judge_B=YY；judge_C=ZZ 等 N 个 judge）`。
        """

        def _format_judge_disagreements(results: dict) -> str:
            """格式化「judge=结论」列表：cap=3 + 按 results 插入序。

            空 dict 兜底返回空串（防御性：consensus="分歧" 必有 ≥2 个 judge，但 results
            字段缺失时不能崩）。
            """
            items = list(results.items())
            if not items:
                return ""
            head = "；".join(f"{j}={r}" for j, r in items[:3])
            if len(items) > 3:
                return f"{head} 等 {len(items)} 个 judge"
            return head

        _total = len(attr_by_class)
        if _total == 0:
            return "无属性可比"
        _inc_map = {a["attribute_class"]: a for a in inconsistent_attributes}
        _main = []   # 事实不一致 / judge 分歧 / 其它（用 ；并列）
        _notm = []   # 无提及属性类名（汇总）
        _ok_list = []  # 「属性一致」的属性类名——cap=3 逐项列出，避免 header 总数与正文
                      # 列出项数不闭合（旧实现只报 `_n_ok` 计数，未说明是哪些类）
        for _cls, _info in attr_by_class.items():
            _c = _info["consensus"]
            if _cls in _inc_map:
                _ia = _inc_map[_cls]
                _ev = _ia.get("eoicd_value"); _hv = _ia.get("hlr_value")
                # 对照符用纯 ASCII `vs`：原 `⟷`(U+27F7) 属冷僻符号区，微软雅黑无字形 →
                # Excel/Word 字体回退，单元格内混用两种字体致行高偏大且不均。改用 ASCII 后无回退。
                _val = f"：EoICD {_ev} vs HLR {_hv}" if (_ev is not None or _hv is not None) else ""
                if _c == "分歧":
                    _dj = _format_judge_disagreements(_info.get("results", {}))
                    _main.append(f"{_cls}{_val}，各 judge 结论分歧（{_dj}）")
                else:
                    _main.append(f"{_cls}{_val}")
                continue
            if _c == "属性一致":
                _ok_list.append(_cls)  # 收集类名，备 cap=3 逐项列出
                continue
            if _c == "无提及":
                _notm.append(_cls)
            elif _c == "分歧":
                _dj = _format_judge_disagreements(_info.get("results", {}))
                _main.append(f"{_cls}：各 judge 结论分歧（{_dj}）")
            else:
                _main.append(f"{_cls} {_c}")
        if _notm:
            if len(_notm) == 1:
                _nm = f"{_notm[0]} 无提及"
            elif len(_notm) == 2:
                _nm = f"{_notm[0]}、{_notm[1]} 无提及"
            else:
                _nm = f"{_notm[0]} 等 {len(_notm)} 项属性无提及"
        else:
            _nm = ""
        if not _main and not _nm:
            return f"属性比对 {_total} 项全部一致"
        _body = "；".join(_main)
        if _main and _nm:
            _body += "，" + _nm
        elif _nm:
            _body = _nm
        # 补报「属性一致」项（cap=3 逐项列出，使 header 总数与正文闭合）
        if _ok_list:
            if len(_ok_list) == 1:
                _ok_tail = f"其余 {_ok_list[0]} 一致"
            elif len(_ok_list) == 2:
                _ok_tail = f"其余 {_ok_list[0]} 和 {_ok_list[1]} 一致"
            elif len(_ok_list) == 3:
                _ok_tail = f"其余 {_ok_list[0]}、{_ok_list[1]} 和 {_ok_list[2]} 一致"
            else:  # >=4：列前 3 个 + "等 N 项一致"（与 _notm "等 N 项" 口径一致，避免摘要过长）
                _ok_tail = f"{_ok_list[0]}、{_ok_list[1]} 和 {_ok_list[2]} 等 {len(_ok_list)} 项一致"
            _body += "，" + _ok_tail
        return f"属性比对 {_total} 项，其中 {_body}"

    # judge 判定分布（用于翻转检测与「多数/一致」措辞）
    _vmap = {j: (v.get("verdict") or "—") for j, v in judges_with.items()}
    _mj = Counter(_vmap.values()).most_common(1)[0][0] if _vmap else "待确认"
    _flipped = (final_verdict != _mj)

    def _match_attr_parts():
        """返回 (匹配子句 or None, 属性子句 or None, 补充句 or None)。

        匹配子句与判定句用 `，` 连接；属性子句、补充句各另起一句（`。`）。
        匹配子句内多个 judge 用 `、` 并列；补充句动态用**实际未匹配的 judge 名**。
        """
        _mh_fields = {d["field"] for d in (field_disagreements or [])}
        if _mh_fields & {"matched_hlr", "name_match_status"}:
            _matched = [j for j in judges_with
                        if (judges_with[j].get("matched_hlr")
                            or judges_with[j].get("name_match_status"))]
            _unmatched = [j for j in judges_with
                          if not (judges_with[j].get("matched_hlr")
                                  or judges_with[j].get("name_match_status"))]
            if _matched:
                # 措施①：先按匹配描述（HLR + 接口地址）归并——同描述的 judge 合并点名，
                # 消除「judge_A 匹配到 X、judge_B 匹配到 X」的完全冗余。
                # 地址不同（R12[iface1] vs R12[iface2]）视为不同描述、不合并，保住接口级分歧可见性。
                _groups: dict[str, list[str]] = {}
                for j in _matched:
                    _groups.setdefault(_match_desc(judges_with[j]), []).append(j)
                # 组内多 judge 用 `、`；组间（不同匹配结果）用 `；`，避免与组内 `、` 撞层。
                _mc = "；".join(f"{'、'.join(_js)} 匹配到 {_desc}"
                               for _desc, _js in _groups.items())
                _suf = (f"另有 {'、'.join(_unmatched)} 未匹配到 HLR，匹配关系存疑"
                        if _unmatched else None)
                return _mc, _build_attr_seg(), _suf
            return None, None, "且各 judge 均未匹配到 HLR，无属性可比"
        return None, _build_attr_seg(), None

    def _assemble(_head, _match, _attr, _suffix):
        """把判定句 + 匹配子句（，）+ 属性子句（。）+ 补充句（。）拼成一段话。"""
        _s = _head
        if _match:
            _s += "，" + _match
        _s += "。"
        if _attr:
            _s += _attr + "。"
        if _suffix:
            _s += _suffix + "。"
        return _s

    if _flipped:
        # 仲裁推翻了 judge 多数判定：仅以「仲裁判定」为结论，前缀已声明这是经裁决的最终结论。
        _head = f"仲裁判定「{final_verdict}」"
        if final_verdict == "未落实" and sub_classification:
            _head += f"（{sub_classification}）"
        _m, _a, _sfx = _match_attr_parts()
        return _assemble(_head, _m, _a, _sfx)

    # 非翻转
    if divergence:
        _agree = [j for j, v in _vmap.items() if v == final_verdict]
        _dissent = sorted(j for j, v in _vmap.items() if v != final_verdict)
        if _dissent and len(_agree) < len(_dissent):
            # 全分叉（无多数）：点名**每个** judge 的实际判定（不重复「判定」二字）
            _dlist = "、".join(f"{j}「{_vmap[j]}」" for j in sorted(_vmap))
            _head = f"各 judge 判定不一：{_dlist}"
        elif not _dissent:
            # verdict 三方一致、但 divergence=True ⇒ 分歧在身份层或属性层。
            if final_verdict == "未落实":
                # 「未落实」含两个二级子类（已识别未承接 / 未识别）。若一律写「一致判定」，
                # 会掩盖"另有一方判为未识别"（如 A/B 识别到 HLR、C 未识别），并与聚合层
                # agreement（majority/split）及据此得出的星级自相矛盾。
                # ⇒ 按**识别状态**（各 judge 是否识别到 HLR）分派：
                #     识别状态一致（全识别到 / 全未识别） → 一致判定「未落实」（子类）
                #     有分歧且有多数派                     → 多数判定「未落实」（子类）（N/M）
                #     平票（无多数派）                     → 各 judge 判定不一
                #   N = 识别状态多数派的 judge 数，M = 总 judge 数（n_judges）。
                #   平票只在「有效 judge 数为偶数」时出现（3-judge 全有效时 2:1/1:2，恒有多数派）；
                #   平票没有多数派可言，故不称「多数判定」，与 verdict 全分叉分支同措辞；
                #   此情形也不带子类括号（子类在平票下取「已识别未承接」是任意约定的兜底，
                #   并非多数结论，写进「判定」会误导）。
                #   非平票时括号内子类用 sub_classification（信号级结论，与报告「子分类」列/
                #   两个二级小节一致）。其它判定无二级子类 ⇒ 保持原措辞。
                _valid = [v for v in judges_with.values() if v.get("coverage") == "ok"]
                if not _valid:
                    _valid = list(judges_with.values())
                _n_ident = sum(1 for v in _valid
                               if (v.get("matched_hlr") or v.get("name_match_status")))
                _n_not = len(_valid) - _n_ident
                if _n_ident and _n_not:                     # 识别状态有分歧
                    if _n_ident == _n_not:                  # 平票 ⇒ 无多数派
                        _head = "各 judge 判定不一"
                    else:
                        _frac = f"（{max(_n_ident, _n_not)}/{n_judges}）"
                        _head = (f"多数判定「{final_verdict}」（{sub_classification}）{_frac}"
                                 if sub_classification else f"多数判定「{final_verdict}」{_frac}")
                else:                                       # 识别状态一致
                    _head = f"一致判定「{final_verdict}」"
                    if sub_classification:
                        _head += f"（{sub_classification}）"
            else:
                _head = f"一致判定「{final_verdict}」"
        else:
            # 多数判定：分数后置（与改造前一致，不做格式调整）
            _head = f"多数判定「{final_verdict}」（{len(_agree)}/{n_judges}）"
            if final_verdict == "未落实" and sub_classification:
                _head += f"（{sub_classification}）"
            # 少数派：动态列出**实际持异议的 judge 名**（不硬编码任何 judge 名）
            _groups = {}
            for j in _dissent:
                _groups.setdefault(_vmap[j], []).append(j)
            _gparts = "、".join(f"{'、'.join(_js)} 判定「{_v}」" for _v, _js in _groups.items())
            _head += "，另有 " + _gparts
        _m, _a, _sfx = _match_attr_parts()
        return _assemble(_head, _m, _a, _sfx)
    else:
        _head = f"一致判定「{final_verdict}」"
        if final_verdict == "未落实" and sub_classification:
            _head += f"（{sub_classification}）"
        _matched = [j for j in judges_with
                    if (judges_with[j].get("matched_hlr")
                        or judges_with[j].get("name_match_status"))]
        _match = f"匹配到 {_match_desc(judges_with[_matched[0]])}" if _matched else None
        return _assemble(_head, _match, _build_attr_seg(), None)


def aggregate(shards: dict[str, dict], judge_meta: dict, rule: dict | None = None,
              n_configured: int | None = None,
              judge_errors: dict[str, str] | None = None) -> dict:
    """聚合所有 judge 分片。

    shards:   {judge_name: {signal_full_name: entry}}
    judge_meta: {judge_name: {"model","base_url","key_hash","frozen"}}
    n_configured: 配置的总 judge 数（用于区分「单 judge 正常」与「多 judge 配置下只剩 1 存活降级」）。
                  未传则按实际分片数推断（向后兼容）。
    judge_errors: {judge_name: 失败原因}，覆盖缺口时在 note 里带上「为什么这个 judge 没结论」，
                  供下游 AI 复核者判断要不要补跑（超时/异常/未找到输出各有不同处置）。
    返回 {signal_full_name: aggregated_entry}
    """
    rule = rule or {}
    need_review_on_split = rule.get("need_review_on_split", True)
    judge_errors = judge_errors or {}

    all_signals: set[str] = set()
    for shard in shards.values():
        all_signals.update(shard.keys())

    judge_names = list(shards.keys())
    n_judges = len(judge_names)
    if n_configured is None:
        n_configured = n_judges

    result: dict[str, dict] = {}
    for full in sorted(all_signals):
        judges_with = {j: shards[j][full] for j in judge_names if full in shards[j]}
        for j, e in judges_with.items():
            e["_judge"] = j

        coverage_gap = [j for j in judge_names
                        if j not in judges_with or judges_with[j].get("coverage") != "ok"]
        # #8 路由化①：缺口原因下传。只报「哪些 judge 没结论」不够——下游 AI 复核者要判断
        # 该不该补跑，得知道是硬超时终止、子进程异常还是压根没产出该信号，三者处置不同。
        gap_detail: list[str] = []
        for j in coverage_gap:
            reason = judge_errors.get(j)
            if not reason:
                reason = ("无结论（未产出该信号）" if j not in judges_with
                          else "分片异常（coverage={0}）".format(judges_with[j].get("coverage")))
            gap_detail.append("{0}({1})".format(j, reason))
        valid = {j: e for j, e in judges_with.items() if e.get("coverage") == "ok"}
        n_valid = len(valid)

        # 两维共识
        identity_keys = [_identity_key(e) for e in valid.values()]
        id_counter = Counter(identity_keys)
        majority_id, majority_count = (id_counter.most_common(1)[0] if id_counter else ((None, 0), 0))
        identity_unanimous = len(id_counter) <= 1

        # 属性共识只统计「匹配到 HLR」的 judge：未匹配 HLR 的 judge 没有 HLR 侧取值可比对，
        # 其属性结论多为「无提及」；若混入会与匹配 judge 的真实结论（如「属性一致」）被 253 行的
        # set 判定误算成「分歧」，产生「各judge结论分歧」的伪分歧。EoICD↔HLR 属性比对本就要求有 HLR 可比。
        # 多 HLR 修复：先按 (judge, attribute_class, req_id) 三键展开（_attr_consensus_per_hlr，唯一真相源），
        # 再派生旧 4-key 形态供下游兼容（_attr_consensus_per_class 薄封装）。同一组 entries 跑两遍成本可忽略。
        attr_hlr = _attr_consensus_per_hlr([e for e in valid.values() if e.get("matched_hlr")])
        attr_by_class = _attr_consensus_per_class([e for e in valid.values() if e.get("matched_hlr")])
        attr_classes = list(attr_by_class.keys())
        attr_unanimous = all(v["consensus"] != "分歧" for v in attr_by_class.values())

        # 信号方向（发送/接收）：供报告「属性比对范围」按方向拆分。无匹配 HLR 的信号方向为空。
        signal_direction = _signal_direction(valid)

        # agreement_level 文案
        if n_valid == 0:
            agreement = "no_consensus"
        elif n_valid == 1:
            agreement = "single_source"
        else:
            top = id_counter.most_common()
            top_count = top[0][1]
            tied = [k for k, c in top if c == top_count]
            if len(tied) > 1:
                agreement = "split"
            elif identity_unanimous and attr_unanimous:
                agreement = "full"
            else:
                agreement = "majority"

        # 身份共识文案（取多数；仅当最高票并列才标「分歧」）
        matched_hlr_consensus = ""
        if n_valid == 0:
            identity_consensus = "覆盖缺口/未复核"
        else:
            top = id_counter.most_common()
            top_count = top[0][1]
            tied = [k for k, c in top if c == top_count]
            # 去重后的各派匹配集合（含接口）：#5 可见性修复的核心数据。
            # 同一 req_id 若同时存在「带接口」与「空接口」两种表示，仅保留带接口版（空接口为冗余，
            # 其编号信息已被带接口版包含），避免把同一 HLR 渲染成两条。
            # 不同 req_id、或同一 req_id 的「不同非空接口」(#5 真实接口级分歧) 仍都保留。
            seen: list[str] = _collapse_matched([k[1] for k in id_counter.keys()])
            if len(tied) > 1:
                identity_consensus = ("分歧：" + " / ".join(seen)) if seen else "分歧"
            else:
                vk, hl, nm, addr = top[0][0]
                parts = [vk]
                if hl:
                    # 共识串自带接口地址，避免 CSV/复核视图丢掉接口级差异；
                    # 同一 req_id 优先带接口版、丢弃空接口冗余。
                    parts.append(",".join(_collapse_matched([hl])))
                if nm:
                    # #7 修复：未落实信号即便 name 命中 HLR，也不渲染为「已确认身份」(name✓)，
                    # 以免把「找到映射目标但未落实」误判为身份共识。
                    parts.append("name(未承接)" if vk == "未落实" else "name✓")
                identity_consensus = "→".join(parts)
            # 机器可读的匹配集合，供 Word/Excel "匹配 HLR" 列直接消费。
            # 多 HLR 修复·显示层口径：用户视角只关心 req_id（HLR 主键），接口描述
            # 已在分析摘要的 `_match_desc`（`R12（a429 / l11 / ...）`）展示，此处不重复。
            # 分隔符 `/` 替代旧的 `⟷`（数学等价符号、不该做列表分隔符；技术文档惯例）。
            seen_ids = _collapse_matched_ids([k[1] for k in id_counter.keys()])
            matched_hlr_consensus = " / ".join(seen_ids)

        # 属性共识文案（整体）
        if n_valid == 0:
            attr_consensus = "覆盖缺口/未复核"
        elif not attr_classes:
            attr_consensus = "无属性"
        elif any(v["consensus"] == "分歧" for v in attr_by_class.values()):
            attr_consensus = "分歧"
        elif all(v["consensus"] == "属性一致" for v in attr_by_class.values()):
            attr_consensus = "一致"
        else:
            attr_consensus = "部分落实"

        # 结构化 diff（field-level）
        structured_diff = []
        for cls, info in attr_by_class.items():
            structured_diff.append({
                "attribute_class": cls,
                "eoicd_value": info["eoicd_value"],
                "hlr_value": info["hlr_value"],
                "diff_type": info["consensus"],
            })

        # 事实不一致（inconsistent_attributes）：某属性被任一 (judge, HLR) 对判为不一致/属性不一致。
        # 多 HLR 修复：每条带 `against_req_id`（驱动该不一致的最差 HLR），明细表追溯用，不进展示。
        inconsistent_attributes = []
        for cls, slot in attr_hlr.items():
            # 跨 (judge, HLR) 全集筛出标不一致的 (judge, HLR) 对
            hit_pairs: list[tuple[str, str]] = []
            for req, per_j in slot["per_hlr"].items():
                for j, r in per_j.items():
                    if r in _INCONSISTENT_RESULTS:
                        hit_pairs.append((j, req))
            if not hit_pairs:
                continue
            inconsistent_judges = sorted({j for j, _ in hit_pairs})
            # against_req_id：驱动该不一致的最差 HLR（per_cls 已有 worst_req 直接复用）
            inconsistent_attributes.append({
                "attribute_class": cls,
                "judges": inconsistent_judges,
                "against_req_id": slot.get("worst_req"),
                "eoicd_value": slot["worst_eoicd"],
                "hlr_value": slot["worst_hlr"],
                "results": {j: slot["by_judge"][j] for j in inconsistent_judges},
            })

        # judge 间分歧（field_disagreements，由 KEY_FIELDS_IDENTITY 单一来源驱动，改一处全局生效）
        field_disagreements = []
        has_key_disagreement = False
        if n_valid > 1:
            # 身份层 KEY_FIELDS：逐字段比对各 valid judge 是否一致，不一致即 key 分歧。
            # 不同字段的取值/归一方式不同（matched_hlr 走 _norm_matched_hlr、地址四元组走
            # _norm_addr、name_match_status 按 bool 比较），由 KEY_FIELDS_IDENTITY 统一驱动
            # 遍历顺序与字段集——增删 key 字段只需改该常量，无需再动此处硬编码。
            for field in KEY_FIELDS_IDENTITY:
                if field == "matched_hlr":
                    comp = {j: _norm_matched_hlr(valid[j].get("matched_hlr")) for j in valid}
                    raw = {j: valid[j].get("matched_hlr") for j in valid}
                elif field == "name_match_status":
                    nms = {j: bool(valid[j].get("name_match_status")) for j in valid}
                    comp = nms
                    raw = nms
                elif field in ADDRESS_KEYS:
                    comp = {j: _norm_addr(valid[j].get("hlr_identity")).get(field) for j in valid}
                    raw = {j: valid[j].get("hlr_identity") for j in valid}
                else:  # verdict（及其它直接取值字段）
                    comp = {j: valid[j].get(field) for j in valid}
                    raw = comp
                if len(set(comp.values())) > 1:
                    field_disagreements.append({"field": field, "category": "key", "values": raw})
                    has_key_disagreement = True
            # 属性层（动态 attribute_class，不在 KEY_FIELDS_IDENTITY 内，单独处理）
            # 多 HLR 修复：使用 attr_hlr 派生 by_judge_per_hlr，检测同一 judge 内不同 HLR 结论是否一致——
            # 不一致则标 `cross_hlr: True`（原本被静默吞的跨 HLR 真分歧）。在 `__main__` 单测验证。
            for cls, slot in attr_hlr.items():
                if slot["consensus"] == "分歧":
                    # 任一 judge 跨 HLR 结论不一致即标 cross_hlr
                    cross_hlr = any(
                        len(set(per_req.values())) > 1
                        for per_req in slot["by_judge_per_hlr"].values()
                    )
                    field_disagreements.append({
                        "field": f"attr:{cls}", "category": "key",
                        "cross_hlr": cross_hlr,
                        "values": {j: slot["by_judge"][j] for j in slot["by_judge"]},
                    })
                    has_key_disagreement = True

        # 星级
        star = _star_rating(agreement, has_key_disagreement, n_valid, n_configured)

        # agreement_level（按全部 judge 计，gap 不算同意）
        majority_count = id_counter.most_common(1)[0][1] if id_counter else 0
        agreement_level = f"{majority_count}/{n_judges}"

        # vote 统计（identity 维度）
        # #5：计数键带接口地址，否则仅接口不同的两派会被折叠成同一个键、计数虚高。
        vote = {("→".join([vk] + ([",".join(_collapse_matched([hl]))] if hl else []) + (["name✓"] if nm else []))): c
                for (vk, hl, nm, addr), c in id_counter.items()}

        # #3 路由判定：与反向 `needs_ai` 对等，条件集中在 NEED_REVIEW_RULES 规则表里，
        # 便于审计与逐条关闭。注意单 judge 配置（n_configured==1）下 single_source 是
        # 正常路径，不强制需复核（退化兼容），这条约束写在规则内部而非散落在主体。
        need_review, nr_hits = needs_review(
            {"n_valid": n_valid, "star": star, "n_configured": n_configured,
             "need_review_on_split": need_review_on_split},
            rule.get("need_review_rules"),
        )
        top = id_counter.most_common()
        if not top:
            final_verdict = "待确认"
        else:
            # 取 identity 计票最高票那派的 verdict；**平票时按信号级严重度取最严重者**。
            # 修复：原先直接取 top[0]，而 Counter.most_common() 在并列时按**插入序**返回
            # ⇒ 平票取值依赖 judge 遍历顺序、不可复现。现按 _VERDICT_SEVERITY（单一来源，
            # 与 _WORST_PRIORITY / VERDICT_GROUPS / classify_signal 同一顺序）裁定，
            # 保守取向：平票取更严重者（与"该信号需要复核"的路由取向一致）。
            _best = top[0][1]
            _tied = [k[0] for k, c in top if c == _best]         # identity_key[0] == verdict
            _known = [v for v in _tied if v in _VERDICT_SEVERITY]
            final_verdict = (min(_known, key=_VERDICT_SEVERITY.index)
                             if _known else _tied[0])
        if n_valid == 0:
            final_verdict = "待确认"

        # #12 未落实二级子类（单一来源 = derive_sub_classification）：按各有效 judge 的
        # **识别状态取多数派**，细则见该函数 docstring（2026-10-16 由 any 口径改为多数派，
        # 与聚合层其余字段 verdict/agreement/星级的表决口径一致）。
        # 仅 final_verdict==未落实 时赋值，其余为 None（JSON 字段与单 judge 同构）。
        sub_classification = derive_sub_classification(final_verdict, valid)

        divergence = (n_valid > 1) and (not identity_unanimous or not attr_unanimous)

        # note
        parts = []
        if n_valid:
            # identity_consensus 自身已含「分歧：…」时不加「一致」，避免 note 重复啰嗦
            if identity_consensus.startswith("分歧"):
                parts.append(f"{majority_count}/{n_judges}：{identity_consensus}")
            else:
                parts.append(f"{majority_count}/{n_judges} 一致：{identity_consensus}")
        else:
            parts.append("所有 judge 均无有效结论")
        if coverage_gap:
            parts.append("覆盖缺口：" + ", ".join(gap_detail))
        if inconsistent_attributes:
            parts.append(f"事实不一致属性 {len(inconsistent_attributes)} 项")
        if field_disagreements:
            parts.append(f"judge 间分歧 {len(field_disagreements)} 项")
        # #11 去歧义：单源时裸「1/1」看不出是「只配了 1 个」还是「配了 N 个只活了 1 个」。
        # 单 judge 配置（正常路径）不加这句，避免噪音。
        if n_valid == 1 and n_configured > 1:
            parts.append(f"仅 1 个 judge 有有效结论（配置 {n_configured} 个）")
        # #3 路由痕迹：命中哪条规则才需复核，报告/复核者可据此判断要不要补跑
        if need_review and nr_hits:
            parts.append("路由原因：" + "；".join(h["detail"] for h in nr_hits))
        note = "；".join(parts)

        # 分析摘要（对齐反向「分析摘要」列，方案A，不调 LLM、可审计）：
        #  - 一致（无分歧）：保留代表 judge 的业务结论原文（analysis，带数值），最可读。
        #  - 分歧：合成一句分析结论（共识判定 + 少数派判定说明 + 匹配关系(编号+规范地址) +
        #           属性比对结论 + 处置建议），纯规则、可审计；不再附「冲突字段」明细
        #           （属性分歧已在前文以「XX 各judge结论分歧」点明，再列即重复）。
        analysis_summary = build_analysis_summary(
            final_verdict=final_verdict,
            judges_with=judges_with,
            attr_by_class=attr_by_class,
            inconsistent_attributes=inconsistent_attributes,
            field_disagreements=field_disagreements,
            divergence=divergence,
            n_judges=n_judges,
        )

        result[full] = {
            "eoicd_attrs": {},  # 由 runner 从阶段产物补全（阶段2 增强）
            "judges": {
                j: {
                    "direction": judges_with[j].get("direction"),
                    "matched_hlr": judges_with[j].get("matched_hlr"),
                    "verdict": judges_with[j].get("verdict"),
                    "name_match_status": judges_with[j].get("name_match_status"),
                    "hlr_identity": judges_with[j].get("hlr_identity"),
                    "attr_summary": judges_with[j].get("attr_summary"),
                    "analysis": judges_with[j].get("analysis"),
                    "confidence": judges_with[j].get("confidence"),
                    "rationale": judges_with[j].get("rationale"),
                    "coverage": judges_with[j].get("coverage"),
                }
                for j in judges_with
            },
            "aggregation": {
                "identity_consensus": identity_consensus,
                "matched_hlr_consensus": matched_hlr_consensus,
                "attr_consensus": attr_consensus,
                "structured_diff": structured_diff,
                "vote": vote,
                "agreement_level": agreement_level,
                "agreement": agreement,
                "consensus_label": _consensus_label(star, agreement),
                "star_rating": star,
                "divergence": divergence,
                "coverage_gap": coverage_gap,
                "inconsistent_attributes": inconsistent_attributes,
                "field_disagreements": field_disagreements,
                "final_verdict": final_verdict,
                "sub_classification": sub_classification,  # #12 未落实二级子类（对齐单 judge）
                "analysis_summary": analysis_summary,  # 对齐反向「分析摘要」列（用户向业务结论）
                "need_review": need_review,
                # #3 路由审计：命中的规则（供报告/复核者判断复核是否必要、能否被单条关掉）
                "need_review_rules": [h["name"] for h in nr_hits],
                "need_review_details": [h["detail"] for h in nr_hits],
                # #11 单源去歧义：有效结论源数 vs 配置数，避免「1/1」两种含义混为一谈
                "n_valid_sources": n_valid,
                "n_judges_participating": n_judges,
                "n_judges_configured": n_configured,
                "attr_consensus_detail": attr_by_class,
                "signal_direction": signal_direction,  # 发送/接收（仅匹配到 HLR 的信号有值），供报告拆分
                "note": note,
            },
            "judge_meta": judge_meta,
        }

    return result


# ---------------------------------------------------------------------------
# 3) 输出：JSON + CSV
# ---------------------------------------------------------------------------

def write_outputs(agg: dict, out_json: Path, out_csv: Path | None = None) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(agg, f, ensure_ascii=False, indent=2)
    print(f"  [聚合] 已写出 JSON: {out_json}（{len(agg)} 个信号）")

    if out_csv is not None:
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(out_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "signal_full_name", "matched_hlr", "identity_consensus", "attr_consensus",
                "agreement_level", "agreement", "consensus_label", "star_rating", "divergence",
                "coverage_gap", "n_judges_ok", "n_judges_gap", "need_review(AI复核路由)", "note",
            ])
            for full, item in agg.items():
                a = item["aggregation"]
                n_ok = sum(1 for jv in item["judges"].values() if jv.get("coverage") == "ok")
                n_gap = len(a["coverage_gap"])
                w.writerow([
                    full, a.get("matched_hlr_consensus"), a["identity_consensus"], a["attr_consensus"],
                    a["agreement_level"], a["agreement"], a.get("consensus_label"), a["star_rating"],
                    a["divergence"], ",".join(a["coverage_gap"]),
                    n_ok, n_gap, ("需AI复核" if a["need_review"] else "否"), a["note"],
                ])
        print(f"  [聚合] 已写出 CSV : {out_csv}")


# ---------------------------------------------------------------------------
# 4) 离线自测
# ---------------------------------------------------------------------------
# 自测已迁出到 tests/test_aggregate.py（生产文件瘦身、便于 pytest 化）。
# 旧 CLI 兼容：python multi_judge_aggregate.py 仍可触发，转调 tests/ 入口。
if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path
    _root = _Path(__file__).resolve().parent.parent
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    from tests.test_aggregate import run_all as _run_self_test
    _run_self_test()
