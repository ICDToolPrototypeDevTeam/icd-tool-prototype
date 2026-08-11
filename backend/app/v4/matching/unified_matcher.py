# -*- coding: utf-8 -*-
"""Unified 7-dimension weighted matcher for EoICD <-> HLR candidate recall."""

from __future__ import annotations

from app.v4.config import MATCH_WEIGHTS, ATTR_CATEGORY_MAP
from app.v4.matching.eoicd_enricher import EnrichedQuery, _resolve_aliases, _get_synonym_lookup, _tokenize_name
from app.v4.matching.text_matcher import TextMatcher
from app.v4.models import HLRLabel, HLRRequirement, MatchCandidate


def _empty_label(hlr_id: str = "") -> HLRLabel:
    return HLRLabel(hlr_id=hlr_id)


class UnifiedMatcher:
    """Score an EoICD EnrichedQuery against labeled HLR requirements across 7 dimensions."""

    def __init__(self, hlr_labels: dict[str, HLRLabel], bm25_index: TextMatcher):
        self.labels = hlr_labels
        self.bm25 = bm25_index
        self.syn = _get_synonym_lookup()

    # Direction conflict penalty: applied when extracted_direction contradicts EoICD side
    DIRECTION_CONFLICT_PENALTY = 30

    def score_all(
        self, eq: EnrichedQuery, hlr_reqs: list[HLRRequirement]
    ) -> list[MatchCandidate]:
        # Get BM25 scores for all docs
        bm25_results = self.bm25.score_all(eq)
        bm25_map: dict[str, float] = {r.hlr_id: r.score for r in bm25_results}

        results: list[MatchCandidate] = []
        for hlr in hlr_reqs:
            lbl = self.labels.get(hlr.requirement_id, _empty_label(hlr.requirement_id))

            dims, total = self._score_dimensions(eq, lbl, bm25_map.get(hlr.requirement_id, 0))

            # Direction conflict penalty (soft, not hard skip)
            if self._direction_conflict(eq, lbl):
                total = max(0, total - self.DIRECTION_CONFLICT_PENALTY)
                dims["direction"]["detail"] = sorted(
                    set(dims["direction"].get("detail", [])) | {"冲突罚分"}
                )

            if total > 0:
                results.append(
                    MatchCandidate(
                        hlr_id=hlr.requirement_id,
                        hlr_content=hlr.content,
                        rationale=hlr.rationale,
                        score=round(total, 2),
                        match_source="unified",
                        matched_fields=self._format_matched_fields(dims),
                    )
                )

        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def score_profile(
        self,
        profile: "SignalProfile",
        hlr_reqs: list[HLRRequirement],
    ) -> list[MatchCandidate]:
        """Profile-level scoring: aggregate all entries in a SignalProfile, score once.

        Uses profile-aggregated token sets for device/signal/bus dimensions and
        profile.attributes for joint bit-range + SDI verification.
        Returns Top-K candidates sorted by score desc.
        """
        from app.v4.matching.signal_profiler import SignalProfile  # noqa: F811

        # Profile-level BM25: concatenate all entry descriptions
        profile_text = " ".join(e.description for e in profile.entries)
        bm25_results = self.bm25.score_text(profile_text)
        bm25_map: dict[str, float] = {r.hlr_id: r.score for r in bm25_results}

        results: list[MatchCandidate] = []
        for hlr in hlr_reqs:
            lbl = self.labels.get(hlr.requirement_id, _empty_label(hlr.requirement_id))
            dims, total = self._score_profile_dimensions(
                profile, lbl, bm25_map.get(hlr.requirement_id, 0),
            )

            # Direction conflict at profile level
            if self._direction_conflict_profile(profile, lbl):
                total = max(0, total - self.DIRECTION_CONFLICT_PENALTY)
                dims["direction"]["detail"] = sorted(
                    set(dims["direction"].get("detail", [])) | {"冲突罚分"}
                )

            if total > 0:
                results.append(
                    MatchCandidate(
                        hlr_id=hlr.requirement_id,
                        hlr_content=hlr.content,
                        rationale=hlr.rationale,
                        score=round(total, 2),
                        match_source="unified",
                        matched_fields=self._format_matched_fields(dims),
                    )
                )

        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def _score_profile_dimensions(
        self,
        profile: "SignalProfile",
        lbl: HLRLabel,
        bm25_score: float,
    ) -> tuple[dict[str, dict], float]:
        """Score a SignalProfile against one HLR label across 9 dimensions.

        Key differences from per-entry _score_dimensions:
        - Device/signal tokens aggregated from all profile entries
        - Bit-range: joint verification of offset+size from profile.attributes (10 pts)
        - SDI: sourced from profile.attributes dict
        - Attr_cat: best match across all attribute names in the profile
        """
        from app.v4.matching.signal_profiler import SignalProfile  # noqa: F811

        dims: dict[str, dict] = {}
        total = 0.0

        # #1 Bus match (0/10)
        hlr_bus_expanded: set[str] = set(lbl.bus_types_set)
        for bt in lbl.bus_types:
            hlr_bus_expanded.update(_resolve_aliases(bt, self.syn))
        bus_overlap = profile.bus_aliases_set & hlr_bus_expanded
        bus_score = MATCH_WEIGHTS["bus"] if bus_overlap else 0
        dims["bus"] = {"score": bus_score, "detail": sorted(bus_overlap)}
        total += bus_score

        # #2 Label match (0/20)
        label_score = 0
        label_detail = ""
        if profile.label:
            lbl_labels_normalized = {l.lower().lstrip("l") for l in lbl.labels}
            if profile.label.lower().lstrip("l") in lbl_labels_normalized:
                label_score = MATCH_WEIGHTS["label"]
                label_detail = profile.label
        dims["label"] = {"score": label_score, "detail": label_detail}
        total += label_score

        # #3 Direction match (0/10)
        dir_overlap = profile.direction_verbs_set & lbl.direction_keywords_set
        if dir_overlap:
            dir_score = MATCH_WEIGHTS["direction"]
        elif not lbl.direction_keywords:
            dir_score = MATCH_WEIGHTS["direction"] // 2  # 5 for unknown
        else:
            dir_score = 0
        dims["direction"] = {"score": dir_score, "detail": sorted(dir_overlap)}
        total += dir_score

        # #4 Device match (0/5/10/15) — aggregated from all entries
        hlr_devices_expanded: set[str] = set(lbl.devices_set)
        for dev in lbl.devices:
            dev_upper = dev.upper()
            for token in _tokenize_name(dev):
                hlr_devices_expanded.add(token)
                hlr_devices_expanded.update(_resolve_aliases(token, self.syn))
            for syn_key in self.syn:
                if syn_key.upper() in dev_upper:
                    hlr_devices_expanded.update(self.syn[syn_key])
                    hlr_devices_expanded.add(syn_key)
        device_overlap = set()
        for dt in profile.device_tokens_set:
            dt_lower = dt.lower()
            if dt_lower in hlr_devices_expanded:
                device_overlap.add(dt)
            else:
                dt_aliases = _resolve_aliases(dt, self.syn)
                if dt_aliases & hlr_devices_expanded:
                    device_overlap.add(dt)

        overlap_count = len(device_overlap)
        if overlap_count >= 2:
            device_score = MATCH_WEIGHTS["device"]
        elif overlap_count == 1:
            device_score = int(MATCH_WEIGHTS["device"] * 0.67)  # 10
        elif profile.bus_aliases_set & lbl.devices_set:
            device_score = int(MATCH_WEIGHTS["device"] * 0.33)  # 5
        else:
            device_score = 0
        dims["device"] = {"score": device_score, "detail": sorted(device_overlap)}
        total += device_score

        # #5 Signal keyword match (0/10/20) — aggregated from all entries
        signal_overlap = profile.leaf_signal_aliases_set & lbl.signal_keywords_set
        sig_count = len(signal_overlap)
        if sig_count >= 2:
            signal_score = MATCH_WEIGHTS["signal"]
        elif sig_count == 1:
            signal_score = MATCH_WEIGHTS["signal"] // 2  # 10
        else:
            signal_score = 0
        dims["signal"] = {"score": signal_score, "detail": sorted(signal_overlap)}
        total += signal_score

        # #6 Attribute category match (0/2/5) — best match across all profile attrs
        attr_score = 0
        attr_detail = ""
        profile_attr_categories = set()
        for aname in profile.attributes:
            cat = ATTR_CATEGORY_MAP.get(aname)
            if cat:
                profile_attr_categories.add(cat.lower())
        if profile_attr_categories:
            cat_overlap = profile_attr_categories & lbl.attr_categories_set
            if cat_overlap:
                attr_score = MATCH_WEIGHTS["attr_cat"]
                attr_detail = ",".join(sorted(cat_overlap))
            else:
                attr_score = MATCH_WEIGHTS["attr_cat"] // 2  # 2 — some categories present but no overlap
        else:
            attr_score = MATCH_WEIGHTS["attr_cat"] // 2  # 2 — unknown
            attr_detail = "unknown"
        dims["attr_cat"] = {"score": attr_score, "detail": attr_detail or "no overlap"}
        total += attr_score

        # #7 Bit-range joint verification (0/10)
        # Requires both BitOffsetWithinDS and ParameterSize in profile.attributes.
        # Jointly verify offset and size against HLR bit_fields.
        bit_score = 0
        bit_detail = ""
        bit_offset_attr = profile.attributes.get("BitOffsetWithinDS")
        param_size_attr = profile.attributes.get("ParameterSize")
        if bit_offset_attr and param_size_attr and lbl.bit_fields:
            try:
                offset_val = int(bit_offset_attr["value"])
                size_val = int(param_size_attr["value"])
            except (ValueError, TypeError):
                offset_val = None
                size_val = None
            if offset_val is not None and size_val is not None:
                for bf in lbl.bit_fields:
                    if bf["offset"] == offset_val and bf["size"] == size_val:
                        bit_score = 10  # joint match: offset + size
                        bit_detail = f"offset={offset_val},size={size_val} (joint)"
                        break
        elif (bit_offset_attr or param_size_attr) and lbl.bit_fields:
            # Partial: only one of the two is available, score 5
            try:
                if bit_offset_attr:
                    eq_val = int(bit_offset_attr["value"])
                    for bf in lbl.bit_fields:
                        if bf["offset"] == eq_val:
                            bit_score = 5
                            bit_detail = f"offset={eq_val},size={bf['size']}"
                            break
                elif param_size_attr:
                    eq_val = int(param_size_attr["value"])
                    for bf in lbl.bit_fields:
                        if bf["size"] == eq_val:
                            bit_score = 5
                            bit_detail = f"offset={bf['offset']},size={eq_val}"
                            break
            except (ValueError, TypeError):
                pass
        dims["bit_range"] = {"score": bit_score, "detail": bit_detail}
        total += bit_score

        # #8 SDI match (0/5) — from profile.attributes
        sdi_score = 0
        sdi_detail = ""
        sdi_attr = profile.attributes.get("SDIExpected")
        if sdi_attr and lbl.sdi_value:
            if sdi_attr["value"].strip() == lbl.sdi_value:
                sdi_score = 5
                sdi_detail = lbl.sdi_value
        dims["sdi"] = {"score": sdi_score, "detail": sdi_detail}
        total += sdi_score

        # #9 BM25 (0-20)
        bm25_rounded = round(bm25_score, 1)
        dims["bm25"] = {"score": bm25_rounded, "detail": f"{bm25_rounded:.1f}"}
        total += bm25_rounded

        return dims, total

    def _direction_conflict_profile(
        self, profile: "SignalProfile", lbl: HLRLabel
    ) -> bool:
        """Check direction conflict at profile level.

        If profile has both DP and RP entries ("发送/接收"), no conflict is reported.
        """
        from app.v4.matching.signal_profiler import SignalProfile  # noqa: F811

        hlr_dir = lbl.extracted_direction
        if not hlr_dir:
            return False
        # If profile has both directions, no conflict
        if profile.direction == "发送/接收":
            return False
        if profile.direction == "发送" and hlr_dir == "接收":
            return True
        if profile.direction == "接收" and hlr_dir == "发送":
            return True
        return False

    def _direction_conflict(self, eq: EnrichedQuery, lbl: HLRLabel) -> bool:
        """Check for explicit direction conflict (regex-extracted direction vs EoICD side).

        Only triggers when HLR has a single clear direction that contradicts the EoICD side.
        DP=send, RP=receive.
        """
        hlr_dir = lbl.extracted_direction
        if not hlr_dir:
            return False
        if eq.side == "DP" and hlr_dir == "接收":
            return True
        if eq.side == "RP" and hlr_dir == "发送":
            return True
        return False

    def _score_dimensions(
        self, eq: EnrichedQuery, lbl: HLRLabel, bm25_score: float
    ) -> tuple[dict[str, dict], float]:
        dims: dict[str, dict] = {}
        total = 0.0

        # #1 Bus match (0/10)
        hlr_bus_expanded: set[str] = set(lbl.bus_types_set)
        for bt in lbl.bus_types:
            hlr_bus_expanded.update(_resolve_aliases(bt, self.syn))
        bus_overlap = eq.bus_aliases_set & hlr_bus_expanded
        bus_score = MATCH_WEIGHTS["bus"] if bus_overlap else 0
        dims["bus"] = {"score": bus_score, "detail": sorted(bus_overlap)}
        total += bus_score

        # #2 Label match (0/20)
        label_score = 0
        label_detail = ""
        if eq.label_value:
            eq_label = eq.label_value.strip().lower().lstrip("l")
            lbl_labels_normalized = {l.lower().lstrip("l") for l in lbl.labels}
            if eq_label in lbl_labels_normalized:
                label_score = MATCH_WEIGHTS["label"]
                label_detail = eq.label_value.strip()
        dims["label"] = {"score": label_score, "detail": label_detail}
        total += label_score

        # #3 Direction match (0/10)
        dir_overlap = eq.direction_verbs_set & lbl.direction_keywords_set
        if dir_overlap:
            dir_score = MATCH_WEIGHTS["direction"]
        elif not lbl.direction_keywords:
            dir_score = MATCH_WEIGHTS["direction"] // 2  # 5 for unknown
        else:
            dir_score = 0
        dims["direction"] = {"score": dir_score, "detail": sorted(dir_overlap)}
        total += dir_score

        # #4 Device match (0/5/10/15)
        # Expand HLR devices with token decomposition + synonym expansion + substring
        hlr_devices_expanded: set[str] = set(lbl.devices_set)
        for dev in lbl.devices:
            dev_upper = dev.upper()
            for token in _tokenize_name(dev):
                hlr_devices_expanded.add(token)
                hlr_devices_expanded.update(_resolve_aliases(token, self.syn))
            # Substring match: catch "AFTEFAN1" containing "FAN"
            for syn_key in self.syn:
                if syn_key.upper() in dev_upper:
                    hlr_devices_expanded.update(self.syn[syn_key])
                    hlr_devices_expanded.add(syn_key)
        device_overlap = set()
        for dt in eq.device_tokens_set:
            dt_lower = dt.lower()
            if dt_lower in hlr_devices_expanded:
                device_overlap.add(dt)
            else:
                # Check synonym expansion
                dt_aliases = _resolve_aliases(dt, self.syn)
                if dt_aliases & hlr_devices_expanded:
                    device_overlap.add(dt)

        overlap_count = len(device_overlap)
        if overlap_count >= 2:
            device_score = MATCH_WEIGHTS["device"]
        elif overlap_count == 1:
            device_score = int(MATCH_WEIGHTS["device"] * 0.67)  # 10
        elif eq.bus_aliases_set & lbl.devices_set:
            device_score = int(MATCH_WEIGHTS["device"] * 0.33)  # 5
        else:
            device_score = 0
        dims["device"] = {"score": device_score, "detail": sorted(device_overlap)}
        total += device_score

        # #5 Signal keyword match (0/10/20)
        signal_overlap = eq.leaf_signal_aliases_set & lbl.signal_keywords_set
        sig_count = len(signal_overlap)
        if sig_count >= 2:
            signal_score = MATCH_WEIGHTS["signal"]
        elif sig_count == 1:
            signal_score = MATCH_WEIGHTS["signal"] // 2  # 10
        else:
            signal_score = 0
        dims["signal"] = {"score": signal_score, "detail": sorted(signal_overlap)}
        total += signal_score

        # #6 Attribute category match (0/2/5)
        if eq.attr_category and eq.attr_category.lower() in lbl.attr_categories_set:
            attr_score = MATCH_WEIGHTS["attr_cat"]
        elif eq.attr_category is None:
            attr_score = MATCH_WEIGHTS["attr_cat"] // 2  # 2 for unknown category
        else:
            attr_score = 0
        dims["attr_cat"] = {
            "score": attr_score,
            "detail": eq.attr_category or "unknown",
        }
        total += attr_score

        # #7 Bit-range match (0/5) — bonus for BitOffsetWithinDS / ParameterSize
        bit_score = 0
        bit_detail = ""
        if eq.attr_name in ("BitOffsetWithinDS", "ParameterSize") and lbl.bit_fields:
            try:
                eq_val = int(eq.attr_value)
            except (ValueError, TypeError):
                eq_val = None
            if eq_val is not None:
                for bf in lbl.bit_fields:
                    if eq.attr_name == "BitOffsetWithinDS" and bf["offset"] == eq_val:
                        bit_score = 5
                        bit_detail = f"offset={eq_val},size={bf['size']}"
                        break
                    elif eq.attr_name == "ParameterSize" and bf["size"] == eq_val:
                        bit_score = 5
                        bit_detail = f"offset={bf['offset']},size={eq_val}"
                        break
        dims["bit_range"] = {"score": bit_score, "detail": bit_detail}
        total += bit_score

        # #8 SDI match (0/5) — bonus for SDIExpected
        sdi_score = 0
        sdi_detail = ""
        if eq.attr_name == "SDIExpected" and lbl.sdi_value:
            if str(eq.attr_value).strip() == lbl.sdi_value:
                sdi_score = 5
                sdi_detail = lbl.sdi_value
        dims["sdi"] = {"score": sdi_score, "detail": sdi_detail}
        total += sdi_score

        # #9 BM25 (0-20, already normalized)
        bm25_rounded = round(bm25_score, 1)
        dims["bm25"] = {"score": bm25_rounded, "detail": f"{bm25_rounded:.1f}"}
        total += bm25_rounded

        return dims, total

    def _format_matched_fields(self, dims: dict[str, dict]) -> list[str]:
        """Format dimension scores into human-readable matched_fields strings."""
        labels_map = {
            "bus": "总线",
            "label": "Label",
            "direction": "方向",
            "device": "设备",
            "signal": "信号关键词",
            "attr_cat": "属性类别",
            "bit_range": "Bit范围",
            "sdi": "SDI",
            "bm25": "BM25",
        }
        fields: list[str] = []
        for dim_key, info in dims.items():
            score = info["score"]
            detail = info.get("detail", "")
            if isinstance(detail, list):
                detail = ",".join(str(d) for d in detail) if detail else "-"
            elif not detail:
                detail = "-"
            cn = labels_map.get(dim_key, dim_key)
            fields.append(f"{cn}({dim_key}):{detail}({score})")
        return fields
