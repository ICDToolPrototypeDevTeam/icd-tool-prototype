# -*- coding: utf-8 -*-
"""Case builder: validates and prepares ComparisonCases for AI judgment."""

from __future__ import annotations

from pathlib import Path

from app.v4.models import EoICDRequirement, HLRRequirement, HLRLabel
from app.v4.matching.candidate_matcher import match_requirements


def build_cases(
    eoicd_reqs: list[EoICDRequirement],
    hlr_reqs: list[HLRRequirement],
    top_k: int = 5,
    limit: int = 0,
    hlr_labels: dict[str, HLRLabel] | None = None,
    labels_cache_path: Path | None = None,
    enriched_output_path: Path | None = None,
    profiles_output_path: Path | None = None,
):
    """Public entry point: run matching and build ComparisonCases.

    Delegates to candidate_matcher.match_requirements().
    """
    return match_requirements(
        eoicd_reqs,
        hlr_reqs,
        top_k=top_k,
        limit=limit,
        hlr_labels=hlr_labels,
        labels_cache_path=labels_cache_path,
        enriched_output_path=enriched_output_path,
        profiles_output_path=profiles_output_path,
    )
