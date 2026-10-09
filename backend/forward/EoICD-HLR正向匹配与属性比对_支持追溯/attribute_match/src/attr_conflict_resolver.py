# attr_conflict_resolver.py
# 职责: Step 3 — 解析 AI 裁决结果并应用到数据，生成 resolved 文件
# (合并原 attr_conflict_parser.py + attr_conflict_applier.py)

import json
import os
import re
from typing import List, Dict, Any


# ============================================================
# 子函数 1: 解析 AI 原始输出
# ============================================================

def parse_ai_resolutions(ai_raw_dir: str, direction: str) -> List[Dict]:
    """
    从所有 batch 文件中解析 AI 裁决结果。
    
    输入: conflict_ai_raw_{dir}_batch{N}.json
    返回: 有效的裁决结果列表
    """
    all_resolutions = []
    
    for filename in sorted(os.listdir(ai_raw_dir)):
        if not filename.startswith(f"conflict_ai_raw_{direction}_batch"):
            continue
        
        with open(os.path.join(ai_raw_dir, filename), 'r', encoding='utf-8') as f:
            batch_data = json.load(f)
        
        response = batch_data["response"]
        
        # 尝试直接解析 JSON
        try:
            resolutions = json.loads(response)
            if isinstance(resolutions, list):
                all_resolutions.extend(resolutions)
                continue
        except json.JSONDecodeError:
            pass
        
        # 降级: 用正则提取 JSON 数组
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            try:
                resolutions = json.loads(json_match.group())
                if isinstance(resolutions, list):
                    all_resolutions.extend(resolutions)
            except:
                pass
    
    # 验证与去重
    valid_resolutions = []
    seen_ids = set()
    
    for r in all_resolutions:
        cid = r.get("conflict_id")
        if not cid or cid in seen_ids:
            continue
        
        if r.get("resolution") == "keep":
            if not r.get("kept_req_id") or not r.get("kept_result") or not r.get("reason"):
                continue
        elif r.get("resolution") == "manual_review":
            if not r.get("reason"):
                continue
        else:
            continue
        
        seen_ids.add(cid)
        valid_resolutions.append(r)
    
    return valid_resolutions


# ============================================================
# 子函数 2: 应用裁决结果
# ============================================================

def build_record_index(records: List[Dict]) -> Dict[tuple, int]:
    """建立 (cluster_id, attribute, req_id) → index 映射"""
    index = {}
    for i, record in enumerate(records):
        key = (
            record.get("eoicd_cluster_id"),
            record.get("attribute"),
            record.get("req_id")
        )
        index[key] = i
    return index


def move_record(source_list: List[Dict], source_index: Dict, target_list: List[Dict], 
                target_index: Dict, key: tuple, new_result: str, 
                conflict_id: str, reason: str) -> None:
    """
    将记录从 source 移动到 target，更新 result 和冲突标记。
    """
    idx = source_index.pop(key)
    record = source_list.pop(idx)
    record["result"] = new_result
    record["conflict_resolved"] = True
    record["conflict_resolution"] = f"kept_{conflict_id}"
    record["conflict_reason"] = reason
    target_list.append(record)
    target_index[key] = len(target_list) - 1


