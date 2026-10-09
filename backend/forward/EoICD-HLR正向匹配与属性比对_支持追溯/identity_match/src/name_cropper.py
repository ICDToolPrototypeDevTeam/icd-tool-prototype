"""
name_cropper.py — 第二层：Name 信息裁剪

对第一层（Bus+Label+Bit）保留的候选对进行信息裁剪：
- 去掉无关属性，只保留身份信息
- HLR name 为 null 的条目直接保留，不进入 AI 处理
- 输出裁剪后的 JSON 文件，供 AI Name 匹配使用
"""


from utils import get_cluster_id


def crop_name_input(retained_bus_label_bit):
    """
    对 Bus+Label+Bit 保留结果进行 Name 匹配前的信息裁剪。

    返回：
    - cropped_pairs: 需要进入 AI 处理的 pair 列表（含定位信息）
    - skip_ai_pairs: (cluster_id, req_id, interface_idx) 集合，name 为 null 直接保留
    """
    cropped_pairs = []
    skip_ai_pairs = set()

    pair_counter = 0

    for item in retained_bus_label_bit:
        eoicd = item["eoicd_cluster"]
        cluster_id = get_cluster_id(eoicd)
        eoicd_identity = eoicd.get("identity", {})

        for candidate in item["candidate_interfaces"]:
            unit = candidate["unit"]
            req_id = unit.get("req_id")
            interface_idx = unit.get("interface_idx")
            hlr_name = unit.get("name")

            if hlr_name is None:
                # HLR name 为 null → 直接保留，不进入 AI
                skip_ai_pairs.add((cluster_id, req_id, interface_idx))
            else:
                # HLR name 有值 → 进入 AI 处理
                pair_counter += 1
                cropped_pairs.append({
                    "pair_id": f"pair_{pair_counter:05d}",
                    "eoicd_cluster_id": cluster_id,
                    "hlr_req_id": req_id,
                    "hlr_interface_idx": interface_idx,
                    "eoicd": {
                        "cluster_id": cluster_id,
                        "signal_short_name": eoicd.get("signal_short_name"),
                        "identity": eoicd_identity,
                    },
                    "hlr": {
                        "req_id": req_id,
                        "interface_idx": interface_idx,
                        "name": hlr_name,
                        "bus": unit.get("bus"),
                        "label": unit.get("label"),
                        "bit": unit.get("bit"),
                    }
                })

    return cropped_pairs, skip_ai_pairs
