# attr_conflict_extractor.py
# 职责: Step 1 — 从聚类结果提取冲突项，补充完整HLR信息+需求原文

import json
import os


def load_match_records(*file_paths) -> dict:
    """
    加载所有匹配结果文件，建立索引。
    索引键: (cluster_id, attribute, req_id)
    返回: dict[key] = record
    """
    records = {}
    for path in file_paths:
        if not os.path.exists(path):
            continue
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        for record in data:
            key = (
                record.get("eoicd_cluster_id"),
                record.get("attribute"),
                record.get("req_id")
            )
            records[key] = record
    return records


def load_hlr_text(hlr_clustered_path: str) -> dict:
    """
    从HLR聚类结果中加载每个req_id对应的req_text。
    返回: dict[req_id] = req_text
    """
    req_text_map = {}
    
    with open(hlr_clustered_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    for req in data.get("requirements", []):
        req_id = req.get("req_id")
        req_text = req.get("req_text", "")
        if req_id and req_text:
            if req_id not in req_text_map or len(req_text) > len(req_text_map[req_id]):
                req_text_map[req_id] = req_text
    
    return req_text_map


def extract_conflict_items(
    clustered_path: str,
    matched_path: str,
    mismatch_path: str,
    null_value_path: str,
    hlr_clustered_path: str,
    output_path: str
) -> int:
    """
    从聚类结果中提取所有冲突项，补充完整HLR信息+需求原文。
    
    输入: 
      - attr_clustered_{dir}.json
      - attr_matched_{dir}.json
      - attr_mismatch_{dir}.json
      - attr_null_value_{dir}.json
      - hlr_clustered.json
    输出: conflict_items_{dir}.json
    返回: 冲突项数量
    """
    # 1. 加载聚类结果
    with open(clustered_path, 'r', encoding='utf-8') as f:
        clustered = json.load(f)
    
    # 2. 加载匹配记录（用于补充 hlr_name, hlr_attr_name, rule）
    all_records = load_match_records(matched_path, mismatch_path, null_value_path)
    
    # 3. 加载HLR需求原文
    req_text_map = load_hlr_text(hlr_clustered_path)
    
    conflict_items = []
    
    # 4. 遍历所有信号的所有属性，提取冲突项
    for signal in clustered["signals"]:
        for attr_name, attr_data in signal["results_by_attribute"].items():
            # 只提取标记为冲突的属性
            if not attr_data.get("is_conflict", False):
                continue
            
            item = {
                "conflict_id": f"C{len(conflict_items)+1:04d}",
                "cluster_id": signal["eoicd_cluster_id"],
                "signal_name": signal["eoicd_signal_name"],
                "signal_short_name": signal.get("eoicd_signal_short_name", ""),
                "attribute": attr_name,
                "eoicd_value": None,
                "results": []
            }
            
            # 5. 补充每条 result 的完整信息
            for r in attr_data["results"]:
                req_id = r["req_id"]
                
                # 基础信息（来自聚类结果）
                # 注意：不携带 upgraded_by_ai / ai_confirmed，这些不影响冲突裁决
                enriched = {
                    "req_id": req_id,
                    "result": r["result"],
                    "eoicd_value": r.get("eoicd_value"),
                    "hlr_value": r.get("hlr_value"),
                }
                
                # 补充1：从匹配文件中提取 hlr_name, hlr_attr_name, rule
                key = (signal["eoicd_cluster_id"], attr_name, req_id)
                if key in all_records:
                    full_record = all_records[key]
                    enriched["hlr_name"] = full_record.get("hlr_identity", {}).get("name", "")
                    enriched["hlr_attr_name"] = full_record.get("hlr_attr_name", "")
                    enriched["rule"] = full_record.get("rule", "")
                else:
                    enriched["hlr_name"] = ""
                    enriched["hlr_attr_name"] = ""
                    enriched["rule"] = ""
                
                # 补充2：从HLR聚类结果中提取 req_text（需求原文）
                enriched["req_text"] = req_text_map.get(req_id, "")
                
                item["results"].append(enriched)
                
                # 回填 eoicd_value（取第一个非空值）
                if item["eoicd_value"] is None and r.get("eoicd_value"):
                    item["eoicd_value"] = r["eoicd_value"]
            
            conflict_items.append(item)
    
    # 6. 保存输出
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({
            "meta": {
                "stage": "conflict_extraction",
                "total_conflicts": len(conflict_items),
                "source": clustered_path,
                "hlr_source": hlr_clustered_path
            },
            "conflict_items": conflict_items
        }, f, ensure_ascii=False, indent=2)
    
    return len(conflict_items)


# CLI 入口
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="冲突项提取工具")
    parser.add_argument("--clustered", required=True, help="聚类结果文件路径")
    parser.add_argument("--matched", required=True, help="matched文件路径")
    parser.add_argument("--mismatch", required=True, help="mismatch文件路径")
    parser.add_argument("--null-value", required=True, help="null_value文件路径")
    parser.add_argument("--hlr-clustered", required=True, help="HLR聚类结果文件路径")
    parser.add_argument("--output", required=True, help="输出文件路径")
    
    args = parser.parse_args()
    
    count = extract_conflict_items(
        clustered_path=args.clustered,
        matched_path=args.matched,
        mismatch_path=args.mismatch,
        null_value_path=args.null_value,
        hlr_clustered_path=args.hlr_clustered,
        output_path=args.output
    )
    
    print(f"✓ 提取冲突项: {count} 个")
