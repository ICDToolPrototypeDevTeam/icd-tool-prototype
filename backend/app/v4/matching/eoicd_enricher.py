# -*- coding: utf-8 -*-
"""Shared EoICD name tokenization and synonym resolution helpers."""

from __future__ import annotations

import re

from app.v4.config import load_synonyms


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
