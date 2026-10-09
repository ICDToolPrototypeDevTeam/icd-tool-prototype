# -*- coding: utf-8 -*-
"""LLM 判定结果缓存：内容寻址 + JSONL 增量落盘（反向管线中断续跑用）。

失效判断不做依赖分析 —— key 里编入实际发给模型的完整载荷（system_prompt +
user_prompt）以及 provider / model / 调用参数。上游任何变化（HLR 标签、匹配
结果、候选块内容、prompt 改字、profile 的 RPDU 附加段）都会改变 key 从而自动
失效，不需要维护「改了 A 要作废 B」的映射。

CACHE_VERSION 必须递增的场景（无法从 prompt 体现、但会改变结果的改动）：
  - LLM client 内部 payload（thinking / reasoning_effort / 截断重试的 max_tokens 倍增）
  - _extract_json 的解析与修复逻辑
  - 判定结果的 schema 或字段语义
  - 任何让「相同 prompt 应产生不同结果」的改动
temperature / max_tokens 已编入 key（见各调用点的 *_PARAMS 常量），无需手工递增。
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from pathlib import Path

CACHE_VERSION = 1
CACHE_FILENAME = "llm_cache.jsonl"

KIND_REVERSE_JUDGE = "reverse_judge"
KIND_CONSENSUS = "consensus"
KIND_RE_REVIEW = "re_review"
KIND_HLR_LABEL = "hlr_label"


def compute_key(
    *,
    kind: str,
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    params: dict | None = None,
) -> str:
    """对完整调用载荷取内容寻址 key。"""
    canonical = json.dumps(
        [CACHE_VERSION, kind, provider, model, params or {}, system_prompt, user_prompt],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_model(provider: str) -> str:
    """取该 provider 当前配置的模型名，用于参与 key。

    构造失败（缺 API key、未知 provider）时返回 ""：与真实 key 不匹配 → 走
    miss → 由调用点原有的异常处理降级。不能让查缓存阶段的异常炸掉整个 step，
    否则会破坏现有「单个 provider 失败不影响其余」的降级语义。
    """
    try:
        from app.v4.llm import get_llm

        return getattr(get_llm(provider), "model", "") or ""
    except Exception:  # noqa: BLE001 — 任何构造失败都归为「无法确定模型」
        return ""


class LLMCache:
    """单文件 JSONL 缓存。同一 job 一个实例，实例内 get/put 由锁保护。

    记录一行一条（绝不 indent，indent 会破坏「一行一条」），字段：
    key / cache_version / kind / provider / model / case_id / params / payload / ts
    case_id 只用于日志与审计，查找永远按 key。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._index: dict[str, dict] = {}
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def __len__(self) -> int:
        return len(self._index)

    def get(self, key: str) -> dict | None:
        with self._lock:
            return self._index.get(key)

    def put(
        self,
        key: str,
        *,
        kind: str,
        provider: str,
        model: str,
        case_id: str,
        payload: dict,
        params: dict | None = None,
    ) -> bool:
        """追加一条记录。同 key 已存在则跳过（幂等），返回是否真的写入。"""
        with self._lock:
            if key in self._index:
                return False
            self._append(
                {
                    "key": key,
                    "cache_version": CACHE_VERSION,
                    "kind": kind,
                    "provider": provider,
                    "model": model,
                    "case_id": case_id,
                    "params": params or {},
                    "payload": payload,
                    "ts": _now(),
                }
            )
            self._index[key] = payload
            return True

    def _load(self) -> None:
        if not self._path.exists():
            print(f"  [cache] {CACHE_FILENAME} not found, starting empty ({self._path})")
            return

        malformed = 0
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                self._index[record["key"]] = record["payload"]
            except Exception:  # noqa: BLE001 — 坏行（含被中断写出的半行）跳过即可
                malformed += 1

        print(f"  [cache] {CACHE_FILENAME}: {len(self._index)} entries loaded ({self._path})")
        if malformed:
            print(
                f"  [cache] {CACHE_FILENAME}: skipped {malformed} malformed line(s)",
                file=sys.stderr,
            )

    def _append(self, record: dict) -> None:
        # 单行 + 立即关闭句柄：进程被杀最多丢「正在写的那一行」，已写行不受影响
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def open_llm_cache(output_dir: Path | None) -> LLMCache | None:
    """按 job 的输出目录打开缓存。output_dir 为空时返回 None（调用方零分支）。"""
    if output_dir is None:
        return None
    return LLMCache(output_dir / CACHE_FILENAME)


class ReuseTracker:
    """恢复运行的实时复用计数：累计命中/重算的判定条数，变化时回调。

    回调（如 job.set_reuse_stats）在锁外执行，避免持锁写盘。
    """

    def __init__(self, on_change=None) -> None:
        self._lock = threading.Lock()
        self._on_change = on_change
        self.reused = 0
        self.rerun = 0

    def add(self, reused: int = 0, rerun: int = 0) -> None:
        if not reused and not rerun:
            return
        with self._lock:
            self.reused += reused
            self.rerun += rerun
            snapshot = (self.reused, self.rerun)
        if self._on_change is not None:
            self._on_change(*snapshot)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
