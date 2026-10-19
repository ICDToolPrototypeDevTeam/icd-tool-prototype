"""
test_runner_review_route.py
===========================

从 multi_judge_runner.py 迁出的 1 个自测函数（复核路由 + 工作区重置，共 ~104 行）。

迁移动机：生产文件 980+ 行，_self_test 占 ~104 行，迁出后生产文件专注编排主流程。
旧 CLI 兼容：python multi_judge_runner.py --selftest 仍可触发（__main__ 入口转调本模块）。

注：原测试同时覆盖「复核路由」与「工作区重置」两个无关主题；为保留分组清晰，
本文件拆成两个 test_* 函数（test_review_route / test_workspace_reset），run_all() 一并跑。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
_MULTI_DIR = _PROJECT / "multi_judge协同"

# runner.py 在项目根目录，aggregate 等在 multi_judge协同/
# 同时把这两处加到 sys.path，否则 from multi_judge_runner import ... 找不到
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))
if str(_MULTI_DIR) not in sys.path:
    sys.path.insert(0, str(_MULTI_DIR))

from multi_judge_aggregate import aggregate  # noqa: E402
from multi_judge_runner import (  # noqa: E402
    KEEP_IN_WORKSPACE,
    JudgeRunContext,
    _clean_workspace,
    _resolve_arbitrator_cfg,
    run_review_route,
    run_single_judge,
)


# ---------------------------------------------------------------------------
# 复核路由自测（原 multi_judge_runner.py:793-866）
# ---------------------------------------------------------------------------
def test_review_route() -> None:
    """复核路由自测：不触发任何真实 LLM 调用（走「模型不可用」降级分支记账）。"""
    # 0 存活 → need_review=True（聚合层已强制标记）
    shards = {
        "judge_a": {"S1": {"matched_hlr": [], "verdict": "待确认", "name_match_status": False,
                           "hlr_identity": None, "coverage": "error", "attributes": []}},
        "judge_b": {"S1": {"matched_hlr": [], "verdict": "待确认", "name_match_status": False,
                           "hlr_identity": None, "coverage": "error", "attributes": []}},
    }
    meta = {j: {"model": "m", "base_url": "b", "key_hash": "k", "frozen": True} for j in shards}
    agg = aggregate(shards, meta, {}, n_configured=2)
    assert agg["S1"]["aggregation"]["need_review"] is True

    # 1) 未配 arbitrator 段 → 回落 judge[0]，但拿不到 key → 记账 skipped
    cfg = {"judges": [{"name": "judge_a", "model": "deepseek", "base_url": "x",
                       "api_key_env": "_NO_SUCH_KEY_"}]}
    arb, source = _resolve_arbitrator_cfg(cfg)
    assert arb["model"] == "deepseek"
    assert "同源" in source, source
    st = run_review_route(agg, shards, meta, cfg)
    assert st["needed"] == 1 and st["executed"] == 0 and st["skipped"] == 1, st
    assert any("仲裁模型不可用" in k for k in st["reasons"]), st["reasons"]
    assert "回落" in st["arb_source"], st["arb_source"]

    # 2) 显式 enabled=false
    cfg_off = {"judges": cfg["judges"], "arbitrator": {"enabled": False, "model": "m"}}
    arb_off, src_off = _resolve_arbitrator_cfg(cfg_off)
    assert "显式关闭" in arb_off.get("_block_reason", ""), arb_off
    assert "显式关闭" in src_off, src_off
    st2 = run_review_route(agg, shards, meta, cfg_off)
    assert st2["skipped"] == 1 and st2["executed"] == 0, st2
    assert "显式关闭" in st2["arb_source"], st2["arb_source"]

    # 2b) 同源识别
    cfg_same = {"judges": cfg["judges"],
                "arbitrator": {"model": "deepseek", "base_url": "x",
                               "api_key_env": "_NO_SUCH_KEY_", "allow_same_as_judge": True}}
    arb_same, src_same = _resolve_arbitrator_cfg(cfg_same)
    assert arb_same.get("_same_as_judge") is True, arb_same
    assert "同源" in src_same and "独立性受限" in src_same, src_same
    st_same = run_review_route(agg, shards, meta, cfg_same)
    assert st_same["arb_same_as_judge"] is True, st_same
    assert st_same["arb_model"] == "deepseek", st_same

    # 2c) 同源 + 配置禁止
    cfg_nosame = dict(cfg_same, arbitrator=dict(
        cfg_same["arbitrator"], allow_same_as_judge=False))
    arb_nosame, src_nosame = _resolve_arbitrator_cfg(cfg_nosame)
    assert "禁止同源仲裁" in arb_nosame.get("_block_reason", ""), arb_nosame
    st_ns = run_review_route(agg, shards, meta, cfg_nosame)
    assert st_ns["skipped"] == 1 and st_ns["executed"] == 0, st_ns
    assert any("禁止同源仲裁" in k for k in st_ns["reasons"]), st_ns["reasons"]

    # 2d) 异模型仲裁 → 不得误判同源
    cfg_diff = {"judges": cfg["judges"],
                "arbitrator": {"model": "qwen-max", "base_url": "y",
                               "api_key_env": "_NO_SUCH_KEY_2_"}}
    arb_diff, _ = _resolve_arbitrator_cfg(cfg_diff)
    assert arb_diff.get("_same_as_judge") is False, arb_diff
    assert run_review_route(agg, shards, meta, cfg_diff)["arb_same_as_judge"] is False

    # 3) 无待复核信号时不产生任何记账噪音
    ok_entry = {"matched_hlr": ["R1"], "verdict": "已落实", "name_match_status": True,
                "hlr_identity": {"bus": "1", "label": "0x10", "bit": "8", "direction": "pub"},
                "coverage": "ok", "attributes": []}
    agg_ok = aggregate({"judge_a": {"S1": dict(ok_entry)}, "judge_b": {"S1": dict(ok_entry)}},
                       meta, {}, n_configured=2)
    assert agg_ok["S1"]["aggregation"]["need_review"] is False
    assert run_review_route(agg_ok, shards, meta, cfg)["needed"] == 0

    print("multi_judge_runner 复核路由自测通过（回落/显式关闭/无 key 降级均按 skipped 记账；"
          "同源识别、同源禁止拒跑、异模型不误判均符合预期）")


# ---------------------------------------------------------------------------
# 工作区重置自测（原 multi_judge_runner.py:868-896）
# ---------------------------------------------------------------------------
def test_workspace_reset() -> None:
    """工作区重置（白名单：只留 3 个 AI map；.bak / 日志 / 子目录一律清除）。"""
    _tmp = Path(tempfile.mkdtemp())
    try:
        _ws = _tmp / "judge_0_judge_A"
        (_ws / "data" / "raw").mkdir(parents=True)
        (_ws / "eoicd_processed").mkdir(parents=True)
        (_ws / "output" / "旧_20260101_000000").mkdir(parents=True)
        for _f in KEEP_IN_WORKSPACE:
            (_ws / _f).write_text("{}", encoding="utf-8")
        (_ws / "name_map.json.bak").write_text("{}", encoding="utf-8")
        (_ws / "integration.log").write_text("x", encoding="utf-8")
        _clean_workspace(_ws, KEEP_IN_WORKSPACE)
        assert sorted(p.name for p in _ws.iterdir()) == sorted(KEEP_IN_WORKSPACE), \
            sorted(p.name for p in _ws.iterdir())
        # shared → 整目录清空
        _sh = _tmp / "shared"
        (_sh / "data" / "raw").mkdir(parents=True)
        _clean_workspace(_sh, set())
        assert not list(_sh.iterdir())
        # 护栏：非 shared/judge_* 目录拒绝清理
        _bad = _tmp / "other"
        _bad.mkdir()
        (_bad / "keep.txt").write_text("x", encoding="utf-8")
        _clean_workspace(_bad, KEEP_IN_WORKSPACE)
        assert (_bad / "keep.txt").exists()
        print("multi_judge_runner 工作区重置自测通过（只留 3 个 map / shared 清空 / 非预期目录护栏）")
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# run_single_judge 字段解出自测（D6）
# ---------------------------------------------------------------------------
# P3.2 把 run_single_judge 从 9 参数改为 JudgeRunContext dataclass 时，
# 把 procs_lock 写成 `_ = ctx.timeout, ctx.procs_lock`（显式未使用占位）——
# 但函数体内 L532/L542 仍用 `with procs_lock:`，导致 NameError 被 except Exception
# 静默吃掉，production 跑出 0/3 judge 失败 0 信号却没有任何错误信息。
# L457 修完后又 P3.2 根治——函数体直接 `with ctx.procs_lock:`，不再解包。
# D6 仍保留：验证 except Exception 触发路径**不**静默吃异常（避免类似 bug 再潜伏）。
def test_run_single_judge_procs_lock_resolved() -> None:
    """验证 run_single_judge(ctx) 函数体在 Popen 抛异常时 except Exception 路径不再静默。"""
    procs: dict = {}
    lock = threading.Lock()
    ctx = JudgeRunContext(
        judge={"name": "test_judge", "model": "m1", "base_url": "u1",
               "api_key_env": "API_KEY"},
        idx=0,
        cfg={"project": "ams"},
        shared_ws=Path("/tmp/dummy_shared"),
        judge_ws=Path("/tmp/dummy_judge"),
        out_dir=Path("/tmp/dummy_judge/output"),
        timeout=3600,
        procs=procs,
        procs_lock=lock,
    )
    # 不让真启动子进程——直接让 Popen 抛 FileNotFoundError 走 except Exception 分支。
    # 如果 P3.2 留下的 procs_lock NameError 没修，这里会抛 NameError 自测失败。
    # P3.2 根治后函数体直接 `with ctx.procs_lock:`，本测试验证 except Exception 路径不再静默吃异常。
    def _raise_filenotfound(*args, **kwargs):
        raise FileNotFoundError("[D6 mock] Popen refused to launch (except Exception test)")

    with patch("multi_judge_runner.subprocess.Popen", side_effect=_raise_filenotfound), \
         patch("multi_judge_runner.shutil.copytree"), \
         patch("multi_judge_runner.Path.mkdir"):
        # 之前 P3.2 的 bug：NameError: name 'procs_lock' is not defined
        # → 这里应正常返回 {coverage: "error", error: "启动失败: [D6 mock] ..."}
        result = run_single_judge(ctx)
    assert result["coverage"] == "error", f"coverage 应为 error，实际 {result}"
    assert "启动失败" in result.get("error", ""), \
        f"error 应含「启动失败」，实际 {result.get('error')}"
    # 关键断言：没有 NameError。error 字段是 FileNotFoundError 的内容，不是 NameError。
    assert "NameError" not in str(result), \
        f"不应出现 NameError（说明函数体内有 ctx 字段解包错误）：{result}"
    print("run_single_judge 字段解出自测通过（except Exception 触发路径不静默吃异常）")


def run_all() -> None:
    """入口：跑所有 runner 复核路由 + 工作区重置 + run_single_judge 字段解出自测。"""
    test_review_route()
    test_workspace_reset()
    test_run_single_judge_procs_lock_resolved()


if __name__ == "__main__":
    run_all()