def apply_conflict_resolutions(
    resolutions: List[Dict],
    conflict_items_data: Dict,
    attr_matched_path: str,
    attr_mismatch_path: str,
    output_dir: str,
    direction: str
) -> Dict[str, Any]:
    """
    应用裁决结果，生成 resolved 文件。
    
    重要: 不修改原数据，生成新文件。
    """
    # 1. 加载冲突项映射
    conflict_map = {
        item["conflict_id"]: item 
        for item in conflict_items_data["conflict_items"]
    }
    
    # 2. 加载原数据（只读，深拷贝到新列表）
    with open(attr_matched_path, 'r', encoding='utf-8') as f:
        matched_data = json.load(f)
    with open(attr_mismatch_path, 'r', encoding='utf-8') as f:
        mismatch_data = json.load(f)
    
    resolved_matched = [dict(r) for r in matched_data]
    resolved_mismatch = [dict(r) for r in mismatch_data]
    
    # 3. 建立索引
    matched_idx = build_record_index(resolved_matched)
    mismatch_idx = build_record_index(resolved_mismatch)
    
    manual_review_items = []
    
    # 4. 逐条处理
    for resolution in resolutions:
        cid = resolution["conflict_id"]
        conflict_item = conflict_map.get(cid)
        if not conflict_item:
            continue
        
        cluster_id = conflict_item["cluster_id"]
        attribute = conflict_item["attribute"]
        
        # CASE B/C: 人工审核
        if resolution["resolution"] == "manual_review":
            manual_review_items.append({
                "conflict_id": cid,
                "resolution": "manual_review",
                "reason": resolution.get("reason", ""),
                "confidence": resolution.get("confidence", "low"),
                "cluster_id": cluster_id,
                "signal_name": conflict_item["signal_name"],
                "signal_short_name": conflict_item.get("signal_short_name", ""),
                "attribute": attribute,
                "eoicd_value": conflict_item["eoicd_value"],
                "results": conflict_item["results"]
            })
            continue
        
        # CASE A: 保留某个结果
        kept_req_id = resolution["kept_req_id"]
        kept_result = resolution["kept_result"]
        rejected_req_ids = resolution.get("rejected_req_ids", [])
        reason = resolution.get("reason", "")
        
        kept_key = (cluster_id, attribute, kept_req_id)
        
        # 4.1 确保 kept 记录在正确的 resolved 文件中
        if kept_result == "matched":
            if kept_key in mismatch_idx:
                move_record(resolved_mismatch, mismatch_idx, 
                          resolved_matched, matched_idx,
                          kept_key, "matched", cid, reason)
        elif kept_result == "mismatch":
            if kept_key in matched_idx:
                move_record(resolved_matched, matched_idx,
                          resolved_mismatch, mismatch_idx,
                          kept_key, "mismatch", cid, reason)
        
        # 4.2 处理 rejected_req_ids
        for rej_req_id in rejected_req_ids:
            rej_key = (cluster_id, attribute, rej_req_id)
            
            if kept_result == "matched":
                # rejected 应该为 mismatch
                if rej_key in matched_idx:
                    move_record(resolved_matched, matched_idx,
                              resolved_mismatch, mismatch_idx,
                              rej_key, "mismatch", cid, reason)
            elif kept_result == "mismatch":
                # rejected 应该为 matched
                if rej_key in mismatch_idx:
                    move_record(resolved_mismatch, mismatch_idx,
                              resolved_matched, matched_idx,
                              rej_key, "matched", cid, reason)
    
    # 5. 保存输出文件
    os.makedirs(output_dir, exist_ok=True)
    
    # 5.1 manual_review
    manual_path = os.path.join(output_dir, f"conflict_manual_review_{direction}.json")
    with open(manual_path, 'w', encoding='utf-8') as f:
        json.dump({
            "meta": {
                "stage": "conflict_manual_review",
                "total_items": len(manual_review_items)
            },
            "items": manual_review_items
        }, f, ensure_ascii=False, indent=2)
    
    # 5.2 resolved_matched
    resolved_matched_path = os.path.join(output_dir, f"attr_matched_resolved_{direction}.json")
    with open(resolved_matched_path, 'w', encoding='utf-8') as f:
        json.dump(resolved_matched, f, ensure_ascii=False, indent=2)
    
    # 5.3 resolved_mismatch
    resolved_mismatch_path = os.path.join(output_dir, f"attr_mismatch_resolved_{direction}.json")
    with open(resolved_mismatch_path, 'w', encoding='utf-8') as f:
        json.dump(resolved_mismatch, f, ensure_ascii=False, indent=2)
    
    return {
        "manual_review_count": len(manual_review_items),
        "manual_review_path": manual_path,
        "resolved_matched_path": resolved_matched_path,
        "resolved_mismatch_path": resolved_mismatch_path
    }


# ============================================================
# 主入口: 解析 + 应用（一步完成）
# ============================================================

