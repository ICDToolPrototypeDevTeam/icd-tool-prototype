"""
test_aggregate.py
=================

从 multi_judge_aggregate.py 迁出的 4 个自测函数（含主 _self_test 与 3 个分项自测）。

迁移动机：原生产文件 1500+ 行，4 个 _self_test_* 占 ~387 行（生产代码与回归护栏混在一起）。
迁到 tests/ 后：
  - 生产文件瘦身、聚焦主流程；
  - 接入 pytest 框架只需 `pytest tests/`；
  - 旧 CLI 兼容：python multi_judge_aggregate.py 仍可触发（__main__ 壳子转调本模块）。

约定：测试入口是 `run_all()`；各 test_xxx() 也可单独 import 跑。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 让 from multi_judge_aggregate import ... 能找到
_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
_MULTI_DIR = _PROJECT / "multi_judge协同"
if str(_MULTI_DIR) not in sys.path:
    sys.path.insert(0, str(_MULTI_DIR))

from multi_judge_aggregate import (  # noqa: E402
    aggregate,
    write_outputs,
    build_analysis_summary,
    derive_sub_classification,
    _attr_consensus_per_hlr,
    _attr_consensus_per_class,
)
from pathlib import Path as _Path  # noqa: E402
import tempfile as _tempfile  # noqa: E402


# ---------------------------------------------------------------------------
# 主 _self_test（原 multi_judge_aggregate.py:1137-1359）
# ---------------------------------------------------------------------------
def test_main() -> None:
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
    # 2026-10-29 口径变更：有效源数 < 配置 judge 数时不再判 full。
    # 旧口径「2 票有效且完全一致（未落实）→ agreement=full / 5★」会把 2/3 误标成
    # 「完全共识」，报告里看不出实际只有 2 个 judge 参与（2026-10-29 实测：judge_C
    # 失效时 372 条里 334 条被标「完全共识」）。现降级为 majority / 3★。
    # 注意：**覆盖缺口仍仅作标记、不强制需复核**（need_review 仍为 False）——这一条
    # 与旧口径一致，本次未变。
    assert c["identity_consensus"] == "未落实", c["identity_consensus"]
    assert c["agreement_level"] == "2/3"
    assert c["agreement"] == "majority", c["agreement"]
    assert c["need_review"] is False
    assert c["star_rating"] == 3, c["star_rating"]
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
    # 共识串（identity_consensus）含接口（0x10 / 0x11）—— 接口级差异在身份层仍可见
    assert "0x10" in a8["identity_consensus"], a8["identity_consensus"]
    assert "0x11" in a8["identity_consensus"], a8["identity_consensus"]
    # matched_hlr_consensus（Word/Excel "匹配 HLR" 列读此字段）只含 req_id，
    # 不带 [iface]——多 HLR 修复·显示层口径：HLR 主键即用户视角，接口描述符
    # 已在分析摘要的 `_match_desc` 展示，此处不重复。
    assert a8["matched_hlr_consensus"] == "R12", a8["matched_hlr_consensus"]
    # vote 计数键不得把仅接口不同的两派折叠成一个键
    assert len(a8["vote"]) == 2, a8["vote"]
    # 旧 report（无接口信息）退化为纯 req_id，不出现空方括号
    if8b = {"j": {"SIG_V": {"matched_hlr": ["R7"], "verdict": "已落实", "name_match_status": True,
                            "hlr_identity": {"bus": "1", "label": "0x30", "bit": "1", "direction": "pub", "name": "C"},
                            "coverage": "ok", "attributes": []}}}
    a8b = aggregate(if8b, {"j": {}}, {}, n_configured=1)["SIG_V"]["aggregation"]
    assert a8b["identity_consensus"].endswith("R7→name✓"), a8b["identity_consensus"]
    assert a8b["matched_hlr_consensus"] == "R7", a8b["matched_hlr_consensus"]

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

    with _tempfile.TemporaryDirectory() as td:
        p = _Path(td) / "out.csv"
        write_outputs(agg, _Path(td) / "out.json", p)
        header = p.read_text(encoding="utf-8").splitlines()[0]
        # CSV 表头显式声明这是 AI 复核路由，不是人工待办清单
        assert "need_review(AI复核路由)" in header, header
        rows = p.read_text(encoding="utf-8").splitlines()
        assert len(rows) == len(agg) + 1, (len(rows), len(agg))

    # --- 多 HLR 修复·根因修复自测（test_breakdown）---
    test_breakdown()

    print("multi_judge_aggregate 自测通过（一致/分歧/覆盖缺口/事实不一致二分/星级/单judge退化兼容/"
          "#5接口级matched_hlr/#7未落实name语义/#5CSV可见性接口地址/#3路由规则表可审计可关闭/"
          "#11单源1·1去歧义/#12未落实二级子类/多HLR三键分组不抹除+最差优先+薄封装兼容 均符合预期）")


# ---------------------------------------------------------------------------
# _self_test_breakdown（原 multi_judge_aggregate.py:1362-1436）
# ---------------------------------------------------------------------------
def test_breakdown() -> None:
    """多 HLR 修复·根因修复自测（4 组断言）。"""
    # 场景 1（核心 bug 修复）：R12 一致 + R99 不一致
    entries_1 = []
    for j in ("judge_A", "judge_B", "judge_C"):
        for req, sr, hv in [("R12", "属性一致", "1000ms"), ("R99", "属性不一致", "500ms")]:
            entries_1.append({"_judge": j, "attributes": [{
                "attribute_class": "周期", "single_result": sr,
                "matched_hlr_req_id": req, "eoicd_value": "1000ms", "hlr_value": hv,
            }]})
    src1 = _attr_consensus_per_hlr(entries_1)
    assert src1["周期"]["consensus"] == "属性不一致", \
        f"场景1: 跨 (judge, HLR) 全集最差应为 属性不一致，实际 {src1['周期']['consensus']}"
    assert src1["周期"]["worst_req"] == "R99", \
        f"场景1: 最差 HLR 应是 R99，实际 {src1['周期']['worst_req']}"
    assert src1["周期"]["worst_hlr"] == "500ms", \
        f"场景1: worst_hlr 应取 R99 的 500ms，实际 {src1['周期']['worst_hlr']}"
    for j, v in src1["周期"]["by_judge"].items():
        assert v == "属性不一致", f"场景1: by_judge[{j}] 应为 属性不一致，实际 {v}"
    assert len(src1["周期"]["per_hlr"]) == 2, f"场景1: per_hlr 应有 2 个 HLR，实际 {len(src1['周期']['per_hlr'])}"
    assert len(src1["周期"]["per_hlr"]["R12"]) == 3, f"场景1: R12 应有 3 个 judge"
    assert len(src1["周期"]["per_hlr"]["R99"]) == 3, f"场景1: R99 应有 3 个 judge"

    # 场景 2（薄封装兼容）
    shim1 = _attr_consensus_per_class(entries_1)
    assert shim1["周期"]["results"] == src1["周期"]["by_judge"], "场景2: 薄封装 results 应等于 by_judge"
    assert shim1["周期"]["eoicd_value"] == src1["周期"]["worst_eoicd"], "场景2: 薄封装 eoicd_value 应等于 worst_eoicd"
    assert shim1["周期"]["hlr_value"] == src1["周期"]["worst_hlr"], "场景2: 薄封装 hlr_value 应等于 worst_hlr"
    assert shim1["周期"]["consensus"] == src1["周期"]["consensus"], "场景2: 薄封装 consensus 应等于 per-hlr 的 consensus"

    # 场景 3（最差优先·部分落实 vs 已落实）
    entries_3 = []
    for j in ("judge_A", "judge_B", "judge_C"):
        for req, sr in [("R12", "部分落实"), ("R99", "已落实")]:
            entries_3.append({"_judge": j, "attributes": [{
                "attribute_class": "X", "single_result": sr,
                "matched_hlr_req_id": req, "eoicd_value": "1", "hlr_value": "1",
            }]})
    src3 = _attr_consensus_per_hlr(entries_3)
    assert src3["X"]["consensus"] == "部分落实", \
        f"场景3: 部分落实+已落实 最差应为 部分落实，实际 {src3['X']['consensus']}"
    assert src3["X"]["worst_req"] == "R12", \
        f"场景3: worst_req 应取 R12（更严重），实际 {src3['X']['worst_req']}"

    # 场景 4（向后兼容·单 HLR）
    entries_4 = []
    for j in ("judge_A", "judge_B", "judge_C"):
        entries_4.append({"_judge": j, "attributes": [{
            "attribute_class": "Y", "single_result": "属性一致",
            "matched_hlr_req_id": "R12", "eoicd_value": "100", "hlr_value": "100",
        }]})
    src4 = _attr_consensus_per_hlr(entries_4)
    assert src4["Y"]["consensus"] == "属性一致", f"场景4: 单 HLR 全一致 应为 属性一致，实际 {src4['Y']['consensus']}"
    assert src4["Y"]["worst_req"] == "R12", f"场景4: worst_req 应取 R12，实际 {src4['Y']['worst_req']}"
    shim4 = _attr_consensus_per_class(entries_4)
    assert shim4["Y"]["hlr_value"] == "100", f"场景4: 薄封装 hlr_value 应为 100，实际 {shim4['Y']['hlr_value']}"

    print("  [单测] test_breakdown （4 组断言：bug 修复 + 薄封装兼容 + 部分落实最差 + 单 HLR 退化）")


# ---------------------------------------------------------------------------
# _self_test_unresolved_phrase（原 multi_judge_aggregate.py:1439-1501）
# ---------------------------------------------------------------------------
def test_unresolved_phrase() -> None:
    """「未落实」判定句护栏（用户 2026-10-16 定稿规则）。"""

    def _jw(verdict, has_hlr, name=False):
        return {"verdict": verdict, "coverage": "ok",
                "matched_hlr": ([["R1", ""]] if has_hlr else []),
                "name_match_status": (True if name else None),
                "hlr_identity": {"bus": "1", "label": "0x10", "bit": "1", "direction": "pub", "name": "N"}}

    def _call(jw, verdict, divergence=True):
        return build_analysis_summary(final_verdict=verdict, judges_with=jw, attr_by_class={},
                                      inconsistent_attributes=[], field_disagreements=[],
                                      divergence=divergence, n_judges=len(jw))

    # 1) 识别状态一致（3 个都识别到）→ 一致判定（带子类），且不得出现「多数判定」
    s1 = _call({"judge_A": _jw("未落实", True, True), "judge_B": _jw("未落实", True, True),
                "judge_C": _jw("未落实", True, True)}, "未落实")
    assert s1.startswith("一致判定「未落实」（已识别未承接）"), s1
    assert "多数判定" not in s1, s1

    # 2) 识别 2:1 有分歧 → 多数判定（2/3）
    s2 = _call({"judge_A": _jw("未落实", True, True), "judge_B": _jw("未落实", True, True),
                "judge_C": _jw("未落实", False)}, "未落实")
    assert s2.startswith("多数判定「未落实」（已识别未承接）（2/3）"), s2

    # 3) 识别 1:2 有分歧 → 同样多数判定（2/3）；子类已改多数派口径
    s3 = _call({"judge_A": _jw("未落实", True, True), "judge_B": _jw("未落实", False),
                "judge_C": _jw("未落实", False)}, "未落实")
    assert s3.startswith("多数判定「未落实」（未识别）（2/3）"), s3

    # 4) 识别状态一致（3 个都没识别到）→ 一致判定（子类=未识别）
    s4 = _call({"judge_A": _jw("未落实", False), "judge_B": _jw("未落实", False),
                "judge_C": _jw("未落实", False)}, "未落实")
    assert s4.startswith("一致判定「未落实」（未识别）"), s4
    assert "多数判定" not in s4, s4

    # 5) 非「未落实」判定不受影响
    s5 = _call({"judge_A": _jw("部分落实", True, True), "judge_B": _jw("部分落实", True, True),
                "judge_C": _jw("部分落实", False)}, "部分落实")
    assert s5.startswith("一致判定「部分落实」"), s5
    assert "多数判定" not in s5, s5

    # 6) 识别状态平票（2 个有效 judge 1:1、总 judge 数 3）→ 无多数派
    _tie = {"judge_A": _jw("未落实", True, True), "judge_B": _jw("未落实", False),
            "judge_C": dict(_jw("未落实", False), coverage="bad")}
    s6 = build_analysis_summary(final_verdict="未落实", judges_with=_tie, attr_by_class={},
                                inconsistent_attributes=[], field_disagreements=[],
                                divergence=True, n_judges=3)
    assert s6.startswith("各 judge 判定不一"), s6
    assert "多数判定" not in s6 and "一致判定" not in s6, s6
    assert "（已识别未承接）" not in s6 and "（未识别）" not in s6, s6

    print("  [单测] test_unresolved_phrase （6 组断言：识别一致→一致判定 / 2:1→多数判定(2/3) /"
          " 1:2→多数判定(2/3) / 全未识别→一致判定 / 非未落实判定不受影响 /"
          " 有效judge平票→各 judge 判定不一）")


# ---------------------------------------------------------------------------
# _self_test_verdict_tie（原 multi_judge_aggregate.py:1504-1530）
# ---------------------------------------------------------------------------
def test_verdict_tie() -> None:
    """护栏：identity 计票平票时 final_verdict 按严重度裁定（不再依赖插入序）。"""
    def _e(verdict, req):
        return {"matched_hlr": [req], "verdict": verdict, "name_match_status": True,
                "hlr_identity": {"bus": "1", "label": "0x10", "bit": "1", "direction": "pub", "name": "N"},
                "coverage": "ok", "attributes": []}

    # 1) 2 judge 平票、verdict 不同 → 取最严重者（不一致 优先于 未落实）
    sh = {"judge_A": {"S": _e("未落实", "R1")}, "judge_B": {"S": _e("不一致", "R2")}}
    a = aggregate(sh, {"judge_A": {}, "judge_B": {}}, {}, n_configured=2)["S"]["aggregation"]
    assert a["final_verdict"] == "不一致", f"平票应取更严重者，实际 {a['final_verdict']!r}"

    # 2) 调换 judge 顺序，结果必须一致（证明不再依赖插入序）
    sh_rev = {"judge_A": {"S": _e("不一致", "R2")}, "judge_B": {"S": _e("未落实", "R1")}}
    a2 = aggregate(sh_rev, {"judge_A": {}, "judge_B": {}}, {}, n_configured=2)["S"]["aggregation"]
    assert a2["final_verdict"] == a["final_verdict"] == "不一致", (a2["final_verdict"], a["final_verdict"])

    # 3) 平票但 verdict 相同（仅匹配到的 HLR 不同）→ 仍是该 verdict，不误判
    sh3 = {"judge_A": {"S": _e("未落实", "R1")}, "judge_B": {"S": _e("未落实", "R2")}}
    a3 = aggregate(sh3, {"judge_A": {}, "judge_B": {}}, {}, n_configured=2)["S"]["aggregation"]
    assert a3["final_verdict"] == "未落实", a3["final_verdict"]

    print("  [单测] test_verdict_tie （3 组断言：平票取最严重 / 与 judge 顺序无关 / "
          "verdict 相同时不受影响）")


def test_attr_seg_formatting() -> None:
    """验证「属性一致」项 cap=3 逐项列出（2026-10-19 改造后）：

    旧实现：所有「属性一致」项省略为「其余 N 项一致」——读者不知道具体是哪些。
    新实现：cap=3 逐项列出类名（与 _notm 的"等 N 项"口径一致，避免摘要过长）。
    1/2/3 项：完整列出；≥4 项：列前 3 个 + "等 N 项一致"。

    边界设计：每种 cap 都加一个"非一致"项（如 1 个无提及）触发 _build_attr_seg
    的"其余"分支——纯"全部一致"会走快路径（无"其余"后缀），无法验证。
    """
    def _call(attr_by_class, inconsistent_attributes=None):
        """最小化调用 build_analysis_summary，让判定句最短，专注属性子句输出。"""
        if inconsistent_attributes is None:
            inconsistent_attributes = []
        return build_analysis_summary(
            final_verdict="已落实",
            judges_with={"judge_A": {"matched_hlr": ["R1"], "verdict": "已落实",
                                     "name_match_status": True,
                                     "hlr_identity": {"bus": "1", "label": "0x10", "bit": "1", "direction": "pub"},
                                     "coverage": "ok", "attributes": []}},
            attr_by_class=attr_by_class,
            inconsistent_attributes=inconsistent_attributes,
            field_disagreements=[],
            divergence=False,
            n_judges=1,
        )

    def _attr(consensus):
        """构造一个最小化的 attr_by_class 元素。"""
        return {"consensus": consensus, "entries": []}

    # --- 1) 1 项一致 + 1 项无提及 → 「其余 X 一致」 ---
    abc = {"DP.CodedSet": _attr("属性一致"), "DP.OneState": _attr("无提及")}
    out = _call(abc)
    assert "其余 DP.CodedSet 一致" in out, f"1 项一致格式错：{out!r}"
    print(f"  [单测] 1 项一致格式正确：…其余 DP.CodedSet 一致")

    # --- 2) 2 项一致 + 1 项无提及 → 「其余 X 和 Y 一致」 ---
    abc = {"DP.A": _attr("属性一致"), "DP.B": _attr("属性一致"), "DP.Z": _attr("无提及")}
    out = _call(abc)
    assert "其余 DP.A 和 DP.B 一致" in out, f"2 项一致格式错：{out!r}"
    print(f"  [单测] 2 项一致格式正确：…其余 DP.A 和 DP.B 一致")

    # --- 3) 3 项一致 + 1 项无提及 → 「其余 X、Y 和 Z 一致」 ---
    abc = {"DP.CodedSet": _attr("属性一致"), "DP.OneState": _attr("属性一致"),
           "DP.ZeroState": _attr("属性一致"), "DP.X": _attr("无提及")}
    out = _call(abc)
    assert "其余 DP.CodedSet、DP.OneState 和 DP.ZeroState 一致" in out, \
        f"3 项一致格式错：{out!r}"
    print(f"  [单测] 3 项一致格式正确：…其余 DP.CodedSet、DP.OneState 和 DP.ZeroState 一致")

    # --- 4) 5 项一致 + 1 项无提及 → cap=3 + "等 N 项一致"（按 attr_by_class 插入顺序列前 3 个） ---
    abc = {f"DP.X{i}": _attr("属性一致") for i in range(5)}
    abc["DP.Z"] = _attr("无提及")
    out = _call(abc)
    # attr_by_class 是 dict（Python 3.7+ 保序），列前 3 个 + "等 5 项一致"
    assert "DP.X0、DP.X1 和 DP.X2 等 5 项一致" in out, \
        f"5 项一致 cap 格式错：{out!r}"
    print(f"  [单测] 5 项一致 cap 格式正确：…DP.X0、DP.X1 和 DP.X2 等 5 项一致")

    # --- 5) 与"无提及"混排：1 项一致 + 2 项无提及 → 两者都列 ---
    abc = {"DP.A": _attr("属性一致"), "DP.B": _attr("无提及"), "DP.C": _attr("无提及")}
    out = _call(abc)
    # 现状：_notm 在 _body 后部、"属性一致"补报在最末
    # 1 项一致 + 2 项无提及 顺序 = _notm(2 项) + _ok_list(1 项)
    assert "DP.B、DP.C 无提及" in out, f"无提及 2 项并列错：{out!r}"
    assert "其余 DP.A 一致" in out, f"1 项一致被吃：{out!r}"
    print(f"  [单测] 属性一致 + 无提及混排正确：DP.B、DP.C 无提及，其余 DP.A 一致")

    # --- 6) 与"事实不一致"混排：1 项不一致 + 1 项一致 + 1 项无提及 ---
    abc = {"DP.A": _attr("属性一致"), "DP.B": _attr("无提及"), "DP.C": _attr("分歧")}
    inc = [{"attribute_class": "DP.C", "eoicd_value": "100", "hlr_value": "200"}]
    out = _call(abc, inc)
    assert "DP.C：EoICD 100 vs HLR 200，各 judge 结论分歧" in out, \
        f"事实不一致子句错：{out!r}"
    assert "DP.B 无提及" in out, f"无提及子句错：{out!r}"
    assert "其余 DP.A 一致" in out, f"1 项一致被吃：{out!r}"
    # header 总数闭合：3 项
    assert "属性比对 3 项" in out, f"header 总数错：{out!r}"
    print(f"  [单测] 不一致/无提及/一致 三类混排正确（header 总数 3 项与分项之和闭合）")

    # --- 7) 全部一致 → 走「全部一致」快路径，不走"其余"分支 ---
    abc = {"DP.A": _attr("属性一致"), "DP.B": _attr("属性一致")}
    out = _call(abc)
    assert "属性比对 2 项全部一致" in out, f"全一致应走快路径：{out!r}"
    assert "其余" not in out, f"全一致不应有'其余'：{out!r}"
    print(f"  [单测] 全部一致走快路径（无'其余'后缀）")

    print("  [单测] test_attr_seg_formatting （7 组断言：1/2/3/4+/与无提及混排/与事实不一致混排/全一致快路径）")


def test_attr_seg_judge_disagreement_detail() -> None:
    """验证「各 judge 结论分歧」列逐个 judge 结论（2026-10-19 改造 2）：

    旧实现：仅写"各 judge 结论分歧"短语——读者不知道具体是哪些 judge 怎么判。
    新实现：cap=3 列出每个 judge 的 single_result，按 results dict 插入序（即
    judge_with 注册序，不为排版重排）。1/2/3 judge 全列；≥4 judge 列前 3 + 等 N 个 judge。

    两个分歧路径都需覆盖：
      - L610：事实不一致 + 结论分歧（既有 EoICD vs HLR 值对比 + 各 judge 结论）
      - L620：仅结论分歧（无事实不一致，仅 judge 间判断不同）
    """
    def _call(attr_by_class, inconsistent_attributes=None):
        if inconsistent_attributes is None:
            inconsistent_attributes = []
        return build_analysis_summary(
            final_verdict="已落实",
            judges_with={"judge_A": {"matched_hlr": ["R1"], "verdict": "已落实",
                                     "name_match_status": True,
                                     "hlr_identity": {"bus": "1", "label": "0x10", "bit": "1", "direction": "pub"},
                                     "coverage": "ok", "attributes": []}},
            attr_by_class=attr_by_class,
            inconsistent_attributes=inconsistent_attributes,
            field_disagreements=[],
            divergence=False,
            n_judges=1,
        )

    def _attr(consensus, results=None):
        return {"consensus": consensus, "results": results or {}, "entries": []}

    # --- 1) 1 judge 分歧 → 「（judge_A=无提及）」（罕见：单 judge 配置下也允许分歧标记） ---
    abc = {"DP.X": _attr("分歧", {"judge_A": "无提及"})}
    out = _call(abc)
    assert "DP.X：各 judge 结论分歧（judge_A=无提及）" in out, \
        f"1 judge 分歧格式错：{out!r}"
    print(f"  [单测] 1 judge 分歧：…各 judge 结论分歧（judge_A=无提及）")

    # --- 2) 2 judge 分歧 → 「（judge_A=XX；judge_B=YY）」（按 results 插入序） ---
    abc = {"DP.X": _attr("分歧", {"judge_A": "属性一致", "judge_B": "无提及"})}
    out = _call(abc)
    assert "DP.X：各 judge 结论分歧（judge_A=属性一致；judge_B=无提及）" in out, \
        f"2 judge 分歧格式错：{out!r}"
    print(f"  [单测] 2 judge 分歧：…各 judge 结论分歧（judge_A=属性一致；judge_B=无提及）")

    # --- 3) 3 judge 分歧 → 「（judge_A=XX；judge_B=YY；judge_C=ZZ）」（生产数据主场景） ---
    abc = {"DP.CodedSet": _attr("分歧", {
        "judge_A": "无提及",
        "judge_B": "属性一致",
        "judge_C": "属性一致",
    })}
    out = _call(abc)
    assert "DP.CodedSet：各 judge 结论分歧（judge_A=无提及；judge_B=属性一致；judge_C=属性一致）" in out, \
        f"3 judge 分歧格式错：{out!r}"
    print(f"  [单测] 3 judge 分歧：…各 judge 结论分歧（judge_A=无提及；judge_B=属性一致；judge_C=属性一致）")

    # --- 4) 4 judge 分歧 → cap=3 + "等 4 个 judge" ---
    abc = {"DP.X": _attr("分歧", {
        "judge_A": "属性不一致",
        "judge_B": "属性一致",
        "judge_C": "无提及",
        "judge_D": "属性一致",
    })}
    out = _call(abc)
    assert "DP.X：各 judge 结论分歧（judge_A=属性不一致；judge_B=属性一致；judge_C=无提及 等 4 个 judge）" in out, \
        f"4 judge cap 格式错：{out!r}"
    print(f"  [单测] 4 judge cap：…各 judge 结论分歧（… 等 4 个 judge）")

    # --- 5) L620 路径：仅结论分歧（无事实不一致）→ 走纯「：各 judge 结论分歧」无 _val 前缀 ---
    abc = {"DP.Y": _attr("分歧", {"judge_A": "属性一致", "judge_B": "无提及"})}
    out = _call(abc)
    # 不应有 "EoICD" / "vs HLR" 等事实不一致字眼
    assert "EoICD" not in out and "vs HLR" not in out, \
        f"无事实不一致时不应有 EoICD vs HLR 子句：{out!r}"
    assert "DP.Y：各 judge 结论分歧（judge_A=属性一致；judge_B=无提及）" in out, \
        f"L620 路径格式错：{out!r}"
    print(f"  [单测] L620 路径（仅分歧）：无 EoICD vs HLR 子句 + 列出 judge 结论")

    # --- 6) L610 路径：事实不一致 + 结论分歧 → 既有 EoICD vs HLR + 各 judge 结论 ---
    abc = {"DP.CodedSet": _attr("分歧", {
        "judge_A": "无提及", "judge_B": "属性一致", "judge_C": "属性不一致",
    })}
    inc = [{"attribute_class": "DP.CodedSet", "eoicd_value": "1=Fail", "hlr_value": "0=Not Fail"}]
    out = _call(abc, inc)
    # 事实不一致子句（EoICD vs HLR）+ 各 judge 结论（圆括号内）
    assert "DP.CodedSet：EoICD 1=Fail vs HLR 0=Not Fail" in out, \
        f"事实不一致子句错：{out!r}"
    assert "，各 judge 结论分歧（judge_A=无提及；judge_B=属性一致；judge_C=属性不一致）" in out, \
        f"L610 路径格式错：{out!r}"
    print(f"  [单测] L610 路径（事实不一致+分歧）：EoICD vs HLR + 各 judge 结论并存")

    # --- 7) results 空 dict 兜底：返回空串 → "...各 judge 结论分歧（）"
    # （防御性：consensus="分歧" 必有 ≥2 个 judge，但 results 字段缺失时不能崩） ---
    abc = {"DP.Z": _attr("分歧", {})}
    out = _call(abc)
    assert "DP.Z：各 judge 结论分歧（）" in out, \
        f"results 空 dict 兜底应输出空括号：{out!r}"
    print(f"  [单测] results 空 dict 兜底：…各 judge 结论分歧（）")

    # --- 8) 与「属性一致」+「无提及」混排：3 judge 分歧 + 1 一致 + 2 无提及 ---
    abc = {
        "DP.CodedSet": _attr("分歧", {"judge_A": "无提及", "judge_B": "属性一致", "judge_C": "属性一致"}),
        "DP.DataFormatType": _attr("无提及"),
        "DP.A": _attr("属性一致"),
    }
    out = _call(abc)
    assert "DP.CodedSet：各 judge 结论分歧（judge_A=无提及；judge_B=属性一致；judge_C=属性一致）" in out, \
        f"混排 judge 分歧子句错：{out!r}"
    # 无提及应被汇总（不止 1 个 → 走"等 N 项"路径）
    assert "DP.DataFormatType 无提及" in out or "等" in out, \
        f"混排无提及子句错：{out!r}"
    # 属性一致应被 cap=3 列出
    assert "其余 DP.A 一致" in out, f"混排 1 项一致被吃：{out!r}"
    print(f"  [单测] judge 分歧 + 无提及 + 属性一致 三类混排正确")

    print("  [单测] test_attr_seg_judge_disagreement_detail "
          "（8 组断言：1/2/3/4+ judge + L620 仅分歧 + L610 事实不一致+分歧 + results 空兜底 + 三类混排）")


def run_all() -> None:
    """入口：跑所有聚合层自测。"""
    test_main()       # 内部已链式调用 test_breakdown
    test_unresolved_phrase()
    test_verdict_tie()
    test_attr_seg_formatting()
    test_attr_seg_judge_disagreement_detail()


if __name__ == "__main__":
    run_all()
