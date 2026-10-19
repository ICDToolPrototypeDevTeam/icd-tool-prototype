"""
test_arbitrator.py
==================

从 multi_judge_arbitrator.py 迁出的 3 个自测函数。

迁移动机：原生产文件 1400+ 行，3 个 _self_test_* 占 ~398 行。迁到 tests/ 后：
  - 生产文件瘦身；
  - 便于 pytest 化；
  - 旧 CLI 兼容：python multi_judge_arbitrator.py 仍可触发（__main__ 壳子转调）。

关键变化：原测试函数里 `global call_llm` 直接修改模块内全局；迁移到独立模块后，
需用 `arb_mod.call_llm = ...` 的形式显式修改生产模块的属性。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
_MULTI_DIR = _PROJECT / "multi_judge协同"
if str(_MULTI_DIR) not in sys.path:
    sys.path.insert(0, str(_MULTI_DIR))

import multi_judge_aggregate as agg_mod  # noqa: E402
import multi_judge_arbitrator as arb_mod  # noqa: E402

# 显式导入所需函数 / 常量
from multi_judge_arbitrator import (  # noqa: E402
    map_star,
    arbitrate,
    peer_aware_review,
    run_arbitration,
    _reconcile_and_recompute,
    _independent_groups,
    _build_review_user,
    _build_consensus_user,
    _scan_self_contradiction,
    CONSENSUS_SYSTEM,
    derive_sub_classification,
)


# ---------------------------------------------------------------------------
# 原 _self_test（multi_judge_arbitrator.py:1036-1214）
# ---------------------------------------------------------------------------
def test_main() -> None:
    # 1) map_star 对照反向星级表
    assert map_star("full", False) == 5
    assert map_star("full", True) == 4
    assert map_star("majority", False) == 3
    assert map_star("majority", True) == 2
    assert map_star("split", False) == 1
    assert map_star("single_source", False) == 1

    # 2) 0 存活降级路径（通过 arbitrate 注入假 call_llm）
    fake = {
        "agreement_level": "full", "final_verdict": "已落实",
        "final_matched_hlr": ["R1"], "inconsistent_attributes": [],
        "field_disagreements": [{"field": "verdict", "category": "key", "summary": "x"}],
        "final_analysis": "ok", "confidence": 0.9,
    }
    shards = {"j1": {"S1": {"verdict": "已落实", "matched_hlr": ["R1"], "name_match_status": True,
                            "hlr_identity": None, "coverage": "ok", "attributes": []}}}
    agg = agg_mod.aggregate(shards, {"j1": {}})
    orig = arb_mod.call_llm
    os.environ["X"] = "dummy-key"
    arb_mod.call_llm = lambda *a, **k: fake
    try:
        agg2 = arbitrate(agg, shards, {"model": "m", "base_url": "u", "api_key_env": "X"})
        agg_deg = agg_mod.aggregate({"j1": shards["j1"], "j2": {}},
                                    {"j1": {}, "j2": {}}, {}, n_configured=2)
        agg_deg2 = arbitrate(agg_deg, {"j1": shards["j1"], "j2": {}},
                             {"model": "m", "base_url": "u", "api_key_env": "X"})
    finally:
        arb_mod.call_llm = orig
        os.environ.pop("X", None)
    assert agg2["S1"]["aggregation"]["agreement"] == "single_source"
    assert agg2["S1"]["aggregation"]["star_rating"] == 5
    assert agg2["S1"]["aggregation"]["arbitrated"] is True
    assert agg_deg2["S1"]["aggregation"]["star_rating"] == 1
    assert agg_deg2["S1"]["aggregation"]["need_review"] is True

    # 2b) #3 合并式 field_disagreements
    sh = {
        "j1": {"S1": {"verdict": "已落实", "matched_hlr": ["R1"], "name_match_status": True,
                      "hlr_identity": {"bus": "A"}, "coverage": "ok",
                      "attributes": [{"attribute_class": "刷新率", "single_result": "属性不一致",
                                     "eoicd_value": "60", "hlr_value": "30"}]}},
        "j2": {"S1": {"verdict": "不一致", "matched_hlr": ["R2"], "name_match_status": True,
                      "hlr_identity": {"bus": "A"}, "coverage": "ok",
                      "attributes": [{"attribute_class": "刷新率", "single_result": "属性一致",
                                     "eoicd_value": "30", "hlr_value": "30"}]}},
    }
    meta = {"j1": {"model": "m", "base_url": "u", "key_hash": "k", "frozen": True},
            "j2": {"model": "m", "base_url": "u", "key_hash": "k", "frozen": True}}
    agg3 = agg_mod.aggregate(sh, meta, {}, n_configured=2)
    fd_before = {d["field"]: d for d in agg3["S1"]["aggregation"]["field_disagreements"]}
    assert "matched_hlr" in fd_before and "verdict" in fd_before and any(
        f.startswith("attr:") for f in fd_before), "后端应已产出含 values 的分歧"
    fake3 = {
        "agreement_level": "majority", "final_verdict": "不一致",
        "inconsistent_attributes": [{"attribute_class": "分辨率", "description": "LLM 新发现的事实不一致"}],
        "field_disagreements": [{"field": "verdict", "category": "non_key", "summary": "语义同属未落实类"}],
        "final_analysis": "merged", "confidence": 0.8,
    }
    orig3 = arb_mod.call_llm
    os.environ["X"] = "dummy-key"
    arb_mod.call_llm = lambda *a, **k: fake3
    try:
        agg3r = arbitrate(agg3, sh, {"model": "m", "base_url": "u", "api_key_env": "X"})
    finally:
        arb_mod.call_llm = orig3
        os.environ.pop("X", None)
    fd_after = {d["field"]: d for d in agg3r["S1"]["aggregation"]["field_disagreements"]}
    assert "matched_hlr" in fd_after, "合并后不得丢失后端 matched_hlr 分歧"
    assert "values" in fd_after["matched_hlr"], "后端 values 必须保留"
    assert fd_after["verdict"]["category"] == "non_key"
    assert fd_after["verdict"]["summary"] == "语义同属未落实类"
    assert fd_after["verdict"]["values"], "verdict 的 values 必须保留"
    assert any(f.startswith("attr:") for f in fd_after), "attr 分歧不得丢失"
    _ia = agg3r["S1"]["aggregation"]["inconsistent_attributes"]
    assert len(_ia) == 1 and _ia[0]["attribute_class"] == "刷新率", \
        f"LLM 越界属性类应被过滤、仅留后端真实条目：{_ia}"
    _dr = agg3r["S1"]["aggregation"].get("llm_attributes_dropped") or []
    assert any(d.get("attribute_class") == "分辨率" for d in _dr), \
        f"被丢弃的 LLM 条目应留痕于 llm_attributes_dropped：{_dr}"
    print("#3 合并式 field_disagreements / inconsistent_attributes 通过（含 LLM 越界属性类过滤 + 留痕）")

    # 3) #9 peer 复查
    def _e9(v: str) -> dict:
        return {"verdict": v, "matched_hlr": ["R1"], "name_match_status": True,
                "hlr_identity": {"bus": "1"}, "coverage": "ok",
                "attributes": [{"attribute_class": "周期", "single_result": "属性不一致",
                                "eoicd_value": "1", "hlr_value": "2"}]}

    shards9 = {"judge_ds": _e9("不一致"), "judge_ds_stability": _e9("不一致"), "judge_qwen": _e9("已落实")}
    meta9 = {
        "judge_ds": {"model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"},
        "judge_ds_stability": {"model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"},
        "judge_qwen": {"model": "qwen-max", "base_url": "https://dashscope.aliyuncs.com/v1"},
    }
    g9 = _independent_groups(list(shards9), meta9)
    assert [len(x) for x in g9] == [2, 1], g9
    assert [len(x) for x in _independent_groups(["j1", "j2"], {})] == [1, 1]

    u9 = _build_review_user("S3", g9, shards9)
    assert "判断 A" in u9 and "判断 B" in u9, u9
    assert "判断 C" not in u9, "不得出现克隆的第三个判断（#9）"
    assert "共 2 份同模型重复" in u9, u9
    assert "重复不构成独立共识" in u9, u9

    sh_same = {"a": {"S3": _e9("不一致")}, "b": {"S3": _e9("已落实")}}
    meta_same = {"a": meta9["judge_ds"], "b": meta9["judge_ds_stability"]}
    agg_same = agg_mod.aggregate(sh_same, meta_same, {}, n_configured=2)
    orig9 = arb_mod.call_llm
    os.environ["AR9"] = "dummy-key"
    arb_mod.call_llm = lambda *a, **k: {"final_verdict": "已落实", "final_matched_hlr": ["R9"],
                                         "attribute_reconciled": [], "analysis": "x", "confidence": 0.9}
    try:
        out_same = peer_aware_review(agg_same, sh_same,
                                     {"model": "m", "base_url": "u", "api_key_env": "AR9"},
                                     meta_same)
        sh_ok = {"a": {"S3": _e9("不一致")},
                 "b": {"S3": _e9("不一致")},
                 "c": {"S3": _e9("已落实")}}
        meta_ok = {"a": meta9["judge_ds"],
                   "b": meta9["judge_ds_stability"],
                   "c": meta9["judge_qwen"]}
        agg_ok = agg_mod.aggregate(sh_ok, meta_ok, {}, n_configured=3)
        out_ok = peer_aware_review(agg_ok, sh_ok,
                                   {"model": "m", "base_url": "u", "api_key_env": "AR9"},
                                   meta_ok)
    finally:
        arb_mod.call_llm = orig9
        os.environ.pop("AR9", None)

    a_same = out_same["S3"]["aggregation"]
    assert a_same["peer_reviewed"] is False
    assert "独立视角数 1" in a_same["peer_review_info"]["reason"], a_same["peer_review_info"]
    a_ok = out_ok["S3"]["aggregation"]
    assert a_ok["peer_reviewed"] is True
    assert a_ok["peer_review_info"]["independent_views"] == 2, a_ok["peer_review_info"]
    assert a_ok["peer_review_info"]["judges_by_view"][0]["judges"] == ["a", "b"]

    # 3e) #10 精简签名
    orig10 = arb_mod.call_llm
    os.environ["AR10"] = "dummy-key"
    arb_mod.call_llm = lambda *a, **k: {"final_verdict": "不一致", "final_matched_hlr": ["R1"],
                                         "attribute_reconciled": [], "analysis": "x", "confidence": 0.9}
    try:
        cfg10 = {"model": "m", "base_url": "u", "api_key_env": "AR10",
                 "re_review": {"enabled": True, "max_rounds": 1}}
        out10 = run_arbitration(agg_mod.aggregate(sh_ok, meta_ok, {}, n_configured=3),
                                sh_ok, meta_ok, cfg10)
    finally:
        arb_mod.call_llm = orig10
        os.environ.pop("AR10", None)
    a10 = out10["S3"]["aggregation"]
    assert a10["peer_review_info"]["independent_views"] == 2, a10["peer_review_info"]
    assert (a10.get("review_trace") or {}).get("peer_review", {}).get("applied") is True, a10["review_trace"]

    blk = _build_consensus_user("S2", {"j1": shards9["judge_ds"]}, "note")
    assert "S2" in blk and "j1" in blk

    print("#9 独立视角分组匿名化复查通过（无克隆 C / 同模型合并 / 视角不足跳过并记账）")
    print("#10 run_arbitration 精简签名通过（4 参数；re_review 并入 arb_cfg 照常驱动复查链路）")


# ---------------------------------------------------------------------------
# 原 _self_test_reconcile（multi_judge_arbitrator.py:1217-1270）
# ---------------------------------------------------------------------------
def test_reconcile() -> None:
    """方案Y 单测：仲裁回写清除 + 派生字段重算 + need_review 永真修复。"""
    # 1) 仲裁判一致 → 清行
    a = {
        "agreement": "majority",
        "field_disagreements": [{"field": "attr:RefreshPeriod", "category": "key",
                                 "values": {"gpt": "属性不一致", "ds": "无提及"}}],
        "inconsistent_attributes": [{"attribute_class": "RefreshPeriod", "judges": ["gpt"],
                                     "eoicd_value": "1000", "hlr_value": "1000ms"}],
        "need_review_rules": ["low_star_under_multijudge"],
        "coverage_gap": None,
        "star_rating": 2,
        "need_review": True,
    }
    out = {"attribute_reconciled": [{"attribute_class": "RefreshPeriod",
                                     "single_result": "属性一致", "reason": "数值相等仅单位差异"}]}
    _reconcile_and_recompute(a, out)
    assert a["inconsistent_attributes"] == [], a["inconsistent_attributes"]
    assert a["field_disagreements"] == [], a["field_disagreements"]
    assert a["star_rating"] == 3, a["star_rating"]
    assert a["need_review"] is False, a["need_review"]

    # 2) 无仲裁回写但重算后 need_review 不应被旧值焊死
    a2 = {"agreement": "full", "field_disagreements": [], "inconsistent_attributes": [],
          "need_review_rules": ["low_star_under_multijudge"], "coverage_gap": None,
          "star_rating": 5, "need_review": True}
    _reconcile_and_recompute(a2, {})
    assert a2["need_review"] is False, a2["need_review"]

    # 3) coverage_gap / no_valid_source 合法标记仍保留
    a3 = {"agreement": "full", "field_disagreements": [], "inconsistent_attributes": [],
          "need_review_rules": ["low_star_under_multijudge"], "coverage_gap": True,
          "star_rating": 5, "need_review": True}
    _reconcile_and_recompute(a3, {})
    assert a3["need_review"] is True, a3["need_review"]
    a4 = {"agreement": "full", "field_disagreements": [], "inconsistent_attributes": [],
          "need_review_rules": ["no_valid_source"], "coverage_gap": None,
          "star_rating": 5, "need_review": True}
    _reconcile_and_recompute(a4, {})
    assert a4["need_review"] is True, a4["need_review"]

    # 4) 未判一致的属性不被误删
    a5 = {"agreement": "majority",
          "field_disagreements": [{"field": "attr:RealDiff", "category": "key",
                                    "values": {"gpt": "属性不一致", "ds": "属性不一致"}}],
          "inconsistent_attributes": [{"attribute_class": "RealDiff", "judges": ["gpt", "ds"],
                                       "eoicd_value": "2", "hlr_value": "1"}],
          "need_review_rules": ["low_star_under_multijudge"], "coverage_gap": None,
          "star_rating": 2, "need_review": True}
    _reconcile_and_recompute(a5, {"attribute_reconciled": []})
    assert len(a5["inconsistent_attributes"]) == 1, a5["inconsistent_attributes"]
    assert a5["need_review"] is True, a5["need_review"]

    print("方案Y _reconcile_and_recompute 通过（清行 + need_review 永真修复 + 合法标记保留 + 不误删）")


# ---------------------------------------------------------------------------
# 原 _self_test_d1（multi_judge_arbitrator.py:1273-1437）
# ---------------------------------------------------------------------------
def test_d1() -> None:
    """D1 单测：Step5 结构化纠错出口 + Step5.5 挂钩清行 + 零成本自相矛盾兜底扫描。"""
    # 1) Step5 prompt 已给出结构化纠错出口
    assert "attribute_reconciled" in CONSENSUS_SYSTEM, "Step5 prompt 缺 attribute_reconciled 出口"
    assert "wrong_judges" in CONSENSUS_SYSTEM, "Step5 prompt 缺 wrong_judges（逐 judge 纠错）"
    assert "不要再重复展开" in CONSENSUS_SYSTEM, "Step5 prompt 缺 token 约束"

    def _mk_agg() -> dict:
        return {
            "S1": {
                "judges": {"judge_A": {"coverage": "ok"}, "judge_B": {"coverage": "ok"}},
                "aggregation": {
                    "star_rating": 2, "agreement": "majority", "final_verdict": "不一致",
                    "need_review": True, "need_review_rules": ["low_star_under_multijudge"],
                    "coverage_gap": None, "note": "",
                    "inconsistent_attributes": [
                        {"attribute_class": "DP.OneState", "judges": ["judge_C"],
                         "eoicd_value": "1", "hlr_value": "0", "results": {"judge_C": "属性不一致"}},
                    ],
                    "field_disagreements": [
                        {"field": "attr:DP.OneState", "category": "key",
                         "values": {"judge_A": "属性一致", "judge_C": "属性不一致"}},
                    ],
                    "attr_consensus_detail": {"DP.OneState": {"consensus": "分歧"}},
                    "structured_diff": [{"attribute_class": "DP.OneState", "diff_type": "属性不一致"}],
                },
            }
        }

    _entry = {"verdict": "不一致", "matched_hlr": [["R1", "1|0x10|1|pub"]],
              "name_match_status": True,
              "hlr_identity": {"bus": "1", "label": "0x10", "bit": "1", "direction": "pub", "name": "N"},
              "coverage": "ok",
              "attributes": [{"attribute_class": "DP.OneState", "single_result": "属性不一致",
                              "eoicd_value": "1", "hlr_value": "0", "matched_hlr_req_id": "R1"}]}
    shards = {"judge_A": {"S1": _entry}, "judge_B": {"S1": _entry}}
    meta = {"judge_A": {"model": "m1", "base_url": "b1"},
            "judge_B": {"model": "m2", "base_url": "b2"}}

    # 2) Step5.5 挂钩
    os.environ["D1KEY"] = "k"
    orig = arb_mod.call_llm
    arb_mod.call_llm = lambda *a, **k: {
        "final_verdict": "已落实", "final_matched_hlr": ["R1"],
        "attribute_reconciled": [{"attribute_class": "DP.OneState", "single_result": "属性一致",
                                  "wrong_judges": ["judge_C"], "reason": "bit 对应关系识别错误"}],
        "analysis": "judge_C 误判 DP.OneState", "confidence": 0.9}
    try:
        out_peer = peer_aware_review(_mk_agg(), shards,
                                     {"model": "m", "base_url": "u", "api_key_env": "D1KEY"}, meta)
    finally:
        arb_mod.call_llm = orig
        os.environ.pop("D1KEY", None)
    ap = out_peer["S1"]["aggregation"]
    assert ap["peer_reviewed"] is True, ap.get("peer_review_info")
    assert ap["inconsistent_attributes"] == [], ap["inconsistent_attributes"]
    assert not any(d.get("field") == "attr:DP.OneState" for d in ap["field_disagreements"]), \
        ap["field_disagreements"]
    assert ap["attribute_rulings"][0]["wrong_judges"] == ["judge_C"], ap.get("attribute_rulings")
    assert ap["star_rating"] > 1, ap["star_rating"]

    # 3) 零成本兜底扫描
    hit = _scan_self_contradiction({
        "final_verdict": "部分落实",
        "final_analysis": "judge_C 因未正确识别 bit 与 RFAN 的对应关系而误判 DP.OneState。",
        "inconsistent_attributes": [{"attribute_class": "DP.OneState"}],
        "field_disagreements": [{"field": "attr:DP.OneState", "category": "key"}],
    })
    assert hit and hit["detected"] and hit["attributes"] == ["DP.OneState"], hit

    hit_b = _scan_self_contradiction({
        "final_verdict": "部分落实",
        "final_analysis": "judge_C 因未精准识别 bit24 与 RFAN 的对应关系而误判属性不一致。",
        "inconsistent_attributes": [{"attribute_class": "DP.OneState"}],
    })
    assert hit_b and hit_b["detected"] and hit_b["attributes"] == [], hit_b

    assert _scan_self_contradiction({
        "final_verdict": "不一致",
        "final_analysis": "judge_C 指出判断 B 的识别错误，最终结论为不一致。",
        "inconsistent_attributes": [{"attribute_class": "DP.OneState"}],
    }) is None

    assert _scan_self_contradiction({
        "final_verdict": "部分落实",
        "final_analysis": "DP.OneState 取值不一致，判定为部分落实。",
        "inconsistent_attributes": [{"attribute_class": "DP.OneState"}],
    }) is None

    assert _scan_self_contradiction({
        "final_verdict": "部分落实",
        "final_analysis": "judge_C 误判 DP.OneState。",
        "inconsistent_attributes": [],
        "field_disagreements": [],
    }) is None

    # 4) 端到端接线
    os.environ["D1KEY2"] = "k"
    orig2 = arb_mod.call_llm
    arb_mod.call_llm = lambda *a, **k: {
        "agreement_level": "majority", "final_verdict": "部分落实", "final_matched_hlr": ["R1"],
        "inconsistent_attributes": [],
        "final_analysis": "judge_C 误判 DP.OneState，其实一致。", "confidence": 0.9}
    try:
        out_run = run_arbitration(_mk_agg(), shards, meta,
                                  {"model": "m", "base_url": "u", "api_key_env": "D1KEY2",
                                   "re_review": {"enabled": False}})
    finally:
        arb_mod.call_llm = orig2
        os.environ.pop("D1KEY2", None)
    ar = out_run["S1"]["aggregation"]
    assert (ar.get("self_contradiction") or {}).get("detected") is True, ar.get("self_contradiction")
    assert "DP.OneState" in ar["self_contradiction"]["attributes"], ar["self_contradiction"]
    assert (ar.get("review_trace") or {}).get("self_contradiction"), ar.get("review_trace")
    assert "待人工确认" in ar["analysis_summary"], ar["analysis_summary"]

    # 5) 子类重算
    _agg5 = {
        "S2": {
            "judges": {
                "judge_A": {"coverage": "ok", "matched_hlr": [["R1", "1|0x10|1|pub"]], "name_match_status": True},
                "judge_B": {"coverage": "ok", "matched_hlr": [["R1", "1|0x10|1|pub"]], "name_match_status": True},
            },
            "aggregation": {
                "star_rating": 2, "agreement": "majority", "final_verdict": "不一致",
                "need_review": True, "need_review_rules": ["low_star_under_multijudge"],
                "coverage_gap": None, "note": "",
                "inconsistent_attributes": [], "field_disagreements": [],
                "attr_consensus_detail": {}, "structured_diff": [],
                "sub_classification": None,
            },
        }
    }
    os.environ["D1KEY3"] = "k"
    orig3 = arb_mod.call_llm
    arb_mod.call_llm = lambda *a, **k: {
        "agreement_level": "majority", "final_verdict": "未落实", "final_matched_hlr": ["R1"],
        "inconsistent_attributes": [], "final_analysis": "双方识别到 HLR 但属性未承接。", "confidence": 0.9}
    try:
        out5 = run_arbitration(_agg5, {"judge_A": {"S2": _entry}, "judge_B": {"S2": _entry}},
                               {"judge_A": {"model": "m1", "base_url": "b1"},
                                "judge_B": {"model": "m2", "base_url": "b2"}},
                               {"model": "m", "base_url": "u", "api_key_env": "D1KEY3",
                                "re_review": {"enabled": False}})
    finally:
        arb_mod.call_llm = orig3
        os.environ.pop("D1KEY3", None)
    a5 = out5["S2"]["aggregation"]
    assert a5["final_verdict"] == "未落实", a5["final_verdict"]
    assert a5["sub_classification"] == "已识别未承接", (
        f"改判为未落实后子类未重算（应为 已识别未承接，实际 {a5['sub_classification']!r}）")
    assert derive_sub_classification("已落实", {"judge_A": {"coverage": "ok"}}) is None

    print("D1 通过（Step5 结构化出口 + Step5.5 挂钩清行 + 裁定留痕 + 兜底扫描命中/不误报/端到端接线"
          " + 改判后子类重算）")


def run_all() -> None:
    """入口：跑所有仲裁层自测。"""
    test_main()
    test_reconcile()
    test_d1()


if __name__ == "__main__":
    run_all()
