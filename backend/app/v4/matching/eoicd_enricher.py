# -*- coding: utf-8 -*-
"""EoICD query enrichment: expands structured fields into multi-lingual query tokens."""

from __future__ import annotations

from dataclasses import dataclass, field

import re

from app.v4.config import (
    ATTR_CATEGORY_MAP,
    ATTR_CN_MAP,
    SIGNAL_LEAF_ALIASES,
    SEND_VERBS,
    RECEIVE_VERBS,
    load_synonyms,
)
from app.v4.models import EoICDRequirement


def _tokenize_name(name: str) -> set[str]:
    """Split a compound name into word tokens on _, -, and camelCase boundaries.

    Returns lowercase tokens, filtering out pure-digit tokens and empty strings.
    """
    # Split on _ and -
    parts = re.split(r"[_\-]+", name)
    tokens: set[str] = set()
    for part in parts:
        if not part:
            continue
        # Split camelCase: "FWD BFan1" → ["FWD", "B", "Fan", "1"]
        camel_parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]|[0-9]+", part)
        for token in camel_parts:
            token_lower = token.lower()
            if token_lower and not token_lower.isdigit():
                tokens.add(token_lower)
    return tokens


@dataclass
class EnrichedQuery:
    """An EoICD requirement expanded into multi-dimensional tokens for matching."""

    signal_segments: list[str] = field(default_factory=list)
    leaf_signal: str = ""
    leaf_signal_aliases: list[str] = field(default_factory=list)
    leaf_signal_aliases_set: set[str] = field(default_factory=set)
    device_tokens: list[str] = field(default_factory=list)
    device_tokens_set: set[str] = field(default_factory=set)
    bus_type: str = ""
    bus_aliases: list[str] = field(default_factory=list)
    bus_aliases_set: set[str] = field(default_factory=set)
    side: str = ""
    direction_verbs: list[str] = field(default_factory=list)
    direction_verbs_set: set[str] = field(default_factory=set)
    attr_name: str = ""
    attr_category: str | None = None
    attr_value: str = ""
    label_value: str | None = None
    enriched_text: str = ""

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict (sets converted to sorted lists)."""
        return {
            "signal_segments": self.signal_segments,
            "leaf_signal": self.leaf_signal,
            "leaf_signal_aliases": self.leaf_signal_aliases,
            "device_tokens": self.device_tokens,
            "bus_type": self.bus_type,
            "bus_aliases": self.bus_aliases,
            "side": self.side,
            "direction_verbs": self.direction_verbs,
            "attr_name": self.attr_name,
            "attr_category": self.attr_category,
            "attr_value": self.attr_value,
            "label_value": self.label_value,
            "enriched_text": self.enriched_text,
        }


def _resolve_aliases(term: str, synonym_map: dict[str, set[str]]) -> set[str]:
    """Resolve a term to itself plus all registered aliases from synonyms.yaml."""
    term_lower = term.lower()
    result = {term_lower}
    # Check if term is itself a canonical key
    if term_lower in synonym_map:
        result.update(synonym_map[term_lower])
    # Check if term is an alias pointing to a canonical key
    for canonical, aliases in synonym_map.items():
        if term_lower == canonical or term_lower in aliases:
            result.add(canonical)
            result.update(aliases)
            break
    return result


def _build_synonym_lookup() -> dict[str, set[str]]:
    """Build {canonical_lower: {canonical, alias1, alias2, ...}} from synonyms.yaml."""
    raw = load_synonyms()
    lookup: dict[str, set[str]] = {}
    for key, aliases in raw.items():
        key_lower = key.lower()
        lookup[key_lower] = {key_lower} | {a.lower() for a in aliases}
    return lookup


# Module-level cache — loaded once
_synonym_lookup: dict[str, set[str]] | None = None


def _get_synonym_lookup() -> dict[str, set[str]]:
    global _synonym_lookup
    if _synonym_lookup is None:
        _synonym_lookup = _build_synonym_lookup()
    return _synonym_lookup


_LABEL_RE = re.compile(r"\.?L(\d+)", re.IGNORECASE)


def enrich_query(
    req: EoICDRequirement,
    signal_label_map: dict[str, str] | None = None,
) -> EnrichedQuery:
    """Expand an EoICD requirement into an EnrichedQuery for matching."""

    syn = _get_synonym_lookup()

    # ——— Signal path decomposition ———
    signal_name = req.signal_name or ""
    segments = [s.strip() for s in signal_name.split(".") if s.strip()]
    leaf = segments[-1] if segments else ""

    # Leaf signal aliases (synonyms.yaml + common-sense map + token decomposition)
    leaf_aliases_raw: set[str] = {leaf.lower()} if leaf else set()
    if leaf:
        leaf_aliases_raw.update(_resolve_aliases(leaf, syn))
        # Exact match against SIGNAL_LEAF_ALIASES keys
        for variant in [leaf, leaf.upper(), leaf.lower()]:
            if variant in SIGNAL_LEAF_ALIASES:
                leaf_aliases_raw.update(a.lower() for a in SIGNAL_LEAF_ALIASES[variant])
        # Token-level match: decompose compound names like "HF_FWDBFAN1" → "fan"
        for token in _tokenize_name(leaf):
            if token in SIGNAL_LEAF_ALIASES:
                leaf_aliases_raw.update(a.lower() for a in SIGNAL_LEAF_ALIASES[token])
            elif token.upper() in SIGNAL_LEAF_ALIASES:
                leaf_aliases_raw.update(a.lower() for a in SIGNAL_LEAF_ALIASES[token.upper()])
        # Substring match: catch all-uppercase names like "FWDBFAN1" containing "FAN"
        leaf_upper = leaf.upper()
        for key in SIGNAL_LEAF_ALIASES:
            if key in leaf_upper:
                leaf_aliases_raw.update(a.lower() for a in SIGNAL_LEAF_ALIASES[key])

    # Device tokens: first 2 segments of signal path + alias expansion + token decomposition
    device_raw: set[str] = set()
    for seg in segments[:2]:
        seg_lower = seg.lower()
        device_raw.add(seg_lower)
        device_raw.update(_resolve_aliases(seg, syn))
        # Decompose compound names like "HF_FWDBFAN1" → tokens: hf, fwd, b, fan, 1
        for token in _tokenize_name(seg):
            device_raw.add(token)
            device_raw.update(_resolve_aliases(token, syn))
        # Substring match: catch all-uppercase "FWDBFAN1" containing "FAN"
        seg_upper = seg.upper()
        for syn_key in syn:
            if syn_key.upper() in seg_upper:
                device_raw.update(syn[syn_key])
                device_raw.add(syn_key)

    # Bus aliases
    bus_raw = _resolve_aliases(req.bus_type, syn)

    # Direction verbs
    direction_list: list[str]
    direction_set: set[str]
    if req.side == "DP":
        direction_list = sorted(SEND_VERBS)
        direction_set = {v.lower() for v in SEND_VERBS}
    else:
        direction_list = sorted(RECEIVE_VERBS)
        direction_set = {v.lower() for v in RECEIVE_VERBS}

    # Attribute category
    attr_cat = ATTR_CATEGORY_MAP.get(req.attribute_name)

    # Label extraction: 3-level fallback
    # 1) Own Label attribute (most reliable)
    # 2) Signal-level propagation map (same signal_name, different attribute)
    # 3) Regex Lxxx from signal_name (best-effort fallback)
    label_val: str | None = None
    if req.attribute_name == "Label" and req.attribute_value is not None:
        label_val = str(req.attribute_value)
    elif signal_label_map:
        label_val = signal_label_map.get(req.signal_name)
    if label_val is None:
        m = _LABEL_RE.search(signal_name)
        if m:
            label_val = m.group(1)

    # Attribute Chinese name
    attr_cn = ATTR_CN_MAP.get(req.attribute_name, req.attribute_name)

    # ——— Build enriched_text (description only; structured tokens handled by 6 dims) ———
    enriched_text = req.description

    return EnrichedQuery(
        signal_segments=segments,
        leaf_signal=leaf,
        leaf_signal_aliases=sorted(leaf_aliases_raw),
        leaf_signal_aliases_set=leaf_aliases_raw,
        device_tokens=sorted(device_raw),
        device_tokens_set=device_raw,
        bus_type=req.bus_type,
        bus_aliases=sorted(bus_raw),
        bus_aliases_set={b.lower() for b in bus_raw},
        side=req.side,
        direction_verbs=direction_list,
        direction_verbs_set=direction_set,
        attr_name=req.attribute_name,
        attr_category=attr_cat,
        attr_value=str(req.attribute_value) if req.attribute_value is not None else "",
        label_value=label_val,
        enriched_text=enriched_text,
    )
