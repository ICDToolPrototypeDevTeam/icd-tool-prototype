"""
bus_label_matcher.py — 第一层：Bus + Label + Bit 脚本匹配

正向匹配方向：以 EoICD 信号簇为锚点，遍历所有 HLR 需求条目，
保留 bus、label、bit 都匹配的候选对。
bit 不匹配直接拒绝，减少后续 AI 匹配量。
"""

from collections import defaultdict

from utils import extract_label, match_field, parse_bit_range, name_similarity
from name_translator import has_chinese


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
    # 名称门控参数（替代 null_is_wildcard 全匹配爆炸）
    # gate_all=True 时对"所有含 name 的候选对"执行名称门控；False 时仅对含 null 字段的 unit 执行（原行为）
    sim_threshold = rules.get("null_identity_sim_threshold", 0.3)
    top_k = rules.get("null_identity_top_k", 30)
    gate_all = rules.get("null_identity_gate_all", True)
    # 每信号封顶（治"一个信号匹配一堆需求"的爆炸）：K = 已填属性数 + 余量，且不低于地板。
    # 作为安全天花板而非主动降秩过滤器；false 时回到无 per-signal 封顶的原行为（召回率安全阀）。
    per_signal_enabled = rules.get("per_signal_cap_enabled", True)
    per_signal_margin = rules.get("per_signal_cap_attr_margin", 2)
    per_signal_floor = rules.get("per_signal_cap_floor", 3)

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

            # 名称门控：对所有"含 name 的候选对"执行（L1 扩展，替代仅含 null 字段才门控）。
            # gate_all=False 时退化为仅含 null 字段的 unit 门控（原行为）。
            # 相似度用"翻译名优先"(name_en or 原名) 计算，供排序/展示；
            # 确定性脚本门控：仅对"可算相似度且英文名(不含中文)"的候选执行——
            #  - 纯英文名（不含中文）：门控
            #  - 含中文名（纯中文 / 混合名）一律不门控，只参与 cap 排序、交 LLM，
            #    避免缩写展开 VLV→Valve、CMD→Command 或中文语义字面不命中被误杀
            unit_name = unit.get("name")
            translated = unit.get("name_abbr") or unit.get("name_en")
            has_null = unit_bus is None or unit_label is None or unit_bit is None
            name_sim = None
            if unit_name is not None and (gate_all or has_null):
                eoicd_name = eoicd.get("signal_name") or eoicd.get("signal_short_name") or ""
                name_sim = name_similarity(translated or unit_name, eoicd_name)
                is_pure_chinese = has_chinese(unit_name)
                if name_sim is not None and not is_pure_chinese:
                    if name_sim is not None and name_sim < sim_threshold:
                        rejected.append({
                            "eoicd_cluster_id": cluster_id,
                            "eoicd_signal_name": eoicd.get("signal_name"),
                            "eoicd_signal_short_name": eoicd.get("signal_short_name"),
                            "hlr_req_id": req_id,
                            "hlr_interface_idx": interface_idx,
                            "reason": "name_identity_gate",
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

    # 每信号封顶（治"一个信号匹配一堆需求"的爆炸）：
    # 一个 EoICD 信号最多保留 K 个最相似候选，K = 该信号「已填属性数」+ 余量，且不低于地板。
    # 上界逻辑：一条 HLR 需求通常描述信号的一个属性，故一个信号的合法需求数 ≲ 其属性数。
    # K 随信号丰富度自适应（属性多→易被多条需求引用→K 大），作为安全天花板而非主动降秩过滤器。
    # 按 name_sim 降序截 top-K；name_sim 为 None（中文名/无名，交 LLM 判定）的候选优先保留，
    # 避免把本要送 LLM 的模糊候选误删。被截候选不计入 rejected（避免重新放大产物体积）。
    #
    # per_signal_cap_group0_exempt（默认 true）：组 0（name_sim 为 None：翻译失败中文名/无名，交 LLM）
    #   全部保送、不占 K；组 1（有相似度）独立按 K 截断（取相似度最高的 top-K），与组0 数量无关。
    #   设为 false 则回到「组0/组1 同池竞争 K」原行为（召回率安全后阀，防组0 过大引爆下游 LLM）。
    #   注：本开关仅控制"封顶阶段组0 是否占 K"，不另加下游护栏；组0 过大时由 false 回退兜底。
    if per_signal_enabled:
        def _eoicd_attr_count(eoicd):
            n = 0
            for _layer, layer_attrs in (eoicd.get("attributes") or {}).items():
                if isinstance(layer_attrs, dict):
                    n += sum(1 for v in layer_attrs.values() if v is not None)
            return n

        def _cap_key(c):
            ns = c["match_info"].get("name_sim")
            # name_sim 为 None（翻译失败的中文名/无名，交 LLM）优先保留（组 0）；
            # 翻译成功的中文名已带真实相似度，按相似度与其他候选一起竞争（组 1），
            # 不再强制占满 K（修复"中文名占满 K"的 L2 缺陷）。
            return (0 if ns is None else 1, -(ns if ns is not None else 0.0))

        group0_exempt = rules.get("per_signal_cap_group0_exempt", True)

        capped_signals = 0
        capped_pairs = 0
        for item in retained:
            cands = item["candidate_interfaces"]
            if len(cands) <= 1:
                continue
            k = max(_eoicd_attr_count(item["eoicd_cluster"]) + per_signal_margin, per_signal_floor)
            if len(cands) <= k:
                continue
            capped_signals += 1
            if group0_exempt:
                # 组 0（name_sim 为 None：翻译失败中文名/无名，交 LLM）全部保送、不占 K；
                # 组 1（有相似度）独立按 K 截断（取相似度最高的 top-K），不受组 0 数量影响。
                # 下游 LLM 成本随组0 数量放大，由 per_signal_cap_group0_exempt=false 回退原行为兜底。
                g0 = [c for c in cands if c["match_info"].get("name_sim") is None]
                g1 = [c for c in cands if c["match_info"].get("name_sim") is not None]
                g1_kept = sorted(g1, key=lambda c: -(c["match_info"]["name_sim"]))[:k]
                capped_pairs += len(cands) - (len(g0) + len(g1_kept))
                item["candidate_interfaces"] = g0 + g1_kept
            else:
                capped_pairs += len(cands) - k
                item["candidate_interfaces"] = sorted(cands, key=_cap_key)[:k]
        if capped_signals:
            print(f"  [每信号封顶] {capped_signals} 个信号被截顶，共减少 {capped_pairs} 对候选")

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
