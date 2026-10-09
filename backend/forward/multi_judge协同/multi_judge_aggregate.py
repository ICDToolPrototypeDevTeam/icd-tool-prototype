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


def _display_matched(value) -> str:
    """把 matched_hlr 富结构压成展示串（只取 req_id，接口仅用于比对不展示）。"""
    return ",".join(r for r, _ in _norm_matched_hlr(value))


def _display_matched_full(value) -> str:
    """把 matched_hlr 富结构压成**带接口地址**的展示串：R12[1|0x10|8|pub],R99[2|0x33|2|sub]。

    #5 可见性修复：只在 CSV 侧显示 req_id 的话，两个 judge「同名 req 不同接口」的结论
    在 CSV 里会长得一模一样，复核人无法察觉接口级分歧，只能去翻 JSON。
    接口为空（旧 report 未提供接口信息）时退化为纯 req_id，保持向后兼容。
    """
    parts = []
    for req, iface in _norm_matched_hlr(value):
        parts.append(f"{req}[{iface}]" if iface else req)
    return ",".join(parts)


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


def _attr_consensus_per_class(valid_entries: list[dict]) -> dict:
    """对每个 attribute_class，收集各 judge 的 single_result 与代表值，给出共识与是否分歧。

    返回 {attribute_class: {"results":{judge:single_result}, "eoicd_value":..,
                            "hlr_value":.., "consensus": <值或"分歧">}}。
    eoicd_value/hlr_value 取首个有效 judge 的值作为代表（共识层展示用）。
    """
    by_class: dict[str, dict] = {}
    for e in valid_entries:
        for a in e.get("attributes", []):
            cls = a.get("attribute_class")
            if not cls:
                continue
            slot = by_class.setdefault(cls, {"results": {}, "eoicd_value": None, "hlr_value": None})
            j = e.get("_judge")
            slot["results"][j] = a.get("single_result")
            if slot["eoicd_value"] is None:
                slot["eoicd_value"] = a.get("eoicd_value")
            if slot["hlr_value"] is None:
                slot["hlr_value"] = a.get("hlr_value")
    out = {}
    for cls, info in by_class.items():
        values = list(info["results"].values())
        consensus = values[0] if len(set(values)) == 1 else "分歧"
        out[cls] = {
            "results": info["results"],
            "eoicd_value": info["eoicd_value"],
            "hlr_value": info["hlr_value"],
            "consensus": consensus,
        }
    return out


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

        attr_by_class = _attr_consensus_per_class(list(valid.values()))
        attr_classes = list(attr_by_class.keys())
        attr_unanimous = all(v["consensus"] != "分歧" for v in attr_by_class.values())

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
            # 并列时若只报「分歧」，「同名 req 不同接口」这类最常见的 #5 分歧在 CSV 里
            # 就完全看不到差异所在，只能去翻 JSON。
            seen: list[str] = []
            for k, _c in sorted(id_counter.items(), key=lambda kv: -kv[1]):
                disp = _display_matched_full(k[1])
                if disp and disp not in seen:
                    seen.append(disp)
            if len(tied) > 1:
                identity_consensus = ("分歧：" + " ⟷ ".join(seen)) if seen else "分歧"
            else:
                vk, hl, nm, addr = top[0][0]
                parts = [vk]
                if hl:
                    # 共识串自带接口地址，避免 CSV/复核视图丢掉接口级差异
                    parts.append(_display_matched_full(hl))
                if nm:
                    # #7 修复：未落实信号即便 name 命中 HLR，也不渲染为「已确认身份」(name✓)，
                    # 以免把「找到映射目标但未落实」误判为身份共识。
                    parts.append("name(未承接)" if vk == "未落实" else "name✓")
                identity_consensus = "→".join(parts)
            # 机器可读的匹配集合（含接口），供 CSV 列 / 筛选使用；并列时列出全部派别
            matched_hlr_consensus = " ⟷ ".join(seen)

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

        # 事实不一致（inconsistent_attributes）：某属性被任一 judge 判为不一致/属性不一致
        inconsistent_attributes = []
        for cls, info in attr_by_class.items():
            inconsistent_judges = [j for j, r in info["results"].items()
                                  if r in _INCONSISTENT_RESULTS]
            if inconsistent_judges:
                inconsistent_attributes.append({
                    "attribute_class": cls,
                    "judges": inconsistent_judges,
                    "eoicd_value": info["eoicd_value"],
                    "hlr_value": info["hlr_value"],
                    "results": {j: info["results"][j] for j in inconsistent_judges},
                })

        # judge 间分歧（field_disagreements，仅在 KEY_FIELDS 上算）
        field_disagreements = []
        has_key_disagreement = False
        if n_valid > 1:
            # verdict
            verdicts = {j: valid[j].get("verdict") for j in valid}
            if len(set(verdicts.values())) > 1:
                field_disagreements.append({"field": "verdict", "category": "key",
                                            "values": {j: verdicts[j] for j in verdicts}})
                has_key_disagreement = True
            # matched_hlr（富结构：req_id+接口，#5 修复——同一 req_id 不同接口亦判为分歧）
            hlrs = {j: _norm_matched_hlr(valid[j].get("matched_hlr")) for j in valid}
            if len(set(hlrs.values())) > 1:
                field_disagreements.append({"field": "matched_hlr", "category": "key",
                                            "values": {j: valid[j].get("matched_hlr") for j in valid}})
                has_key_disagreement = True
            # name_match_status
            nms = {j: bool(valid[j].get("name_match_status")) for j in valid}
            if len(set(nms.values())) > 1:
                field_disagreements.append({"field": "name_match_status", "category": "key",
                                            "values": {j: nms[j] for j in nms}})
                has_key_disagreement = True
            # 地址四元组 bus/label/bit/direction
            for ak in ADDRESS_KEYS:
                comp = {j: _norm_addr(valid[j].get("hlr_identity")).get(ak) for j in valid}
                if len(set(comp.values())) > 1:
                    field_disagreements.append({"field": ak, "category": "key",
                                                "values": {j: valid[j].get("hlr_identity") for j in valid}})
                    has_key_disagreement = True
            # 属性层 KEY_FIELDS：逐 attribute_class 结论分歧
            for cls, info in attr_by_class.items():
                if info["consensus"] == "分歧":
                    field_disagreements.append({"field": f"attr:{cls}", "category": "key",
                                                "values": {j: info["results"][j] for j in info["results"]}})
                    has_key_disagreement = True

        # 星级
        star = _star_rating(agreement, has_key_disagreement, n_valid, n_configured)

        # agreement_level（按全部 judge 计，gap 不算同意）
        majority_count = id_counter.most_common(1)[0][1] if id_counter else 0
        agreement_level = f"{majority_count}/{n_judges}"

        # vote 统计（identity 维度）
        # #5：计数键带接口地址，否则仅接口不同的两派会被折叠成同一个键、计数虚高。
        vote = {("→".join([vk] + ([_display_matched_full(hl)] if hl else []) + (["name✓"] if nm else []))): c
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
        final_verdict = top[0][0][0] if top else "待确认"
        if n_valid == 0:
            final_verdict = "待确认"

        # #12 未落实二级子类（对齐单 judge `summary.py:543-544` 的 sub_classification）：
        # consensus verdict 为「未落实」时，按「信号是否识别到 HLR」细分根因——
        #   已识别未承接：任一有效 judge 命中 HLR（matched_hlr / name_match_status 非空）
        #   未识别：      所有有效 judge 均未匹配到 HLR
        # 仅 final_verdict==未落实 时赋值，其余为 None（JSON 字段与单 judge 同构）。
        sub_classification = None
        if final_verdict == "未落实":
            _identified = bool(n_valid) and any(
                (valid[j].get("matched_hlr") or valid[j].get("name_match_status"))
                for j in valid
            )
            sub_classification = "已识别未承接" if _identified else "未识别"

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

        # 分析摘要（对齐反向「分析摘要」列）：取多数方 judge 的业务结论原文（analysis，带数值）。
        # 优先 coverage=ok 的 judge；verdict 与 final_verdict 一致者优先；judge 间分歧时前缀标注。
        _rep_pool = [j for j in (valid if valid else judges_with) if j in judges_with]
        _rep_judge = None
        if _rep_pool:
            _matched = [j for j in _rep_pool if judges_with[j].get("verdict") == final_verdict]
            _rep_judge = (_matched or _rep_pool)[0]
        analysis_summary = ""
        if _rep_judge is not None:
            analysis_summary = (judges_with[_rep_judge].get("analysis") or "").strip() \
                              or (judges_with[_rep_judge].get("attr_summary") or "").strip()
            if divergence and analysis_summary:
                analysis_summary = "（judge 结论不一致）" + analysis_summary

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

def _self_test() -> None:
    """用合成分片验证聚合逻辑（不依赖任何真实 LLM / 数据）。"""
    shards = {
        "judge_ds": {
            "SIG_A": {"matched_hlr": ["R12"], "verdict": "已落实", "name_match_status": True,
                      "hlr_identity": {"bus": "1", "label": "0x10", "bit": "8-9", "direction": "pub", "name": "SDI"},
                      "coverage": "ok",
                      "attributes": [{"attribute_class": "周期", "single_result": "属性一致",
                                     "matched_hlr_req_id": "R12", "eoicd_value": "100ms", "hlr_value": "100ms"}]},
            "SIG_B": {"matched_hlr": ["R13"], "verdict": "不一致", "name_match_status": True,
                      "hlr_identity": {"bus": "1", "label": "0x11", "bit": "2", "direction": "pub", "name": "X1"},
                      "coverage": "ok",
                      "attributes": [{"attribute_class": "周期", "single_result": "属性不一致",
                                     "matched_hlr_req_id": "R13", "eoicd_value": "100ms", "hlr_value": "200ms"}]},
            "SIG_C": {"matched_hlr": [], "verdict": "未落实", "name_match_status": False,
                      "hlr_identity": None, "coverage": "ok", "attributes": []},
        },
        "judge_qwen": {
            "SIG_A": {"matched_hlr": ["R12"], "verdict": "已落实", "name_match_status": True,
                      "hlr_identity": {"bus": "1", "label": "0x10", "bit": "8-9", "direction": "pub", "name": "SDI"},
                      "coverage": "ok",
                      "attributes": [{"attribute_class": "周期", "single_result": "属性一致",
                                     "matched_hlr_req_id": "R12", "eoicd_value": "100ms", "hlr_value": "100ms"}]},
            "SIG_B": {"matched_hlr": ["R13"], "verdict": "不一致", "name_match_status": True,
                      "hlr_identity": {"bus": "1", "label": "0x11", "bit": "2", "direction": "pub", "name": "X1"},
                      "coverage": "ok",
                      "attributes": [{"attribute_class": "周期", "single_result": "属性不一致",
                                     "matched_hlr_req_id": "R13", "eoicd_value": "100ms", "hlr_value": "200ms"}]},
            "SIG_C": {"matched_hlr": [], "verdict": "未落实", "name_match_status": False,
                      "hlr_identity": None, "coverage": "ok", "attributes": []},
        },
        "judge_same": {
            "SIG_A": {"matched_hlr": ["R12"], "verdict": "已落实", "name_match_status": True,
                      "hlr_identity": {"bus": "1", "label": "0x10", "bit": "8-9", "direction": "pub", "name": "SDI"},
                      "coverage": "ok",
                      "attributes": [{"attribute_class": "周期", "single_result": "属性一致",
                                     "matched_hlr_req_id": "R12", "eoicd_value": "100ms", "hlr_value": "100ms"}]},
            "SIG_B": {"matched_hlr": ["R99"], "verdict": "不一致", "name_match_status": True,
                      "hlr_identity": {"bus": "1", "label": "0x99", "bit": "2", "direction": "pub", "name": "X9"},
                      "coverage": "ok",
                      "attributes": [{"attribute_class": "周期", "single_result": "属性不一致",
                                     "matched_hlr_req_id": "R99", "eoicd_value": "100ms", "hlr_value": "999ms"}]},
            # SIG_C 缺失 → 覆盖缺口
        },
    }
    meta = {j: {"model": "x", "base_url": "x", "key_hash": "x", "frozen": True} for j in shards}
    agg = aggregate(shards, meta)

    a = agg["SIG_A"]["aggregation"]
    assert a["identity_consensus"] == "已落实→R12→name✓", a["identity_consensus"]
    assert a["agreement_level"] == "3/3"
    assert a["agreement"] == "full"
    assert a["star_rating"] == 5
    assert a["divergence"] is False
    assert a["need_review"] is False
    assert len(a["structured_diff"]) == 1

    b = agg["SIG_B"]["aggregation"]
    assert b["identity_consensus"] == "不一致→R13→name✓", b["identity_consensus"]  # 2/3 多数（R13）
    assert b["agreement_level"] == "2/3"
    assert b["agreement"] == "majority"
    # judge_same 判 R99 vs 其他 R13 → 地址+matched_hlr 关键分歧 → 2★
    assert b["star_rating"] == 2, b["star_rating"]
    assert b["need_review"] is True
    # 事实不一致（属性确实不一致）应记录，但不算 judge 分歧（三 judge 都判不一致）
    assert len(b["inconsistent_attributes"]) == 1
    assert any(d["field"] == "matched_hlr" for d in b["field_disagreements"])
    assert any(d["field"] == "label" for d in b["field_disagreements"])
    assert b["divergence"] is True

    c = agg["SIG_C"]["aggregation"]
    assert c["coverage_gap"] == ["judge_same"], c["coverage_gap"]
    # 2 票有效且完全一致（未落实）→ agreement=full，覆盖缺口仅作标记不强制需复核
    assert c["identity_consensus"] == "未落实", c["identity_consensus"]
    assert c["agreement_level"] == "2/3"
    assert c["agreement"] == "full"
    assert c["need_review"] is False
    assert c["star_rating"] == 5, c["star_rating"]
    # #12 未落实二级子类：SIG_C 两票均 matched_hlr=空 & name_match_status=False → 未识别
    assert c["sub_classification"] == "未识别", c["sub_classification"]

    # --- #1 修复：单 judge 退化兼容 ---
    # n_configured==1 时 single_source 不应强制 need_review / 压星
    single_shards = {"only_one": shards["judge_ds"]}
    single_meta = {"only_one": meta["judge_ds"]}
    single_agg = aggregate(single_shards, single_meta, {}, n_configured=1)
    sa = single_agg["SIG_A"]["aggregation"]
    assert sa["agreement_level"] == "1/1"
    assert sa["agreement"] == "single_source"
    assert sa["star_rating"] == 5, sa["star_rating"]            # 单 judge 正常 → 5★
    assert sa["need_review"] is False, sa["need_review"]         # 不强制需复核
    sb = single_agg["SIG_B"]["aggregation"]
    assert sb["star_rating"] == 5 and sb["need_review"] is False
    assert len(sb["inconsistent_attributes"]) == 1               # 事实不一致仍记录（非 judge 分歧）
    sc = single_agg["SIG_C"]["aggregation"]
    assert sc["star_rating"] == 5 and sc["need_review"] is False

    # 多 judge 配置但仅 1 存活（降级）→ 应需复核（star=1）
    degraded = {"j1": shards["judge_ds"], "j2": {}}              # j2 全缺 → 仅 j1 存活
    deg_agg = aggregate(degraded, {"j1": meta["judge_ds"], "j2": meta["judge_ds"]},
                        {}, n_configured=2)
    da = deg_agg["SIG_A"]["aggregation"]
    assert da["star_rating"] == 1 and da["need_review"] is True, (da["star_rating"], da["need_review"])

    # --- #5 修复：matched_hlr 细到 req_id+interface，同一 req_id 不同接口应判为分歧 ---
    if5 = {
        "jp": {"SIG_X": {"matched_hlr": [["R12", "bus=1|label=0x10|bit=8|dir=pub"]],
                          "verdict": "已落实", "name_match_status": True,
                          "hlr_identity": {"bus": "1", "label": "0x10", "bit": "8", "direction": "pub", "name": "SDI"},
                          "coverage": "ok", "attributes": []}},
        "jq": {"SIG_X": {"matched_hlr": [["R12", "bus=1|label=0x11|bit=2|dir=pub"]],
                          "verdict": "已落实", "name_match_status": True,
                          "hlr_identity": {"bus": "1", "label": "0x11", "bit": "2", "direction": "pub", "name": "SDI"},
                          "coverage": "ok", "attributes": []}},
    }
    agg5 = aggregate(if5, {j: {"model": "x", "base_url": "x", "key_hash": "x", "frozen": True} for j in if5},
                     {}, n_configured=2)
    a5 = agg5["SIG_X"]["aggregation"]
    assert any(d["field"] == "matched_hlr" for d in a5["field_disagreements"]), \
        "同一 req_id 不同接口应触发 matched_hlr 分歧(#5)"
    # 两 judge 身份完全 tie（matched_hlr 接口 + 地址四元组均不同）→ split → 1★ 才正确；
    # 本用例核心断言是「同一 req_id 不同接口触发 matched_hlr 分歧」(上一行)，评星为 split=1★。
    assert a5["star_rating"] == 1 and a5["need_review"] is True, (a5["star_rating"], a5["need_review"])

    # --- #7 修复：未落实 + name 命中 不应渲染为已确认身份(name✓) ---
    if7 = {"j": {"SIG_Y": {"matched_hlr": [["R9", "bus=1|label=0x20|bit=3|dir=pub"]],
                           "verdict": "未落实", "name_match_status": True,
                           "hlr_identity": {"bus": "1", "label": "0x20", "bit": "3", "direction": "pub", "name": "Y1"},
                           "coverage": "ok", "attributes": []}}}
    agg7 = aggregate(if7, {"j": {}}, {}, n_configured=1)
    a7 = agg7["SIG_Y"]["aggregation"]
    assert "name(未承接)" in a7["identity_consensus"], a7["identity_consensus"]
    assert "name✓" not in a7["identity_consensus"], a7["identity_consensus"]
    # #12 未落实 + 命中 HLR（matched_hlr/name_match_status 非空）→ 已识别未承接
    assert a7["sub_classification"] == "已识别未承接", a7["sub_classification"]
    # 对照：已落实 + name 命中 仍应渲染 name✓
    if7b = {"j": {"SIG_Z": {"matched_hlr": [["R9", "bus=1|label=0x20|bit=3|dir=pub"]],
                            "verdict": "已落实", "name_match_status": True,
                            "hlr_identity": {"bus": "1", "label": "0x20", "bit": "3", "direction": "pub", "name": "Z1"},
                            "coverage": "ok", "attributes": []}}}
    a7b = aggregate(if7b, {"j": {}}, {}, n_configured=1)["SIG_Z"]["aggregation"]
    assert "name✓" in a7b["identity_consensus"], a7b["identity_consensus"]

    # --- #5 可见性：CSV/共识串必须带接口地址，不能只显示 req_id ---
    if8 = {
        "ja": {"SIG_W": {"matched_hlr": [["R12", "bus=1|label=0x10|bit=8|dir=pub"]],
                         "verdict": "已落实", "name_match_status": True,
                         "hlr_identity": {"bus": "1", "label": "0x10", "bit": "8", "direction": "pub", "name": "A"},
                         "coverage": "ok", "attributes": []}},
        "jb": {"SIG_W": {"matched_hlr": [["R12", "bus=1|label=0x11|bit=2|dir=sub"]],
                         "verdict": "已落实", "name_match_status": True,
                         "hlr_identity": {"bus": "1", "label": "0x11", "bit": "2", "direction": "sub", "name": "B"},
                         "coverage": "ok", "attributes": []}},
    }
    a8 = aggregate(if8, {j: {} for j in if8}, {}, n_configured=2)["SIG_W"]["aggregation"]
    # 共识串与 CSV 用的 matched_hlr_consensus 都要含接口（0x10 / 0x11）
    assert "0x10" in a8["identity_consensus"], a8["identity_consensus"]
    assert "0x11" in a8["matched_hlr_consensus"], a8["matched_hlr_consensus"]
    # vote 计数键不得把仅接口不同的两派折叠成一个键
    assert len(a8["vote"]) == 2, a8["vote"]
    # 旧 report（无接口信息）退化为纯 req_id，不出现空方括号
    if8b = {"j": {"SIG_V": {"matched_hlr": ["R7"], "verdict": "已落实", "name_match_status": True,
                            "hlr_identity": {"bus": "1", "label": "0x30", "bit": "1", "direction": "pub", "name": "C"},
                            "coverage": "ok", "attributes": []}}}
    a8b = aggregate(if8b, {"j": {}}, {}, n_configured=1)["SIG_V"]["aggregation"]
    assert a8b["identity_consensus"].endswith("R7→name✓"), a8b["identity_consensus"]

    # #8①：覆盖缺口的 note 必须带「为什么这个 judge 没结论」，下游复核者据此判断补不补跑
    agg_err = aggregate(shards, meta, judge_errors={"judge_same": "线程未返回（硬超时终止）"})
    nc = agg_err["SIG_C"]["aggregation"]
    assert "judge_same" in nc["note"], nc["note"]
    assert "线程未返回（硬超时终止）" in nc["note"], nc["note"]
    # 未传 judge_errors 也要有兜底文案，不能凭空消失
    nc0 = agg["SIG_C"]["aggregation"]
    assert "覆盖缺口" in nc0["note"], nc0["note"]
    # coverage_gap 名单保持原样（下游 #8 用它判断缺口面），只有 note 带原因
    assert nc0["coverage_gap"] == ["judge_same"], nc0["coverage_gap"]

    # --- #3 路由判定层：与反向 `_needs_ai_for` 对等，命中规则要可查、可单条关闭 ---
    assert b["need_review_rules"] == ["low_star_under_multijudge"], b["need_review_rules"]
    # 命中原因写进 note，复核者据此判断要不要补跑
    assert "路由原因" in b["note"], b["note"]
    # 单条规则可关闭：关掉「低置信」后 2★ 信号不再强制复核
    rule_off = {"need_review_rules": {"low_star_under_multijudge": False}}
    b_off = aggregate(shards, meta, rule_off, n_configured=3)["SIG_B"]["aggregation"]
    assert b_off["need_review"] is False, b_off["need_review"]
    assert b_off["need_review_rules"] == [], b_off["need_review_rules"]
    # 0 存活信号命中 no_valid_source；关掉低置信规则后它是唯一命中项
    zero_shards = {"j1": {"S0": {"matched_hlr": [], "verdict": "未落实", "name_match_status": False,
                                 "hlr_identity": None, "coverage": "gap", "attributes": []}}}
    z_on = aggregate(zero_shards, {"j1": meta["judge_ds"]}, {}, n_configured=2)["S0"]["aggregation"]
    assert "no_valid_source" in z_on["need_review_rules"], z_on["need_review_rules"]
    z_off = aggregate(zero_shards, {"j1": meta["judge_ds"]}, rule_off, n_configured=2)["S0"]["aggregation"]
    assert z_off["need_review"] is True and z_off["need_review_rules"] == ["no_valid_source"], \
        z_off["need_review_rules"]
    # 单 judge 正常路径（n_configured==1）不该被低置信规则命中
    assert "low_star_under_multijudge" not in sa["need_review_rules"], sa["need_review_rules"]

    # --- #11 单源「1/1」去歧义：有效源数 vs 配置数 ---
    assert sa["n_valid_sources"] == 1 and sa["n_judges_configured"] == 1
    assert "配置" not in sa["note"], sa["note"]                  # 只配 1 个 → 正常路径，不加噪音
    assert da["n_valid_sources"] == 1 and da["n_judges_configured"] == 2
    assert "仅 1 个 judge 有有效结论（配置 2 个）" in da["note"], da["note"]

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "out.csv"
        write_outputs(agg, Path(td) / "out.json", p)
        header = p.read_text(encoding="utf-8").splitlines()[0]
        # CSV 表头显式声明这是 AI 复核路由，不是人工待办清单
        assert "need_review(AI复核路由)" in header, header
        rows = p.read_text(encoding="utf-8").splitlines()
        assert len(rows) == len(agg) + 1, (len(rows), len(agg))

    print("✅ multi_judge_aggregate 自测通过（一致/分歧/覆盖缺口/事实不一致二分/星级/单judge退化兼容/#5接口级matched_hlr/#7未落实name语义/#5CSV可见性接口地址/#3路由规则表可审计可关闭/#11单源1·1去歧义/#12未落实二级子类 均符合预期）")


if __name__ == "__main__":
    _self_test()
