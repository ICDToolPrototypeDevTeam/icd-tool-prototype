# -*- coding: utf-8 -*-
"""确定性相关信号处理：只保留与 HLR 明确引用的信号名相关的 matched ICD Block，
并可对"匹配层 top-N 候选漏采"的信号从完整 ICD Block 索引补回。

背景（case01 验收问题 1）：RPDU profile 的 top_k=50 反向匹配会给每条 HLR 返回
50 个候选 ICD Block，报告中混入大量与需求无关的信号（例如加热组需求混入 EEC/FCM
信号，Drain_VLV 需求只匹配到 RPDU 故障/状态块）；同时 top-50 候选也可能漏掉真正
相关的信号（例如 Drain_VLV_RPDU_ESW_CMD 在完整 ICD 中存在，却未进入该 HLR 的
候选池）。

设计原则：
- 纯新增模块，不修改任何已有匹配/裁判/报告代码；输出与 reverse_matches.json 同构。
- 只保留「leaf 信号名（去掉 RX_/TX_/DS 等方向/数据源前缀后）精确命中 HLR 中出现的
  信号名（含 HLR label 的 signal_keywords）」的 block；语义相关但无精确名称的证据
  留给 AI 裁判判断，本层不做语义猜测，保证可审计、可复现。
- 当传入 block_index（完整 ICD Block 索引）时，额外补采完整 ICD 中同名但未被
  top-N 候选覆盖的 block，消除"匹配层漏采导致误判需确认"的问题。
"""
from __future__ import annotations

import re
from typing import Optional

from app.v4.config import load_synonyms

# 方向/数据源前缀：比较前从 leaf 信号名与 HLR 信号名两侧剥离
_LEAF_PREFIX_RE = re.compile(r"^(?:RX_|TX_|DS_?\d*_)+", re.IGNORECASE)
# HLR 正文中的下划线式信号 token（如 Heater_Group_3_RPDU_ESW_CMD）
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+")
# leaf 名称中的分隔符（子串比较前去掉 _ - 空格）
_SEP_RE = re.compile(r"[\s_\-]+")


def _to_label_dict(hlr_label):
    """把 HLR label 统一转成 dict：兼容 plain dict 与 pydantic HLRLabel。

    pipeline 内嵌路径传入的 hlr_labels 值为 pydantic 模型（无 .get），
    缓存文件路径为 plain dict；统一转 dict 后下游按 dict 访问。
    """
    if hlr_label is None:
        return {}
    if isinstance(hlr_label, dict):
        return hlr_label
    if hasattr(hlr_label, "model_dump"):
        try:
            return hlr_label.model_dump()
        except Exception:
            pass
    if hasattr(hlr_label, "signal_keywords"):
        return {"signal_keywords": list(hlr_label.signal_keywords or [])}
    return {}


def _normalize_signal_name(name: str) -> str:
    """去掉方向/数据源前缀并转大写，用于信号名比较。"""
    s = (name or "").strip().upper()
    return _LEAF_PREFIX_RE.sub("", s)


def _extract_underscore_tokens(content: str) -> set[str]:
    """从 HLR 正文提取下划线式 token（大写）。"""
    if not content:
        return set()
    return {t.upper() for t in _TOKEN_RE.findall(content) if len(t) >= 3}


def hlr_signal_names(hlr_content: str, hlr_label: Optional[dict]) -> set[str]:
    """构造 HLR 关注的信号名集合（规范化后）。

    来源：
      1) HLR 正文中出现的下划线式信号 token；
      2) HLR label 的 signal_keywords（LLM 抽取的精确信号名）。
    """
    names: set[str] = set()
    for t in _extract_underscore_tokens(hlr_content):
        names.add(t)
        names.add(_normalize_signal_name(t))
    hlr_label = _to_label_dict(hlr_label)
    for kw in (hlr_label.get("signal_keywords") or []):
        k = (kw or "").strip()
        if not k:
            continue
        names.add(k.upper())
        names.add(_normalize_signal_name(k))
    return names


# 补采专用：从"原有"app/v4/synonyms.yaml（config.load_synonyms）读取"信号名级"同义词组。
# 仅白名单组参与补采（如 Airspeed → 空速/空速信号），
# 不扫描总线/设备别名（A664/AFDX/FCM/FAN等），避免子串匹配把无关 block 拉入候选池。
RECOVERY_SYNONYM_GROUPS: tuple[str, ...] = ("Airspeed",)
# 英文词条判定：仅由字母/数字/下划线组成（中文词条不参与子串补采）
_EN_TERM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*$")


