# -*- coding: utf-8 -*-
"""Header-semantic detection of glossary / terminology tables in HLR Word files.

Design constraints (see docs/development/hscu-full-doc-adaptation-plan.md for
the same philosophy used by the HSCU catalog detector):

* table position, column count, column order and exact header wording are not
  hard-coded;
* a table is accepted as an abbreviation glossary or a terminology table only
  when its header carries the corresponding semantic marker;
* a shape-only fallback is allowed *before the first requirement table* (front
  matter), and it is recorded as a parse warning.  Tables after the first
  requirement are never shape-classified, so data-layout / revision tables in
  the requirement body cannot be mistaken for glossaries;
* the caller supplies ``cell_text(table, row, col)`` so this module stays
  parser-agnostic and keeps ``HLRWordParser``'s nested-table flattening.

The detector intentionally does not call an LLM: input parsing must be
deterministic, cheap and reproducible ("parse each input once").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from app.v4.models import HLRGlossaryEntry, HLRTermEntry

CellText = Callable[[object, int, int], str]


# ── header vocabulary ────────────────────────────────────────────────
# Normalised (lowercase, spaces/punctuation removed) aliases.  Add a new
# spelling here instead of touching the detection flow.
_ABBR_HEADER_ALIASES = (
    "缩写", "缩略语", "缩略词", "简称", "英文缩写", "英文简称", "代号",
    "abbreviation", "abbr", "acronym",
)
_TERM_HEADER_ALIASES = (
    "术语", "名词", "词汇", "用语", "术语表", "缩略语表",
    "term", "terminology", "glossary",
)
_ABBR_COL_ALIASES = (
    "缩写", "缩略语", "缩略词", "简称", "代号",
    "abbreviation", "abbr", "acronym",
)
_TERM_COL_ALIASES = ("术语", "名词", "词汇", "用语", "term")
_ENGLISH_COL_ALIASES = (
    "英文全称", "英文名称", "英文名", "英文全名", "英文解释", "英文",
    "fullname", "englishname", "english",
)
_CHINESE_COL_ALIASES = (
    "中文全称", "中文名称", "中文名", "中文说明", "中文描述",
    "中文解释", "中文", "汉语", "chinese",
)
_DEFINITION_COL_ALIASES = (
    "定义", "说明", "释义", "解释", "描述", "含义",
    "definition", "description", "meaning", "explanation",
)

_REQUIREMENT_MARKERS = (
    "需求id", "需求编号", "需求正文", "需求中文", "需求描述",
    "需求名称", "需求内容", "对象类型", "是否衍生", "基本原理",
    "安全相关",
)
# Front-matter document-info / revision / TOC tables have vague two-column
# shapes that can look like a terminology table (short label | long text).
# Shape fallback is conservative: a header carrying these structure words is
# never accepted without an explicit glossary/terminology marker.
_STRUCTURAL_HEADER_MARKERS = (
    "项目", "内容", "名称", "编号", "版本", "日期", "修订", "变更",
    "作者", "审批", "审核", "批准", "备注", "序号", "范围", "目的",
    "目录", "章节", "页", "文件",
)

_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")
_SPACE_RE = re.compile(r"[\s　]+")
_PUNCT_RE = re.compile(r"[_\-/\\、,，:：;；.。]+")
_LATINISH_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-./ ]{0,39}")


@dataclass(frozen=True)
class GlossaryTableSpec:
    """One table accepted as a glossary/terminology table."""

    table_index: int
    kind: str                      # "abbreviation" | "term"
    header_row: int
    columns: dict[str, int]        # role -> column index


def _norm(text: str) -> str:
    """Normalise a header cell for alias matching."""
    s = (text or "").strip().lower()
    s = s.replace("（", "(").replace("）", ")")
    s = _PAREN_RE.sub("", s)
    s = _SPACE_RE.sub("", s)
    s = _PUNCT_RE.sub("", s)
    return s


def _contains_any(norm_cell: str, aliases: tuple[str, ...]) -> bool:
    return bool(norm_cell) and any(a in norm_cell for a in aliases)


def _row_cells(table, row_idx: int, cell_text: CellText) -> list[str]:
    try:
        ncols = len(table.columns)
    except (AttributeError, IndexError):
        return []
    return [cell_text(table, row_idx, c) for c in range(ncols)]


def _distinct_nonempty(cells: list[str]) -> set[str]:
    return {c.strip() for c in cells if c and c.strip()}


def _first_nonempty(table, cell_text: CellText) -> str:
    try:
        nrows, ncols = len(table.rows), len(table.columns)
    except (AttributeError, IndexError):
        return ""
    for r in range(nrows):
        for c in range(ncols):
            text = cell_text(table, r, c).strip()
            if text:
                return text
    return ""


def _is_header_like(value: str) -> bool:
    """True for a repeated header / sub-header cell inside the data rows."""
    n = _norm(value)
    if not n or len(n) > 16:
        return False
    return any(
        a in n
        for a in (
            *_ABBR_HEADER_ALIASES,
            *_TERM_HEADER_ALIASES,
            *_DEFINITION_COL_ALIASES,
        )
    )


def _classify_header(cells: list[str]) -> str | None:
    """Return "abbreviation" / "term" when the row is a glossary header."""
    norms = [_norm(c) for c in cells if c and c.strip()]
    if not norms:
        return None
    if any(marker in n for n in norms for marker in _REQUIREMENT_MARKERS):
        return None

    abbr_hit = any(_contains_any(n, _ABBR_HEADER_ALIASES) for n in norms)
    term_hit = any(_contains_any(n, _TERM_HEADER_ALIASES) for n in norms)
    if not abbr_hit and not term_hit:
        return None

    if abbr_hit and term_hit:
        # "术语和缩略语 | 定义" is a terminology table; a table with an
        # explicit abbreviation-column header is an abbreviation glossary.
        if any(_contains_any(n, _ABBR_COL_ALIASES) for n in norms) and len(norms) >= 3:
            return "abbreviation"
        return "term"
    return "abbreviation" if abbr_hit else "term"


def _find_col(
    cells: list[str], aliases: tuple[str, ...], used: set[int]
) -> int | None:
    for i, cell in enumerate(cells):
        if i in used:
            continue
        n = _norm(cell)
        if n and any(a in n for a in aliases):
            used.add(i)
            return i
    return None


def _resolve_columns(cells: list[str], kind: str) -> dict[str, int]:
    """Map semantic roles to columns by header text, then by position."""
    ncols = len(cells)
    used: set[int] = set()

    if kind == "abbreviation":
        abbr = _find_col(cells, _ABBR_COL_ALIASES, used)
        if abbr is None:
            abbr = 0
            used.add(0)
        english = _find_col(cells, _ENGLISH_COL_ALIASES, used)
        chinese = _find_col(cells, _CHINESE_COL_ALIASES, used)
        definition = _find_col(cells, _DEFINITION_COL_ALIASES, used)

        if chinese is None:
            if definition is not None:
                chinese = definition
                used.add(definition)
            elif ncols == 2:
                chinese = 1 if abbr != 1 else 0
                used.add(chinese)
            elif ncols >= 3:
                rest = [i for i in range(ncols) if i not in used and i != abbr]
                if rest:
                    chinese = rest[-1]
                    used.add(chinese)

        if english is None and ncols >= 3:
            rest = [i for i in range(ncols) if i not in used and i != abbr]
            if rest:
                english = rest[0]
                used.add(english)
        if english == chinese:
            english = None

        columns = {"abbreviation": abbr}
        if english is not None:
            columns["english_name"] = english
        if chinese is not None:
            columns["chinese_description"] = chinese
        return columns

    term = _find_col(cells, _TERM_COL_ALIASES, used)
    if term is None:
        term = 0
        used.add(0)
    definition = _find_col(cells, _DEFINITION_COL_ALIASES, used)
    if definition is None:
        rest = [i for i in range(ncols) if i not in used]
        if rest:
            definition = rest[-1]
            used.add(definition)
    english = _find_col(cells, _ENGLISH_COL_ALIASES, used)
    if english == definition:
        english = None

    columns = {"term": term}
    if definition is not None:
        columns["definition"] = definition
    if english is not None:
        columns["english_name"] = english
    return columns


def _detect_header_kind(table, cell_text: CellText) -> tuple[int | None, str | None]:
    """Find the header row and table kind.

    Row 0 is normally the header.  When row 0 is a single-cell title row
    (e.g. a merged "缩略语表" banner), row 1 is tried first.
    """
    try:
        nrows = len(table.rows)
    except (AttributeError, IndexError):
        return None, None
    rows_to_try = [0]
    if nrows > 1:
        cells0 = _row_cells(table, 0, cell_text)
        if len(_distinct_nonempty(cells0)) <= 1:
            rows_to_try = [1, 0]
    for row_idx in rows_to_try:
        cells = _row_cells(table, row_idx, cell_text)
        kind = _classify_header(cells)
        if kind is not None:
            return row_idx, kind
    return None, None


def _data_rows(
    table, header_row: int, cell_text: CellText
) -> list[list[str]]:
    try:
        nrows, ncols = len(table.rows), len(table.columns)
    except (AttributeError, IndexError):
        return []
    return [
        [cell_text(table, r, c) for c in range(ncols)]
        for r in range(header_row + 1, nrows)
    ]


def _looks_latin(text: str) -> bool:
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return latin > 0 and latin >= cjk


def _column_values(rows: list[list[str]], col: int) -> list[str]:
    values: list[str] = []
    for cells in rows:
        if 0 <= col < len(cells):
            value = cells[col].strip()
            if value:
                values.append(value)
    return values


def _passes_header_shape(
    rows: list[list[str]], columns: dict[str, int], kind: str
) -> bool:
    """Lighter validation for tables whose header already names the kind.

    An explicit glossary/terminology header is the strong signal; a table
    with a single data row is still a valid (small) glossary.  Only empty
    data and purely numeric left columns are rejected here.
    """
    left_key = "abbreviation" if kind == "abbreviation" else "term"
    left_col = columns.get(left_key)
    if left_col is None:
        return False
    left = _column_values(rows, left_col)
    if not left:
        return False
    return sum(1 for v in left if v.isdigit()) < len(left)


def _passes_shape(
    rows: list[list[str]], columns: dict[str, int], kind: str
) -> bool:
    """Sanity-check candidate rows so empty/garbage tables do not qualify."""
    left_key = "abbreviation" if kind == "abbreviation" else "term"
    left_col = columns.get(left_key)
    if left_col is None:
        return False
    left = _column_values(rows, left_col)
    if len(left) < 2:
        return False
    if sum(1 for v in left if v.isdigit()) >= max(2, int(0.8 * len(left))):
        return False

    if kind == "abbreviation":
        short = sum(
            1 for v in left
            if len(v) <= 40 and not re.search(r"[。；;]", v)
        )
        return short >= max(2, int(0.6 * len(left)))

    definition_col = columns.get("definition")
    if definition_col is None:
        return False
    definitions = _column_values(rows, definition_col)
    meaningful = sum(1 for v in definitions if len(v) >= 4)
    if meaningful < max(2, int(0.5 * len(left))):
        return False
    # A terminology table's definition column must carry more text than the
    # term column overall (guards against a two-column data table).
    return sum(len(v) for v in definitions) >= sum(len(v) for v in left)


def _mostly_latin_abbr(values: list[str]) -> bool:
    latin = sum(1 for v in values if _LATINISH_RE.fullmatch(v))
    return latin >= max(2, int(0.6 * len(values)))


def _shape_spec(
    table, table_index: int, cell_text: CellText
) -> GlossaryTableSpec | None:
    """Shape-only fallback for front-matter tables with vague headers."""
    try:
        nrows, ncols = len(table.rows), len(table.columns)
    except (AttributeError, IndexError):
        return None
    if nrows < 3 or ncols not in (2, 3):
        return None

    header_cells = _row_cells(table, 0, cell_text)
    if any(
        marker in _norm(c)
        for c in header_cells
        for marker in (*_REQUIREMENT_MARKERS, *_STRUCTURAL_HEADER_MARKERS)
    ):
        return None

    rows = _data_rows(table, 0, cell_text)

    if ncols == 2:
        columns = {"term": 0, "definition": 1}
        if _passes_shape(rows, columns, "term"):
            return GlossaryTableSpec(table_index, "term", 0, columns)
        return None

    columns = {
        "abbreviation": 0,
        "english_name": 1,
        "chinese_description": 2,
    }
    values = _column_values(rows, 0)
    if _passes_shape(rows, columns, "abbreviation") and _mostly_latin_abbr(values):
        return GlossaryTableSpec(table_index, "abbreviation", 0, columns)

    columns = {"term": 0, "english_name": 1, "definition": 2}
    if _passes_shape(rows, columns, "term"):
        return GlossaryTableSpec(table_index, "term", 0, columns)
    return None


def detect_glossary_tables(
    tables,
    cell_text: CellText,
    *,
    first_requirement_index: int | None = None,
    requirement_indices: set[int] | frozenset[int] = frozenset(),
) -> tuple[list[GlossaryTableSpec], list[dict]]:
    """Return accepted glossary specs plus parse-warning candidates.

    Header-semantic detection runs on every table (a glossary may live in an
    annex).  Shape fallback runs only before the first requirement table.
    """
    specs: list[GlossaryTableSpec] = []
    warnings: list[dict] = []

    for idx, table in enumerate(tables):
        if idx in requirement_indices:
            continue
        try:
            nrows, ncols = len(table.rows), len(table.columns)
        except (AttributeError, IndexError):
            continue
        if nrows < 2 or ncols < 2:
            continue

        header_row, kind = _detect_header_kind(table, cell_text)
        if kind is not None:
            assert header_row is not None
            cells = _row_cells(table, header_row, cell_text)
            columns = _resolve_columns(cells, kind)
            rows = _data_rows(table, header_row, cell_text)
            if _passes_header_shape(rows, columns, kind):
                specs.append(GlossaryTableSpec(idx, kind, header_row, columns))
            else:
                warnings.append(
                    {
                        "table_index": idx,
                        "kind": "glossary_table_rejected",
                        "reason": "表头像术语/缩略语表，但数据行不满足基本形态",
                        "sample": _first_nonempty(table, cell_text)[:120],
                    }
                )
            continue

        if first_requirement_index is not None and idx >= first_requirement_index:
            continue
        spec = _shape_spec(table, idx, cell_text)
        if spec is not None:
            specs.append(spec)
            warnings.append(
                {
                    "table_index": idx,
                    "kind": "glossary_shape_fallback",
                    "reason": (
                        "表头未命中术语/缩略语关键字，按首张需求表之前的"
                        "形状特征识别（左列短词 / 右列定义）"
                    ),
                    "sample": _first_nonempty(table, cell_text)[:120],
                }
            )

    return specs, warnings


def parse_abbreviation_table(
    table, spec: GlossaryTableSpec, cell_text: CellText
) -> list[HLRGlossaryEntry]:
    entries: list[HLRGlossaryEntry] = []
    seen: set[str] = set()
    for cells in _data_rows(table, spec.header_row, cell_text):
        abbr = _cell(cells, spec.columns.get("abbreviation"))
        if not abbr or _is_header_like(abbr):
            continue
        english = _cell(cells, spec.columns.get("english_name"))
        chinese = _cell(cells, spec.columns.get("chinese_description"))

        # Column order varies across documents; if the "English" slot holds
        # Chinese text, move it to the Chinese/description slot.
        if english and not _looks_latin(english):
            if not chinese:
                chinese, english = english, ""
            elif _looks_latin(chinese):
                english, chinese = chinese, english
            else:
                # Both slots are Chinese: the shorter cell is the name, the
                # longer one is a description (unknown header wording).
                name, desc = (
                    (english, chinese)
                    if len(english) <= len(chinese)
                    else (chinese, english)
                )
                english = ""
                chinese = name if not desc or desc == name else f"{name}（{desc}）"
        elif not english and chinese and _looks_latin(chinese):
            english, chinese = chinese, ""

        key = abbr.lower()
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            HLRGlossaryEntry(
                abbreviation=abbr,
                english_name=english,
                chinese_description=chinese,
            )
        )
    return entries


def parse_term_table(
    table, spec: GlossaryTableSpec, cell_text: CellText
) -> list[HLRTermEntry]:
    entries: list[HLRTermEntry] = []
    seen: set[str] = set()
    for cells in _data_rows(table, spec.header_row, cell_text):
        term = _cell(cells, spec.columns.get("term"))
        if not term or _is_header_like(term):
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            HLRTermEntry(
                term=term,
                definition=_cell(cells, spec.columns.get("definition")),
                english_name=_cell(cells, spec.columns.get("english_name")),
            )
        )
    return entries


def _cell(cells: list[str], col: int | None) -> str:
    if col is None or not (0 <= col < len(cells)):
        return ""
    return cells[col].strip()
