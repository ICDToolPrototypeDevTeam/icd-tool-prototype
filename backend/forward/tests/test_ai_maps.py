"""
test_ai_maps.py
===============

针对 `HLR需求聚类/hlr_cluster_tool/ai_maps.py` 的 ① ③ 修复单测：

- ① update_all_maps 单 map 异常不影响其他 map（results 字典能区分两类）
- ③ update_map 写文件失败时 .tmp 清理 + reload 磁盘版本 + raise

设计：不调真 AI——mock client + mock spec['builder']，覆盖 update_map 写文件路径。
旧 CLI 兼容：`python ai_maps.py --help` 仍可用（__main__ 不动）。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
_AI_MAPS = _PROJECT / "HLR需求聚类" / "hlr_cluster_tool"

import sys
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))
if str(_AI_MAPS) not in sys.path:
    sys.path.insert(0, str(_AI_MAPS))

from ai_maps import MAP_SPECS, update_all_maps, update_map  # noqa: E402


# ---------------------------------------------------------------------------
# ① update_all_maps 单 map 失败不影响其他
# ---------------------------------------------------------------------------
def test_update_all_maps_single_map_failure_isolated() -> None:
    """验证 signal 抛异常时，name / state 仍成功，结果字典能区分两类。"""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        paths = {
            "name": td_path / "name_map.json",
            "state": td_path / "state_map.json",
            "signal": td_path / "signal_map.json",
        }
        reqs = [{"id": "R1", "需求中文": "测试需求"}, {"id": "R2", "需求中文": "另一条"}]

        # Mock client + 让 spec['builder'] 返回固定 values（避免调真 AI）
        mock_client = SimpleNamespace(enabled=True, cfg={"batch_size": 20})

        # 原始 builder 引用，便于 signal 路径单独抛异常
        original_builders = {k: v["builder"] for k, v in MAP_SPECS.items()}

        def _mock_builder(client, req_list, log=None):
            # 把所有 req 的 id 都返回个标记 value
            return {r["id"]: {"_mock": True} for r in req_list}

        def _signal_builder_broken(client, req_list, log=None):
            raise RuntimeError("[mock] signal AI endpoint 503 (服务端临时故障)")

        # 用 patch.object 临时改 MAP_SPECS
        with patch.dict(MAP_SPECS, {
            "name": {**MAP_SPECS["name"], "builder": _mock_builder},
            "state": {**MAP_SPECS["state"], "builder": _mock_builder},
            "signal": {**MAP_SPECS["signal"], "builder": _signal_builder_broken},
        }):
            results = update_all_maps(mock_client, reqs, paths, force=True, log=lambda *a: None)

        # ① 断言：name / state 成功（是 dict 且有 written 字段），signal 失败（error 标记）
        assert "written" in results["name"], f"name 应成功，结果：{results['name']}"
        assert results["name"]["written"] == 2
        assert "written" in results["state"], f"state 应成功，结果：{results['state']}"
        assert results["state"]["written"] == 2
        # signal 失败标记（不会 raise 整个 update_all_maps）
        assert results["signal"].get("ok") is False, f"signal 应标记 ok=False：{results['signal']}"
        assert "RuntimeError" in results["signal"]["error_type"], \
            f"signal 异常类型应是 RuntimeError，实际 {results['signal'].get('error_type')}"
        assert "[mock] signal AI endpoint 503" in results["signal"]["error"], \
            f"signal 错误消息：{results['signal'].get('error')}"

        # 关键：name_map.json / state_map.json 应真的写入了磁盘（不只是返回值标记）
        assert paths["name"].exists(), "name_map.json 应已写入"
        assert paths["state"].exists(), "state_map.json 应已写入"
        assert not paths["signal"].exists(), \
            "signal_map.json 不应写入（因 signal 抛异常未到 json.dump 步骤）"

        # 关键：① 之前这个用例会让 update_all_maps raise（signal 是第三个循环）
        # ——现在不会 raise，调用方拿到完整 results 字典
        print("update_all_maps 单 map 失败隔离自测通过（name/state 成功 + signal 失败标记）")


# ---------------------------------------------------------------------------
# ③ update_map 写失败时 .tmp 清理 + reload 磁盘版本 + raise
# ---------------------------------------------------------------------------
def test_update_map_write_failure_cleans_tmp_and_reload() -> None:
    """验证 update_map 写文件失败时 .tmp 被清理 + reload 磁盘旧版本 + 文件未破坏。"""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        path = td_path / "signal_map.json"
        reqs = [{"id": "R1", "需求中文": "测试需求"}]
        mock_client = SimpleNamespace(enabled=True, cfg={"batch_size": 20})

        # 预先写入"老版本"到 path——模拟"上次成功写完"的状态，
        # 验证写失败 reload 能看到这份老版本并写诊断日志
        old_payload = {
            "__meta__": {"generation": 3, "covered_reqs": ["R_OLD"], "source_doc": "old.docx"},
            "R_OLD": {"value": {"_old": True}, "text_hash": "h_old"},
        }
        path.write_text(json.dumps(old_payload, ensure_ascii=False), encoding="utf-8")

        def _builder(_client, _req_list, log=None):
            return {"R1": {"_mock": True}}

        logs: list[str] = []

        def _log(msg: str) -> None:
            logs.append(msg)

        with patch.dict(MAP_SPECS, {
            "signal": {**MAP_SPECS["signal"], "builder": _builder},
        }):
            # 让 json.dump 抛模拟写入失败（磁盘满 / 权限拒绝）
            def _dump_broken(obj, fp, **kwargs):
                raise OSError("[mock] 模拟磁盘满 / 写权限拒绝")

            with patch("ai_maps.json.dump", side_effect=_dump_broken):
                try:
                    update_map("signal", mock_client, reqs, path,
                               force=True, log=_log)
                    raise AssertionError("update_map 应 raise 但没 raise")
                except OSError as e:
                    assert "[mock] 模拟磁盘满" in str(e)

        # ③ 断言 1：.tmp 临时文件应已被清理（不污染磁盘）
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        assert not tmp_path.exists(), f".tmp 应被清理，实际仍存在：{tmp_path}"

        # ③ 断言 2：reload 日志应写出"已写入版本"+ 老版本 generation=3
        reload_msgs = [m for m in logs if "reload" in m]
        assert len(reload_msgs) >= 1, f"应至少有 1 条 reload 日志，实际 {logs}"
        assert any("generation=3" in m for m in reload_msgs), \
            f"reload 日志应记录老版本 generation=3，实际 {reload_msgs}"

        # ③ 断言 3：老版本 path 文件内容未被破坏（reload 后还是上次写完的版本）
        with open(path, encoding="utf-8") as f:
            current = json.load(f)
        assert current["__meta__"]["generation"] == 3, \
            f"老版本文件应保持 generation=3 不变，实际 {current['__meta__'].get('generation')}"
        assert "R_OLD" in current, "老版本 R_OLD 应保持"
        print("update_map 写失败自测通过（.tmp 清理 + reload 老版本 + 文件未破坏）")


def test_update_map_atomic_replace_success() -> None:
    """验证 update_map 正常写路径用 .tmp + os.replace 原子替换（原文件先备份。bak）。"""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        path = td_path / "signal_map.json"
        reqs = [{"id": "R1", "需求中文": "测试需求"}]
        mock_client = SimpleNamespace(enabled=True, cfg={"batch_size": 20})

        def _builder(_client, _req_list, log=None):
            return {"R1": {"_mock": True}}

        with patch.dict(MAP_SPECS, {
            "signal": {**MAP_SPECS["signal"], "builder": _builder},
        }):
            update_map("signal", mock_client, reqs, path, force=True, log=lambda *a: None)

        # 断言：原文件存在 + .tmp 不存在（已被 replace）
        assert path.exists(), "signal_map.json 应存在"
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        assert not tmp_path.exists(), ".tmp 不应残留"

        # 断言：写入的 payload 是合法 JSON 且含 __meta__ + 我们的 mock value
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert "__meta__" in data
        assert "R1" in data
        assert data["R1"]["value"] == {"_mock": True}
        # generation 自增到 1
        assert data["__meta__"]["generation"] == 1
        print("update_map 原子写自测通过（.tmp + os.replace + 合法 JSON + generation=1）")


# ---------------------------------------------------------------------------
# ② run_all_maps API 400 退化——3 个 map 全标 ok=False，不抛异常
# ---------------------------------------------------------------------------
def test_run_all_maps_api_400_marks_failed() -> None:
    """验证 client.chat 抛 AIError('HTTP 400 ...') 时 update_all_maps 正确标记失败。

    这是 ① 修复后必须验证的关键场景：API 400 退化时不能抛异常（让 caller 看到"全失败"
    的精确状态），且不能留下半成品 map（③ 原子写 + 异常 reload 已保证）。
    """
    from ai_client import AIError

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        paths = {
            "name": td_path / "name_map.json",
            "state": td_path / "state_map.json",
            "signal": td_path / "signal_map.json",
        }
        reqs = [{"id": "R1", "需求中文": "测试需求"}]

        # Mock AIClient：enabled=True，但 chat_json 抛 AIError('HTTP 400 ...')
        # 模拟 ② 修复前的隐藏脆弱性——run_ai_update 退化时 map 不重建会被破坏
        # 注意 builder 内部调的是 client.chat_json（不是 chat）——必须 mock 这个
        mock_client = SimpleNamespace(
            enabled=True,
            cfg={"batch_size": 20},
            call_count=0,
            usage_tokens=0,
        )

        def _raise_http400_json(system, user):
            mock_client.call_count += 1
            raise AIError("HTTP 400: response_format not supported (mock)")

        mock_client.chat_json = _raise_http400_json

        # spec['builder'] 调 client.chat_json 抛 AIError——update_map 拿到 values={} 时
        # 不会抛（builder 直接 return values）——但我们让 builder 抛 AIError 模拟 chat_json 抛错
        # 直接让 builder 抛 AIError 来模拟 client.chat_json 抛 400

        def _builder_raises_http400(_client, _req_list, log=None):
            raise AIError("HTTP 400: response_format not supported (mock)")

        with patch.dict(MAP_SPECS, {
            "name": {**MAP_SPECS["name"], "builder": _builder_raises_http400},
            "state": {**MAP_SPECS["state"], "builder": _builder_raises_http400},
            "signal": {**MAP_SPECS["signal"], "builder": _builder_raises_http400},
        }):
            # 关键断言：① 修复后这里不会抛异常——即使 3 个 map 全失败
            try:
                results = update_all_maps(mock_client, reqs, paths,
                                           force=True, log=lambda *a: None)
            except AIError as e:
                raise AssertionError(f"update_all_maps 不应 raise，实际 raise AIError: {e}")

        # 断言 1：3 个 map 全标 ok=False（API 400 失败）
        for kind in ("name", "state", "signal"):
            assert results[kind].get("ok") is False, \
                f"{kind} 应标 ok=False，实际 {results[kind]}"
            assert "HTTP 400" in results[kind].get("error", ""), \
                f"{kind} 错误应含 'HTTP 400'，实际 {results[kind].get('error')}"
            assert results[kind].get("error_type") == "AIError", \
                f"{kind} 异常类型应是 AIError，实际 {results[kind].get('error_type')}"

        # 断言 2：3 个 map 文件都不应被写入（AI 失败未到 json.dump 步骤）
        for kind in ("name", "state", "signal"):
            assert not paths[kind].exists(), \
                f"{kind}_map.json 不应被写入（避免半成品）"

        # 断言 3：cluster_hlr.py 死代码段已改为读 results 字典——读到这里 caller 能区分
        failed = [k for k, r in results.items() if r.get("ok") is False]
        ok_kinds = [k for k, r in results.items() if not r.get("ok") is False]
        assert failed == ["name", "state", "signal"] and not ok_kinds, \
            f"全部失败应被正确分类：failed={failed} ok={ok_kinds}"

        print("run_all_maps API 400 退化自测通过（3 map 全标 ok=False + 不抛异常 + 无半成品）")


def test_run_ai_update_all_fail_returns_none() -> None:
    """端到端：3 map 全失败 → return None + stderr 含"全部失败" + 3 map 文件未写。

    这是 P1 (cluster_hlr.py L880-894 改读 results) + ① (update_all_maps 单 map 隔离)
    联动验证：API 400 退化的真实链路能正确分类、不留半成品、caller 拿到 None 走 fallback。
    """
    from contextlib import redirect_stderr
    import io

    from ai_client import AIError
    from cluster_hlr import run_ai_update

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        name_map = td_path / "name_map.json"
        state_map = td_path / "state_map.json"
        signal_map = td_path / "signal_map.json"

        # Mock AIClient.from_config 让 client.enabled=True（不读真 ai_config.json）
        mock_client = SimpleNamespace(
            enabled=True, cfg={"batch_size": 20}, call_count=0, usage_tokens=0,
        )
        mock_client.describe = lambda: "mock-client(enabled=True, base=http://dummy, model=dummy)"

        # Mock 3 个 builder 全抛 AIError 模拟 3 map 全部 API 400 退化
        def _builder_raises_http400(_client, _req_list, log=None):
            raise AIError("HTTP 400: response_format not supported (mock)")

        # args：让 file_missing=True（map 文件不存在）+ ai_force=True 双重触发 update 流程
        args = SimpleNamespace(
            ai_force=True,
            ai_dry_run=False,
            ai_key="dummy",
            ai_base="http://dummy",
            ai_model="dummy",
            ai_config=None,  # run_ai_update 会取默认路径，但 from_config 被 mock 不读
            docx="dummy.docx",
            no_ai_scan_new=False,
            name_map=str(name_map),
            state_map=str(state_map),
            signal_map=str(signal_map),
        )

        # name_st 让 trigger changes（stale 让 file_missing 或 stale 检测触发）
        name_st = {"missing": True}
        reqs = [{"id": "R1", "需求中文": "测试需求"}]

        # Capture stderr（run_ai_update L890 print → stderr）
        err_buf = io.StringIO()

        # Patch：AIClient.from_config 返回 mock client + 3 builders 全抛错
        with patch("ai_client.AIClient.from_config", return_value=mock_client), \
             patch.dict(MAP_SPECS, {
                 "name": {**MAP_SPECS["name"], "builder": _builder_raises_http400},
                 "state": {**MAP_SPECS["state"], "builder": _builder_raises_http400},
                 "signal": {**MAP_SPECS["signal"], "builder": _builder_raises_http400},
             }), \
             redirect_stderr(err_buf):
            result = run_ai_update(args, reqs, name_st, None, None)

        # 断言 1：全失败 → return None（走 fallback 分支 L888-891）
        assert result is None, \
            f"全失败应返回 None 让 caller 走默认映射，实际：{result}"

        # 断言 2：stderr 含"AI 更新全部失败"
        err_output = err_buf.getvalue()
        assert "AI 更新全部失败" in err_output, \
            f"stderr 应提示全部失败，实际：{err_output!r}"

        # 断言 3：3 个 map 文件都不应被写入（防半成品）
        assert not name_map.exists(), "name_map.json 不应被写入（全失败）"
        assert not state_map.exists(), "state_map.json 不应被写入（全失败）"
        assert not signal_map.exists(), "signal_map.json 不应被写入（全失败）"

        print("run_ai_update 端到端 全失败走 fallback 自测通过"
              "（return None + stderr 全部失败告警 + 无半成品）")


def test_run_ai_update_partial_fail_continues_with_warn() -> None:
    """端到端：name 失败 + state/signal 成功 → return 非空 + stderr 含"部分失败" +
    name map 未写 + state/signal map 已写。

    这是 P1 L892-894 新代码分支的覆盖——之前 P2 只测了 L888-891 全失败路径，
    "部分失败"分支从未被任何单测触发过。
    """
    from contextlib import redirect_stderr
    import io

    from ai_client import AIError
    from cluster_hlr import run_ai_update

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        name_map = td_path / "name_map.json"
        state_map = td_path / "state_map.json"
        signal_map = td_path / "signal_map.json"

        mock_client = SimpleNamespace(
            enabled=True, cfg={"batch_size": 20}, call_count=0, usage_tokens=0,
        )
        mock_client.describe = lambda: "mock-client(enabled=True, base=http://dummy, model=dummy)"

        # name builder 抛 AIError；state / signal 返回正常 values
        def _name_builder_raises(_client, _req_list, log=None):
            raise AIError("HTTP 400: response_format not supported (mock, name only)")

        def _state_builder_ok(_client, req_list, log=None):
            return {r["id"]: {"_state": True} for r in req_list}

        def _signal_builder_ok(_client, req_list, log=None):
            return {r["id"]: {"_signal": True} for r in req_list}

        args = SimpleNamespace(
            ai_force=True,
            ai_dry_run=False,
            ai_key="dummy",
            ai_base="http://dummy",
            ai_model="dummy",
            ai_config=None,
            docx="dummy.docx",
            no_ai_scan_new=False,
            name_map=str(name_map),
            state_map=str(state_map),
            signal_map=str(signal_map),
        )

        reqs_list = [{"id": "R1", "需求中文": "测试需求"}]

        err_buf = io.StringIO()

        with patch("ai_client.AIClient.from_config", return_value=mock_client), \
             patch.dict(MAP_SPECS, {
                 "name": {**MAP_SPECS["name"], "builder": _name_builder_raises},
                 "state": {**MAP_SPECS["state"], "builder": _state_builder_ok},
                 "signal": {**MAP_SPECS["signal"], "builder": _signal_builder_ok},
             }), \
             redirect_stderr(err_buf):
            # file_missing 让 update 触发（map 文件不存在）
            result = run_ai_update(args, reqs_list, None, None, None)

        # 断言 1：部分失败 → return 非 None（含 stats 字典）
        assert result is not None, \
            "部分失败应返回部分 stats（不全失败），不应 return None"
        assert result.get("updated") is True, \
            f"部分失败也应标记 updated=True，实际：{result}"
        assert "stats" in result, f"返回应含 stats 字典，实际：{result}"

        # 断言 2：stats 字典中 name 失败、state/signal 成功
        stats = result["stats"]
        assert stats["name"].get("ok") is False, \
            f"name 应标 ok=False，实际：{stats['name']}"
        assert "HTTP 400" in stats["name"].get("error", ""), \
            f"name 错误应含 HTTP 400，实际：{stats['name'].get('error')}"
        assert "written" in stats["state"], \
            f"state 应成功（有 written 字段），实际：{stats['state']}"
        assert stats["state"]["written"] == 1, \
            f"state 应写入 1 条，实际 {stats['state']['written']}"
        assert "written" in stats["signal"], \
            f"signal 应成功（有 written 字段），实际：{stats['signal']}"
        assert stats["signal"]["written"] == 1, \
            f"signal 应写入 1 条，实际 {stats['signal']['written']}"

        # 断言 3：stderr 含"AI 更新部分失败"
        err_output = err_buf.getvalue()
        assert "AI 更新部分失败" in err_output, \
            f"stderr 应提示部分失败，实际：{err_output!r}"
        # 不应走"全部失败"路径
        assert "AI 更新全部失败" not in err_output, \
            f"部分失败不应同时走全失败分支，stderr：{err_output!r}"

        # 断言 4：name map 未写、state / signal map 已写
        assert not name_map.exists(), \
            "name_map.json 不应被写入（name builder 抛错）"
        assert state_map.exists(), \
            "state_map.json 应已写入（state 成功）"
        assert signal_map.exists(), \
            "signal_map.json 应已写入（signal 成功）"

        print("run_ai_update 端到端 部分失败走 warn 自测通过"
              "（return 含 stats + stderr 部分失败告警 + name 未写 state/signal 已写）")


def run_all() -> None:
    """入口：跑所有 ai_maps 单测。"""
    test_update_all_maps_single_map_failure_isolated()
    test_update_map_write_failure_cleans_tmp_and_reload()
    test_update_map_atomic_replace_success()
    test_run_all_maps_api_400_marks_failed()
    test_run_ai_update_all_fail_returns_none()
    test_run_ai_update_partial_fail_continues_with_warn()


if __name__ == "__main__":
    run_all()