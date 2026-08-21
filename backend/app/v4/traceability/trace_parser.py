# -*- coding: utf-8 -*-
"""Traceability parser: reads two Excel traceability tables and builds
an HLR -> ICD BlockKey index for reverse-matching pre-filtering.

Trace chains:
  Table 2 (设备->高层): ERD编号 -> HLR ID
  Table 1 (ICD->设备):   ERD编号 -> ICD FullName
  Combined:             HLR ID -> ICD FullName -> BlockKey
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


# -- Regex patterns (mirror signal_profiler.py logic) -----------------

_LABEL_SEGMENT_RE = re.compile(r'^(L\d+)', re.IGNORECASE)
_SIGNAL_FAMILY_PREFIX_RE = re.compile(r'^L\d+_(?:\d+_)?[A-Z]+\d+_')

# BlockKey suffixes that represent A429 protocol overhead fields.
# These are excluded from the traceability index because they will be
# filtered out by entry_filter before matching anyway — keeping them
# in the index inflates traced-block counts without providing usable
# candidates.  See docs/development/traceability-prefilter-issue-analysis.md
_PROTOCOL_BLOCKKEY_SUFFIXES = ('/SDI', '/LABEL', '/PARITY', '/SSM', '/OCTLBL')


# -- Data structures -------------------------------------------------

@dataclass
class TraceabilityIndex:
    """Pre-computed index mapping HLR IDs to ICD BlockKeys.

    Built from two Excel traceability tables:
      - 设备->高层需求矩阵 (ERD -> HLR)
      - 设备->ICD追溯表 (ERD -> ICD FullName)
    """

    hlr_to_blocks: dict[str, set[str]] = field(default_factory=dict)
    hlr_to_erds: dict[str, list[str]] = field(default_factory=dict)
    erd_to_icd: dict[str, list[str]] = field(default_factory=dict)

    # Statistics
    total_hlrs_traced: int = 0
    total_erds: int = 0
    total_icd_fullnames: int = 0
    icd_mapped_to_blocks: int = 0
    icd_unmapped: list[str] = field(default_factory=list)


# -- Core mapping function -------------------------------------------

def name_to_block_key(name: str) -> str | None:
    """Convert an ICD FullName or signal_name to its ICDBlock.block_key.

    Mirrors the logic in signal_profiler._extract_profile_key +
    _extract_signal_family, but as a standalone function (zero imports
    from matching to avoid coupling).

    A429 example:
      "HF_AMSC2.pi429_B2_275_2.L275_2_B2_OVHD_CPA_PACK_FLOW_RECIRC_TRIM_AIR_R_PACK_2"
      -> "L275/OVHD_CPA_PACK_FLOW_RECIRC_TRIM_AIR_R_PACK_2"

    Non-label example (CAN/A825):
      "HF_AMSC2.pi825/SPEED_CMD"
      -> "pi825/SPEED_CMD"

    Returns None for empty or unparseable names.
    """
    if not name or not name.strip():
        return None

    segments = [s.strip() for s in name.split('.') if s.strip()]
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
        label_value = label[1:]  # strip 'L'
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


# -- Table readers ---------------------------------------------------

def _read_table2_erd_to_hlr(fpath: Path, config: dict) -> dict[str, list[str]]:
    """Read Table 2 (单模块需求矩阵分析) to build ERD -> HLR mapping.

    Uses config for column indices and module skip logic.

    Returns: {erd_id: [hlr_id, ...]}

    fpath 由 build_trace_index 经文件名发现后传入；这里只读不挑文件。
    """
    import openpyxl

    erd_to_hlr: dict[str, list[str]] = defaultdict(list)
    if not fpath.exists():
        raise FileNotFoundError(f"Table 2 file not found: {fpath}")

    wb = openpyxl.load_workbook(fpath, data_only=True)
    sheet_index = config.get("trace_table2_sheet_index", 0)
    ws = wb[wb.sheetnames[sheet_index]]

    start_row = config.get("trace_table2_start_row", 4)
    erd_col = config.get("trace_table2_erd_col", 0)
    hlr_col = config.get("trace_table2_hlr_col", 3)
    module_col = config.get("trace_table2_module_col", 4)
    module_skip = config.get("trace_table2_module_skip")

    current_erd = ""

    for row in ws.iter_rows(min_row=start_row, max_row=ws.max_row, values_only=True):
        erd_cell = str(row[erd_col]).strip() if len(row) > erd_col and row[erd_col] else ""
        hlr_cell = str(row[hlr_col]).strip() if len(row) > hlr_col and row[hlr_col] else ""
        module_name = str(row[module_col]).strip() if len(row) > module_col and row[module_col] else ""

        # Fill-forward: non-empty ERD updates current
        if erd_cell and erd_cell != 'None':
            current_erd = erd_cell

        # Skip rows without a current ERD
        if not current_erd:
            continue

        # Skip rows whose module is EICD (direct ERD->ICD ref, not HLR)
        if module_skip and module_name.upper() == module_skip.upper():
            continue

        # Skip rows with no HLR ID
        if not hlr_cell or hlr_cell == 'None':
            continue

        erd_to_hlr[current_erd].append(hlr_cell)

    wb.close()
    return dict(erd_to_hlr)


def _read_table1_erd_to_icd(fpath: Path, config: dict) -> dict[str, list[str]]:
    """Read Table 1 (设备需求与系统ICD追溯表) to build ERD -> ICD FullName mapping.

    Uses config for sheet index and column indices.

    Returns: {erd_id: [icd_fullname, ...]}

    fpath 由 build_trace_index 经文件名发现后传入；这里只读不挑文件。
    """
    import openpyxl

    erd_to_icd: dict[str, list[str]] = defaultdict(list)
    if not fpath.exists():
        raise FileNotFoundError(f"Table 1 file not found: {fpath}")

    wb = openpyxl.load_workbook(fpath, data_only=True)
    sheet_index = config.get("trace_table1_sheet_index", 1)
    ws = wb[wb.sheetnames[sheet_index]]

    start_row = config.get("trace_table1_start_row", 2)
    erd_col = config.get("trace_table1_erd_col", 3)
    icd_col = config.get("trace_table1_icd_col", 7)

    current_erd = ""

    for row in ws.iter_rows(min_row=start_row, max_row=ws.max_row, values_only=True):
        erd_cell = str(row[erd_col]).strip() if len(row) > erd_col and row[erd_col] else ""
        icd_fn = str(row[icd_col]).strip() if len(row) > icd_col and row[icd_col] else ""

        if erd_cell and erd_cell != 'None':
            current_erd = erd_cell

        if not current_erd:
            continue

        if not icd_fn or icd_fn == 'None':
            continue

        erd_to_icd[current_erd].append(icd_fn)

    wb.close()
    return dict(erd_to_icd)


# -- Index builder ---------------------------------------------------

def _discover_trace_files(trace_dir: Path, config: dict) -> tuple[Path, Path]:
    """Locate the two traceability Excel files (Table 1 + Table 2).

    Primary strategy: exact filename match for each using config.
    Fallback: sorted *.xlsx glob in trace_dir, allocating in order
    (Table 1 first, then Table 2). Ensures the two paths never collide.

    Robust to:
    - Filename mangling by MSYS bash / Windows console encoding.
    - User renaming files.
    - Single xlsx with both tables (same file used for both readers).
    """
    t1_primary = trace_dir / config["trace_table1_filename"]
    t2_primary = trace_dir / config["trace_table2_filename"]
    t1 = t1_primary if t1_primary.exists() else None
    t2 = t2_primary if t2_primary.exists() else None

    if t1 and t2:
        return t1, t2

    # Pool of .xlsx files in trace_dir, sorted for determinism.
    pool = [p for p in sorted(trace_dir.glob("*.xlsx"))]

    # Assign first available unmatched file to whichever slot is empty.
    if t1 is None:
        for p in pool:
            if p != t2_primary:
                t1 = p
                break
    if t2 is None:
        for p in pool:
            if p != t1_primary and p != t1:
                t2 = p
                break

    if t1 is None or not t1.exists():
        raise FileNotFoundError(
            f"Cannot locate Table 1 ({config['trace_table1_filename']} or any .xlsx) in {trace_dir}"
        )
    if t2 is None or not t2.exists():
        raise FileNotFoundError(
            f"Cannot locate Table 2 ({config['trace_table2_filename']} or any .xlsx) in {trace_dir}"
        )

    return t1, t2


def build_trace_index(trace_dir: Path, system_config: dict | None = None) -> TraceabilityIndex:
    """Build the complete HLR -> BlockKey traceability index.

    Args:
        trace_dir: Directory containing the two traceability Excel files.
        system_config: System-specific traceability config. If None, uses HVAC defaults
            for backward compatibility.

    Returns:
        TraceabilityIndex with hlr_to_blocks, statistics, and raw mappings.

    Raises:
        FileNotFoundError: if either expected Excel file is missing.
    """
    # Use HVAC defaults if no config provided (backward compatibility)
    if system_config is None:
        system_config = {
            "trace_table1_filename": "设备需求与系统ICD追溯表.xlsx",
            "trace_table2_filename": "单模块需求矩阵分析（设备2软件高层）-裁剪.xlsx",
        }

    # Step 1: Locate the two files (primary exact + glob fallback), then read.
    t1_path, t2_path = _discover_trace_files(trace_dir, system_config)
    erd_to_hlr = _read_table2_erd_to_hlr(t2_path, system_config)
    erd_to_icd = _read_table1_erd_to_icd(t1_path, system_config)

    # Step 2: Combine -> HLR -> [ICD FullName, ...]
    index = TraceabilityIndex()
    index.hlr_to_erds = defaultdict(list)
    index.erd_to_icd = erd_to_icd
    index.total_erds = len(erd_to_hlr)  # ERDs that link to HLRs

    hlr_to_icd: dict[str, set[str]] = defaultdict(set)
    for erd, hlr_list in erd_to_hlr.items():
        icd_list = erd_to_icd.get(erd, [])
        for hlr in hlr_list:
            index.hlr_to_erds[hlr].append(erd)
            for icd_fn in icd_list:
                hlr_to_icd[hlr].add(icd_fn)

    index.total_hlrs_traced = len(hlr_to_icd)

    # Step 3: Map ICD FullName -> block_key
    # Protocol overhead blocks are excluded here because entry_filter
    # removes them before matching anyway.
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
        # Assign this block_key to ALL HLRs that reference this ICD FullName
        for hlr, icd_set in hlr_to_icd.items():
            if icd_fn in icd_set:
                index.hlr_to_blocks[hlr].add(bk)
    if protocol_skipped:
        print(f"  Trace index: skipped {protocol_skipped} protocol-overhead block(s)")

    # Convert defaultdict to plain dict
    index.hlr_to_blocks = dict(index.hlr_to_blocks)
    index.hlr_to_erds = dict(index.hlr_to_erds)

    return index
