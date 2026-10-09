# -*- coding: utf-8 -*-
"""多 judge 聚合结果的汇总统计（对齐反向 review_agent._build_summary 口径）。

设计约定
--------
- **保留粒度**：一行 = 一个 EoICD 信号。本模块只做统计，不改变任何信号粒度。
- 字段与反向一致：`total` / `star_distribution` / `agreement_distribution` /
  `status_distribution`（中文标签）/ `average_star_rating`。
- **AI 复核覆盖**（`review_coverage`，对齐反向 `coverage.ai_review_calls`）：复核是
  AI 做的（Step5 仲裁 / Step5.5 复查），不是人工待办。报告必须能看出「有多少信号
  拿到了第二裁决、哪些因故没有」，否则 1★ 和「随便看了一眼」在产物里长得一样。
  统计以 agg 里的 `review_trace` 为准（产物即事实），`review_stats` 仅补充元信息。
- 1★ 的三个降级子类型（split / single_source / no_consensus）**仍计入
  `star_distribution["1"]`**，细分标签由 `agreement_distribution` 承载，
  与反向「星级分布」表的口径一致。
"""
from __future__ import annotations

# 路由判定规则表由聚合层定义（对齐反向 `_needs_ai_for`），此处只回读用于报告展示。
# 延迟导入失败也不影响统计本身，故用 None 兜底。
try:
    from multi_judge_aggregate import NEED_REVIEW_RULES as AGGREGATE_RULES  # type: ignore
except Exception:  # pragma: no cover
    AGGREGATE_RULES = None

# 判定分组顺序：与 summary.py 的优先级「不一致 ＞ 未落实 ＞ 部分落实 ＞ 已落实」一致，
# 末尾追加「覆盖缺口」（无 judge 给出有效结论）。Word 报告按此顺序分组出表。
VERDICT_GROUPS: list[str] = ["不一致", "未落实", "部分落实", "已落实", "覆盖缺口"]

# 判定 → 颜色（对齐反向 consensus_word_generator 的 status_colors）
VERDICT_COLORS: dict[str, str] = {
    "不一致": "CC3300",
    "未落实": "CC5500",
    "部分落实": "1F4E78",
    "已落实": "006100",
    "覆盖缺口": "808080",
}

# agreement → 中文（仅用于展示与注释；计数仍以原始 key 为准）
AGREEMENT_CN: dict[str, str] = {
    "full": "完全共识",
    "majority": "多数共识",
    "split": "三方分歧",
    "single_source": "仅单源",
    "no_consensus": "全部失效",
}


