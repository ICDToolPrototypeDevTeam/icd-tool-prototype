"""
direction_matcher.py — 方向匹配层：Direction 脚本匹配

在 Bus+Label+Bit 匹配之后、Name 匹配之前，加入方向筛选。
方向一致保留，方向不一致拒绝；null 通配。

输入：retained_bus_label_bit（Bus+Label+Bit 保留结果）
输出：(retained_direction, rejected_direction)
"""


def match_direction(eoicd_direction, hlr_direction, null_is_wildcard=True):
    """
    方向匹配：EoICD 方向与 HLR 方向是否一致。

    规则：
      - null = 通配符（任一为 null 视为匹配）
      - 非 null 且相等 → 匹配
      - 非 null 且不等 → 不匹配
    """
    if null_is_wildcard and (eoicd_direction is None or hlr_direction is None):
        return True
    # 统一转为大写字符串比较
    return str(eoicd_direction).strip().upper() == str(hlr_direction).strip().upper()


def run_direction_matching(retained_bus_label_bit, config=None):
    """
    执行方向匹配筛选。

    参数：
    - retained_bus_label_bit: Bus+Label+Bit 保留结果列表
    - config: 配置字典（预留，当前不使用）

    返回：
    - retained_direction: 方向匹配保留结果（结构同输入）
    - rejected_direction: 方向不匹配拒绝列表
    """
    retained_direction = []
    rejected_direction = []

    for item in retained_bus_label_bit:
        eoicd = item["eoicd_cluster"]
        eoicd_identity = eoicd.get("identity", {})
        eoicd_direction = eoicd_identity.get("direction")

        cluster_id = eoicd.get("cluster_id")
        if cluster_id is None:
            cluster_meta = eoicd.get("_cluster", {})
            cluster_id = cluster_meta.get("cluster_id", "unknown")

        candidate_interfaces = []

        for candidate in item["candidate_interfaces"]:
            unit = candidate["unit"]
            req_id = unit.get("req_id")
            interface_idx = unit.get("interface_idx")
            hlr_direction = unit.get("direction")

            direction_match = match_direction(eoicd_direction, hlr_direction)

            if not direction_match:
                rejected_direction.append({
                    "eoicd_cluster_id": cluster_id,
                    "eoicd_signal_name": eoicd.get("signal_name"),
                    "hlr_req_id": req_id,
                    "hlr_interface_idx": interface_idx,
                    "reason": "direction mismatch",
                    "eoicd_direction": eoicd_direction,
                    "hlr_direction": hlr_direction,
                })
            else:
                # 保留原有 match_info，追加 direction 匹配结果
                new_match_info = dict(candidate.get("match_info", {}))
                new_match_info["direction_matched"] = True
                candidate_interfaces.append({
                    "unit": unit,
                    "match_info": new_match_info,
                })

        if candidate_interfaces:
            retained_direction.append({
                "eoicd_cluster": eoicd,
                "candidate_interfaces": candidate_interfaces,
            })

    return retained_direction, rejected_direction
