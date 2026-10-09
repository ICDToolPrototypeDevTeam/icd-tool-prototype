# -*- coding: utf-8 -*-
"""
EoICD 追溯链解析工具
====================

用途：在「去重归簇」之后，若输入目录中存在追溯表，则按需求追溯链路逐级追溯，
      求出与输入软件高层需求（HLR）相关的 EoICD FullName 集合，用于缩小处理范围。

支持两种链路：
  - 3 层链路：软件高层需求 --(设备↔软件追溯表)--> 设备层级需求 --(EoICD↔设备追溯表)--> EoICD
  - 4 层链路：软件高层需求 --(设备↔软件追溯表)--> 设备层级需求
              --(系统↔设备追溯表)--> 系统层级需求 --(EoICD↔系统追溯表)--> EoICD

若输入目录中没有追溯表，则输出 enabled=false，流水线按原方案全量保留。

用法：
  python3 src/trace_eoicd.py -c config/traceability.yaml -r ../input/ams -o data/processed/trace_result.json
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import openpyxl
import yaml


# ============================ 通用工具 ============================

def _norm(value: Any) -> str:
    """单元格值转去空格字符串"""
    if value is None:
        return ""
    return str(value).strip()


def _contains_keyword(text: str, keywords: list[str], ignore_case: bool = True) -> bool:
    """text 是否包含任一关键字"""
    if ignore_case:
        text = text.lower()
    for kw in keywords:
        kw2 = str(kw).strip()
        if not kw2:
            continue
        hay = kw2.lower() if ignore_case else kw2
        if hay in text:
            return True
    return False


def _first_match(values: list[str], keywords: list[str]) -> str | None:
    """在字符串列表中找第一个「包含任一关键字」的元素"""
    for kw in keywords:
        kw2 = str(kw).strip()
        if not kw2:
            continue
        for v in values:
            if v and kw2 in v:
                return v
    return None


# ============================ Excel 读取 ============================

def collect_header_hints(cfg: dict) -> list[str]:
    """
    收集所有可能的列名关键字（来自 columns 配置），用于定位真正的表头行。
    例如 ["需求编号", "模块名称", "ERD编号", "ICD FullName", "链接类型", ...]
    """
    hints: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, str) and v.strip():
                    hints.append(v.strip())
        elif isinstance(node, str) and node.strip():
            hints.append(node.strip())

    walk(cfg.get("columns", {}))
    # 长关键字优先，避免短词误命中
    return sorted(set(hints), key=len, reverse=True)


def _read_header(ws, max_rows: int = 5,
                 hint_keywords: list[str] | None = None) -> tuple[int, list[str]]:
    """
    定位表头行。

    策略（按优先级）：
      1. 前 max_rows 行中，第一个命中 hint 关键字的行 —— 最可靠
      2. 退回「非空单元格最多」的行

    为什么不能只用「非空最多」：矩阵表的数据行往往填得比表头行还满
    （如「单模块需求矩阵分析(系统2设备)」数据行 8 个非空 > 表头行 7 个），
    会把数据行误当成表头，导致识别失败。
    """
    rows: list[list[Any]] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        rows.append(list(row))
        if i + 1 >= max_rows:
            break
    if not rows:
        return 1, []

    if hint_keywords:
        for i, row in enumerate(rows):
            line = " | ".join(_norm(c) for c in row)
            if any(kw in line for kw in hint_keywords):
                return i + 1, [_norm(c) for c in row]

    best_idx, best_count = 0, -1
    for i, row in enumerate(rows):
        cnt = sum(1 for c in row if _norm(c))
        if cnt > best_count:
            best_idx, best_count = i, cnt
    return best_idx + 1, [_norm(c) for c in rows[best_idx]]


def _header_text(ws, max_rows: int = 3) -> str:
    """取前若干行所有单元格文本，拼接用于类型识别"""
    parts: list[str] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        for c in row:
            s = _norm(c)
            if s:
                parts.append(s)
        if i + 1 >= max_rows:
            break
    return " | ".join(parts)


def _repeat_ok(tcfg: dict, header: list[str]) -> bool:
    """
    校验表头中指定关键字出现的列数是否达标。
    配置形如：required_repeat: {需求编号: 2}
    """
    for kw, min_count in (tcfg.get("required_repeat") or {}).items():
        cnt = sum(1 for h in header if h and str(kw) in h)
        if cnt < int(min_count):
            return False
    return True


def _locate_col(header: list[str], candidates: list[str], occurrence: int = 0) -> int | None:
    """
    在表头中定位列：按候选关键字顺序，找第 occurrence 个命中的列索引。
    occurrence=0 表示第一个命中，1 表示第二个命中（矩阵表左右两栏同名列）。
    """
    hits: list[int] = []
    for kw in candidates:
        kw2 = str(kw).strip()
        if not kw2:
            continue
        for idx, name in enumerate(header):
            if name and kw2 in name and idx not in hits:
                hits.append(idx)
    # 按列顺序稳定排序
    hits = sorted(set(hits))
    if len(hits) > occurrence:
        return hits[occurrence]
    return None


def _forward_fill(rows: list[list[Any]], col: int, start: int = 0) -> list[str]:
    """
    对指定列做向下填充（应对 Excel 合并单元格：只有首行有值，后续为空）。
    返回与 rows 等长的字符串列表。
    """
    out: list[str] = []
    last = ""
    for r in rows[start:]:
        v = _norm(r[col]) if col < len(r) else ""
        if v:
            last = v
        out.append(last)
    return out


# ============================ 表类型识别 ============================

def _is_hlr_clustered_json(path: Path, hlr_cfg: dict) -> bool:
    """
    判断 json 是否为「已结构化的 HLR 结果」（如 hlr_clustered.json）。
    条件：文件名含关键字，且能取到需求列表（array_field 里是 dict 且含 id_field）。
    """
    jcfg = hlr_cfg.get("clustered_json", {})
    name_kw = jcfg.get("filename_keywords", ["hlr_clustered", "hlr"])
    array_field = jcfg.get("array_field", "requirements")
    id_field = jcfg.get("id_field", "req_id")

    if not _contains_keyword(path.name, name_kw):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        return False

    items = data.get(array_field) if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        return False
    return any(isinstance(it, dict) and it.get(id_field) for it in items[:5])


def detect_tables(cfg: dict, raw_dirs: list[Path]) -> dict[str, list[dict]]:
    """
    扫描输入目录，识别追溯表与 HLR 输入文件。
    返回 {"eoicd_erd": [...], "sys_erd": [...], "req_matrix": [...], "hlr_source": [...]}

    raw_dirs 由调用方决定（-r 参数优先，否则取配置的 scan_dirs），本函数不再自行回退。
    """
    exts = set(cfg.get("scan_extensions", [".xlsx"]))
    skip = cfg.get("skip_file_patterns", ["~$"])
    types_cfg = cfg.get("table_types", {})
    hlr_cfg = cfg.get("hlr_source", {})
    hlr_kw = hlr_cfg.get("filename_keywords", ["软件高层需求", "HLR"])
    hints = collect_header_hints(cfg)
    skip_sheet = cfg.get("skip_sheet_keywords", [])

    found: dict[str, list[dict]] = {
        "eoicd_erd": [],
        "sys_erd": [],
        "req_matrix": [],
        "hlr_source": [],
    }

    # 只扫描调用方传入的目录（-r 优先，其次配置的 scan_dirs）。
    # 不要在这里回退到配置，否则命令行 -r 会失效。
    for scan_dir in raw_dirs:
        if not scan_dir.exists():
            continue

        for path in sorted(scan_dir.iterdir()):
            if not path.is_file():
                continue
            if any(p in path.name for p in skip):
                continue
            if path.suffix.lower() not in exts:
                continue

            # ---- HLR 输入文件（优先结构化 JSON，其次 Word / Excel）----
            is_trace_table = False

            if path.suffix.lower() == ".json":
                # 已结构化的 HLR 结果（如 hlr_clustered.json）
                if _is_hlr_clustered_json(path, hlr_cfg):
                    found["hlr_source"].append({"path": str(path), "type": "json"})
                continue

            if path.suffix.lower() == ".docx":
                if _contains_keyword(path.name, hlr_kw):
                    found["hlr_source"].append({"path": str(path), "type": "docx"})
                continue

            # ---- Excel：逐 Sheet 判断类型 ----
            try:
                wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠️  无法读取 {path.name}: {e}")
                continue

            for sheet_name in wb.sheetnames:
                # 跳过说明页/修订记录等叙述性 Sheet。
                # 这类 Sheet 正文常出现 ERD编号 / ICD FullName 字样，
                # 仅靠表头关键字匹配无法排除，必须按 Sheet 名过滤。
                if skip_sheet and _contains_keyword(sheet_name, skip_sheet):
                    continue

                ws = wb[sheet_name]
                _, header = _read_header(ws, hint_keywords=hints)
                # 只用「表头行」做关键字匹配。
                # 若用前几行的拼接文本，会把叙述性内容误判为追溯表。
                if not any(header):
                    continue
                text = " | ".join(h for h in header if h)

                matched = None
                for tname in ("eoicd_erd", "sys_erd", "req_matrix"):
                    tcfg = types_cfg.get(tname, {})
                    required = tcfg.get("required_header_keywords", [])
                    if not required:
                        continue
                    if not all(_contains_keyword(text, [kw]) for kw in required):
                        continue
                    # 重复列数校验（如矩阵表需左右两栏各一个「需求编号」）
                    if not _repeat_ok(tcfg, header):
                        continue
                    matched = tname
                    break

                if matched is None:
                    continue

                is_trace_table = True
                found[matched].append({
                    "path": str(path),
                    "type": "xlsx",
                    "sheet": sheet_name,
                })
                print(f"  ✅ 识别为 {matched}: {path.name} / [{sheet_name}]")

            wb.close()

            # Excel 但不是追溯表，且文件名像 HLR → 作为 HLR 输入
            if not is_trace_table and path.suffix.lower() in (".xlsx", ".xlsm"):
                if _contains_keyword(path.name, hlr_kw):
                    found["hlr_source"].append({"path": str(path), "type": "xlsx"})

    return found


# ============================ HLR 需求编号提取 ============================

def load_hlr_ids(cfg: dict, sources: list[dict]) -> list[str]:
    """
    提取 HLR 需求编号。

    来源优先级：
      1) 结构化 HLR 结果 JSON（hlr_clustered.json）—— 存在则只用它
      2) Word 需求文档（docx）
      3) Excel 需求清单（xlsx）
    """
    pattern = re.compile(cfg.get("id_patterns", {}).get("hlr", r'FSF\d+_HLR_\d+'))
    ids: set[str] = set()

    json_sources = [s for s in sources if s.get("type") == "json"]
    if json_sources:
        for src in json_sources:
            ids.update(_extract_ids_from_clustered_json(Path(src["path"]), cfg))
        print(f"  HLR 来源: 结构化 JSON × {len(json_sources)} 个文件")
        return sorted(ids)

    for src in sources:
        path = Path(src["path"])
        if src.get("type") == "docx":
            ids.update(_extract_ids_from_docx(path, pattern))
        else:
            ids.update(_extract_ids_from_xlsx(path, pattern, cfg))
    print(f"  HLR 来源: 需求文档 × {len(sources)} 个文件（docx/xlsx 解析）")

    return sorted(ids)


def _extract_ids_from_clustered_json(path: Path, cfg: dict) -> set[str]:
    """从结构化 HLR 结果 JSON 中取需求编号（requirements[].req_id）"""
    jcfg = cfg.get("hlr_source", {}).get("clustered_json", {})
    array_field = jcfg.get("array_field", "requirements")
    id_field = jcfg.get("id_field", "req_id")

    ids: set[str] = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get(array_field) if isinstance(data, dict) else data
        for it in items or []:
            if isinstance(it, dict):
                v = _norm(it.get(id_field))
                if v:
                    ids.add(v)
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  读取 HLR JSON 失败 {path.name}: {e}")
    return ids


def _extract_ids_from_docx(path: Path, pattern: re.Pattern) -> set[str]:
    """从 docx 正文中提取需求编号（直接解析 xml，不依赖 python-docx）"""
    ids: set[str] = set()
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
        text = re.sub(r"<[^>]+>", "\n", xml)
        ids.update(pattern.findall(text))
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  读取 docx 失败 {path.name}: {e}")
    return ids


def _extract_ids_from_xlsx(path: Path, pattern: re.Pattern, cfg: dict) -> set[str]:
    """从 xlsx 中提取需求编号：优先读「需求编号」列，找不到则全表正则"""
    ids: set[str] = set()
    id_col_kw = ["需求编号", "需求ID", "Requirement ID"]
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            _, header = _read_header(ws, hint_keywords=collect_header_hints(cfg))
            col = _locate_col(header, id_col_kw)
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i == 0:
                    continue
                cells = list(row)
                if col is not None:
                    if col < len(cells):
                        ids.update(pattern.findall(_norm(cells[col])))
                else:
                    for c in cells:
                        ids.update(pattern.findall(_norm(c)))
        wb.close()
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  读取 xlsx 失败 {path.name}: {e}")
    return ids


# ============================ 需求层级判定 ============================

def _compile_patterns(cfg: dict) -> dict[str, re.Pattern]:
    """编译 id_patterns 中的各层正则"""
    pats = cfg.get("id_patterns", {})
    out = {}
    for level in ("hlr", "erd", "srd"):
        p = pats.get(level)
        if p:
            try:
                out[level] = re.compile(p)
            except re.error as e:  # noqa: BLE001
                print(f"  ⚠️  id_patterns.{level} 正则非法: {e}")
    return out


def classify_level(req_id: str, patterns: dict[str, re.Pattern]) -> str | None:
    """
    按编号命名规则判定需求属于哪一层。
    判定顺序 hlr → erd → srd，返回 "hlr" / "erd" / "srd"，无法归类返回 None。
    """
    if not req_id:
        return None
    for level in ("hlr", "erd", "srd"):
        pat = patterns.get(level)
        if pat and pat.fullmatch(req_id):
            return level
    return None


# ============================ 追溯表解析 ============================

def parse_matrix_table(path: str, sheet: str, cfg: dict) -> tuple[list[tuple[str, str]], dict]:
    """
    解析需求矩阵表（左右两栏并排：左=上层需求，右=下层需求）。

    返回 ([(上层ID, 下层ID)], 统计信息)

    左右两栏分别属于哪一层（SRD / ERD / HLR）**按编号规则自动判定**，
    不依赖文件名——因为「单模块需求矩阵分析(系统2设备)」与「(设备2软件)」
    的表头完全相同，只有编号格式不同。
    """
    mcfg = cfg.get("columns", {}).get("req_matrix", {})
    fcfg = cfg.get("filter", {})
    ignore_case = fcfg.get("ignore_case", True)

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet]
    header_row, header = _read_header(ws, hint_keywords=collect_header_hints(cfg))

    c_upper_id = _locate_col(header, mcfg.get("upper_id", ["需求编号"]), 0)
    c_lower_id = _locate_col(header, mcfg.get("lower_id", ["需求编号"]), 1)
    c_upper_doc = _locate_col(header, mcfg.get("upper_doc", ["模块名称"]), 0)
    c_lower_doc = _locate_col(header, mcfg.get("lower_doc", ["模块名称"]), 1)
    c_link = _locate_col(header, mcfg.get("link_type", ["链接类型"]), 0)

    if c_upper_id is None or c_lower_id is None:
        wb.close()
        raise ValueError(f"矩阵表缺少「需求编号」列（需左右两栏各一个）: {path} [{sheet}]")

    rows = [list(r) for r in ws.iter_rows(min_row=header_row + 1, values_only=True)]
    wb.close()

    upper_ids = _forward_fill(rows, c_upper_id)
    upper_docs = _forward_fill(rows, c_upper_doc) if c_upper_doc is not None else [""] * len(rows)

    patterns = _compile_patterns(cfg)
    require_both = fcfg.get("require_both_levels", True)

    pairs: list[tuple[str, str]] = []
    skipped = 0
    level_pairs: dict[str, int] = {}
    for i, row in enumerate(rows):
        upper = upper_ids[i]
        lower = _norm(row[c_lower_id]) if c_lower_id < len(row) else ""
        lower_doc = _norm(row[c_lower_doc]) if (c_lower_doc is not None and c_lower_doc < len(row)) else ""
        link = _norm(row[c_link]) if (c_link is not None and c_link < len(row)) else ""

        if not upper or not lower:
            continue

        up_lv = classify_level(upper, patterns)
        low_lv = classify_level(lower, patterns)
        if require_both and (up_lv is None or low_lv is None):
            skipped += 1
            continue

        # 可选：按文档名关键字过滤（配置为空则不过滤）
        if fcfg.get("lower_doc_keywords") and lower_doc:
            if not _contains_keyword(lower_doc, fcfg["lower_doc_keywords"], ignore_case):
                skipped += 1
                continue
        if fcfg.get("upper_doc_keywords") and upper_docs[i]:
            if not _contains_keyword(upper_docs[i], fcfg["upper_doc_keywords"], ignore_case):
                skipped += 1
                continue
        if link and _contains_keyword(link, fcfg.get("exclude_link_types", []), ignore_case):
            skipped += 1
            continue

        pairs.append((upper, lower))
        key = f"{up_lv}→{low_lv}"
        level_pairs[key] = level_pairs.get(key, 0) + 1

    stats = {
        "rows": len(rows),
        "kept": len(pairs),
        "skipped": skipped,
        "level_pairs": level_pairs,
    }
    return pairs, stats


def parse_sys_erd_table(path: str, sheet: str, cfg: dict) -> tuple[list[tuple[str, str]], dict]:
    """解析「系统层级需求 ↔ 设备层级需求」表，返回 ([(系统ID, 设备ID)], 统计)"""
    scfg = cfg.get("columns", {}).get("sys_erd", {})

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet]
    header_row, header = _read_header(ws, hint_keywords=collect_header_hints(cfg))

    c_sys = _locate_col(header, scfg.get("sys_id", ["系统需求编号"]), 0)
    c_erd = _locate_col(header, scfg.get("erd_id", ["设备需求编号"]), 0)
    if c_sys is None or c_erd is None:
        wb.close()
        raise ValueError(f"系统↔设备表缺少必要列: {path} [{sheet}]")

    rows = [list(r) for r in ws.iter_rows(min_row=header_row + 1, values_only=True)]
    wb.close()

    sys_ids = _forward_fill(rows, c_sys)
    pairs = [
        (sys_ids[i], _norm(r[c_erd]) if c_erd < len(r) else "")
        for i, r in enumerate(rows)
    ]
    pairs = [(s, e) for s, e in pairs if s and e]
    return pairs, {"rows": len(rows), "kept": len(pairs)}


def parse_icd_table(path: str, sheet: str, cfg: dict) -> tuple[dict[str, set[str]], str, dict]:
    """
    解析「EoICD ↔ 需求」追溯表。
    自动判断左侧 ID 列是设备层级需求(ERD) 还是系统层级需求(SRD)。
    返回 ({需求ID: {FullName...}}, "erd"|"srd", 统计)
    """
    icfg = cfg.get("columns", {}).get("eoicd_erd", {})
    patterns = _compile_patterns(cfg)

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet]
    header_row, header = _read_header(ws, hint_keywords=collect_header_hints(cfg))

    c_id = _locate_col(header, icfg.get("erd_id", ["ERD编号"]), 0)
    c_sys = _locate_col(header, icfg.get("sys_id", ["系统需求编号", "SRD编号"]), 0)
    c_name = _locate_col(header, icfg.get("icd_fullname", ["ICD FullName"]), 0)
    c_cat = _locate_col(header, icfg.get("icd_category", ["ICD类别"]), 0)
    keep_cat = icfg.get("icd_category_keep", ["EoICD"])

    if c_name is None:
        wb.close()
        raise ValueError(f"EoICD 追溯表缺少「ICD FullName」列: {path} [{sheet}]")
    if c_id is None and c_sys is None:
        wb.close()
        raise ValueError(f"EoICD 追溯表缺少需求编号列: {path} [{sheet}]")

    # 编号列与 FullName 列不能是同一列。
    # 「说明」类 Sheet 里常有一格同时写着「必填[ERD编号]、[ICD类别]和[ICD FullName]」，
    # 会让两列都定位到同一格，解析出垃圾数据。
    id_col_candidate = c_sys if c_sys is not None else c_id
    if id_col_candidate == c_name:
        wb.close()
        raise ValueError(
            f"EoICD 追溯表的需求编号列与 ICD FullName 列重合（疑似说明页）: {path} [{sheet}]"
        )

    rows = [list(r) for r in ws.iter_rows(min_row=header_row + 1, values_only=True)]
    wb.close()

    # 判断 ID 列语义：优先用「系统需求编号」列；否则按 ID 内容归类
    id_col = c_sys if c_sys is not None else c_id
    id_values = _forward_fill(rows, id_col)
    sample = [v for v in id_values[:2000] if v]
    n_erd = sum(1 for s in sample if classify_level(s, patterns) == "erd")
    n_srd = sum(1 for s in sample if classify_level(s, patterns) == "srd")
    id_kind = "srd" if (c_sys is not None or n_srd > n_erd) else "erd"

    print(f"    EoICD 追溯表 ID 列判定: {id_kind}（抽样 ERD {n_erd} / SRD {n_srd}）")

    mapping: dict[str, set[str]] = defaultdict(set)
    skipped_cat = 0
    for i, row in enumerate(rows):
        rid = id_values[i]
        name = _norm(row[c_name]) if c_name < len(row) else ""
        if not rid or not name:
            continue
        if c_cat is not None and keep_cat:
            cat = _norm(row[c_cat]) if c_cat < len(row) else ""
            if cat and not _contains_keyword(cat, keep_cat):
                skipped_cat += 1
                continue
        mapping[rid].add(name)

    # 有效编号占比：能归类到 erd/srd 的键占比。
    # 若几乎全部无法归类，说明这张表不是真正的追溯表（如说明页），调用方应忽略。
    ids = list(mapping.keys())
    valid = sum(1 for k in ids if classify_level(k, patterns) in ("erd", "srd"))
    valid_ratio = (valid / len(ids)) if ids else 0.0

    stats = {
        "rows": len(rows),
        "id_kind": id_kind,
        "skipped_by_category": skipped_cat,
        "distinct_ids": len(mapping),
        "distinct_fullnames": len({n for s in mapping.values() for n in s}),
        "valid_id_ratio": round(valid_ratio, 3),
    }
    return dict(mapping), id_kind, stats


# ============================ 主流程 ============================

def build_trace(cfg: dict, base_dir: Path, raw_dir_arg: str | None = None) -> dict:
    """识别追溯表并按链路逐级追溯"""
    result: dict[str, Any] = {
        "enabled": False,
        "chain": None,
        "reason": "",
        "sources": {},
        "counts": {},
        "hlr_ids": [],
        "erd_ids": [],
        "sys_ids": [],
        "eoicd_fullnames": [],
        "details": {},
    }

    raw_dirs: list[Path] = []
    if raw_dir_arg:
        raw_dirs = [Path(raw_dir_arg)]
    else:
        for rel in cfg.get("scan_dirs", ["data/raw"]):
            raw_dirs.append((base_dir / rel) if not Path(rel).is_absolute() else Path(rel))

    print("扫描输入目录:")
    for d in raw_dirs:
        print(f"  {d}")

    tables = detect_tables(cfg, raw_dirs)

    has_matrix = bool(tables["req_matrix"])
    has_icd = bool(tables["eoicd_erd"])

    if not has_icd or not (has_matrix or tables["sys_erd"]):
        result["reason"] = (
            "未检测到完整追溯链路（需同时存在需求矩阵表与「EoICD↔需求」追溯表），"
            "按原方案全量保留"
        )
        print(f"\n⚠️  {result['reason']}")
        return result

    # ---- 起点：软件高层需求编号 ----
    if not tables["hlr_source"]:
        result["reason"] = "未找到软件高层需求输入文件，按原方案全量保留"
        print(f"\n⚠️  {result['reason']}")
        return result

    hlr_ids = load_hlr_ids(cfg, tables["hlr_source"])
    print(f"\n软件高层需求(HLR): {len(hlr_ids)} 个编号，来源 {len(tables['hlr_source'])} 个文件")
    if not hlr_ids:
        result["reason"] = "软件高层需求文件中未提取到需求编号，按原方案全量保留"
        return result

    patterns = _compile_patterns(cfg)
    hlr_set = set(hlr_ids)

    # ---- 解析所有需求矩阵表，按编号规则归类层级关系 ----
    # 关系形如：erd→hlr（设备2软件）、srd→erd（系统2设备）
    relations: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    matrix_stats: dict[str, dict] = {}
    for t in tables["req_matrix"]:
        pairs, stats = parse_matrix_table(t["path"], t["sheet"], cfg)
        matrix_stats[Path(t["path"]).name] = stats
        for upper, lower in pairs:
            up_lv = classify_level(upper, patterns)
            low_lv = classify_level(lower, patterns)
            if up_lv and low_lv and up_lv != low_lv:
                relations[(up_lv, low_lv)].add((upper, lower))
        lp = stats.get("level_pairs", {})
        print(f"  矩阵表 {Path(t['path']).name} [{t['sheet']}]: "
              f"有效关系 {stats['kept']} 条，层级组合 {lp}")

    # 独立的「系统↔设备」表（非矩阵格式）
    for t in tables["sys_erd"]:
        pairs, stats = parse_sys_erd_table(t["path"], t["sheet"], cfg)
        for s, e in pairs:
            if classify_level(s, patterns) == "srd" and classify_level(e, patterns) == "erd":
                relations[("srd", "erd")].add((s, e))
        matrix_stats[Path(t["path"]).name] = stats

    def _related(level_a: str, level_b: str, ids_a: set[str]) -> set[str]:
        """给定 level_a 的 ID 集合，返回 relations 中与 level_b 相连的 ID。

        兼容 (a,b) 与 (b,a) 两种存储方向：需求矩阵表左右两栏的上下层顺序
        因项目而异，本函数对方向不敏感，消除「矩阵方向依赖」导致的追溯失效。
        """
        out: set[str] = set()
        # 方向1：关系以 (a,b) 存储（a 为上层）
        for up, low in relations.get((level_a, level_b), set()):
            if low in ids_a:
                out.add(up)
        # 方向2：关系以 (b,a) 存储（a 为下层）
        for low, up in relations.get((level_b, level_a), set()):
            if low in ids_a:
                out.add(up)
        return out

    # ---- 逐级向上追溯（方向无关）----
    erd_ids = _related("erd", "hlr", hlr_set)
    print(f"第 1 跳 HLR → 设备层级需求(ERD): {len(erd_ids)} 个")

    sys_ids: set[str] = set()
    if erd_ids:
        sys_ids = _related("srd", "erd", erd_ids)
        if sys_ids:
            print(f"第 2 跳 设备层级需求 → 系统层级需求(SRD): {len(sys_ids)} 个")

    # ---- 最后一跳：需求 → EoICD FullName ----
    icd_map: dict[str, set[str]] = {}
    icd_stats: dict[str, dict] = {}
    icd_kind = None
    for t in tables["eoicd_erd"]:
        try:
            mapping, kind, stats = parse_icd_table(t["path"], t["sheet"], cfg)
        except ValueError as e:  # noqa: BLE001
            print(f"  ⚠️  跳过 [{t['sheet']}]: {e}")
            continue
        icd_stats[Path(t["path"]).name] = stats

        # 有效编号占比过低 → 不是真正的追溯表，忽略
        if stats.get("valid_id_ratio", 0) < 0.1:
            print(f"  ⚠️  跳过 [{t['sheet']}]: 编号可归类比例仅 "
                  f"{stats.get('valid_id_ratio', 0):.1%}，判定为非追溯表")
            continue

        icd_kind = kind
        for k, v in mapping.items():
            icd_map.setdefault(k, set()).update(v)

    if not icd_map:
        result["reason"] = "EoICD 追溯表解析后无有效需求编号，按原方案全量保留"
        print(f"\n⚠️  {result['reason']}")
        return result

    # 4 层：EoICD 追溯表左列是 SRD；3 层：左列是 ERD
    if icd_kind == "srd":
        chain = "4-layer"
        if sys_ids:
            target_ids, target_name = sys_ids, "系统层级需求"
        else:
            # 未检测到「系统↔设备」表，但 EoICD 追溯表 ID 列确为 SRD，
            # 直接以 EoICD 追溯表内的 SRD 主键作为目标层，避免静默归零。
            target_ids, target_name = set(icd_map.keys()), "系统层级需求(取自EoICD表)"
            print("⚠️ 未检测到「系统↔设备」表，但 EoICD 追溯表 ID 列为 SRD，"
                  "已按 4 层链路处理并以 EoICD 表 SRD 主键作为目标层")
    else:
        chain = "3-layer"
        target_ids, target_name = erd_ids, "设备层级需求"

    result["chain"] = chain
    print(f"\n链路判定: {chain}（EoICD 追溯表 ID 列 = {icd_kind}）")

    fullnames: set[str] = set()
    for rid in target_ids:
        fullnames.update(icd_map.get(rid, set()))

    print(f"最后 1 跳 {target_name} → EoICD: {len(fullnames)} 个 FullName")

    result["enabled"] = True
    result["sources"] = tables
    result["hlr_ids"] = sorted(hlr_ids)
    result["erd_ids"] = sorted(erd_ids)
    result["sys_ids"] = sorted(sys_ids)
    result["eoicd_fullnames"] = sorted(fullnames)
    result["counts"] = {
        "hlr": len(hlr_ids),
        "erd": len(erd_ids),
        "sys": len(sys_ids),
        "eoicd_fullname": len(fullnames),
    }
    result["details"] = {
        "matrix": matrix_stats,
        "icd_trace": icd_stats,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="EoICD 追溯链解析")
    parser.add_argument("-c", "--config", required=True, help="追溯配置 traceability.yaml")
    parser.add_argument("-r", "--raw-dir", default=None, help="输入目录（覆盖配置中的 scan_dirs）")
    parser.add_argument("-o", "--output", required=True, help="输出 trace_result.json 路径")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    base_dir = Path(args.config).parent.parent.parent.resolve()
    result = build_trace(cfg, base_dir, args.raw_dir)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n已保存: {out_path}")
    if result["enabled"]:
        print(f"链路: {result['chain']} | HLR {result['counts']['hlr']} → "
              f"ERD {result['counts']['erd']} → "
              f"{'SRD ' + str(result['counts']['sys']) + ' → ' if result['chain'] == '4-layer' else ''}"
              f"EoICD {result['counts']['eoicd_fullname']}")
    else:
        print(f"追溯未启用: {result['reason']}")


if __name__ == "__main__":
    main()