def build_review_coverage(agg: dict, review_stats: dict | None = None,
                          rules: list[dict] | None = None) -> dict:
    """统计 AI 复核覆盖（谁能证明「这条低置信信号确实过过第二道裁决」）。

    数据来源优先级：agg 里每条信号的 `review_trace` 是最终事实（Step5/5.5 的实际
    落点），`review_stats` 只用来补全局元信息（仲裁来源/模型/预算/同源标记）。

    #3 路由判定层：规则表默认取聚合层的 NEED_REVIEW_RULES，命中情况从每条信号的
    `need_review_rules` 回读——报告能看出「本轮按哪些规则判需复核、各命中多少条」，
    而不是只看一个「需复核 N」的数。
    """
    if rules is None:
        rules = AGGREGATE_RULES
    cov = {
        "available": False,
        "signals_total": 0,
        "need_review": 0,
        "reviewed": 0,
        "skipped": 0,
        "skip_reasons": {},
        "peer_reviewed": 0,
        "arb_source": "",
        "arb_model": "",
        "arb_same_as_judge": None,
        "budget": 0,
        "rules_enabled": [r["name"] for r in (rules or [])],
        "rule_hits": {},
    }
    for _full, item in (agg or {}).items():
        a = (item or {}).get("aggregation") or {}
        cov["signals_total"] += 1
        if not a.get("need_review"):
            continue
        cov["need_review"] += 1
        for rn in (a.get("need_review_rules") or []):
            cov["rule_hits"][rn] = cov["rule_hits"].get(rn, 0) + 1
        trace = a.get("review_trace") or {}
        if trace.get("applied"):
            cov["reviewed"] += 1
        elif trace:
            reason = trace.get("reason") or "未记录原因（review_trace.reason 缺失）"
            cov["skipped"] += 1
            cov["skip_reasons"][reason] = cov["skip_reasons"].get(reason, 0) + 1
        else:
            # 整段复核路由没跑（同源被拒 / 仲裁模型不可用 / 未配置），产物里没有 trace。
            # 此时原因以 review_stats 的 skipped 明细为准，不在此凭空编造 reason。
            cov["skipped"] += 1
            cov["no_trace"] = cov.get("no_trace", 0) + 1
        # Step5.5 的结论存在 trace 的子字段里（Step5.6 重仲裁会刷新 trace，
        # 但不会改写 peer_review，否则「复查为何没做」会被抹掉）
        if (trace.get("peer_review") or a.get("peer_review") or {}).get("applied"):
            cov["peer_reviewed"] += 1

    if review_stats:
        # 路由被消费过（needed>0）即视为本轮确有复核阶段；needed==0 说明无信号需复核，
        # 此时报告不必标注「未执行复核」，但元信息仍要带上以便溯源。
        cov["available"] = bool(review_stats.get("needed", 0) > 0)
        cov["arb_source"] = review_stats.get("arb_source") or ""
        cov["arb_model"] = review_stats.get("arb_model") or ""
        cov["arb_same_as_judge"] = review_stats.get("arb_same_as_judge")
        cov["budget"] = int(review_stats.get("budget") or 0)
        # 只补 agg 中未出现的异常原因（如「复核后信号从结果中消失」），避免同一条
        # 信号被 trace 和 review_stats 各记一次导致计数翻倍。
        agg_reasons = set(cov["skip_reasons"])
        for reason, cnt in (review_stats.get("reasons") or {}).items():
            if reason in agg_reasons:
                continue
            cov["skip_reasons"][reason] = cov["skip_reasons"].get(reason, 0) + cnt
        for reason, cnt in (review_stats.get("review_gaps") or {}).items():
            tag = "复核已生效，复查未生效：" + reason
            if tag in agg_reasons:
                continue
            cov["skip_reasons"][tag] = cov["skip_reasons"].get(tag, 0) + cnt
    return cov


def build_summary(agg: dict, review_stats: dict | None = None) -> dict:
    """从 aggregate() 结果构建汇总统计。

    agg: {signal_full_name: {"aggregation": {...}, ...}}（即聚合产物本体）。
    review_stats: run_review_route() 的本轮记账（可选，仅用于补充元信息）。
    返回结构对齐反向 review_agent._build_summary，并附加 `review_coverage`。
    """
    star_dist = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
    agreement_dist: dict[str, int] = {}
    status_dist: dict[str, int] = {}
    # #12 未落实二级子类计数（对齐单 judge 的 sub_classification 展示）：已识别未承接 / 未识别
    sub_status_dist: dict[str, int] = {}
    # 判定顺序：主序 + 其余按字母序兜底，保证任何判定都不会从合计中静默消失
    status_keys_ordered = list(VERDICT_GROUPS)

    total = 0
    star_sum = 0
    for _full, item in (agg or {}).items():
        a = (item or {}).get("aggregation") or {}
        total += 1

        raw_star = a.get("star_rating") or 0
        try:
            star = int(raw_star)
        except (TypeError, ValueError):
            star = 0
        star_sum += star
        # 越界星级统一归入 1★ 桶（与反向同口径：非 1-5 一律按 1 计数）
        star_dist[str(star) if star in (1, 2, 3, 4, 5) else "1"] += 1

        agreement = a.get("agreement") or "no_consensus"
        agreement_dist[agreement] = agreement_dist.get(agreement, 0) + 1

        verdict = a.get("final_verdict") or "覆盖缺口"
        status_dist[verdict] = status_dist.get(verdict, 0) + 1
        if verdict not in status_keys_ordered:
            status_keys_ordered.append(verdict)
        # #12 未落实二级子类计数（对齐单 judge 的 sub_classification 展示）
        if verdict == "未落实":
            _sc = a.get("sub_classification")
            if _sc:
                sub_status_dist[_sc] = sub_status_dist.get(_sc, 0) + 1

    ordered_status = {k: status_dist[k] for k in status_keys_ordered if status_dist.get(k)}

    return {
        "total": total,
        "star_distribution": star_dist,
        "agreement_distribution": dict(sorted(agreement_dist.items())),
        "status_distribution": ordered_status,
        # #12 未落实二级子类计数（对齐单 judge：已识别未承接 / 未识别），主 status_distribution 维持 5 组不变
        "status_distribution_detail": {
            "已识别未承接": sub_status_dist.get("已识别未承接", 0),
            "未识别": sub_status_dist.get("未识别", 0),
        },
        "average_star_rating": round(star_sum / total, 2) if total else 0.0,
        "review_coverage": build_review_coverage(agg, review_stats),
    }


