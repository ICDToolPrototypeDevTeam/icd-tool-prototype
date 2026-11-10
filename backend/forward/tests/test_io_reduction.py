"""
test_io_reduction.py
====================

①「retained_* 中间态默认不落盘」与 ③「去掉两处纯拷贝」的行为自测。

背景（2026-11-03）：单 judge 的中间产物在 hscu/ams 上可达 3.4GB，其中
  · retained_bus_label_bit / retained_direction / retained_name 六份  ≈ 614MB
    —— 阶段间**已在内存里直接传参**，全流程（--step all）下写完从不被读，
       落盘仅为「分步续跑」准备；
  · summary/input/ 下的 attribute_match_report_*.json 拷贝 ≈ 513MB
    —— 汇总模块支持 --match 直接指向原始位置，拷贝纯占盘；
  · shared/eoicd_processed/ 整目录拷贝 ≈ 40MB
    —— judge 只用到其中 4 个 *_clustered*.json，其余只有阶段1自己用。

这些测试锁住「不再产生」的行为，避免后续改动把拷贝/落盘加回来。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
_MATCH_DIR = _PROJECT / "EoICD-HLR正向匹配与属性比对_支持追溯" / "identity_match"

if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))


def _load_identity_module():
    """按文件路径加载 run_identity_match.py（它不在包内，且会自改 sys.path）。"""
    spec = importlib.util.spec_from_file_location(
        "_run_identity_match_under_test", _MATCH_DIR / "run_identity_match.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_retained_save_policy() -> None:
    """①retained_* 落盘判定：全流程默认不落盘；分步续跑必须落盘；配置可强制开启。"""
    mod = _load_identity_module()
    resolve = mod._resolve_save_retained

    # ①-a 全流程（--step all）：默认不落盘（阶段间内存直传，落盘写完从不被读）
    assert resolve({"output": {}}, "all") is False, "全流程默认应不落盘"
    # 配置显式开启 → 落盘（调试/留证）
    assert resolve({"output": {"save_retained_intermediate": True}}, "all") is True
    # 显式关闭 → 不落盘
    assert resolve({"output": {"save_retained_intermediate": False}}, "all") is False

    # ①-b 分步续跑：**必须落盘**，否则下一步读不到上一步结果
    for st in ("bus_label_bit", "direction", "name", "report"):
        assert resolve({"output": {}}, st) is True, f"--step {st} 必须落盘"
        # 即使配置写了 false，分步续跑也要落盘（否则续跑断链）
        assert resolve({"output": {"save_retained_intermediate": False}}, st) is True

    # ①-c 缺 output 段也不崩
    assert resolve({}, "all") is False
    assert resolve({}, "direction") is True
    print("①retained_* 落盘判定自测通过"
          "（全流程默认不落盘 / 分步续跑强制落盘 / 配置可强制开启）")


def test_prepare_summary_input_no_copy() -> None:
    """③a summary/input 不再拷贝属性匹配报告（改为 --match 指向原始位置）。"""
    import run_integration as ri

    _tmp = Path(tempfile.mkdtemp())
    try:
        ws = _tmp / "ws"
        attr_out = ws / "data" / "output" / "attribute_match"
        attr_out.mkdir(parents=True)
        # 造两个"属性匹配报告"（体积小即可，只验证不产生拷贝）
        (attr_out / "attribute_match_report_pub.json").write_text("{}", encoding="utf-8")
        (attr_out / "attribute_match_report_sub.json").write_text("{}", encoding="utf-8")

        rejected_dir = ws / "data" / "output" / "identity_match" / "rejected"
        rejected_dir.mkdir(parents=True)
        (rejected_dir / "rejected_name_pub.json").write_text("{}", encoding="utf-8")
        (rejected_dir / "rejected_direction_pub.json").write_text("{}", encoding="utf-8")
        # 非 rejected_ 前缀的文件不应被复制
        (rejected_dir / "other.txt").write_text("x", encoding="utf-8")

        hlr_doc = _tmp / "测试设备高层需求规范.docx"
        hlr_doc.write_bytes(b"dummy")

        class _Args:
            hlr = str(hlr_doc)

        ri.prepare_summary_input(_Args(), ws)

        summary_input = ws / "summary" / "input"
        # ③a 关键断言：属性匹配报告**没有**被复制到 summary/input
        assert not (summary_input / "attribute_match_report_pub.json").exists(), \
            "属性匹配报告不应再被拷贝到 summary/input"
        assert not (summary_input / "attribute_match_report_sub.json").exists(), \
            "属性匹配报告不应再被拷贝到 summary/input"
        # 仍需保留：rejected（完整性兜底，summary.py 硬编码从 input/rejected 读）
        assert (summary_input / "rejected" / "rejected_name_pub.json").exists(), \
            "rejected 文件必须保留（完整性兜底依赖）"
        assert (summary_input / "rejected" / "rejected_direction_pub.json").exists()
        assert not (summary_input / "rejected" / "other.txt").exists(), \
            "非 rejected_ 前缀的文件不应被复制"
        # 仍需保留：HLR 文档（summary.py 从 input 下识别设备名前缀）
        assert (summary_input / hlr_doc.name).exists(), "HLR 文档必须保留（设备名识别依赖）"

        print("③a summary/input 去拷贝自测通过"
              "（属性报告不拷贝 / rejected 与 HLR 文档保留）")
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)


def test_judge_no_eoicd_processed_copy() -> None:
    """③b runner 不再把 shared/eoicd_processed/ 整目录拷进 judge 工作区。"""
    import multi_judge_runner as mj

    src = Path(mj.__file__).read_text(encoding="utf-8")
    # 不应再有把 shared/eoicd_processed 拷进 judge 的 copytree
    assert "src_eoicd" not in src, "runner 不应再拷贝 shared/eoicd_processed/"
    assert 'shutil.copytree(src_eoicd' not in src
    # 应改为传参引用 shared
    assert "--eoicd-processed-dir" in src, "runner 应通过 --eoicd-processed-dir 引用 shared"
    print("③b eoicd_processed 去拷贝自测通过（改为 --eoicd-processed-dir 引用 shared）")


def test_run_integration_eoicd_processed_dir() -> None:
    """③b run_integration 支持 --eoicd-processed-dir（只读引用外部阶段1产物）。"""
    import run_integration as ri

    src = Path(ri.__file__).read_text(encoding="utf-8")
    assert "--eoicd-processed-dir" in src
    assert "args.eoicd_processed_dir" in src
    print("③b run_integration --eoicd-processed-dir 参数自测通过")


def test_not_mentioned_cleanup() -> None:
    """②属性比对中间态：聚类读完 attr_not_mentioned 后删除；attr_clustered 必须保留。

    依据：全工程仅 attr_result_clustering.py 读 attr_not_mentioned（读一次即弃）；
    而 attr_clustered 是冲突处理阶段（--step conflict_resolution）的输入，必须保留。
    """
    import importlib.util

    src_dir = (_PROJECT / "EoICD-HLR正向匹配与属性比对_支持追溯"
               / "attribute_match" / "src")
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    spec = importlib.util.spec_from_file_location(
        "_attr_clustering_under_test", src_dir / "attr_result_clustering.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    _tmp = Path(tempfile.mkdtemp())
    try:
        out_dir = _tmp / "out"
        out_dir.mkdir()
        nm = out_dir / "attr_not_mentioned_pub.json"
        cl = out_dir / "attr_clustered_pub.json"
        nm.write_text(json.dumps({"meta": {}, "not_mentioned_by_signal": []}),
                      encoding="utf-8")
        cl.write_text("{}", encoding="utf-8")

        # ① 默认：删掉 attr_not_mentioned，保留 attr_clustered
        mod._cleanup_consumed_intermediates(str(out_dir), "pub", {})
        assert not nm.exists(), "attr_not_mentioned 应在聚类消费后被删除"
        assert cl.exists(), "attr_clustered 必须保留（冲突处理阶段要读）"

        # ② 开关开启：保留（调试/留证）
        nm.write_text("{}", encoding="utf-8")
        mod._cleanup_consumed_intermediates(
            str(out_dir), "pub", {"output": {"keep_not_mentioned_intermediate": True}})
        assert nm.exists(), "开关开启时 attr_not_mentioned 应保留"

        # ③ 文件不存在时不报错（幂等）
        nm.unlink()
        mod._cleanup_consumed_intermediates(str(out_dir), "pub", {})
        print("②attr_not_mentioned 清理自测通过"
              "（默认删除 / 开关开启保留 / 文件缺失幂等 / attr_clustered 始终保留）")
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)


def test_match_report_slim() -> None:
    """B写入端瘦身：matched_hlrs 只保留下游必需字段；开关可恢复完整版。

    下游（属性匹配阶段 aggregate_hlr_by_req）实际读取：
      · req_id                    —— 必需（按它聚合）
      · hlr_identity             —— 必需（_select_attr_record 用 name/bit 选接口）
      · attribute_classes/attr_value —— fallback 路径实测触发 5544 次，必需
      · evidence / match_pipeline —— 下游**完全不读**，可删
    """
    shared_dir = (_PROJECT / "EoICD-HLR正向匹配与属性比对_支持追溯" / "shared")
    if str(shared_dir) not in sys.path:
        sys.path.insert(0, str(shared_dir))

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_shared_utils_under_test", shared_dir / "utils.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # 构造最小可用的 final_retained（1 条信号 × 2 个候选）
    final_retained = [{
        "eoicd_cluster": {
            "identity": {"bus": "B1", "label": "L1", "name": "N", "bit": "0", "direction": "out"},
            "signal_name": "SIG1",
            "signal_short_name": "S1",
            "attributes": {"RP.X": "1"},
        },
        "candidate_interfaces": [
            {"unit": {"req_id": "R1", "bus": "B1", "label": "L1", "name": "N1",
                      "bit": "0", "direction": "out"}},
            {"unit": {"req_id": "R2", "bus": "B1", "label": "L2", "name": "N2",
                      "bit": "1", "direction": "out"}},
        ],
    }]
    hlr_clustered = {
        "attribute_classes": [{
            "attribute": "RP.X",
            "requirements": [{"req_id": "R1", "attr_value": "9", "evidence": ["e1"]}],
        }]
    }

    # ① 默认 slim：字段集恰为 4 个，且不含 evidence / match_pipeline
    rep = mod.generate_match_report_from_dedup(final_retained, hlr_clustered, {})
    cand = rep["matches"][0]["matched_hlrs"][0]
    assert set(cand.keys()) == {"req_id", "hlr_identity", "attribute_classes", "attr_value"}, \
        "slim 模式的字段集应恰为下游必需的 4 个，实际: %s" % sorted(cand.keys())
    assert cand["req_id"] == "R1"
    assert cand["hlr_identity"]["name"] == "N1"
    assert cand["attribute_classes"] == ["RP.X"]
    assert cand["attr_value"] == "9", "attr_value 必须保留（fallback 路径依赖）"

    # ② 开关关闭：恢复完整版（含 evidence / match_pipeline）
    rep_full = mod.generate_match_report_from_dedup(
        final_retained, hlr_clustered, {"output": {"slim_match_report": False}})
    cand_full = rep_full["matches"][0]["matched_hlrs"][0]
    assert "evidence" in cand_full and "match_pipeline" in cand_full, \
        "开关关闭时应写完整版字段"
    assert cand_full["evidence"] == ["e1"]

    # ③ slim 模式下 evidence 不参与计算（不应因缺 evidence 报错）
    assert "evidence" not in cand

    print("Bmatch_report 写入端瘦身自测通过"
          "（默认只留 4 个下游必需字段 / evidence+match_pipeline 已删 / 开关可恢复完整版）")


def test_no_report_archive() -> None:
    """方案A：多 judge 不再把 report_*.json 归档到 judge 工作区 output/。

    背景（2026-11-09）：两处 report_{pub,sub}.json 内容 md5 相同（同一份存两处）。
      · ws/summary/data/<ts>/  = **原件**，聚合层唯一读它
        （runner._latest_summary_folder 只扫 ws/"summary"/"data"）。
      · ws/output/<ts>/         = copy_final_outputs 的 shutil.copyfile **副本**。
    多 judge 时 judge 的 --output-dir 指向自己工作区，顶层 output/ 里并没有单 judge
    的 report_*.json ⇒ 该副本无任何消费者，纯白占盘（pub 侧单份约 44MB，×N judge）。

    ★必须保证单 judge 直跑路径（--output-dir 默认顶层 BASE_DIR/output）不受影响：
    那里的 report_*.json 就是**用户向交付物**。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_run_integration_under_test", _PROJECT / "run_integration.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    _tmp = Path(tempfile.mkdtemp())
    try:
        # 造一个 summary 的产出目录（run_dir）
        run_dir = _tmp / "summary" / "data" / "DEV_EoICD到软件高层需求的落实检查_20260101_000000"
        run_dir.mkdir(parents=True)
        for name in ("report_pub.json", "report_sub.json",
                     "summary_pub.docx", "summary_pub.xlsx",
                     "summary_sub.docx", "summary_sub.xlsx"):
            (run_dir / name).write_text("x", encoding="utf-8")

        # ① 多 judge 场景（archive_reports=False）：不归档 report_*.json，但 docx/xlsx 保留
        out_multi = _tmp / "ws" / "output"
        copied = mod.copy_final_outputs(run_dir, str(out_multi), archive_reports=False)
        dst = out_multi / run_dir.name
        assert not (dst / "report_pub.json").exists(), \
            "多 judge 场景不应归档 report_pub.json 副本"
        assert not (dst / "report_sub.json").exists(), \
            "多 judge 场景不应归档 report_sub.json 副本"
        assert (dst / "summary_pub.docx").exists(), "docx 必须保留"
        assert (dst / "summary_pub.xlsx").exists(), "xlsx 必须保留"
        assert "report_pub.json" not in copied

        # ② 单 judge 直跑（默认 archive_reports=True）：report_*.json 必须归档
        out_single = _tmp / "top_output"
        copied2 = mod.copy_final_outputs(run_dir, str(out_single))
        dst2 = out_single / run_dir.name
        assert (dst2 / "report_pub.json").exists(), \
            "单 judge 直跑必须归档 report_pub.json（它是用户向交付物）"
        assert (dst2 / "report_sub.json").exists(), \
            "单 judge 直跑必须归档 report_sub.json"
        assert (dst2 / "summary_pub.docx").exists()
        assert "report_pub.json" in copied2

        # ③ 原件（summary/data/ 下那份）始终不动 —— 聚合层从那里读
        assert (run_dir / "report_pub.json").exists(), "原件必须保留在 summary/data/ 下"

        # ④ runner 必须给 judge 子进程传 --no-report-archive
        import multi_judge_runner as mj
        src = Path(mj.__file__).read_text(encoding="utf-8")
        assert '"--no-report-archive"' in src, \
            "runner 应给 judge 子进程传 --no-report-archive"

        print("方案A report 归档去重自测通过"
              "（多 judge 不归档副本 / 单 judge 照常归档 / 原件不动 / runner 已传参）")
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)


def run_all() -> None:
    """入口：跑 ①③ 相关的全部自测。"""
    test_retained_save_policy()
    test_prepare_summary_input_no_copy()
    test_judge_no_eoicd_processed_copy()
    test_run_integration_eoicd_processed_dir()
    test_not_mentioned_cleanup()
    test_match_report_slim()
    test_no_report_archive()


if __name__ == "__main__":
    run_all()
    print("[OK] test_io_reduction 全部自测通过")
