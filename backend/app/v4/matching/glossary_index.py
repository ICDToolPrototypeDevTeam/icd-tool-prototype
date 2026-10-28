# -*- coding: utf-8 -*-
"""Case-scoped glossary index for the reverse judge prompts.

The document glossary / terminology table explains *SWHLR wording* (domain
terms, abbreviations, measurement conventions).  It is **not** an EoICD
definition and must never override the EoICD Block.  To keep prompts small
and cache keys stable, only the entries actually mentioned by a case are
attached to that case; cases with no hit render nothing and keep their
previous prompt byte-for-byte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.v4.models import HLRGlossaryEntry, HLROutput, HLRTermEntry, ReverseCase


MAX_HITS = 20
MAX_PROMPT_CHARS = 1500
MAX_DEFINITION_CHARS = 160

# Single generic words are not useful retrieval keys: they occur in almost
# every requirement and would attach irrelevant entries to most cases.
_GENERIC_ENGLISH = frozenset(
    {
        "valve", "sensor", "system", "status", "control", "signal", "data",
        "value", "switch", "unit", "fault", "position", "temperature",
        "pressure", "flow", "air", "engine", "channel", "command", "word",
        "label", "bit", "bus", "input", "output", "power", "on", "off",
        "yes", "no", "true", "false", "open", "closed", "valid", "invalid",
    }
)
_GENERIC_CHINESE = frozenset(
    {
        "信号", "通道", "数据", "状态", "指令", "告警", "系统", "设备",
        "参数", "定义", "说明", "功能", "接口", "总线", "故障", "数值",
        "值", "是", "否", "开", "关", "有效", "无效", "正常", "异常",
    }
)

_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")
_WHITESPACE_RE = re.compile(r"[\s　]+")
_MEANINGFUL_RE = re.compile(r"[A-Za-z0-9一-鿿]")
_CJK_RE = re.compile(r"[一-鿿]")
_KEY_SPLIT_RE = re.compile(r"[/、,，;；]")


@dataclass(frozen=True)
class GlossaryHit:
    """One glossary entry mentioned by a case."""

    kind: str                      # "abbreviation" | "term"
    term: str
    english_name: str = ""
    chinese_name: str = ""
    definition: str = ""
    matched_text: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "term": self.term,
            "english_name": self.english_name,
            "chinese_name": self.chinese_name,
            "definition": self.definition,
            "matched_text": self.matched_text,
        }


@dataclass(frozen=True)
class _Entry:
    hit: GlossaryHit
    keys: tuple[str, ...]


def _one_line(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text or "").strip()


def _add_key(keys: list[str], seen: set[str], text: str) -> None:
    value = _one_line(text)
    if not value:
        return
    variants = [value]
    stripped = _one_line(_PAREN_RE.sub("", value))
    if stripped and stripped != value:
        variants.append(stripped)
    for part in _KEY_SPLIT_RE.split(value):
        part = _one_line(part)
        if part:
            variants.append(part)
    for variant in variants:
        key = variant.lower()
        if key in seen:
            continue
        if not _is_useful_key(variant):
            continue
        seen.add(key)
        keys.append(variant)


def _is_useful_key(key: str) -> bool:
    if len(key) < 2 or len(key) > 80:
        return False
    if not _MEANINGFUL_RE.search(key):
        return False
    if _CJK_RE.search(key):
        return key not in _GENERIC_CHINESE
    # Pure Latin / numeric keys.
    return key.lower() not in _GENERIC_ENGLISH


def _entry_from_abbreviation(entry: HLRGlossaryEntry) -> _Entry | None:
    keys: list[str] = []
    seen: set[str] = set()
    _add_key(keys, seen, entry.abbreviation)
    _add_key(keys, seen, entry.english_name)
    _add_key(keys, seen, entry.chinese_description)
    if not keys:
        return None
    return _Entry(
        hit=GlossaryHit(
            kind="abbreviation",
            term=_one_line(entry.abbreviation),
            english_name=_one_line(entry.english_name),
            chinese_name=_one_line(entry.chinese_description),
        ),
        keys=tuple(keys),
    )


def _entry_from_term(entry: HLRTermEntry) -> _Entry | None:
    keys: list[str] = []
    seen: set[str] = set()
    _add_key(keys, seen, entry.term)
    _add_key(keys, seen, entry.english_name)
    if not keys:
        return None
    return _Entry(
        hit=GlossaryHit(
            kind="term",
            term=_one_line(entry.term),
            english_name=_one_line(entry.english_name),
            definition=_one_line(entry.definition)[:MAX_DEFINITION_CHARS],
        ),
        keys=tuple(keys),
    )


class GlossaryIndex:
    """Document glossary + terminology entries, queryable per case text."""

    def __init__(
        self,
        glossary: list[HLRGlossaryEntry] | None = None,
        terms: list[HLRTermEntry] | None = None,
    ) -> None:
        entries: list[_Entry] = []
        for entry in glossary or []:
            built = _entry_from_abbreviation(entry)
            if built is not None:
                entries.append(built)
        for entry in terms or []:
            built = _entry_from_term(entry)
            if built is not None:
                entries.append(built)
        self._entries = entries

    def __bool__(self) -> bool:
        return bool(self._entries)

    def hits_for_texts(
        self,
        texts: list[str],
        *,
        limit: int = MAX_HITS,
        max_chars: int = MAX_PROMPT_CHARS,
    ) -> list[GlossaryHit]:
        combined = "\n".join(t for t in texts if t)
        if not combined or not self._entries:
            return []

        found: list[tuple[int, GlossaryHit]] = []
        for entry in self._entries:
            position, matched = self._first_match(entry, combined)
            if position is None:
                continue
            hit = GlossaryHit(
                kind=entry.hit.kind,
                term=entry.hit.term,
                english_name=entry.hit.english_name,
                chinese_name=entry.hit.chinese_name,
                definition=entry.hit.definition,
                matched_text=matched or "",
            )
            found.append((position, hit))

        found.sort(key=lambda item: (item[0], item[1].term.lower()))
        selected: list[GlossaryHit] = []
        used = 0
        for _, hit in found:
            cost = (
                len(hit.term)
                + len(hit.english_name)
                + len(hit.chinese_name)
                + len(hit.definition)
                + 12
            )
            if selected and (len(selected) >= limit or used + cost > max_chars):
                break
            selected.append(hit)
            used += cost
        return selected

    @staticmethod
    def _first_match(entry: _Entry, combined: str) -> tuple[int | None, str]:
        """Return the earliest match, preferring the longest key on a tie."""
        best_pos: int | None = None
        best_key = ""
        for key in entry.keys:
            if _CJK_RE.search(key):
                position = combined.find(key)
                if position < 0:
                    continue
            else:
                match = GlossaryIndex._pattern_for(key).search(combined)
                if match is None:
                    continue
                position = match.start()
            if (
                best_pos is None
                or position < best_pos
                or (position == best_pos and len(key) > len(best_key))
            ):
                best_pos = position
                best_key = key
        if best_pos is None:
            return None, ""
        return best_pos, best_key

    _PATTERN_CACHE: dict[str, re.Pattern[str]] = {}

    @classmethod
    def _pattern_for(cls, key: str) -> re.Pattern[str]:
        pattern = cls._PATTERN_CACHE.get(key.lower())
        if pattern is None:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])",
                re.IGNORECASE,
            )
            cls._PATTERN_CACHE[key.lower()] = pattern
        return pattern


def build_glossary_index(hlr_out: HLROutput | None) -> GlossaryIndex:
    if hlr_out is None:
        return GlossaryIndex()
    return GlossaryIndex(
        getattr(hlr_out, "glossary", None),
        getattr(hlr_out, "terms", None),
    )


def _case_texts(case: ReverseCase) -> list[str]:
    """Texts that can mention a glossary entry.

    HLR content / rationale drive the "SWHLR wording" use case; the matched
    Block's signal names are included because a case where both sides use the
    same abbreviation benefits from its expansion.  EoICD names alone never
    create a hit for an entry the HLR does not mention, because the HLR text
    is always scanned first and every hit is case-scoped.
    """
    texts: list[str] = []
    hlr = case.hlr_requirement or {}
    for key in ("content", "rationale"):
        value = hlr.get(key)
        if value:
            texts.append(str(value))
    for blk in case.matched_profiles or []:
        for key in ("signal_family", "block_key"):
            value = blk.get(key)
            if value:
                texts.append(str(value))
        for sub in blk.get("sub_signals") or []:
            value = sub.get("dp_name")
            if value:
                texts.append(str(value))
        for view in blk.get("side_views") or []:
            for names_key in ("dp_names", "rp_names"):
                for name in view.get(names_key) or []:
                    if name:
                        texts.append(str(name))
    return texts


def attach_glossary_hits(
    cases: list[ReverseCase], index: GlossaryIndex
) -> list[ReverseCase]:
    """Attach case-scoped glossary hits in place; no-op for empty indexes."""
    if not index:
        return cases
    for case in cases:
        hits = index.hits_for_texts(_case_texts(case))
        if hits:
            case.glossary_hits = [hit.to_dict() for hit in hits]
    return cases


def append_glossary_section(parts: list[str], hits: list[dict]) -> None:
    """Render the case-scoped glossary block into a prompt parts list."""
    if not hits:
        return
    parts.append("")
    parts.append("## 文档术语与缩略语（仅本 case 命中项）")
    parts.append(
        "以下条目摘自本 SWHLR 文档的术语表/缩略语表，仅用于理解 SWHLR 的"
        "用词、范围与数值口径；它们不是 EoICD 定义，不作为 EoICD 字段的"
        "依据。与 EoICD 冲突时，以 EoICD 为准。"
    )
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        kind = hit.get("kind")
        term = _one_line(str(hit.get("term") or ""))
        if not term:
            continue
        english = _one_line(str(hit.get("english_name") or ""))
        chinese = _one_line(str(hit.get("chinese_name") or ""))
        definition = _one_line(str(hit.get("definition") or ""))
        if kind == "abbreviation":
            expansion = english
            if chinese:
                expansion = f"{expansion}（{chinese}）" if expansion else chinese
            line = f"- 缩略语「{term}」"
            if expansion:
                line += f" = {expansion}"
        else:
            line = f"- 术语「{term}」"
            if english:
                line += f"（{english}）"
            if definition:
                line += f"：{definition}"
        parts.append(line)