def format_summary_text(summary: dict, include_review_coverage: bool = True) -> str:
    """把汇总统计渲染成人类可读文本（runner 控制台打印 / Word 报告复用）。

    include_review_coverage：是否附带「AI 复核覆盖」一行。报告侧传 False
    （复核过程是中间产物，用户向交付物只呈现落实结论）；控制台侧保持 True。
    """
    if not summary:
        return "汇总统计：无数据"
    lines = [
        f"信号总数 {summary['total']} 条，"
        f"平均星级 {summary['average_star_rating']:.2f}",
        "判定分布：" + "，".join(
            f"{k} {v} 条" for k, v in summary.get("status_distribution", {}).items()
        ),
    ]
    # #12 未落实二级子类：控制台一并给出根因拆分（已识别未承接 / 未识别）
    _detail = summary.get("status_distribution_detail")
    if _detail and any(_detail.values()):
        lines.append("未落实子类：" + "，".join(
            f"{k} {v} 条" for k, v in _detail.items() if v))
    lines += [
        "星级分布：" + "，".join(
            f"{k}★ {v} 条" for k, v in summary.get("star_distribution", {}).items()
        ),
        "共识类型：" + "，".join(
            f"{AGREEMENT_CN.get(k, k)} {v} 条"
            for k, v in summary.get("agreement_distribution", {}).items()
        ),
    ]
    if include_review_coverage:
        lines.append(format_review_coverage_text(summary))
    return "\n  ".join(lines)


def format_review_coverage_text(summary: dict) -> str:
    """渲染 AI 复核覆盖说明（runner 控制台打印用；报告侧不渲染复核覆盖）。

    没有这一行，低星结论在控制台无法自证「是否经过第二裁决」。
    """
    cov = summary.get("review_coverage") or {}
    if not cov:
        return "AI 复核覆盖：无复核记录"
    # #3 路由判定层：命中哪几条规则、各多少条，让「需复核」可追溯（产物事实，与
    # 「本轮路由是否被消费」无关，故在 available 为 False 时也要带出来）
    hits = cov.get("rule_hits") or {}
    hits_txt = ""
    if hits:
        hits_txt = "；路由判定命中：" + "，".join(
            "{0} {1} 条".format(n, c) for n, c in sorted(hits.items()))
    if not cov.get("available"):
        base = ("AI 复核覆盖：本轮无信号进入复核路由，"
                "下列低星结论均为单轮 judge 结论，未经第二裁决")
        return base + hits_txt if hits_txt else base
    text = ("AI 复核覆盖：需复核 {need} 条 → 获第二裁决 {done} 条、未生效 {skip} 条"
            .format(need=cov.get("need_review", 0), done=cov.get("reviewed", 0),
                    skip=cov.get("skipped", 0)))
    if cov.get("peer_reviewed"):
        text += "；其中 {0} 条另经 Step5.5 复查".format(cov["peer_reviewed"])
    if cov.get("arb_same_as_judge"):
        text += "（⚠ 仲裁视角与 judge 同源，第二裁决独立性受限）"
    return text + hits_txt


