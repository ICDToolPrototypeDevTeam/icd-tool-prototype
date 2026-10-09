"""
name_result_feeder.py — AI Name 匹配结果反哺工具

功能：
- 读取 AI 分批匹配结果
- 按 (cluster_id, req_id) 定位回原始保留数据
- 匹配 → 保留；不匹配 → 拒绝归档
- name 为 null 的 pair（skip_ai_pairs）直接保留
"""


from utils import get_cluster_id


def feed_ai_results(retained_bus_label_bit, ai_results, skip_ai_pairs):
    """
    将 AI Name 匹配结果反哺回 Bus+Label+Bit 保留数据。

    参数：
    - retained_bus_label_bit: [{eoicd_cluster, candidate_interfaces: [...]}, ...]
    - ai_results: [{pair_id, eoicd_cluster_id, hlr_req_id, hlr_interface_idx, result}, ...]
    - skip_ai_pairs: (cluster_id, req_id, interface_idx) 集合，name 为 null 直接保留

    返回：
    - final_retained: 过滤后的保留列表
    - rejected_name: 拒绝归档列表
    """
    # 建立 AI 结果映射: (cluster_id, req_id, interface_idx) -> "匹配"/"不匹配"
    match_map = {}
    for r in ai_results:
        key = (r["eoicd_cluster_id"], r["hlr_req_id"], r.get("hlr_interface_idx", 0))
        match_map[key] = r["result"]

    final_retained = []
    rejected_name = []

    for item in retained_bus_label_bit:
        eoicd = item["eoicd_cluster"]
        cluster_id = get_cluster_id(eoicd)

        kept = []
        for candidate in item["candidate_interfaces"]:
            unit = candidate["unit"]
            req_id = unit.get("req_id")
            interface_idx = unit.get("interface_idx")
            key = (cluster_id, req_id, interface_idx)

            if key in skip_ai_pairs:
                # HLR name 为 null，直接保留
                kept.append(candidate)
            elif key in match_map:
                if match_map[key] == "匹配":
                    kept.append(candidate)
                else:
                    # AI 判定不匹配 → 拒绝归档
                    rejected_name.append({
                        "eoicd_cluster_id": cluster_id,
                        "eoicd_signal_name": eoicd.get("signal_name"),
                        "hlr_req_id": req_id,
                        "hlr_interface_idx": interface_idx,
                        "hlr_name": unit.get("name"),
                        "eoicd_signal_short_name": eoicd.get("signal_short_name"),
                        "reason": "name mismatch (AI)",
                    })
            else:
                # 这个 pair 不在 AI 结果也不在 skip 列表中（理论上不应发生）
                # 保守处理：保留
                kept.append(candidate)

        if kept:
            final_retained.append({
                "eoicd_cluster": eoicd,
                "candidate_interfaces": kept,
            })

    return final_retained, rejected_name
