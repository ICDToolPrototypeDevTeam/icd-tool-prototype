# -*- coding: utf-8 -*-
"""Forward ICD block builder (Stage C3).

Groups leaf DP/RP EoICD entries into business-signal-level blocks
(ForwardICDBlock), each carrying a stable business_object_id derived from the
standardized EoICD business identity (NOT the raw trace FullName, NOT hash()):

  - A429 (label-based):                 L{label}/{family}
  - A429 (word-based, e.g. A664/CAN):   {family}
  - non-A429 (A825/Analog/Discrete):    {device}/{message_or_port}/{signal}

正向缺陷修正 #2：non-A429 的 identity 必须包含 device，禁止跨设备聚合。
  * A825 = device + message/port + signal
  * Analog = device + channel/port + signal
  * Discrete = device + port + signal
  6 个风扇设备（HF_AFTEFAN1/2、HF_FWDBFAN1/2、HF_FWDEFAN1/2）此前因仅按
  family（po825_FAN_STATUS_MSG/SPEED）聚合被合并为单个 block，现按 device
  拆分。DP/RP 若同设备同端口同信号仍会聚合（显式 DP/RP 关系）。

Protocol classification uses layer_path_types + signal_name + Label. Native
A664 (an A664Message layer with no A429Word/A429Channel) is marked unsupported
and KEPT in the output (not silently dropped) for later reporting.
"""

from __future__ import annotations

import re
from collections import defaultdict

from app.v4.config import SIGNAL_LEAF_ALIASES
from app.v4.matching.entry_filter import should_keep
from app.v4.matching.eoicd_enricher import _get_synonym_lookup, _resolve_aliases
from app.v4.matching.hlr_identity_index import tokenize_identifier
from app.v4.matching.signal_profiler import (
    _extract_label_value,
    _extract_profile_key,
    _extract_signal_family,
)
from app.v4.models import (
    EoICDOutput,
    ForwardBlocksOutput,
    ForwardICDBlock,
    ForwardIdentity,
    ForwardScopeItem,
    ForwardScopeOutput,
)
from app.v4.traceability.trace_parser import name_to_block_key

_LABEL_SEGMENT_RE = re.compile(r"^(L\d+)", re.IGNORECASE)
# Channel variants A1/A2/B1/B2. 正向缺陷修正 #7：`\b` 在 "_"（word char）前不构成
# 边界，导致 "pi429_B2"/"L275_2_B2_..." 这类下划线名无法提取通道；改用
# "前一位非字母数字、后一位非数字" 边界，覆盖下划线/点分隔与独立片段。
_CHANNEL_RE = re.compile(r"(?<![A-Za-z0-9])([AB][1-4])(?![0-9])")

# Protocol-overhead family names (same convention as signal_profiler.build_blocks).
_PROTOCOL_FAMILIES = {"LABEL", "SDI", "SSM", "PARITY", "OCTLBL"}


def _has_label(signal_name: str) -> bool:
    for seg in (signal_name or "").split("."):
        if _LABEL_SEGMENT_RE.match(seg.strip()):
            return True
    return False


def _classify_protocol(
    layer_path_types: list[str] | None,
    signal_name: str,
    bus_type: str,
) -> tuple[str, bool]:
    """Return (protocol, unsupported) for one leaf entry.

    protocol ∈ {A429, A825, Analog, Discrete, A664, unknown}.
    unsupported is True only for native A664 (A664Message without A429Word).

    Bus-specific bus_type (A825/CAN, Analog, Discrete, A664) takes priority over
    the generic "A429Word"/"A429Channel" layer-type heuristic, because CAN (A825)
    messages also carry an "A429Word" sub-layer as a data-model artifact — a
    CANMessage must stay A825, not be mis-classified A429.
    """
    upper_types = {x.upper() for x in (layer_path_types or [])}
    bt = (bus_type or "").strip().upper()

    if bt in ("A825", "CAN") or "CANMESSAGE" in upper_types:
        return "A825", False
    if bt == "ANALOG":
        return "Analog", False
    if bt == "DISCRETE":
        return "Discrete", False
    if bt == "A664":
        # Native A664 (no A429 label) → unsupported; an A664 carrying an A429
        # label is really A429 (correction 八).
        return ("A429", False) if _has_label(signal_name) else ("A664", True)

    if (
        bt == "A429"
        or _has_label(signal_name)
        or "A429WORD" in upper_types
        or "A429CHANNEL" in upper_types
    ):
        return "A429", False
    if "A664MESSAGE" in upper_types:
        return "A664", True
    return (bt or "unknown"), False