if __name__ == "__main__":  # pragma: no cover
    # 自测：与 aggregate 自测共用同一套合成数据形态。
    _agg = {
        "SIG_A": {"aggregation": {"star_rating": 5, "agreement": "full",
                                  "final_verdict": "已落实"}},
        "SIG_B": {"aggregation": {"star_rating": 1, "agreement": "split",
                                  "final_verdict": "未落实"}},
        "SIG_C": {"aggregation": {"star_rating": 2, "agreement": "majority",
                                  "final_verdict": "部分落实"}},
        "SIG_D": {"aggregation": {"star_rating": 1, "agreement": "no_consensus",
                                  "final_verdict": "不一致"}},
        "SIG_E": {"aggregation": {"star_rating": 0, "agreement": "no_consensus",
                                  "final_verdict": "覆盖缺口"}},
    }
    s = build_summary(_agg)
    assert s["total"] == 5, s
    # 越界星级 0 归入 1★ 桶 → 1★ 共 3 条
    assert s["star_distribution"] == {"1": 3, "2": 1, "3": 0, "4": 0, "5": 1}, s
    assert s["agreement_distribution"] == {"full": 1, "majority": 1,
                                           "no_consensus": 2, "split": 1}, s
    assert list(s["status_distribution"].keys()) == VERDICT_GROUPS, s
    # 平均星级按实际值：(5+1+2+1+0)/5 = 1.8
    assert abs(s["average_star_rating"] - 1.8) < 1e-6, s

    empty = build_summary({})
    assert empty["total"] == 0 and empty["average_star_rating"] == 0.0

    # AI 复核覆盖：以产物里的 review_trace 为准，review_stats 只补元信息
    _agg2 = {
        "SIG_A": {"aggregation": {"star_rating": 1, "agreement": "split",
                                  "final_verdict": "未落实", "need_review": True,
                                  "review_trace": {"stage": "step5", "applied": True,
                                                   "reason": "Step5 仲裁已生效"}}},
        "SIG_B": {"aggregation": {"star_rating": 2, "agreement": "majority",
                                  "final_verdict": "部分落实", "need_review": True,
                                  "review_trace": {"stage": "step5", "applied": False,
                                                   "reason": "超出复核预算"},
                                  "peer_review": {"applied": True}}},
    }
    _st = {"needed": 2, "executed": 1, "skipped": 1, "reasons": {"超出复核预算": 1},
           "arb_source": "回落：沿用 judge[0]", "arb_model": "deepseek",
           "arb_same_as_judge": True, "budget": 1}
    s2 = build_summary(_agg2, _st)
    cov = s2["review_coverage"]
    assert cov["need_review"] == 2 and cov["reviewed"] == 1 and cov["skipped"] == 1, cov
    assert cov["peer_reviewed"] == 1, cov
    assert cov["available"] is True and cov["arb_same_as_judge"] is True
    # agg 里已记过的原因不再被 review_stats 重复累加（防计数翻倍）
    assert cov["skip_reasons"].get("超出复核预算") == 1, cov
    _txt = format_review_coverage_text(s2)
    assert "仲裁视角与 judge 同源" in _txt and "⚠" in _txt, _txt
    # #3 路由判定层：规则命中要能在文本里看出，且启用清单来自聚合层规则表
    assert cov["rules_enabled"] == [r["name"] for r in AGGREGATE_RULES], cov["rules_enabled"]
    _agg3 = {
        "SIG_X": {"aggregation": {"star_rating": 1, "agreement": "split",
                                  "final_verdict": "不一致", "need_review": True,
                                  "need_review_rules": ["low_star_under_multijudge",
                                                        "no_valid_source"],
                                  "review_trace": {"applied": True, "reason": "x"}}},
        "SIG_Y": {"aggregation": {"star_rating": 1, "agreement": "no_consensus",
                                  "final_verdict": "覆盖缺口", "need_review": True,
                                  "need_review_rules": ["no_valid_source"]}},
    }
    cov3 = build_summary(_agg3)["review_coverage"]
    assert cov3["rule_hits"] == {"low_star_under_multijudge": 1, "no_valid_source": 2}, cov3
    assert "路由判定命中" in format_review_coverage_text(build_summary(_agg3))
    # 未传 review_stats 时元信息留空，available=False（不假装复核过）
    s3 = build_summary(_agg2)
    assert s3["review_coverage"]["arb_same_as_judge"] is None
    assert "未经第二裁决" in format_review_coverage_text(s3)
    assert "未经第二裁决" in format_summary_text(s3)

    print("✅ multi_judge_summary 自测通过（分布统计/越界星级归桶/判定顺序/空集/AI复核覆盖均符合预期）")
    print("  " + format_summary_text(s))
