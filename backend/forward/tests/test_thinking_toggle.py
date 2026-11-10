"""
test_thinking_toggle.py
=======================

前端「模型设置」思考开关在正向管线的接入自测（不触发真实网络）。

覆盖：
  1. 共享 helper 的开关语义（默认开启、仅显式关闭生效、未知 provider 不变）；
  2. 5 个 LLM 请求构造点在关闭时追加 provider 字段、开启时请求体与历史一致；
  3. config_builder 为真实 judge / arbitrator 写入 provider，mock 不写。

这些测试锁定「只在关闭时追加字段」的契约：任何改动若让默认开启路径的请求体
发生变化，都应在此处失败。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent                       # backend/forward
_BACKEND = _PROJECT.parent                    # backend
_MATCH_DIR = _PROJECT / "EoICD-HLR正向匹配与属性比对_支持追溯"
_IDENTITY_SRC = _MATCH_DIR / "identity_match" / "src"
_ATTR_SRC = _MATCH_DIR / "attribute_match" / "src"
_SHARED_DIR = _MATCH_DIR / "shared"
_MULTI_DIR = _PROJECT / "multi_judge协同"
_CLUSTER_DIR = _PROJECT / "HLR需求聚类" / "hlr_cluster_tool"

for _p in (_BACKEND, _PROJECT, _SHARED_DIR, _IDENTITY_SRC, _ATTR_SRC, _MULTI_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import app.forward.config_builder as config_builder  # noqa: E402
import utils as shared_utils  # noqa: E402
import name_ai_matcher  # noqa: E402
import name_translator  # noqa: E402
import attr_mismatch_ai_matcher  # noqa: E402
import multi_judge_arbitrator as arbitrator  # noqa: E402


@contextmanager
def _patched_env(**values):
    """临时设置/清除环境变量（None 表示删除），退出时恢复。"""
    old = {k: os.environ.get(k) for k in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _FakeRequestsResponse:
    """requests.post 的最小替身（流式读取由测试自行 mock）。"""

    status_code = 200
    headers: dict = {}

    def __init__(self, body: bytes):
        self._body = body

    def raise_for_status(self):
        return None

    def close(self):
        return None


def _patch_requests(module, body: bytes, captured: dict):
    """把模块里的 requests 换成只记录 payload 的替身，返回还原函数。"""
    original = module.requests

    def _post(url, headers=None, json=None, timeout=None, stream=False):
        captured["url"] = url
        captured["payload"] = dict(json or {})
        return _FakeRequestsResponse(body)

    module.requests = types.SimpleNamespace(post=_post)
    return lambda: setattr(module, "requests", original)


def _patch_read(module, body: bytes):
    original = module.read_stream_with_stall_guard
    module.read_stream_with_stall_guard = lambda resp, **kwargs: body
    return lambda: setattr(module, "read_stream_with_stall_guard", original)


class _FakeUrlopenResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def _patch_urlopen(module, body: bytes, captured: dict):
    """把模块里的 urllib.request.urlopen 换成记录请求体的替身。"""
    original = module.urllib.request.urlopen

    def _urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeUrlopenResponse(body)

    module.urllib.request.urlopen = _urlopen
    return lambda: setattr(module.urllib.request, "urlopen", original)


def _load_cluster_ai_client():
    spec = importlib.util.spec_from_file_location(
        "_forward_ai_client_under_test", _CLUSTER_DIR / "ai_client.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _identity_cfg():
    return {
        "model": "m", "api_key": "k", "base_url": "http://x/v1",
        "max_tokens": 16, "temperature": 0.1, "timeout": 1, "retry": 1,
    }


def _attr_cfg():
    return {
        "model": "m", "api_key": "k", "base_url": "http://x/v1",
        "max_tokens": 16, "temperature": 0.1, "timeout": 1, "retry": 1,
    }


def test_shared_helper_semantics():
    """开关语义：默认开启、仅显式关闭、未知 provider 不追加。"""
    with _patched_env(AI_PROVIDER="deepseek", DEEPSEEK_THINKING="0"):
        assert shared_utils.thinking_payload() == {"thinking": {"type": "disabled"}}
        assert shared_utils.thinking_enabled() is False
        assert shared_utils.thinking_payload("qwen") == {}  # 显式 provider 优先
    with _patched_env(AI_PROVIDER="qwen", QWEN_THINKING="false"):
        assert shared_utils.thinking_payload() == {"enable_thinking": False}
    with _patched_env(AI_PROVIDER="minimax", MINIMAX_THINKING="off"):
        assert shared_utils.thinking_payload() == {"enable_thinking": False}
    with _patched_env(AI_PROVIDER="deepseek", DEEPSEEK_THINKING="1"):
        assert shared_utils.thinking_payload() == {}
    with _patched_env(AI_PROVIDER="deepseek", DEEPSEEK_THINKING=""):
        assert shared_utils.thinking_payload() == {}
    with _patched_env(AI_PROVIDER="openai", DEEPSEEK_THINKING="0"):
        assert shared_utils.thinking_payload() == {}
    with _patched_env(AI_PROVIDER=None, DEEPSEEK_THINKING="0"):
        assert shared_utils.thinking_payload() == {}


def test_name_matcher_payload():
    """阶段3 名称判定：关闭时追加字段，开启时保持历史请求体。"""
    body = json.dumps({"choices": [{"message": {"content": "1. 匹配"}}]}).encode("utf-8")
    captured: dict = {}
    restore_requests = _patch_requests(name_ai_matcher, body, captured)
    restore_read = _patch_read(name_ai_matcher, body)
    try:
        with _patched_env(AI_PROVIDER="deepseek", DEEPSEEK_THINKING="0"):
            name_ai_matcher.call_ai_api("prompt", _identity_cfg())
        assert captured["payload"]["thinking"] == {"type": "disabled"}
        assert "enable_thinking" not in captured["payload"]

        captured.clear()
        with _patched_env(AI_PROVIDER="deepseek", DEEPSEEK_THINKING="1"):
            name_ai_matcher.call_ai_api("prompt", _identity_cfg())
        assert "thinking" not in captured["payload"]
        assert "enable_thinking" not in captured["payload"]
    finally:
        restore_read()
        restore_requests()


def test_name_translator_payload():
    """阶段3 翻译预扫：关闭时追加字段。"""
    body = json.dumps({"choices": [{"message": {"content": "EN: Test ABBR: TST"}}]}).encode("utf-8")
    captured: dict = {}
    restore_requests = _patch_requests(name_translator, body, captured)
    restore_read = _patch_read(name_translator, body)
    try:
        with _patched_env(AI_PROVIDER="qwen", QWEN_THINKING="0"):
            name_translator._call_translate_api("prompt", _identity_cfg())
        assert captured["payload"]["enable_thinking"] is False
        assert "thinking" not in captured["payload"]
    finally:
        restore_read()
        restore_requests()


def test_attribute_matcher_payload():
    """阶段4 属性比对（冲突判定复用该函数）：关闭时追加字段。"""
    body = json.dumps({"choices": [{"message": {"content": "1. 一致"}}]}).encode("utf-8")
    captured: dict = {}
    restore_requests = _patch_requests(attr_mismatch_ai_matcher, body, captured)
    restore_read = _patch_read(attr_mismatch_ai_matcher, body)
    try:
        with _patched_env(AI_PROVIDER="minimax", MINIMAX_THINKING="0"):
            attr_mismatch_ai_matcher.call_ai_api("prompt", _attr_cfg())
        assert captured["payload"]["enable_thinking"] is False
    finally:
        restore_read()
        restore_requests()


def test_arbitrator_payload():
    """Step5/5.5 仲裁：provider 显式传入，关闭时追加字段。"""
    body = json.dumps({
        "choices": [{"message": {"content": json.dumps({"ok": True})}}],
    }).encode("utf-8")
    captured: dict = {}
    restore = _patch_urlopen(arbitrator, body, captured)
    try:
        with _patched_env(AI_PROVIDER=None, DEEPSEEK_THINKING="0"):
            out = arbitrator.call_llm(
                "m", "http://x/v1", "k", "sys", "user",
                timeout=1, max_retries=0, provider="deepseek")
        assert out == {"ok": True}
        assert captured["payload"]["thinking"] == {"type": "disabled"}

        captured.clear()
        with _patched_env(AI_PROVIDER=None, DEEPSEEK_THINKING="1"):
            arbitrator.call_llm(
                "m", "http://x/v1", "k", "sys", "user",
                timeout=1, max_retries=0, provider="deepseek")
        assert "thinking" not in captured["payload"]
    finally:
        restore()


def test_cluster_ai_client_payload():
    """阶段2 AI 映射：读 AI_PROVIDER，关闭时追加字段。"""
    mod = _load_cluster_ai_client()
    body = json.dumps({
        "choices": [{"message": {"content": json.dumps({"ok": True})}}],
    }).encode("utf-8")
    captured: dict = {}
    restore = _patch_urlopen(mod, body, captured)
    try:
        client = mod.AIClient({
            "base_url": "http://x/v1", "model": "m", "api_key": "k",
            "max_retries": 0, "timeout": 1,
        })
        with _patched_env(AI_PROVIDER="qwen", QWEN_THINKING="0"):
            client.chat("sys", "user")
        assert captured["payload"]["enable_thinking"] is False
    finally:
        restore()


def test_config_builder_provider_fields():
    """真实 judge / arbitrator 带 provider；mock 不带（保证 mock 请求体不变）。"""
    env = {
        "DEEPSEEK_API_KEY": "k1", "DEEPSEEK_MODEL": "m1",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
        "MINIMAX_API_KEY": "k2", "MINIMAX_MODEL": "m2",
        "MINIMAX_BASE_URL": "https://api.minimax.chat",
        "QWEN_API_KEY": "k3", "QWEN_MODEL": "m3",
        "QWEN_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }
    hlr = Path("hlr.docx")
    cfg, child_env = config_builder.build_real_config(
        project="p", eoicd_pub=None, eoicd_sub=None, hlr=hlr,
        use_trace=False, env=env)
    assert [j["provider"] for j in cfg["judges"]] == ["deepseek", "minimax", "qwen"]
    assert cfg["arbitrator"]["provider"] == "deepseek"
    assert child_env["FORWARD_DEEPSEEK_API_KEY"] == "k1"

    mock_cfg, _ = config_builder.build_mock_config(
        project="p", eoicd_pub=None, eoicd_sub=None, hlr=hlr, use_trace=False)
    assert all("provider" not in j for j in mock_cfg["judges"])
    assert "provider" not in mock_cfg["arbitrator"]


def run_all():
    """供 tests/run_all.py 调用；任一断言失败即抛出。"""
    test_shared_helper_semantics()
    test_name_matcher_payload()
    test_name_translator_payload()
    test_attribute_matcher_payload()
    test_arbitrator_payload()
    test_cluster_ai_client_payload()
    test_config_builder_provider_fields()
    print("[OK] thinking toggle tests passed")