def _split(signal_name: str) -> list[str]:
    return [s.strip() for s in (signal_name or "").split(".") if s.strip()]


def _derive_identity(signal_name: str, protocol: str) -> ForwardIdentity:
    segs = _split(signal_name)
    leaf = segs[-1] if segs else signal_name
    profile_key = _extract_profile_key(signal_name)
    label = _extract_label_value(profile_key)
    family = _extract_signal_family(profile_key)
    device = segs[0] if segs else ""

    if protocol == "A429" and label:
        identity_key = f"L{label}/{family}"
    elif protocol == "A429":
        # word-based A429 (e.g. A664/CAN carrying A429 words, no explicit label):
        # keep device-agnostic family (no Label to anchor device-level identity).
        identity_key = family
    else:
        # non-A429 (A825/Analog/Discrete): device + message/port + signal.
        # `family` for non-label already encodes "{port}/{leaf}" (see
        # _extract_signal_family), so prepending device separates same-port
        # signals across different devices (正向缺陷修正 #2).
        identity_key = f"{device}/{family}" if device else family

    return ForwardIdentity(
        identity_key=identity_key,
        protocol=protocol,
        label=label or "",
        signal_family=family,
        device=device,
        port=segs[1] if len(segs) >= 2 else "",
        message=segs[-2] if len(segs) >= 2 else "",
        signal=leaf,
    )


def _collect_aliases(signal_name: str, family: str, syn: dict) -> list[str]:
    """Collect English + Chinese alias tokens for a block."""
    segs = _split(signal_name)
    leaf = segs[-1] if segs else ""

    aliases: set[str] = set()
    if leaf:
        aliases.add(leaf.lower())
        aliases.update(a.lower() for a in _resolve_aliases(leaf, syn))
        # Chinese aliases from the common-sense leaf map.
        for cn in SIGNAL_LEAF_ALIASES.get(leaf.upper(), []):
            aliases.add(cn)
    if family:
        aliases.add(family.lower())
        # Family tokens (underscore-split) as additional match surface.
        for tok in re.split(r"[_/\s]+", family):
            tok = tok.strip().lower()
            if tok:
                aliases.add(tok)
                for cn in SIGNAL_LEAF_ALIASES.get(tok.upper(), []):
                    aliases.add(cn)
    return sorted(a for a in aliases if a)


def _extract_channels(signal_names: list[str]) -> list[str]:
    channels: set[str] = set()
    for name in signal_names:
        for seg in _split(name):
            for m in _CHANNEL_RE.findall(seg):
                channels.add(m)
    return sorted(channels)


# ── A429 sub-object identity (SDI / bit) ────────────────────────────────────
#
# 正向统一判定规则：A429 块的身份在 Label 之外还可用 SDI（区分不同业务子对象）
# 与 bit（区分同一 Label 下的不同字段）辅助判定。二者都只在证据可靠时使用：
#   - SDI：仅当该 Label 存在 >1 个不同非 N/A 的 SDI 值（`sdi_is_discriminator`）。
#   - bit：叶子的"自身" bit 通过结构化关系推导（非 dp_ref 的 BitOffsetWithinDS，
#     或 dp_ref 子字段名与叶名对应），不依据 DataFormatType/ParameterSize 做
#     "排除 BOOL / 优先 BNR" 等类型推断；推导不可靠时不留 bit 证据（不猜测）。

