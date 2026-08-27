# -*- coding: utf-8 -*-
"""HLR identity index (Stage C4): deterministic + AI-labeled tokens.

Builds an inverted index over parsed HLR requirements:

  - deterministic tokens: regex extraction (hlr_classifier) + the Chinese→English
    keyword map. These can support a `covered` conclusion.
  - llm_label tokens: from `label_hlrs()` + `enrich_all_labels()` (bus_types /
    labels / devices / signal_keywords). These ONLY enhance candidate recall
    (source="llm_label") and never support a covered conclusion.

`label_hlrs()` is optional: if it fails or is not provided, the index degrades
to deterministic-only and forward analysis still works (never fails the task).

Each HLR is reduced to:
  - labels:        A429 label numbers (L275 ...) via hlr_classifier.extract_labels
  - signal_tokens: deterministic English identifiers + Chinese-keyword-mapped tokens
  - llm_label_tokens: AI-derived recall-only tokens
  - direction / signal_category: deterministic classification

The resulting token_index + llm_token_index (token -> [hlr_id]) power candidate
recall (C5).
"""

from __future__ import annotations

import re

from app.v4.config import CN_SIGNAL_KEYWORD_MAP
from app.v4.matching.hlr_classifier import (
    classify_hlr,
    extract_direction,
    extract_labels,
)
from app.v4.models import HLRLabel, HLRIdentityEntry, HLRIdentityIndex, HLROutput

_EN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")

# Tokens that are protocol/bus noise, not business signal identity.
_NOISE_TOKENS = {
    "A429", "A825", "A664", "CAN", "AFDX", "ARINC", "SDI", "SSM", "ADC",
    "BIT", "BITS", "DIS", "BNR", "BCD", "OCTLBL", "PARITY", "TRUE", "FALSE",
    "BOOL", "BOOLEAN", "DISCRETE", "THE", "AND", "FOR", "LOW", "HIGH",
}


def tokenize_identifier(raw: str) -> list[str]:
    """Normalize one identifier into recall tokens (whole + fragments, uppercase).

    e.g. "OVHD_CPA_PACK_FLOW" → ["OVHD_CPA_PACK_FLOW", "OVHD", "CPA", "PACK", "FLOW"].

    Shared by both the HLR index (indexing side) and candidate recall (query side)
    so both use identical tokenization.
    """
    tok = (raw or "").strip().upper()
    if not tok or tok in _NOISE_TOKENS or not _EN_TOKEN_RE.fullmatch(tok):
        return []
    out: list[str] = [tok]
    for frag in re.split(r"[_/\s]+", tok):
        frag = frag.strip()
        if len(frag) >= 3 and frag not in _NOISE_TOKENS and _EN_TOKEN_RE.fullmatch(frag):
            if frag not in out:
                out.append(frag)
    return out


def _extract_signal_tokens(text: str) -> list[str]:
    """English identifiers + Chinese-keyword-mapped English tokens."""
    tokens: set[str] = set()
    for m in _EN_TOKEN_RE.finditer(text or ""):
        tokens.update(tokenize_identifier(m.group(0)))
    for cn, en_list in CN_SIGNAL_KEYWORD_MAP.items():
        if cn in (text or ""):
            for en in en_list:
                tokens.update(tokenize_identifier(en))
    return sorted(tokens)


def _derive_llm_label_tokens(
    label: HLRLabel,
    deterministic_labels: set[str],
) -> list[str]:
    """Derive recall-only (llm_label) tokens from a merged HLRLabel.

    Sources: bus_types, labels (AI-only, i.e. not already regex-extracted),
    devices, signal_keywords. English values are tokenized directly; Chinese
    values are bridged via CN_SIGNAL_KEYWORD_MAP. These tokens are recall-only.
    """
    tokens: set[str] = set()
    det_labels = {l.upper() for l in deterministic_labels}

    for raw in label.bus_types:
        tokens.update(tokenize_identifier(raw))
    for raw in label.labels:
        up = raw.upper()
        if up not in det_labels:  # regex labels are already deterministic
            tokens.update(tokenize_identifier(raw))
    for raw in label.devices:
        tokens.update(tokenize_identifier(raw))
        for en in CN_SIGNAL_KEYWORD_MAP.get(raw, []):
            tokens.update(tokenize_identifier(en))
    for raw in label.signal_keywords:
        tokens.update(tokenize_identifier(raw))
        for en in CN_SIGNAL_KEYWORD_MAP.get(raw, []):
            tokens.update(tokenize_identifier(en))

    # Never let an llm_label token collide with a deterministic signal token
    # of the same HLR — deterministic tokens already live in signal_tokens.
    return sorted(tokens)


def build_hlr_identity_index(
    hlr: HLROutput,
    hlr_labels: dict[str, HLRLabel] | None = None,
) -> HLRIdentityIndex:
    """Build the HLR identity index (deterministic) + optional AI label tokens.

    Args:
        hlr: parsed HLR output.
        hlr_labels: optional merged labels (label_hlrs + enrich_all_labels). When
            omitted or empty, the index degrades to deterministic-only.
    """
    entries: dict[str, HLRIdentityEntry] = {}
    token_index: dict[str, set[str]] = {}
    llm_token_index: dict[str, set[str]] = {}

    for req in hlr.requirements:
        text = req.content or ""
        labels = extract_labels(text)
        label_set = {l.upper() for l in labels}
        signal_tokens = [t for t in _extract_signal_tokens(text) if t not in label_set]

        llm_label_tokens: list[str] = []
        lbl = (hlr_labels or {}).get(req.requirement_id)
        if lbl is not None:
            llm_label_tokens = _derive_llm_label_tokens(lbl, label_set)

        entry = HLRIdentityEntry(
            hlr_id=req.requirement_id,
            labels=labels,
            signal_tokens=signal_tokens,
            llm_label_tokens=llm_label_tokens,
            direction=extract_direction(text),
            signal_category=classify_hlr(text),
        )
        entries[req.requirement_id] = entry

        for tok in labels:
            token_index.setdefault(tok.upper(), set()).add(req.requirement_id)
        for tok in signal_tokens:
            token_index.setdefault(tok, set()).add(req.requirement_id)
        for tok in llm_label_tokens:
            llm_token_index.setdefault(tok, set()).add(req.requirement_id)

    return HLRIdentityIndex(
        total_hlrs=len(entries),
        entries=entries,
        token_index={k: sorted(v) for k, v in token_index.items()},
        llm_token_index={k: sorted(v) for k, v in llm_token_index.items()},
    )
