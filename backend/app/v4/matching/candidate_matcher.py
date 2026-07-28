# -*- coding: utf-8 -*-
"""Candidate matcher orchestrator: signal profiles, enrichment, labeling, unified matching, Top-K ranking."""

from __future__ import annotations

from pathlib import Path

from app.v4.config import LOW_SCORE_THRESHOLD, DEFAULT_TOP_K
from app.v4.matching.entry_filter import should_keep
from app.v4.matching.eoicd_enricher import enrich_query
from app.v4.matching.hlr_classifier import enrich_all_labels
from app.v4.matching.hlr_labeler import label_hlrs
from app.v4.matching.signal_profiler import build_profiles, SignalProfile
from app.v4.matching.text_matcher import TextMatcher
from app.v4.matching.unified_matcher import UnifiedMatcher
from app.v4.models import (
    EoICDRequirement,
    HLRRequirement,
    HLRLabel,
    ComparisonCase,
    MatchCandidate,
)


def _normalize_label(val: str) -> str:
    """Normalize a label value for comparison: strip L/l prefix, lowercase."""
    return val.strip().lower().lstrip("l")


def _build_hlr_label_lookup(
    hlr_labels: dict[str, HLRLabel],
) -> dict[str, set[str]]:
    """Build hlr_id -> {normalized_label, ...} lookup."""
    lookup: dict[str, set[str]] = {}
    for hlr_id, lbl in hlr_labels.items():
        lookup[hlr_id] = {_normalize_label(l) for l in lbl.labels}
    return lookup


def _build_signal_context(profile: SignalProfile) -> dict:
    """Build signal_context dict from a SignalProfile for injection into ComparisonCase."""
    return {
        "profile_key": profile.profile_key,
        "label_value": profile.label,
        "direction": profile.direction,
        "bus_types": sorted(profile.bus_types),
        "peer_attributes": {
            k: v["value"] for k, v in profile.attributes.items()
        },
        "entry_count": len(profile.entries),
    }


def _build_case(
    eoicd_req: EoICDRequirement,
    candidates: list[MatchCandidate],
    case_index: int,
    signal_context: dict | None = None,
) -> ComparisonCase:
    """Build a ComparisonCase from an EoICD requirement and its matched candidates."""
    eoicd_dict: dict = {
        "ird_id": eoicd_req.ird_id,
        "signal_name": eoicd_req.signal_name,
        "description": eoicd_req.description,
        "bus_type": eoicd_req.bus_type,
        "side": eoicd_req.side,
        "source": eoicd_req.source,
    }
    if signal_context:
        eoicd_dict["signal_context"] = signal_context

    return ComparisonCase(
        case_id=f"CMP-{case_index:04d}",
        eoicd_requirement=eoicd_dict,
        candidates=candidates,
        match_evidence={
            "top_score": candidates[0].score if candidates else 0.0,
            "candidate_count": len(candidates),
            "low_confidence": (
                not candidates
                or candidates[0].score < LOW_SCORE_THRESHOLD
            ),
        },
    )