def resolve_conflicts(
    ai_raw_dir: str,
    conflict_items_path: str,
    attr_matched_path: str,
    attr_mismatch_path: str,
    output_dir: str,
    direction: str,
    save_judgement: bool = True
) -> Dict[str, Any]:
    """
    主入口: 解析 AI 裁决结果并应用到数据。
    
    参数:
        ai_raw_dir: conflict_ai_raw_{dir}_batch{N}.json 所在目录
        conflict_items_path: conflict_items_{dir}.json 路径
        attr_matched_path: 原 matched 文件路径（只读）
        attr_mismatch_path: 原 mismatch 文件路径（只读）
        output_dir: 输出目录
        direction: "pub" 或 "sub"
        save_judgement: 是否保存中间 judgement 文件（默认True，用于审计）
    
    返回: 统计信息
    """
    # Step 3.1: 解析
    resolutions = parse_ai_resolutions(ai_raw_dir, direction)
    
    keep_count = sum(1 for r in resolutions if r["resolution"] == "keep")
    manual_count = sum(1 for r in resolutions if r["resolution"] == "manual_review")
    
    # Step 3.2: 加载冲突项
    with open(conflict_items_path, 'r', encoding='utf-8') as f:
        conflict_items_data = json.load(f)
    
    # 可选: 保存中间 judgement 文件（审计用）
    if save_judgement:
        # 回带 signal_name 到每个 resolution
        conflict_map = {
            item["conflict_id"]: item
            for item in conflict_items_data["conflict_items"]
        }
        enriched_resolutions = []
        for r in resolutions:
            cid = r.get("conflict_id")
            item = conflict_map.get(cid, {})
            enriched = dict(r)
            enriched["signal_name"] = item.get("signal_name", "")
            enriched["signal_short_name"] = item.get("signal_short_name", "")
            enriched_resolutions.append(enriched)
        
        judgement_path = os.path.join(output_dir, f"conflict_judgement_{direction}.json")
        with open(judgement_path, 'w', encoding='utf-8') as f:
            json.dump({
                "meta": {
                    "stage": "conflict_judgement",
                    "total_resolutions": len(enriched_resolutions),
                    "keep_count": keep_count,
                    "manual_review_count": manual_count
                },
                "resolutions": enriched_resolutions
            }, f, ensure_ascii=False, indent=2)
    
    # Step 3.3: 应用
    result = apply_conflict_resolutions(
        resolutions, conflict_items_data,
        attr_matched_path, attr_mismatch_path,
        output_dir, direction
    )
    
    result.update({
        "total_resolutions": len(resolutions),
        "keep_count": keep_count,
        "manual_review_count": manual_count
    })
    
    return result


# ============================================================
# CLI 入口（单独运行调试用）
# ============================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="冲突项解析+应用工具")
    parser.add_argument("--ai-raw-dir", required=True, help="AI原始输出目录")
    parser.add_argument("--conflict-items", required=True, help="冲突项文件路径")
    parser.add_argument("--matched", required=True, help="原matched文件路径")
    parser.add_argument("--mismatch", required=True, help="原mismatch文件路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--direction", required=True, choices=["pub", "sub"])
    parser.add_argument("--no-judgement", action="store_true", help="不保存中间judgement文件")
    
    args = parser.parse_args()
    
    result = resolve_conflicts(
        ai_raw_dir=args.ai_raw_dir,
        conflict_items_path=args.conflict_items,
        attr_matched_path=args.matched,
        attr_mismatch_path=args.mismatch,
        output_dir=args.output_dir,
        direction=args.direction,
        save_judgement=not args.no_judgement
    )
    
    print(f"✓ 冲突处理完成: keep={result['keep_count']}, manual_review={result['manual_review_count']}")
    print(f"  resolved_matched: {result['resolved_matched_path']}")
    print(f"  resolved_mismatch: {result['resolved_mismatch_path']}")
    if result['manual_review_count'] > 0:
        print(f"  manual_review: {result['manual_review_path']}")
