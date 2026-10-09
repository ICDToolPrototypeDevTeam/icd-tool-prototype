"""
bit_matcher.py — 身份匹配报告生成

原职责：第三层 Bit 范围脚本匹配（已合并到 bus_label_bit_matcher）
现职责：根据 Name 匹配后的保留结果，生成最终 match_report

功能：
  - 生成 match_report_*.json（含 matched_hlrs、identity 信息）
  - 生成 match_summary 速查表

注意：本文件作为薄封装，内部使用 utils.py 的 generate_match_report_from_dedup()
      生成报告。旧版兼容路径（无聚类数据回填）也保留在本文件中。
"""

from utils import get_cluster_id, generate_match_report_from_dedup, _fmt_identity_key


def generate_match_report(final_retained, hlr_clustered=None, config=None):
    """
    根据 Name 匹配后的保留结果，生成最终身份匹配报告。

    参数：
    - final_retained: Name 匹配后的保留列表
      [{eoicd_cluster, candidate_interfaces: [{unit, match_info}, ...]}, ...]
    - hlr_clustered: HLR 聚类结果（用于回填 attribute_class, attr_value, evidence）
    - config: 配置字典（保留参数，便于后续扩展）

    返回：
    - match_report: 最终报告
    """
    if hlr_clustered is not None:
        # 新版：使用去重结果回填聚类属性
        return generate_match_report_from_dedup(final_retained, hlr_clustered, config)
    else:
        # 旧版兼容：不填聚类属性（用于无 hlr_clustered 的场景）
        return _generate_match_report_legacy(final_retained, config)


def _generate_match_report_legacy(final_retained, config):
    """旧版报告生成（无聚类数据回填，用于兼容）。"""
    matches = []
    match_summary = []

    for item in final_retained:
        eoicd = item["eoicd_cluster"]
        cluster_id = get_cluster_id(eoicd)
        eoicd_identity = eoicd.get("identity", {})
        signal_name = eoicd.get("signal_name", "")

        matched_hlrs = []

        for candidate in item.get("candidate_interfaces", []):
            unit = candidate["unit"]
            hlr_identity = {
                "bus": unit.get("bus"),
                "label": unit.get("label"),
                "name": unit.get("name"),
                "bit": unit.get("bit"),
                "direction": unit.get("direction"),
            }

            req_id = unit.get("req_id")
            matched_hlrs.append({
                "req_id": req_id,
                "hlr_identity": hlr_identity,
                "attribute_classes": [],
                "attr_value": None,
                "evidence": [],
                "match_pipeline": {
                    "bus_label_bit": {"status": "matched"},
                    "direction": {"status": "matched"},
                    "name": {"status": "matched"},
                },
            })

            # 身份主键字符串
            eoicd_id_key = _fmt_identity_key(eoicd_identity)
            hlr_id_key = _fmt_identity_key(hlr_identity)

            match_summary.append(
                f"{cluster_id}---{signal_name}---{eoicd_id_key}---{req_id}---{hlr_id_key}"
            )

        matches.append({
            "eoicd_cluster_id": cluster_id,
            "eoicd_signal_name": signal_name,
            "eoicd_signal_short_name": eoicd.get("signal_short_name"),
            "eoicd_identity": eoicd_identity,
            "eoicd_attributes": eoicd.get("attributes", {}),
            "matched_hlrs": matched_hlrs,
        })

    report = {
        "meta": {
            "stage": "identity_match_complete",
            "description": "EoICD → HLR 正向匹配 - 身份匹配阶段（含 Bus+Label+Bit + Direction + Name）",
            "total_clusters": len(matches),
            "match_summary": match_summary,
            "stages": ["bus_label_bit", "direction", "name"],
        },
        "matches": matches,
    }

    return report