def match_requirements(
    eoicd_reqs: list[EoICDRequirement],
    hlr_reqs: list[HLRRequirement],
    top_k: int = DEFAULT_TOP_K,
    limit: int = 0,
    hlr_labels: dict[str, HLRLabel] | None = None,
    labels_cache_path: Path | None = None,
    enriched_output_path: Path | None = None,
    profiles_output_path: Path | None = None,
) -> list[ComparisonCase]:
    """Run unified matching for each EoICD signal profile.

    Architecture (v0.8.0):
    1. Filter noise entries (protocol overhead, etc.)
    2. Cluster remaining entries into SignalProfiles by Label segment
    3. Profile-level matching: label-first filter + 9-dimension unified scoring
    4. Expand profiles back to per-attribute ComparisonCases with signal_context

    Args:
        eoicd_reqs: Parsed EoICD requirements.
        hlr_reqs: Parsed HLR requirements.
        top_k: Number of top candidates per case.
        limit: Max EoICD items to process (0 = all).
        hlr_labels: Pre-computed HLR labels (if available).
        labels_cache_path: Path to hlr_labels.json for caching.
        enriched_output_path: If set, write EnrichedQuery JSON to this path.

    Returns:
        List of ComparisonCase, one per EoICD requirement.
    """
    # Get HLR labels (from cache, AI labeling, or fallback)
    if hlr_labels is None:
        hlr_labels = label_hlrs(hlr_reqs, cache_path=labels_cache_path)

    # Enrich with script-based classifier (supplements AI labels)
    hlr_labels = enrich_all_labels(hlr_reqs, hlr_labels)

    # Build BM25 index from enriched HLR text
    text_matcher = TextMatcher()
    enriched_map = {hlr_id: lbl.enriched_text for hlr_id, lbl in hlr_labels.items()}
    text_matcher.fit(enriched_map)

    # Initialize unified matcher
    matcher = UnifiedMatcher(hlr_labels, text_matcher)

    # Build HLR label lookup for label-first filtering
    hlr_label_lookup = _build_hlr_label_lookup(hlr_labels)

    # ── Step 1: Apply entry filters ──
    items = eoicd_reqs[:limit] if limit > 0 else eoicd_reqs
    kept_entries = [req for req in items if should_keep(req)]
    filtered_out_count = len(items) - len(kept_entries)

    # ── Step 2: Cluster into signal profiles ──
    profiles = build_profiles(kept_entries)
    total_profiles = len(profiles)

    label_filtered_count = 0

    cases: list[ComparisonCase] = []
    enriched_records: list[dict] = []
    profile_records: list[dict] = []
    case_index = 0

    for idx, profile in enumerate(profiles):
        # ── Step 3: Profile-level matching ──

        # Label-first filtering: if profile has a label, only score HLRs that mention it
        if profile.label:
            target_label = _normalize_label(profile.label)
            filtered_hlrs = [
                h for h in hlr_reqs
                if target_label in hlr_label_lookup.get(h.requirement_id, set())
            ]
            if filtered_hlrs:
                candidates = matcher.score_profile(profile, filtered_hlrs)
                label_filtered_count += 1
            else:
                candidates = matcher.score_profile(profile, hlr_reqs)
        else:
            candidates = matcher.score_profile(profile, hlr_reqs)

        top_candidates = candidates[:top_k]

        # ── Step 4: Build signal_context once per profile ──
        signal_context = _build_signal_context(profile)

        # ── Step 5: Expand profile to per-entry ComparisonCases ──
        for entry in profile.entries:
            case_index += 1
            cases.append(_build_case(entry, top_candidates, case_index, signal_context))

            if enriched_output_path is not None:
                eq = enrich_query(entry)
                enriched_records.append({
                    "ird_id": entry.ird_id,
                    "query": eq.to_dict(),
                })

        # Collect profile summary record
        profile_records.append({
            "profile_key": profile.profile_key,
            "label": profile.label,
            "direction": profile.direction,
            "bus_types": sorted(profile.bus_types),
            "entry_count": len(profile.entries),
            "attribute_names": sorted(profile.attributes.keys()),
            "top_hlr_ids": [c.hlr_id for c in top_candidates[:3]],
            "top_score": top_candidates[0].score if top_candidates else 0,
        })

        if (idx + 1) % 50 == 0:
            print(f"  [match] {idx + 1}/{total_profiles} profiles processed "
                  f"({case_index} cases built)")

    if enriched_output_path is not None:
        import json
        enriched_output_path.parent.mkdir(parents=True, exist_ok=True)
        enriched_output_path.write_text(
            json.dumps(enriched_records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  [match] Enriched queries written to {enriched_output_path}")

    if profiles_output_path is not None:
        import json
        from collections import Counter
        # Compute aggregate stats
        has_label = sum(1 for p in profiles if p.label)
        l_keys = [p.profile_key for p in profiles if p.profile_key.upper().startswith("L")]
        entry_counts = [len(p.entries) for p in profiles]
        profiles_out = {
            "total_profiles": total_profiles,
            "total_entries_in_profiles": sum(entry_counts),
            "filtered_out_entries": filtered_out_count,
            "label_filtered_profile_count": label_filtered_count,
            "stats": {
                "profiles_with_label": has_label,
                "l_prefix_profiles": len(l_keys),
                "non_l_profiles": total_profiles - len(l_keys),
                "entries_per_profile_min": min(entry_counts) if entry_counts else 0,
                "entries_per_profile_max": max(entry_counts) if entry_counts else 0,
                "entries_per_profile_avg": round(sum(entry_counts) / len(entry_counts), 1) if entry_counts else 0,
            },
            "profiles": sorted(profile_records, key=lambda p: p["entry_count"], reverse=True),
        }
        profiles_output_path.parent.mkdir(parents=True, exist_ok=True)
        profiles_output_path.write_text(
            json.dumps(profiles_out, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  [match] Profiles written to {profiles_output_path}")

    print(f"  [match] {total_profiles} profiles → {case_index} cases "
          f"({label_filtered_count} label-filtered, {filtered_out_count} filtered-out)")
    return cases