# SDIExpected = -1 表示 N/A；空串表示未出现。
_NA_SDI = {"", "-1"}


def _derive_block_sdi(sdi_values: set[str]) -> str:
    """单一非 N/A SDI 值 → 该值；否则空串（无证据或歧义）。"""
    vals = {v for v in sdi_values if v not in _NA_SDI}
    return next(iter(vals)) if len(vals) == 1 else ""


def _name_corresponds(family: str, name: str) -> bool:
    """True if a dp_ref sub-field name shares a significant token with the family."""
    if not family or not name:
        return False
    fam_toks = set(tokenize_identifier(family))
    name_toks = set(tokenize_identifier(name))
    sig = {t for t in (fam_toks & name_toks) if len(t) >= 3}
    return bool(sig)


def _derive_bit_fields(
    bit_rows: list[tuple[str, bool, str]],
    size_rows: list[tuple[str, bool, str]],
    family: str,
) -> list[dict]:
    """Derive the leaf's own bit range(s) [{offset, size}] via structured relation.

    只认两种可靠来源：
      1. 非 dp_ref 的 BitOffsetWithinDS（叶自身声明的 bit）+ 非 dp_ref ParameterSize；
      2. dp_ref 子字段中，名称与叶名（family）有显著 token 对应的那一个。
    其余情况返回空列表（不使用 bit 证据，不猜测）。
    """
    offset_by: dict[tuple[bool, str], int] = {}
    for val, ref, name in bit_rows:
        try:
            offset_by.setdefault((ref, name), int(val))
        except (TypeError, ValueError):
            continue
    if not offset_by:
        return []
    size_by: dict[tuple[bool, str], int] = {}
    for val, ref, name in size_rows:
        try:
            size_by.setdefault((ref, name), int(val))
        except (TypeError, ValueError):
            continue

    own_key = (False, "")
    if own_key in offset_by:
        return [{"offset": offset_by[own_key], "size": size_by.get(own_key, 1)}]

    ref_names = {name for (ref, name) in offset_by if ref}
    matches = [n for n in ref_names if _name_corresponds(family, n)]
    if len(matches) == 1:
        k = (True, matches[0])
        return [{"offset": offset_by[k], "size": size_by.get(k, 1)}]
    return []


