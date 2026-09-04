# -*- coding: utf-8 -*-
"""Signal profile clustering: group EoICD entries by Label segment for profile-level matching.

For A429 signals with a Label segment, sub-cluster by leaf node so that
protocol overhead (OCTLBL/SDI/SSM_BNR/PARITY) and data fields (BNR/DIS)
have separate profiles — avoiding cross-contamination of attributes.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.v4.config import ATTR_CATEGORY_MAP, SEND_VERBS, RECEIVE_VERBS, PROTOCOL_DATAFORMATS, REVERSE_KEY_ATTRS
from app.v4.matching.eoicd_enricher import _resolve_aliases, _get_synonym_lookup, _tokenize_name
from app.v4.models import EoICDRequirement

_LABEL_SEGMENT_RE = re.compile(r"^(L\d+)", re.IGNORECASE)


def _extract_profile_key(signal_name: str) -> str:
    """Extract profile clustering key from signal_name.

    For A429 signals with a Label segment: "Lxxx/LeafName" — sub-clustering
    by leaf node within the Label so that protocol overhead and data fields
    don't mix attributes.

    For non-Label signals (CAN/A825 etc.): uses the parent port name as a
    clustering prefix — "pi825/SPEED_CMD" — so that different ports'
    same-named signals don't merge into a single block.
    """
    if not signal_name:
        return "_unknown_"
    segments = [s.strip() for s in signal_name.split(".") if s.strip()]
    label_seg = None
    for seg in segments:
        m = _LABEL_SEGMENT_RE.match(seg)
        if m:
            label_seg = m.group(1)
            break

    leaf = segments[-1] if segments else signal_name

    if label_seg:
        # A429 Label signal: sub-cluster by leaf node
        if leaf.upper() == label_seg.upper():
            return f"{label_seg}/__label__"
        return f"{label_seg}/{leaf}"

    # Non-Label signal (CAN/A825 etc.): use {parent}/{leaf} for clustering.
    # Parent is typically the port name (e.g. "pi825") which distinguishes
    # same-named signals on different buses.
    if len(segments) >= 2:
        parent = segments[-2]
        return f"{parent}/{leaf}"

    return leaf if segments else signal_name


def _extract_label_value(profile_key: str) -> str | None:
    """Extract numeric label value from profile key. Returns None for non-Label profiles."""
    m = _LABEL_SEGMENT_RE.match(profile_key)
    if m:
        return m.group(1)[1:]  # strip leading L
    return None


@dataclass
class SignalProfile:
    """Clustered EoICD entries sharing the same signal Label / leaf name.

    All entries in a profile share a Label number or signal semantic identity.
    Profiles are aggregated into ICDBlocks (by label + signal_family) for
    HLR → EoICD reverse matching.
    """

    profile_key: str                     # "L214" | "SPEED" etc.
    label: str | None                    # "214" (A429 only)
    direction: str = ""                  # "发送" | "接收" | "发送/接收"
    bus_types: set[str] = field(default_factory=set)
    entries: list[EoICDRequirement] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)  # {attr_name: {value, unit, entry_id}}

    # Pre-computed aggregated token sets (filled by build_profiles)
    device_tokens_set: set[str] = field(default_factory=set)
    leaf_signal_aliases_set: set[str] = field(default_factory=set)
    bus_aliases_set: set[str] = field(default_factory=set)
    direction_verbs_set: set[str] = field(default_factory=set)


# ── Signal family extraction ──────────────────────────────────────

# Strip L{label}_[{port}_]{bus_ch}_ prefix from leaf names.
# Port number is optional — some leaf names skip it: L121_A1_SIGNAL vs L34_1_A1_SIGNAL
_SIGNAL_FAMILY_PREFIX_RE = re.compile(r"^L\d+_(?:\d+_)?[A-Z]+\d+_")


def _extract_signal_family(profile_key: str) -> str:
    """Extract semantic signal family name from a profile key.

    Label:   'L34/L34_1_A1_AFTEFAN1_HW_FAULT' -> 'AFTEFAN1_HW_FAULT'
    Non-Label: 'pi825/SPEED_CMD' -> 'pi825/SPEED_CMD' (full key as family)

    For non-Label signals the full profile_key is used so that
    (label=None, family="pi825/SPEED_CMD") and (label=None, family="pi826/SPEED_CMD")
    stay in separate blocks.
    """
    if "/" not in profile_key:
        return profile_key

    leaf = profile_key.split("/")[-1]
    m = _SIGNAL_FAMILY_PREFIX_RE.match(leaf)
    if m:
        return leaf[m.end():]

    # Non-Label key like "pi825/SPEED_CMD" — use full key as family.
    if not _LABEL_SEGMENT_RE.match(profile_key):
        return profile_key

    return leaf


# ── ICD Block ────────────────────────────────────────────────────


@dataclass
class ICDBlock:
    """Group of SignalProfiles sharing the same signal family under a Label.

    Multiple leaf profiles that represent the same signal across different
    channels/buses are merged into one block.  The block — not the individual
    profile — is the matching unit for reverse (HLR → EoICD) comparison.
    """

    block_key: str                     # "L34/AFTEFAN1_HW_FAULT"
    label: str | None                  # "34" (A429 only)
    signal_family: str                 # "AFTEFAN1_HW_FAULT"
    direction: str = ""                # "发送" | "接收" | "发送/接收"
    bus_types: set[str] = field(default_factory=set)
    profiles: list[SignalProfile] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)

    # Aggregated token sets
    device_tokens_set: set[str] = field(default_factory=set)
    leaf_signal_aliases_set: set[str] = field(default_factory=set)
    bus_aliases_set: set[str] = field(default_factory=set)
    direction_verbs_set: set[str] = field(default_factory=set)

    channel_count: int = 0

    # Sub-signals within the same label at different bit offsets (L126-class).
    # Populated only when label has >1 data profiles with distinct bit positions.
    # Each entry: {"dp_name": str, "bit_offset": str, "size": str, "dtype": str}
    sub_signals: list[dict] = field(default_factory=list)

    # Same-word protocol field definitions attached for judge-side context
    # (SSM enrichment). Not a matching candidate — only serialized into the
    # case so the judge can verify HLR assertions about protocol bits.
    # Each entry: {"name": str, "attrs": {attr: value}}
    word_protocol_fields: list[dict] = field(default_factory=list)


def build_blocks(profiles: list[SignalProfile]) -> list[ICDBlock]:
    """Group SignalProfiles into ICDBlocks by signal family.

    Profiles whose signal family is a protocol overhead node (LABEL, SSM,
    PARITY, OCTLBL) are skipped — they don't represent data signals.
    SDI is kept as a block: the SDI leaf carries business semantics
    (CodedSet, e.g. 1=System1) and HLRs assert SDI values explicitly;
    its matching eligibility is gated in reverse_matcher by HLR text.
    """
    groups: dict[tuple[str | None, str], list[SignalProfile]] = defaultdict(list)
    for prof in profiles:
        label = prof.label
        family = _extract_signal_family(prof.profile_key)
        groups[(label, family)].append(prof)

    protocol_names = {"LABEL", "SSM", "PARITY", "OCTLBL"}

    # Same-word protocol-field anchors: label → [{name, attrs}], covering the
    # LABEL/SDI/SSM/PARITY protocol leaves of an A429 word.  These leaves carry
    # the word's bit layout (BitOffsetWithinDS/ParameterSize/DataFormatType)
    # and, for SDI, value semantics (CodedSet).  LABEL/SSM/PARITY/OCTLBL stay
    # out of block matching (protocol_names above); SDI becomes a block *and*
    # is anchored here too so that data-block cases still expose the SDI field
    # position.  The anchor rows give the judge (1) the ICD bit-offset base
    # (offset=0/8/29/31 → physical 1-8/9-10/30-31/32) from data alone, and
    # (2) grounds to verify HLR assertions like "将第9和10位设置为1/0" → SDI.
    _PROTOCOL_ANCHOR_FAMILIES = {"LABEL", "OCTLBL", "SDI", "SSM", "PARITY"}
    proto_anchor_by_label: dict[str, list[dict]] = defaultdict(list)
    for prof in profiles:
        if prof.label is None:
            continue
        family = _extract_signal_family(prof.profile_key).upper()
        if family not in _PROTOCOL_ANCHOR_FAMILIES:
            continue
        attrs: dict[str, str] = {}
        for k, v in prof.attributes.items():
            if k in REVERSE_KEY_ATTRS:
                attrs[k] = str(v.get("value", "")) + (f" {v['unit']}" if v.get("unit") else "")
        if attrs:
            proto_anchor_by_label[prof.label].append({"name": family, "attrs": attrs})

    # Sort anchors by ICD bit offset so the layout reads naturally
    # (LABEL at 0, SDI at 8, SSM at 29, PARITY at 31 in 32-bit words).
    def _anchor_sort_key(entry: dict) -> tuple[int, str]:
        off = entry.get("attrs", {}).get("BitOffsetWithinDS", "")
        try:
            return (int(str(off).split()[0]), entry["name"])
        except (ValueError, IndexError):
            return (999, entry["name"])
    for entries in proto_anchor_by_label.values():
        entries.sort(key=_anchor_sort_key)

    # Build label → bit_offset → {dp_name, size, dtype} from pure-DP profiles.
    # These are Publisher-table profiles (sides={'DP'}) with complete per-field attributes.
    label_bit_dp: dict[str, dict[str, dict]] = defaultdict(dict)
    for prof in profiles:
        if prof.label is None:
            continue
        sides_in_prof = {e.side for e in prof.entries}
        if sides_in_prof != {"DP"}:
            continue
        family = _extract_signal_family(prof.profile_key)
        if family.upper() in protocol_names:
            continue
        bit = str(prof.attributes.get("BitOffsetWithinDS", {}).get("value", ""))
        if not bit:
            continue
        label_bit_dp[prof.label][bit] = {
            "dp_name": family,
            "size": str(prof.attributes.get("ParameterSize", {}).get("value", "")),
            "dtype": str(prof.attributes.get("DataFormatType", {}).get("value", "")),
        }

    # Fallback: when Publisher-table DP profiles don't cover a bit offset,
    # use sibling RP profiles that have exactly 1 dp_ref bit as a name source.
    # E.g. L134: CHAR_1 has bits {10,17,24}, CHAR_2 has {17}, CHAR_3 has {24}.
    # CHAR_2 (single bit=17) → bit 17 is named CHAR_2; same for CHAR_3 → bit 24.
    label_bit_rp: dict[str, dict[str, str]] = defaultdict(dict)
    for (label, family), profs in groups.items():
        if label is None:
            continue
        if family.upper() in protocol_names:
            continue
        # Collect dp_ref bits for this profile group
        ref_bits: set[str] = set()
        for p in profs:
            for e in p.entries:
                if e.is_dp_ref and e.attribute_name == "BitOffsetWithinDS":
                    val = str(e.attribute_value) if e.attribute_value is not None else ""
                    if val:
                        ref_bits.add(val)
        # Single-bit profiles can serve as name source
        if len(ref_bits) == 1:
            bit = next(iter(ref_bits))
            if bit not in label_bit_dp.get(label, {}):
                label_bit_rp[label][bit] = family

    blocks: list[ICDBlock] = []

    for (label, family), profs in groups.items():
        if family.upper() in protocol_names:
            continue

        if label:
            block_key = f"L{label}/{family}"
        else:
            block_key = family

        # Merge attributes (first value wins, profiles in same block
        # should agree on shared attributes). Exception: multi-value
        # attributes (SDIExpected / CodedSet) — one block can aggregate
        # channels with different SDI values (e.g. L051's ABV1/ABV2/ABV3
        # words carry SDIExpected=1/2/3; L173's SDI leaf carries
        # CodedSet 1=System1/2=System2/3=System3 per QTY_SYSx word).
        # "First wins" would present a single wrong value to the judge;
        # join distinct values instead.
        attrs: dict[str, dict] = {}
        for prof in profs:
            for k, v in prof.attributes.items():
                if k not in attrs:
                    attrs[k] = v
                elif k in ("SDIExpected", "CodedSet"):
                    cur = str(attrs[k].get("value", ""))
                    new = str(v.get("value", ""))
                    if new and new != cur:
                        joined = f"{cur}/{new}" if cur else new
                        attrs[k] = dict(v, value=joined)

        # Merge direction
        directions = {p.direction for p in profs}
        if directions == {"发送"}:
            direction = "发送"
        elif directions == {"接收"}:
            direction = "接收"
        else:
            direction = "发送/接收"

        # Merge bus types
        bus_types: set[str] = set()
        for p in profs:
            bus_types |= p.bus_types

        # Merge token sets
        device_tokens: set[str] = set()
        signal_aliases: set[str] = set()
        bus_aliases: set[str] = set()
        dir_verbs: set[str] = set()
        for p in profs:
            device_tokens |= p.device_tokens_set
            signal_aliases |= p.leaf_signal_aliases_set
            bus_aliases |= p.bus_aliases_set
            dir_verbs |= p.direction_verbs_set

        # Sub-signals: populated when dp_ref entries in this profile group
        # carry >1 distinct BitOffsetWithinDS values (L126-class multi-field data word).
        sub_signals: list[dict] = []
        if label:
            dp_ref_bits: set[str] = set()
            # Map bit_offset → dp_ref_name from entries themselves (P2.4 pub_names)
            bit_dp_name: dict[str, str] = {}
            for p in profs:
                for e in p.entries:
                    if e.is_dp_ref and e.attribute_name == "BitOffsetWithinDS":
                        val = str(e.attribute_value) if e.attribute_value is not None else ""
                        if val:
                            dp_ref_bits.add(val)
                            if e.dp_ref_name and val not in bit_dp_name:
                                bit_dp_name[val] = e.dp_ref_name
            if len(dp_ref_bits) > 1:
                dp_map = label_bit_dp.get(label, {})
                rp_map = label_bit_rp.get(label, {})
                claimed_bits: set[str] = set(rp_map.keys()) | set(dp_map.keys())
                # Per-sub-signal size/dtype from dp_ref entries (P2.4 step b now
                # includes DataFormatType/ParameterSize with dp_ref_name).
                dp_ref_size: dict[str, str] = {}
                dp_ref_dtype: dict[str, str] = {}
                for p in profs:
                    for e in p.entries:
                        if e.is_dp_ref and e.dp_ref_name:
                            if e.attribute_name == "ParameterSize":
                                val = str(e.attribute_value) if e.attribute_value is not None else ""
                                if val and e.dp_ref_name not in dp_ref_size:
                                    dp_ref_size[e.dp_ref_name] = val
                            elif e.attribute_name == "DataFormatType":
                                val = str(e.attribute_value) if e.attribute_value is not None else ""
                                if val and e.dp_ref_name not in dp_ref_dtype:
                                    dp_ref_dtype[e.dp_ref_name] = val
                # Group-level fallback for profiles where dp_ref entries don't carry
                # per-sub-signal dtype/size (e.g. signals without per-layer DP binding).
                group_size = ""
                group_dtype = ""
                for p in profs:
                    if not group_size:
                        group_size = str(p.attributes.get("ParameterSize", {}).get("value", ""))
                    if not group_dtype:
                        group_dtype = str(p.attributes.get("DataFormatType", {}).get("value", ""))
                    if group_size and group_dtype:
                        break
                for bit in sorted(dp_ref_bits, key=lambda b: int(b) if b.lstrip("-").isdigit() else 0):
                    dp_name = bit_dp_name.get(bit, "")
                    per_ref_size = dp_ref_size.get(dp_name, "")
                    per_ref_dtype = dp_ref_dtype.get(dp_name, "")
                    dp_size = dp_map.get(bit, {}).get("size", "")
                    dp_dtype = dp_map.get(bit, {}).get("dtype", "")
                    size = per_ref_size or dp_size or group_size
                    dtype = per_ref_dtype or dp_dtype or group_dtype
                    if bit in bit_dp_name:
                        sub_signals.append({
                            "dp_name": bit_dp_name[bit],
                            "bit_offset": bit,
                            "size": size,
                            "dtype": dtype,
                        })
                    elif bit in dp_map:
                        info = dp_map[bit]
                        sub_signals.append({
                            "dp_name": info["dp_name"],
                            "bit_offset": bit,
                            "size": info["size"] or group_size,
                            "dtype": info["dtype"] or group_dtype,
                        })
                    elif bit in rp_map:
                        sub_signals.append({
                            "dp_name": rp_map[bit],
                            "bit_offset": bit,
                            "size": group_size,
                            "dtype": group_dtype,
                        })
                    elif bit not in claimed_bits:
                        sub_signals.append({
                            "dp_name": family,
                            "bit_offset": bit,
                            "size": group_size,
                            "dtype": group_dtype,
                        })
                    else:
                        sub_signals.append({
                            "dp_name": "",
                            "bit_offset": bit,
                            "size": group_size,
                            "dtype": group_dtype,
                        })

        blocks.append(ICDBlock(
            block_key=block_key,
            label=label,
            signal_family=family,
            direction=direction,
            bus_types=bus_types,
            profiles=profs,
            attributes=attrs,
            device_tokens_set=device_tokens,
            leaf_signal_aliases_set=signal_aliases,
            bus_aliases_set=bus_aliases,
            direction_verbs_set=dir_verbs,
            channel_count=len(profs),
            sub_signals=sub_signals,
            word_protocol_fields=proto_anchor_by_label.get(label, []),
        ))

    return blocks


def build_profiles(eoicd_reqs: list[EoICDRequirement]) -> list[SignalProfile]:
    """Cluster EoICD requirements into SignalProfiles by extracted profile key.

    Each profile aggregates:
    - All entries sharing the Label segment (or leaf name)
    - Pre-computed token sets (device, signal, bus, direction) for matching
    - Key attributes for joint verification (bit range, SDI)
    """
    syn = _get_synonym_lookup()

    # Group by profile key (only leaf-layer entries: DP, RP)
    groups: dict[str, list[EoICDRequirement]] = defaultdict(list)
    for req in eoicd_reqs:
        if req.layer_type not in ("DP", "RP"):
            continue
        key = _extract_profile_key(req.signal_name)
        groups[key].append(req)

    profiles: list[SignalProfile] = []
    for key, entries in groups.items():
        label = _extract_label_value(key)

        # Determine direction from side
        sides = {e.side for e in entries}
        if sides == {"DP"}:
            direction = "发送"
        elif sides == {"RP"}:
            direction = "接收"
        else:
            direction = "发送/接收"

        bus_types = {e.bus_type for e in entries}

        # Build attributes dict (first value wins per attr_name).
        # Process DP entries first so RP entries (with merged DP data from
        # parser) can override Label + SDIExpected with RP-authoritative values.
        # Exception: CodedSet is multi-value (e.g. L173's SDI leaf carries
        # 1=System1 / 2=System2 / 3=System3 as separate entries) — join
        # distinct values instead of first-wins, so the judge sees the full
        # coding set.
        attrs: dict[str, dict] = {}
        sorted_entries = sorted(entries, key=lambda e: 0 if e.side == "DP" else 1)
        for e in sorted_entries:
            if e.attribute_value is None:
                continue
            new_val = str(e.attribute_value)
            if e.attribute_name not in attrs:
                attrs[e.attribute_name] = {
                    "value": new_val,
                    "unit": e.unit or "",
                    "entry_id": e.ird_id,
                }
            elif e.attribute_name == "CodedSet":
                cur = attrs[e.attribute_name]["value"]
                if new_val and new_val not in cur.split("/"):
                    attrs[e.attribute_name]["value"] = f"{cur}/{new_val}" if cur else new_val

        # --- Aggregate token sets from all entries ---
        device_tokens: set[str] = set()
        signal_aliases: set[str] = set()
        bus_aliases: set[str] = set()
        dir_verbs: set[str] = set()

        for e in entries:
            signal_name = e.signal_name or ""
            segments = [s.strip() for s in signal_name.split(".") if s.strip()]

            # Device tokens: first 2 segments of signal path
            for seg in segments[:2]:
                seg_lower = seg.lower()
                device_tokens.add(seg_lower)
                device_tokens.update(_resolve_aliases(seg, syn))
                for token in _tokenize_name(seg):
                    device_tokens.add(token)
                    device_tokens.update(_resolve_aliases(token, syn))

            # Leaf signal aliases
            leaf = segments[-1] if segments else ""
            if leaf:
                signal_aliases.add(leaf.lower())
                signal_aliases.update(_resolve_aliases(leaf, syn))

            # Bus aliases
            bus_aliases.update(_resolve_aliases(e.bus_type, syn))

        # Direction verbs
        if "DP" in sides:
            dir_verbs.update(v.lower() for v in SEND_VERBS)
        if "RP" in sides:
            dir_verbs.update(v.lower() for v in RECEIVE_VERBS)

        profiles.append(SignalProfile(
            profile_key=key,
            label=label,
            direction=direction,
            bus_types=bus_types,
            entries=entries,
            attributes=attrs,
            device_tokens_set=device_tokens,
            leaf_signal_aliases_set=signal_aliases,
            bus_aliases_set={b.lower() for b in bus_aliases},
            direction_verbs_set=dir_verbs,
        ))

    return profiles
