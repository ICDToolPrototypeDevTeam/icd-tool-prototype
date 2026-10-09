"""
bus_label_matcher.py — 第一层：Bus + Label + Bit 脚本匹配

正向匹配方向：以 EoICD 信号簇为锚点，遍历所有 HLR 需求条目，
保留 bus、label、bit 都匹配的候选对。
bit 不匹配直接拒绝，减少后续 AI 匹配量。
"""

from collections import defaultdict

from utils import extract_label, match_field, parse_bit_range, name_similarity


def get_cluster_id(eoicd):
    """从 EoICD 数据中提取 cluster_id（兼容不同层级）。"""
    if "cluster_id" in eoicd and eoicd["cluster_id"] is not None:
        return eoicd["cluster_id"]
    cluster_meta = eoicd.get("_cluster", {})
    return cluster_meta.get("cluster_id", "unknown")


def match_bit_field(eoicd_bit, hlr_bit, null_is_wildcard=True):
    """
    bit 范围匹配：EoICD bit 范围是否被 HLR bit 范围包含。
    规则：
      - null = 通配符（任一为 null 视为匹配）
      - EoICD bit 范围 ⊆ HLR bit 范围 → 匹配
      - 其他（冲突/不包含）→ 不匹配
    返回：True（匹配）/ False（不匹配）
    """
    if null_is_wildcard and (eoicd_bit is None or hlr_bit is None):
        return True

    eoicd_set = parse_bit_range(eoicd_bit)
    hlr_set = parse_bit_range(hlr_bit)

    # 解析后为 None（空字符串等）也视为无约束，匹配
    if eoicd_set is None or hlr_set is None:
        return True

    return eoicd_set.issubset(hlr_set)