def build_forward_blocks(
    eoicd: EoICDOutput,
    scope: ForwardScopeOutput | None = None,
) -> ForwardBlocksOutput:
    """Build business-level blocks from leaf EoICD entries.

    In trace mode (scope.analysis_mode == "trace"), only blocks whose
    business_object_id maps from a scope FullName are kept, and each kept block
    carries the merged scope item(s) in `trace` (candidate_hlr_ids included).
    In full mode, all blocks are kept with trace=None.
    """
    syn = _get_synonym_lookup()
    analysis_mode = scope.analysis_mode if scope else "full"

    # 1. Trace-mode scope: identity key -> scope items (for candidate-HLR merge).
    scope_items_by_identity: dict[str, list[ForwardScopeItem]] = defaultdict(list)
    if scope and analysis_mode == "trace":
        for item in scope.scope_items:
            key = name_to_block_key(item.icd_fullname)
            if key:
                scope_items_by_identity[key].append(item)

    # 2. Filter + classify + group leaf entries by identity key.
    grouped: dict[str, dict] = {}
    order: list[str] = []
    label_sdi_values: dict[str, set[str]] = defaultdict(set)

    for req in eoicd.requirements:
        if req.layer_type not in ("DP", "RP"):
            continue
        if not should_keep(req):
            continue

        protocol, unsupported = _classify_protocol(
            req.layer_path_types, req.signal_name, req.bus_type
        )
        identity = _derive_identity(req.signal_name, protocol)

        # Skip protocol-overhead families (LABEL/SDI/SSM/PARITY/OCTLBL).
        if identity.signal_family.upper().strip("_") in _PROTOCOL_FAMILIES:
            continue

        key = identity.identity_key
        if key not in grouped:
            grouped[key] = {
                "identity": identity,
                "unsupported": unsupported,
                "protocol": protocol,
                "dp_signal_names": set(),
                "rp_signal_names": set(),
                "dp_entry_ids": [],
                "rp_entry_ids": [],
                "all_signal_names": set(),
                "devices": set(),
                "sdi_values": set(),
                "bit_rows": [],
                "size_rows": [],
            }
            order.append(key)

        g = grouped[key]
        g["unsupported"] = g["unsupported"] or unsupported
        g["all_signal_names"].add(req.signal_name)
        g["devices"].add(identity.device)
        if req.side == "DP":
            g["dp_signal_names"].add(req.signal_name)
            g["dp_entry_ids"].append(req.ird_id)
        else:
            g["rp_signal_names"].add(req.signal_name)
            g["rp_entry_ids"].append(req.ird_id)

        # Collect A429 sub-object identity attributes (SDI / bit / size).
        attr = req.attribute_name
        if attr == "SDIExpected":
            v = str(req.attribute_value).strip()
            g["sdi_values"].add(v)
            if identity.label:
                label_sdi_values[identity.label].add(v)
        elif attr == "BitOffsetWithinDS":
            g["bit_rows"].append((str(req.attribute_value).strip(), req.is_dp_ref, req.dp_ref_name or ""))
        elif attr == "ParameterSize":
            g["size_rows"].append((str(req.attribute_value).strip(), req.is_dp_ref, req.dp_ref_name or ""))

    # 3. Assemble blocks.
    blocks: list[ForwardICDBlock] = []
    for key in order:
        g = grouped[key]
        identity: ForwardIdentity = g["identity"]
        all_names = sorted(g["all_signal_names"])

        # A429 sub-object identity（SDI 仅在 Label 级歧义时启用；bit 仅在可靠时留证）。
        if identity.protocol == "A429":
            identity.sdi = _derive_block_sdi(g["sdi_values"])
            identity.bit_fields = _derive_bit_fields(g["bit_rows"], g["size_rows"], identity.signal_family)
            label_sdis = {v for v in label_sdi_values.get(identity.label, set()) if v not in _NA_SDI}
            identity.sdi_is_discriminator = len(label_sdis) > 1

        block = ForwardICDBlock(
            business_object_id=key,
            identity=identity,
            dp_signal_names=sorted(g["dp_signal_names"]),
            rp_signal_names=sorted(g["rp_signal_names"]),
            aliases=_collect_aliases(next(iter(all_names), key), identity.signal_family, syn),
            dp_entry_ids=g["dp_entry_ids"],
            rp_entry_ids=g["rp_entry_ids"],
            variants=_extract_channels(all_names),
            devices=sorted(g["devices"]),
            trace=None,
            unsupported=g["unsupported"],
        )

        if analysis_mode == "trace":
            scope_items = scope_items_by_identity.get(key, [])
            if not scope_items:
                continue  # not in trace scope
            # Merge candidate HLRs across all matching scope items (raw + missing).
            merged_hlr: list[str] = []
            merged_missing: list[str] = []
            merged_erd: list[str] = []
            for item in scope_items:
                for h in item.candidate_hlr_ids:
                    if h not in merged_hlr:
                        merged_hlr.append(h)
                for h in item.missing_hlr_ids:
                    if h not in merged_missing:
                        merged_missing.append(h)
                for e in item.erd_ids:
                    if e not in merged_erd:
                        merged_erd.append(e)
            block.trace = ForwardScopeItem(
                icd_fullname=scope_items[0].icd_fullname,
                protocol=identity.protocol,
                erd_ids=merged_erd,
                candidate_hlr_ids=merged_hlr,
                missing_hlr_ids=merged_missing,
                located_eoicd_signal_names=all_names,
            )

        blocks.append(block)

    return ForwardBlocksOutput(
        analysis_mode=analysis_mode,
        total_blocks=len(blocks),
        blocks=blocks,
    )
