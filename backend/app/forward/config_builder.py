# -*- coding: utf-8 -*-
"""正向多 judge 配置生成（mock / 真实两套）+ base_url 归一化。

密钥纪律：真实 judge 的 key 只从进程环境（backend/.env → os.environ，由
app/v4/config.py 的 load_dotenv 填充）读取；写盘的配置里只出现 **env 变量名**
（api_key_env），与 正向待集成/multi_judge_config.json 的约定一致。明文 key 由
调用方经子进程 env 注入（见 execution.run_multi_judge(env_extra=...)）。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Mapping, Optional

# 本地 mock LLM 服务地址（backend/forward/mock_llm_server.py 监听 127.0.0.1:8731；
# 其只接受以 /chat/completions 结尾的 POST，故 base_url 必须带版本段）
MOCK_BASE_URL = "http://127.0.0.1:8731/v1"
MOCK_KEY_ENV = "MOCK_KEY"
MOCK_KEY_VALUE = "mock-key"

# 真实 judge 来源：复用 backend/.env 已有的三家密钥，不新增凭证
# (provider, env 前缀)
JUDGE_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("deepseek", "DEEPSEEK"),
    ("minimax", "MINIMAX"),
    ("qwen", "QWEN"),
)

_VERSION_SUFFIX_RE = re.compile(r"/v\d+[a-z0-9]*$", re.IGNORECASE)


class ForwardConfigError(RuntimeError):
    """无法生成可运行的正向配置（如 .env 里一个可用 judge 都没有）。"""


def normalize_base_url(base_url: str) -> str:
    """补版本段。

    正向 AI 客户端按 ``base_url + "/chat/completions"`` 拼 URL（不自动补 /v1），
    而 .env 里默认值多为站点根（https://api.deepseek.com），直接拼会 404；已含
    ``/vN``（如 qwen 的 .../compatible-mode/v1）的原样返回。
    """
    url = (base_url or "").strip().rstrip("/")
    if not url or _VERSION_SUFFIX_RE.search(url):
        return url
    return url + "/v1"


def _get(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def _base_config(*, project: str, eoicd_pub: Optional[Path], eoicd_sub: Optional[Path],
                 hlr: Path, use_trace: bool) -> dict:
    """两套配置共用的骨架（runner.load_config 把 input.* 提升为顶层绝对路径）。

    单侧上传：缺哪侧就不写哪个键。写 None 会落盘成字面量 "None"，被
    runner.load_config 的真值判断提升为 ``<配置目录>/None``，阶段1 以误导路径
    报错（应为既有的「输入文件不存在: …_Publisher_Table.xlsx」）。
    """
    inp: dict[str, str] = {"hlr": str(hlr), "trace_dir": ""}
    if eoicd_pub is not None:
        inp["eoicd_pub"] = str(eoicd_pub)
    if eoicd_sub is not None:
        inp["eoicd_sub"] = str(eoicd_sub)
    return {
        "project": project,
        "input": inp,
        "device": "",
        "use_trace": bool(use_trace),
        "aggregation": {"need_review_on_split": True, "need_review_rules": {}},
        "re_review": {"enabled": True, "max_rounds": 1},
    }


def build_mock_config(*, project: str, eoicd_pub: Optional[Path], eoicd_sub: Optional[Path],
                      hlr: Path, use_trace: bool) -> tuple[dict, dict[str, str]]:
    """mock 配置：3 个 judge 指向本机 mock 服务（对齐源目录 mock 模板）。

    两个模型名（mock-ds / mock-qwen）对应 mock 服务的不同判定种子，第三个是同模型
    稳定性重复——与源目录 mock 模板一致，保证 MOCK 行为与正向侧自测一致。
    """
    cfg = _base_config(project=project, eoicd_pub=eoicd_pub, eoicd_sub=eoicd_sub,
                       hlr=hlr, use_trace=use_trace)
    cfg["judges"] = [
        {"name": "judge_ds", "model": "mock-ds",
         "base_url": MOCK_BASE_URL, "api_key_env": MOCK_KEY_ENV},
        {"name": "judge_qwen", "model": "mock-qwen",
         "base_url": MOCK_BASE_URL, "api_key_env": MOCK_KEY_ENV},
        {"name": "judge_ds_stability", "model": "mock-ds",
         "base_url": MOCK_BASE_URL, "api_key_env": MOCK_KEY_ENV},
    ]
    cfg["arbitrator"] = {"model": "mock-arb", "base_url": MOCK_BASE_URL,
                         "api_key_env": MOCK_KEY_ENV, "allow_same_as_judge": True}
    return cfg, {MOCK_KEY_ENV: MOCK_KEY_VALUE}


def build_real_config(*, project: str, eoicd_pub: Optional[Path], eoicd_sub: Optional[Path],
                      hlr: Path, use_trace: bool,
                      env: Optional[Mapping[str, str]] = None) -> tuple[dict, dict[str, str]]:
    """真实配置：.env 里存在密钥的 provider 各出一个 judge。

    仲裁视角固定用 **deepseek**（用户指定）；未配置 deepseek 时回落第一个可用
    judge，并保留模板默认 allow_same_as_judge=true（报告会如实标注仲裁与 judge
    同源、独立性受限）。
    """
    env = env if env is not None else os.environ
    judges: list[dict] = []
    child_env: dict[str, str] = {}
    for provider, prefix in JUDGE_PROVIDERS:
        key = _get(env, f"{prefix}_API_KEY")
        model = _get(env, f"{prefix}_MODEL")
        base = normalize_base_url(_get(env, f"{prefix}_BASE_URL"))
        if not (key and model and base):
            continue
        api_key_env = f"FORWARD_{prefix}_API_KEY"
        judges.append({"name": f"judge_{provider}", "model": model,
                       "base_url": base, "api_key_env": api_key_env})
        child_env[api_key_env] = key
    if not judges:
        raise ForwardConfigError(
            "未找到任何可用的正向 judge 密钥：请在 backend/.env 配置 "
            "DEEPSEEK_API_KEY / MINIMAX_API_KEY / QWEN_API_KEY（及对应 MODEL、BASE_URL）")

    arb_src = next((j for j in judges if j["name"] == "judge_deepseek"), judges[0])
    cfg = _base_config(project=project, eoicd_pub=eoicd_pub, eoicd_sub=eoicd_sub,
                       hlr=hlr, use_trace=use_trace)
    cfg["judges"] = judges
    cfg["arbitrator"] = {"model": arb_src["model"], "base_url": arb_src["base_url"],
                         "api_key_env": arb_src["api_key_env"], "allow_same_as_judge": True}
    return cfg, child_env


def write_config(config: dict, path: Path) -> Path:
    """配置落盘（UTF-8 JSON）；调用方把它作为 runner 的 --config。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
