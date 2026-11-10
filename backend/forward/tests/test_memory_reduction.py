"""
test_memory_reduction.py
========================
2026-11-09 服务器 OOM 治理的三项自测（①②③）。

背景：单 judge 的 summary 阶段整树读取 446MB 的属性匹配报告，
实测 json.load 峰值 **2230MB**；3 judge 并发 ⇒ 8G 服务器会 OOM。

① summary 流式读取大报告（common/jsonstream.py）
   峰值 2230MB → ~20MB；产出必须与整体读取**逐字段完全一致（含顺序）**。
② 交付物去纯追溯字段（missing_in_hlrs / signal_id_matched_hlrs）
   占 attributes 的 98%；下游（聚合层 / 报告层）均不读 ⇒ 可剥离。
③ attr_clustered 中间态在冲突处理消费完后删除（分步续跑时保留）。

本测试用**构造的小样本**（非真实 446MB 文件）跑，秒级完成；
真实数据的等价性由端到端 mock 跑验证。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent

if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))


def _load_summary_module():
    """按路径加载 单judge结果汇总/summary.py（它不在包内）。"""
    spec = importlib.util.spec_from_file_location(
        "_summary_under_test", _PROJECT / "单judge结果汇总" / "summary.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_report(path: Path, n_signals: int = 3) -> None:
    """构造一个结构与真实属性匹配报告一致的小样本。

    ★顶层键的**物理顺序**刻意与真实文件一致：
      meta / mismatch_by_attribute / matched / mismatch / null_value /
      not_mentioned / clustering
    （真实文件里 mismatch 在 matched 之前，而下游规范顺序是
      matched → mismatch → null_value → not_mentioned —— 这个错位正是
      流式实现最容易踩的坑，必须在测试里覆盖。）
    """
    report = {
        "meta": {"direction": "pub", "summary": {"matched": 2, "not_mentioned": 4}},
        "mismatch_by_attribute": {"Attr.X": 1},
        "matched": [
            {"eoicd_cluster_id": "C1", "attribute": "Attr.X", "req_id": "R1",
             "hlr_value": "v1", "hlr_identity": {"bus": "B", "label": "L",
                                                "name": "N", "bit": "0",
                                                "direction": "out"},
             "eoicd_signal_name": "SIG.S1", "eoicd_signal_short_name": "S1",
             "eoicd_value": "v1", "rule": "eq"},
        ],
        "mismatch": [
            {"eoicd_cluster_id": "C2", "attribute": "Attr.Y", "req_id": "R2",
             "hlr_value": "v2", "hlr_identity": {"bus": "B", "label": "L",
                                                "name": "N", "bit": "1",
                                                "direction": "out"},
             "eoicd_signal_name": "SIG.S2", "eoicd_signal_short_name": "S2",
             "eoicd_value": "v2", "rule": "ne"},
        ],
        "null_value": [
            {"eoicd_cluster_id": "C3", "attribute": "Attr.Z", "req_id": "R3",
             "hlr_value": None, "hlr_identity": {"bus": "B", "label": "L",
                                                "name": "N", "bit": "2",
                                                "direction": "out"},
             "eoicd_signal_name": "SIG.S3", "eoicd_signal_short_name": "S3",
             "eoicd_value": None},
        ],
        "not_mentioned": {
            "not_mentioned_by_signal": [
                {
                    "eoicd_cluster_id": "C4",
                    "eoicd_signal_name": "SIG.S4",
                    "eoicd_signal_short_name": "S4",
                    "id_matched_hlrs": ["R4", "R5"],
                    "not_mentioned_count": 2,
                    "not_mentioned_attributes": [
                        {"attribute": "Attr.A", "eoicd_value": "a",
                         "missing_in_hlrs": [
                             {"req_id": "R4", "hlr_identity": {"name": "N4"}},
                             {"req_id": "R5", "hlr_identity": {"name": "N5"}},
                         ]},
                        {"attribute": "Attr.B", "eoicd_value": "b",
                         "missing_in_hlrs": []},
                    ],
                },
            ],
        },
        "clustering": {"total_signals": n_signals},
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def test_stream_equals_whole() -> None:
    """① 流式读取与整体读取产出**逐字段完全一致（含顺序）**。"""
    mod = _load_summary_module()
    tmp = Path(tempfile.mkdtemp())
    try:
        rep = tmp / "attribute_match_report_pub.json"
        _make_report(rep)

        raw = mod.load_json(str(rep))
        whole = mod.normalize_attribute_match(raw)
        stream = mod.normalize_attribute_match_streaming(str(rep))

        assert whole["meta"] == stream["meta"], "meta 应完全一致"
        assert len(whole["comparisons"]) == len(stream["comparisons"]), \
            "comparisons 条数应一致：%d vs %d" % (len(whole["comparisons"]),
                                                  len(stream["comparisons"]))
        # 逐条逐字段比对（含顺序）
        for i, (a, b) in enumerate(zip(whole["comparisons"], stream["comparisons"])):
            assert a == b, "第 %d 条 comparison 不一致:\n  整体=%s\n  流式=%s" % (
                i, json.dumps(a, ensure_ascii=False)[:200],
                json.dumps(b, ensure_ascii=False)[:200])

        # 规范顺序必须是 matched → mismatch → null_value → not_mentioned
        # （文件物理顺序里 mismatch 在 matched 之前，流式实现必须归位）
        results = [c["result"] for c in stream["comparisons"]]
        assert results == ["一致", "不一致", "无具体值", "未提及", "未提及"], \
            "comparisons 顺序应与规范顺序一致，实际: %s" % results

        # run_direction 的产出也必须一致（判定结论零变化）
        cfg = mod.load_cfg(str(_PROJECT / "单judge结果汇总" / "config_summary.json"))
        out_w = mod.run_direction(whole, cfg)
        out_s = mod.run_direction(stream, cfg)
        assert out_w["meta"] == out_s["meta"], "run_direction 的 meta 应一致"

        def key_of(o):
            return {s["signal_full_name"]: {
                "final_classification": s["final_classification"],
                "sub_classification": s.get("sub_classification"),
                "name_match_status": s.get("name_match_status"),
                "matched_hlrs": s.get("matched_hlrs"),
                "attr_summary": s.get("attr_summary"),
                "analysis": s.get("analysis"),
                "attributes": s.get("attributes"),
            } for s in o["signals"]}

        kw, ks = key_of(out_w), key_of(out_s)
        assert set(kw) == set(ks), "信号集合应一致"
        diff = [k for k in kw if kw[k] != ks.get(k)]
        assert not diff, "以下信号的产出不一致: %s" % diff[:3]
        print("①summary 流式读取自测通过"
              "（comparisons 逐字段一致 / 顺序归位 / run_direction 产出零差异）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_trace_fields_stripped() -> None:
    """② 交付物去纯追溯字段：默认剥离；开关开启保留完整版。"""
    mod = _load_summary_module()
    tmp = Path(tempfile.mkdtemp())
    try:
        rep = tmp / "attribute_match_report_pub.json"
        _make_report(rep)
        stream = mod.normalize_attribute_match_streaming(str(rep))
        cfg = mod.load_cfg(str(_PROJECT / "单judge结果汇总" / "config_summary.json"))

        # ① 默认（keep_trace_fields 未开或 False）：剥离两个追溯字段
        out_slim = mod.run_direction(stream, dict(cfg, keep_trace_fields=False))
        slim_attrs = [a for s in out_slim["signals"] for a in s.get("attributes", [])]
        assert slim_attrs, "应至少有一条属性"
        for a in slim_attrs:
            assert "missing_in_hlrs" not in a, "瘦身模式不应保留 missing_in_hlrs"
            assert "signal_id_matched_hlrs" not in a, \
                "瘦身模式不应保留 signal_id_matched_hlrs"
        # 判定结论不受影响
        for s in out_slim["signals"]:
            assert s["final_classification"] in ("已落实", "不一致", "未落实", "部分落实")

        # ② 开关开启：恢复完整追溯
        out_full = mod.run_direction(stream, dict(cfg, keep_trace_fields=True))
        full_attrs = [a for s in out_full["signals"] for a in s.get("attributes", [])]
        assert any("missing_in_hlrs" in a for a in full_attrs), \
            "开关开启时应保留 missing_in_hlrs"
        assert any("signal_id_matched_hlrs" in a for a in full_attrs), \
            "开关开启时应保留 signal_id_matched_hlrs"

        # ③ 两种模式的判定结论必须完全一致（剥离只影响体积，不影响判定）
        def verdicts(o):
            return {s["signal_full_name"]: s["final_classification"]
                    for s in o["signals"]}
        assert verdicts(out_slim) == verdicts(out_full), \
            "剥离追溯字段不应改变判定结论"

        # ④ matched_hlrs 不受影响（它由 signal_id_matched_hlrs 派生，
        #    剥离发生在其构造之后 ⇒ 必须仍然完整）
        for s in out_slim["signals"]:
            if s["signal_full_name"].endswith("SIG.S4"):
                assert s["matched_hlrs"], "signal_id_matched_hlrs 派生的 matched_hlrs 必须保留"
        print("②交付物去追溯字段自测通过"
              "（默认剥离 / 开关保留 / 判定结论不变 / matched_hlrs 不受影响）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_clustered_cleanup() -> None:
    """③ attr_clustered 在冲突处理消费完后删除；分步续跑时保留。"""
    mod_dir = (_PROJECT / "EoICD-HLR正向匹配与属性比对_支持追溯"
               / "attribute_match")
    spec = importlib.util.spec_from_file_location(
        "_run_attr_match_under_test", mod_dir / "run_attribute_match.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    tmp = Path(tempfile.mkdtemp())
    try:
        clustered = tmp / "attr_clustered_pub.json"
        clustered.write_text("{}", encoding="utf-8")

        class _Args:
            step = "all"

        # ① step=all：删除
        mod._cleanup_clustered(str(clustered), "pub", _Args(), {})
        assert not clustered.exists(), "step=all 时应删除 attr_clustered"

        # ② step=attribute_match：保留（后续要单独跑冲突处理）
        clustered.write_text("{}", encoding="utf-8")
        _Args.step = "attribute_match"
        mod._cleanup_clustered(str(clustered), "pub", _Args(), {})
        assert clustered.exists(), "step=attribute_match 时应保留 attr_clustered"

        # ③ step=conflict_resolution：保留（该阶段以它为输入）
        _Args.step = "conflict_resolution"
        mod._cleanup_clustered(str(clustered), "pub", _Args(), {})
        assert clustered.exists(), "step=conflict_resolution 时应保留 attr_clustered"

        # ④ 开关 keep_clustered_intermediate=true：一律保留
        _Args.step = "all"
        mod._cleanup_clustered(str(clustered), "pub", _Args(),
                               {"output": {"keep_clustered_intermediate": True}})
        assert clustered.exists(), "开关开启时应保留 attr_clustered"

        # ⑤ 文件不存在时幂等（不抛异常）
        clustered.unlink()
        mod._cleanup_clustered(str(clustered), "pub", _Args(), {})
        print("③attr_clustered 清理自测通过"
              "（step=all 删除 / 分步续跑保留 / 开关保留 / 文件缺失幂等）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_all() -> None:
    """入口：跑 ①②③ 相关的全部自测。"""
    test_stream_equals_whole()
    test_trace_fields_stripped()
    test_clustered_cleanup()


if __name__ == "__main__":
    run_all()
    print("[OK] test_memory_reduction 全部自测通过")
