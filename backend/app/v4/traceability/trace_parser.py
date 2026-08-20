# -*- coding: utf-8 -*-
"""Traceability parser: reads traceability Excel tables and builds
an HLR -> ICD BlockKey index for reverse-matching pre-filtering.

Profile-driven: file patterns, sheet selection, and column positions are
all controlled by TraceabilityConfig.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.v4.profiles.base import TraceabilityConfig, TraceabilityTableConfig


_LABEL_SEGMENT_RE = re.compile(r"^(L\d+)", re.IGNORECASE)
_SIGNAL_FAMILY_PREFIX_RE = re.compile(r"^L\d+_(?:\d+_)?[A-Z]+\d+_")
_PROTOCOL_BLOCKKEY_SUFFIXES = ("/SDI", "/LABEL", "/PARITY", "/SSM", "/OCTLBL")


@dataclass
class TraceabilityIndex:
    """Pre-computed index mapping HLR IDs to ICD BlockKeys."""

    hlr_to_blocks: dict[str, set[str]] = field(default_factory=dict)
    hlr_to_erds: dict[str, list[str]] = field(default_factory=dict)
    erd_to_icd: dict[str, list[str]] = field(default_factory=dict)

    total_hlrs_traced: int = 0
    total_erds: int = 0
    total_icd_fullnames: int = 0
    icd_mapped_to_blocks: int = 0
    icd_unmapped: list[str] = field(default_factory=list)


def name_to_block_key(name: str) -> str | None:
    """Convert an ICD FullName or signal_name to its ICDBlock.block_key.

    Mirrors signal_profiler._extract_profile_key + _extract_signal_family.
    """
    if not name or not name.strip():
        return None
    segments = [s.strip() for s in name.split(".") if s.strip()]
    if not segments:
        return None
    label = None
    for seg in segments:
        m = _LABEL_SEGMENT_RE.match(seg)
        if m:
            label = m.group(1)
            break
    leaf = segments[-1]
    if label:
        label_value = label[1:]
        m2 = _SIGNAL_FAMILY_PREFIX_RE.match(leaf)
        if m2:
            family = leaf[m2.end():]
        else:
            family = leaf
        return f"L{label_value}/{family}"
    else:
        if len(segments) >= 2:
            parent = segments[-2]
            return f"{parent}/{leaf}"
        return leaf


def _select_sheet(wb, cfg: TraceabilityTableConfig):
    """Select sheet by name keyword match, then fallback to index."""
    if cfg.sheet_match.by_name_keywords:
        for kw in cfg.sheet_match.by_name_keywords:
            for sname in wb.sheetnames:
                if kw in sname:
                    return wb[sname]
    if cfg.sheet_match.fallback_index < len(wb.sheetnames):
        return wb[wb.sheetnames[cfg.sheet_match.fallback_index]]
    return wb[wb.sheetnames[0]]


def _read_table2_erd_to_hlr(
    fpath: Path, cfg: TraceabilityTableConfig
) -> dict[str, list[str]]:
    """Read Table 2 (parent<->HLR matrix) to build parent -> HLR mapping.

    Returns: {parent_id: [hlr_id, ...]}
    """
    import openpyxl

    erd_to_hlr: dict[str, list[str]] = defaultdict(list)
    if not fpath.exists():
        raise FileNotFoundError(f"Table 2 file not found: {fpath}")

    wb = openpyxl.load_workbook(fpath, data_only=True)
    ws = _select_sheet(wb, cfg)

    col_erd = cfg.columns["erd"]
    col_hlr = cfg.columns["hlr"]
    col_module = cfg.columns.get("module", -1)

    # Header / data split is now controlled by cfg.data_start_row, so the
    # iteration starts at (data_start_row + 1) -- row index is 1-based in openpyxl.
    data_start = cfg.data_start_row

    current_parent = ""
    for row_idx, row in enumerate(
        ws.iter_rows(min_row=data_start + 1, max_row=ws.max_row, values_only=True),
        start=data_start + 1,
    ):
        parent_cell = str(row[col_erd]).strip() if len(row) > col_erd and row[col_erd] else ""
        hlr_cell = str(row[col_hlr]).strip() if len(row) > col_hlr and row[col_hlr] else ""
        module_name = ""
        if col_module >= 0 and len(row) > col_module and row[col_module]:
            module_name = str(row[col_module]).strip()

        # Fill-forward: non-empty parent updates current
        if parent_cell and parent_cell != "None":
            current_parent = parent_cell

        if not current_parent:
            continue

        # Skip rows whose module matches skip_module list (case-insensitive)
        if module_name and module_name.upper() in {m.upper() for m in cfg.skip_module}:
            continue

        if not hlr_cell or hlr_cell == "None":
            continue

        erd_to_hlr[current_parent].append(hlr_cell)

    wb.close()
    return dict(erd_to_hlr)


def _read_table1_erd_to_icd(
    fpath: Path, cfg: TraceabilityTableConfig
) -> dict[str, list[str]]:
    """Read Table 1 (ERD<->ICD mapping) to build parent -> ICD FullName mapping."""
    import openpyxl

    erd_to_icd: dict[str, list[str]] = defaultdict(list)
    if not fpath.exists():
        raise FileNotFoundError(f"Table 1 file not found: {fpath}")

    wb = openpyxl.load_workbook(fpath, data_only=True)
    ws = _select_sheet(wb, cfg)

    col_erd = cfg.columns["erd"]
    col_icd = cfg.columns["icd_fullname"]

    current_erd = ""
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True):
        erd_cell = str(row[col_erd]).strip() if len(row) > col_erd and row[col_erd] else ""
        icd_fn = str(row[col_icd]).strip() if len(row) > col_icd and row[col_icd] else ""

        if erd_cell and erd_cell != "None":
            current_erd = erd_cell

        if not current_erd:
            continue

        if not icd_fn or icd_fn == "None":
            continue

        erd_to_icd[current_erd].append(icd_fn)

    wb.close()
    return dict(erd_to_icd)


def _discover_trace_files(
    trace_dir: Path, cfg: TraceabilityConfig
) -> tuple[Path, Path]:
    """Locate the two traceability Excel files via glob patterns."""
    pool = sorted(p for p in trace_dir.glob("*.xlsx") if p.is_file())

    def _find_one(patterns: tuple[str, ...]) -> Path | None:
        for pat in patterns:
            for p in pool:
                if p.match(pat):
                    return p
        return None

    t1 = _find_one(cfg.table1.filename_patterns)
    t2 = _find_one(cfg.table2.filename_patterns)

    if t1 is None:
        raise FileNotFoundError(
            f"Cannot locate Table 1 (patterns {cfg.table1.filename_patterns}) in {trace_dir}"
        )
    if t2 is None:
        raise FileNotFoundError(
            f"Cannot locate Table 2 (patterns {cfg.table2.filename_patterns}) in {trace_dir}"
        )

    return t1, t2


def build_trace_index(
    trace_dir: Path, cfg: TraceabilityConfig
) -> TraceabilityIndex:
    """Build the complete HLR -> BlockKey traceability index."""
    t1_path, t2_path = _discover_trace_files(trace_dir, cfg)
    erd_to_hlr = _read_table2_erd_to_hlr(t2_path, cfg.table2)
    erd_to_icd = _read_table1_erd_to_icd(t1_path, cfg.table1)

    index = TraceabilityIndex()
    index.hlr_to_erds = defaultdict(list)
    index.erd_to_icd = erd_to_icd
    index.total_erds = len(erd_to_hlr)

    hlr_to_icd: dict[str, set[str]] = defaultdict(set)
    for erd, hlr_list in erd_to_hlr.items():
        icd_list = erd_to_icd.get(erd, [])
        for hlr in hlr_list:
            index.hlr_to_erds[hlr].append(erd)
            for icd_fn in icd_list:
                hlr_to_icd[hlr].add(icd_fn)

    index.total_hlrs_traced = len(hlr_to_icd)

    all_icd_fullnames: set[str] = set()
    for icd_set in hlr_to_icd.values():
        all_icd_fullnames.update(icd_set)
    index.total_icd_fullnames = len(all_icd_fullnames)

    index.hlr_to_blocks = defaultdict(set)
    protocol_skipped = 0
    for icd_fn in all_icd_fullnames:
        bk = name_to_block_key(icd_fn)
        if not bk:
            index.icd_unmapped.append(icd_fn)
            continue
        if bk.endswith(_PROTOCOL_BLOCKKEY_SUFFIXES):
            protocol_skipped += 1
            continue
        index.icd_mapped_to_blocks += 1
        for hlr, icd_set in hlr_to_icd.items():
            if icd_fn in icd_set:
                index.hlr_to_blocks[hlr].add(bk)
    if protocol_skipped:
        print(f"  Trace index: skipped {protocol_skipped} protocol-overhead block(s)")

    index.hlr_to_blocks = dict(index.hlr_to_blocks)
    index.hlr_to_erds = dict(index.hlr_to_erds)
    return index