def hlr_synonym_english_terms(hlr_content: str, hlr_label: Optional[dict]) -> set[str]:
    """从 HLR 中文信号描述解析出英文同义词条（如 空速 → AIRSPEED/AIR_SPEED）。

    数据源为原有 app/v4/synonyms.yaml 的白名单组（config.load_synonyms），
    与项目原有同义词机制（eoicd_enricher）共用同一份映射，避免新增重复内容。
    用于把 HLR 中"没有下划线信号名、只有中文描述"的信号（如"空速信号"）映射到
    ICD 的英文 leaf 名，再做子串补采。只检索 RECOVERY_SYNONYM_GROUPS
    （信号名级同义词），不扫描总线/设备别名，防止过度匹配。
    """
    pieces = [hlr_content or ""]
    hlr_label = _to_label_dict(hlr_label)
    for kw in (hlr_label.get("signal_keywords") or []):
        pieces.append(kw or "")
    haystack = "\n".join(pieces)
    terms: set[str] = set()
    try:
        raw = load_synonyms()
    except Exception:
        return terms
    for group in RECOVERY_SYNONYM_GROUPS:
        aliases = [a for a in (raw.get(group) or []) if isinstance(a, str)]
        if not any(a and a in haystack for a in aliases):
            continue
        for a in aliases:
            if a and _EN_TERM_RE.match(a):
                terms.add(a.upper())
    return terms


def _leaf_contains_term(block_key: str, terms: set[str]) -> bool:
    """leaf 信号名（去分隔符后）是否包含任一英文同义词词条。"""
    if not terms:
        return False
    leaf = _leaf_of(block_key)
    if not leaf:
        return False
    flat = _SEP_RE.sub("", leaf).upper()
    for t in terms:
        if t and t in flat:
            return True
    return False


def _leaf_of(block_key: str) -> str:
    """取 block_key 中 '/' 后的 leaf 信号名；无 '/' 时取整体。"""
    if "/" in block_key:
        return block_key.rsplit("/", 1)[1]
    return block_key


def keep_block_key(block_key: str, hlr_names: set[str]) -> bool:
    """block 是否与 HLR 相关：规范化 leaf 精确命中 HLR 信号名集合即保留。"""
    if not hlr_names:
        return False
    leaf = _normalize_signal_name(_leaf_of(block_key))
    if not leaf:
        return False
    return leaf in hlr_names


def filter_matched_blocks(
    match_data: dict,
    hlr_labels: Optional[dict] = None,
    block_index: Optional[dict] = None,
) -> dict:
    """原地处理 reverse_matches.json 数据：过滤无关 block + 从完整 ICD 补采漏采信号。

    参数：
      match_data:  reverse_matches.json 反序列化后的 dict（会被原地修改并返回）。
      hlr_labels:  hlr_labels.json 的顶层 dict（含 "labels": {hlr_id: HLRLabel}）
                   或直接是 {hlr_id: HLRLabel}；用于补充 signal_keywords。
      block_index: 完整 ICD Block 索引 {block_key: ICDBlock}。传入时会对每个 HLR
                   补采"完整 ICD 中存在同名 leaf 定义但不在 top-N 候选内"的 block。

    返回：修改后的 match_data（同构），并更新 matched_profile_count /
          match_evidence.matched_block_keys|count / top_scores 与 stats。
    """
    labels: dict = {}
    if isinstance(hlr_labels, dict):
        inner = hlr_labels.get("labels", hlr_labels)
        if isinstance(inner, dict):
            labels = inner

    results = match_data.get("results", [])
    for r in results:
        content = r.get("hlr_content", "")
        label = _to_label_dict(labels.get(r.get("hlr_id", "")))
        names = hlr_signal_names(content, label)
        keys = r.get("matched_profile_keys", []) or []
        kept = [k for k in keys if keep_block_key(k, names)]
        kept_set = set(kept)

        # 补采：匹配层 top-N 漏掉的、但在完整 ICD 中存在同名定义的 block
        if block_index is not None:
            for bk in block_index:
                if bk not in kept_set and keep_block_key(bk, names):
                    kept.append(bk)
                    kept_set.add(bk)

        # 同义词补采：HLR 中文术语（如 空速→airspeed）匹配的 ICD leaf
        # 仅在该 HLR 没有任何精确信号名匹配时才启用，
        # 避免"风扇→FAN"等既有同义词对已精确匹配 HLR 的再污染
        if not kept_set and block_index is not None:
            syn_terms = hlr_synonym_english_terms(content, label)
            if syn_terms:
                for bk in block_index:
                    if _leaf_contains_term(bk, syn_terms):
                        kept.append(bk)
                        kept_set.add(bk)

        r["matched_profile_keys"] = kept
        r["matched_profile_count"] = len(kept)

        ev = r.get("match_evidence")
        if isinstance(ev, dict):
            ev["matched_block_keys"] = kept
            ev["matched_block_count"] = len(kept)
            ev["recovered_block_count"] = len(kept) - len([k for k in kept if k in set(keys)])
            top = ev.get("top_scores") or []
            if top:
                # 保留原有 top_scores 中仍被采纳者；补采的 block 无 top_scores 条目
                ev["top_scores"] = [ts for ts in top if ts.get("block_key") in kept_set]

    # 重新统计：相关 block 的并集
    all_matched: set[str] = set()
    for r in results:
        all_matched.update(r.get("matched_profile_keys", []) or [])
    stats = match_data.setdefault("stats", {})
    stats["eoicd_blocks_total"] = len(all_matched)
    stats["eoicd_blocks_matched"] = len(all_matched)
    stats["eoicd_blocks_unmatched"] = 0
    return match_data