def run_bus_label_matching(eoicd_clusters, hlr_units, config):
    """
    执行 Bus + Label + Bit 脚本匹配。

    参数：
    - eoicd_clusters: EoICD 信号簇列表
    - hlr_units: HLR 匹配单元列表（来自 load_hlr_dedup 展开后的 interfaces）
    - config: 配置字典

    返回：
    - retained: [{eoicd_cluster, candidate_interfaces: [{unit, match_info}, ...]}, ...]
    - rejected: 拒绝归档列表
    """
    rules = config.get("matching_rules", {})
    null_is_wildcard = rules.get("null_is_wildcard", True)
    strip_prefix = rules.get("hlr_label_strip_prefix", "L")
    # 含 null 字段 unit 的名称门控参数（替代 null_is_wildcard 全匹配爆炸）
    sim_threshold = rules.get("null_identity_sim_threshold", 0.3)
    top_k = rules.get("null_identity_top_k", 30)

    retained = []
    rejected = []

    for eoicd in eoicd_clusters:
        cluster_id = get_cluster_id(eoicd)
        eoicd_identity = eoicd.get("identity", {})
        eoicd_bus = eoicd_identity.get("bus")
        eoicd_label = eoicd_identity.get("label")
        # 兼容旧数据 "bit" 和新数据 "bit_range"
        eoicd_bit = eoicd_identity.get("bit")
        if eoicd_bit is None:
            eoicd_bit = eoicd_identity.get("bit_range")

        candidate_interfaces = []

        for unit in hlr_units:
            unit_bus = unit.get("bus")
            unit_label_raw = unit.get("label")
            unit_label = extract_label(unit_label_raw, strip_prefix)
            unit_bit = unit.get("bit")
            req_id = unit.get("req_id")
            interface_idx = unit.get("interface_idx")

            bus_match = match_field(eoicd_bus, unit_bus, null_is_wildcard)
            if not bus_match:
                rejected.append({
                    "eoicd_cluster_id": cluster_id,
                    "eoicd_signal_name": eoicd.get("signal_name"),
                    "hlr_req_id": req_id,
                    "hlr_interface_idx": interface_idx,
                    "reason": "bus mismatch",
                    "eoicd_bus": eoicd_bus,
                    "hlr_bus": unit_bus,
                    "hlr_label_raw": unit_label_raw,
                })
                continue

            label_match = match_field(eoicd_label, unit_label, null_is_wildcard)
            if not label_match:
                rejected.append({
                    "eoicd_cluster_id": cluster_id,
                    "eoicd_signal_name": eoicd.get("signal_name"),
                    "hlr_req_id": req_id,
                    "hlr_interface_idx": interface_idx,
                    "reason": "label mismatch",
                    "eoicd_label": eoicd_label,
                    "hlr_label_raw": unit_label_raw,
                    "hlr_label_extracted": unit_label,
                })
                continue

            # bit 范围匹配（不通过直接拒绝）
            bit_match = match_bit_field(eoicd_bit, unit_bit, null_is_wildcard)
            if not bit_match:
                rejected.append({
                    "eoicd_cluster_id": cluster_id,
                    "eoicd_signal_name": eoicd.get("signal_name"),
                    "hlr_req_id": req_id,
                    "hlr_interface_idx": interface_idx,
                    "reason": "bit mismatch",
                    "eoicd_bit": eoicd_bit,
                    "hlr_bit": unit_bit,
                })
                continue

            # 含 null 字段且 HLR name 非 None 的 unit 走名称门控：
            # 替代 null_is_wildcard 触发的全匹配爆炸，仅保留名称覆盖比例 ≥ 阈值的簇。
            # name 为 None 的 unit 不走门控（保留原通配行为，下游 skip_ai_pairs 处理）。
            # 含中文(无拉丁子串) unit 经 name_similarity 返回 None → 跳过确定性门控，交 LLM 判定。
            unit_name = unit.get("name")
            has_null = unit_bus is None or unit_label is None or unit_bit is None
            name_sim = None
            if has_null and unit_name is not None:
                eoicd_name = eoicd.get("signal_name") or eoicd.get("signal_short_name") or ""
                name_sim = name_similarity(unit_name, eoicd_name)
                # 仅对"可判定"的英文名做确定性门控；含中文(name_sim=None)交 LLM，不丢弃。
                if name_sim is not None and name_sim < sim_threshold:
                    rejected.append({
                        "eoicd_cluster_id": cluster_id,
                        "eoicd_signal_name": eoicd.get("signal_name"),
                        "hlr_req_id": req_id,
                        "hlr_interface_idx": interface_idx,
                        "reason": "null_identity_gate",
                        "name_sim": name_sim,
                        "hlr_name": unit_name,
                    })
                    continue

            candidate_interfaces.append({
                "unit": unit,
                "match_info": {
                    "bus_matched": bus_match,
                    "label_matched": label_match,
                    "bit_matched": bit_match,
                    "hlr_label_raw": unit_label_raw,
                    "hlr_label_extracted": unit_label,
                    "name_sim": name_sim,
                }
            })

        if candidate_interfaces:
            retained.append({
                "eoicd_cluster": eoicd,
                "candidate_interfaces": candidate_interfaces,
            })

    # 按 unit 聚合，单 unit 候选簇 > top_k 则截断到相似度最高的 top_k。
    # 仅"全 null + 名字泛化"的病理信号会触发；正常数据到不了，故零开销。
    agg = defaultdict(list)
    for ri, item in enumerate(retained):
        for ci, cand in enumerate(item["candidate_interfaces"]):
            if cand["match_info"].get("name_sim") is not None:
                key = (cand["unit"].get("req_id"), cand["unit"].get("interface_idx"))
                agg[key].append((ri, ci, cand["match_info"]["name_sim"]))
    drop = set()
    for key, lst in agg.items():
        if len(lst) > top_k:
            keep = {(r, c) for r, c, _ in sorted(lst, key=lambda x: -x[2])[:top_k]}
            for r, c, _ in lst:
                if (r, c) not in keep:
                    drop.add((r, c))
    if drop:
        by_item = defaultdict(list)
        for r, c in drop:
            by_item[r].append(c)
        for r, cands in by_item.items():
            old = retained[r]["candidate_interfaces"]
            retained[r]["candidate_interfaces"] = [
                o for i, o in enumerate(old) if i not in set(cands)
            ]
        retained = [it for it in retained if it["candidate_interfaces"]]

    return retained, rejected
