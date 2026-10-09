# attr_conflict_ai_judge.py
# 职责: Step 2 — AI 分批裁决冲突项

import json
import os
import sys

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from attr_mismatch_ai_matcher import call_ai_api, get_api_config

BATCH_SIZE = 20


def build_ai_prompt(batch_items: list) -> str:
    """
    构建 AI 裁决 Prompt（v9）。
    输入: batch_items（来自 conflict_items.json 的 conflict_items 子集）
    输出: Prompt 字符串
    """
    prompt = """你是航空电子接口属性匹配验证专家。以下每组是一个"冲突项"——同一 EoICD 信号的同一属性，在不同 HLR 需求中出现了不同的匹配结果（有的 matched，有的 mismatch）。需要你基于完整的上下文信息（含需求原文），判断这些结果中哪个更可信。

══════════════════════════════════════════════════════════════════
【输入数据说明】
══════════════════════════════════════════════════════════════════

每个冲突项包含：
- conflict_id: 冲突唯一标识
- cluster_id: EoICD 信号簇 ID
- signal_name: EoICD 信号完整名称（含系统/设备/层级信息，用于语义判断）
- signal_short_name: 信号简称
- attribute: 属性名（如 DP.Units、DP.CodedSet）
- eoicd_value: EoICD 的属性值

每个 result（HLR检查结果）包含：
- req_id: HLR 需求 ID
- hlr_name: HLR 需求名称（描述该需求涉及的功能/信号）
- hlr_attr_name: HLR 属性名
- result: 当前匹配结果（matched / mismatch / null_value / not_mentioned）
- eoicd_value: EoICD 属性值
- hlr_value: HLR 的属性值
- rule: 应用的匹配规则（如 default、codedset）
- req_text: HLR 需求的完整原文（这是最重要的上下文信息）

══════════════════════════════════════════════════════════════════
【判定标准（四维度综合评估）】
══════════════════════════════════════════════════════════════════

【属性值对应关系】
评估 hlr_value 与 eoicd_value 的对应程度：
- 严格一致：完全一致（忽略大小写、空格）→ 高可信 matched
- 语义等价：表述不同但物理含义相同 → 高可信 matched
  例："1000ms" vs "1000"（上下文中单位已明确）
- 编码等价：不同编码描述同一状态 → 中可信 matched
  例：二进制 "bit8=0,bit9=1" vs 文字 "通道2A"
- 数值差异：数值明显不同 → 需结合属性名判断
  例：Units属性出现 "RPM" vs "Hz" → 高可信 mismatch
       CodedSet属性出现 "0,1" vs "0,1,2" → 需结合语义判断
- 类型差异：数据类型不同 → 需结合属性名判断
  例：Units属性出现 "DISCRETE" vs "BOOL" → 高可信 mismatch（技术定义不同）
       但同一属性的不同表述如 "FLOAT" vs "FLOAT32" → 可能为语义等价
- 语义不符：描述的不是同一个概念 → 高可信 mismatch

【信号名称语义关联】
评估 signal_name 与 hlr_name 的语义关联度：
- 关联度高：signal_name 中的功能/设备名与 hlr_name 描述一致
  例：signal_name="...CPCS_Cabin_Pressure...", hlr_name="座舱压力控制"
- 关联度低：signal_name 与 hlr_name 描述的功能不相关
  例：signal_name="...CPCS_Cabin_Pressure...", hlr_name="风扇速度指令"

【需求原文上下文】
评估 req_text 中对该属性的描述是否清晰、具体：
- 明确提及：req_text 中明确提到了该属性的具体值/约束 → 可信度高
  例：req_text="...以1000ms为周期发送..."，attribute="TransmissionIntervalMinimum"，hlr_value="1000ms"
- 间接涉及：req_text 中涉及该属性但描述模糊 → 可信度中
- 未涉及：req_text 中未提及该属性相关内容 → 可信度低

【属性名一致性】
- attribute 与 hlr_attr_name 一致 → 正常比对
- attribute 与 hlr_attr_name 不同 → 需关注是否是同一属性的不同命名

══════════════════════════════════════════════════════════════════
【裁决规则】
══════════════════════════════════════════════════════════════════

对每组冲突项：

1. 先排除 null_value / not_mentioned 的结果（不参与可信度比较）

2. 对剩余结果（matched / mismatch）逐一评估以下方面的可信度：
   - 属性值对应程度：高/中/低（需结合属性名判断）
   - 信号语义关联度：高/中/低
   - 需求原文支持度：高/中/低
   - 属性名一致性：一致/不一致

3. 综合以上各方面，判定每个结果的总体可信度：
   - 高可信：多数方面支持度高，或关键方面明确支持
   - 中可信：部分方面支持度高，部分为中等
   - 低可信：多数方面支持度低，或关键方面矛盾

4. 比较各结果的总体可信度：

   CASE A: 某个结果的总体可信度明显高于其他
      → 保留该结果
      例：HLR_A 属性值严格匹配 + 语义关联度高 + 需求原文明确支持 = 高可信 matched
          HLR_B 属性值类型差异 + 语义关联度低 + 需求原文未涉及 = 低可信 mismatch
      → 保留 HLR_A 的 matched

   CASE B: 多个结果的总体可信度都很高（都有充分依据）
      → 标记 "manual_review"
      例：HLR_A 语义等价 + 需求原文明确支持 = 高可信 matched
          HLR_B 数值差异 + 需求原文明确支持 = 高可信 mismatch
      → 两个结果都有强依据，无法判定 → 人工审核

   CASE C: 所有结果的总体可信度都不高（都缺乏充分依据）
      → 标记 "manual_review"
      例：编码规则不明确、语义模糊、需求原文未涉及该属性

5. 给出裁决理由：
   - 用自然语言说明判定依据，不要引用"维度1/2/3/4"等编号
   - 如涉及需求原文，引用 req_text 中的关键句
   - 说明为什么保留的结果更可信，以及为什么拒绝的结果不可信

══════════════════════════════════════════════════════════════════
【输出格式】
══════════════════════════════════════════════════════════════════

必须输出严格的 JSON 数组，每个元素对应一个冲突项：

[
  {
    "conflict_id": "C0001",
    "resolution": "keep",
    "kept_req_id": "FSF21000101_HLR_4210",
    "kept_result": "matched",
    "rejected_req_ids": ["FSF21000101_HLR_544"],
    "reason": "HLR_4210的Units值'DISCRETE'与EoICD值完全一致，且需求原文明确要求状态消息的格式为DISCRETE，可信度高；HLR_544的'BOOL'为布尔量类型，与DISCRETE技术定义不同，且需求原文为SDI编码规则，未涉及Units属性，可信度低",
    "confidence": "high"
  },
  {
    "conflict_id": "C0002",
    "resolution": "manual_review",
    "reason": "HLR_A的'1000ms'与EoICD'1000'语义等价，需求原文明确提及'以1000ms为周期'，高可信matched；但HLR_B的'100ms'与'1000ms'数值差异明显，需求原文也明确提及'100ms'，高可信mismatch。两个结果都有强依据，无法判定",
    "confidence": "low"
  }
]

【要求】
- 必须输出合法的 JSON 格式
- 不要输出任何 JSON 之外的内容
- 数组长度必须与输入的冲突项数量一致
- 按 conflict_id 顺序输出
- reason 必须用自然语言说明判定依据，不要引用"维度1/2/3/4"等编号
- 如涉及需求原文，引用 req_text 中的关键句

══════════════════════════════════════════════════════════════════
【待裁决冲突项】
══════════════════════════════════════════════════════════════════

"""
    prompt += json.dumps(batch_items, ensure_ascii=False, indent=2)
    prompt += "\n\n【请输出 JSON 格式的裁决结果】"
    return prompt


def batch_ai_judge_conflicts(
    input_path: str, 
    output_dir: str, 
    direction: str,
    ai_cfg: dict
) -> list:
    """
    分批 AI 裁决冲突项。
    
    输入: conflict_items_{dir}.json
    输出: conflict_ai_raw_{dir}_batch{N}.json（每批一个文件）
    返回: 所有批次合并后的裁决结果
    """
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    items = data["conflict_items"]
    all_resolutions = []
    
    os.makedirs(output_dir, exist_ok=True)
    
    for batch_idx in range(0, len(items), BATCH_SIZE):
        batch = items[batch_idx:batch_idx + BATCH_SIZE]
        prompt = build_ai_prompt(batch)
        
        # 调用 AI
        api_cfg = get_api_config(ai_cfg)
        response = call_ai_api(prompt, api_cfg)
        
        # 保存原始输出
        batch_file = os.path.join(
            output_dir, 
            f"conflict_ai_raw_{direction}_batch{batch_idx//BATCH_SIZE + 1}.json"
        )
        with open(batch_file, 'w', encoding='utf-8') as f:
            json.dump({
                "meta": {
                    "batch_index": batch_idx//BATCH_SIZE + 1, 
                    "batch_size": len(batch)
                },
                "prompt": prompt,
                "response": response
            }, f, ensure_ascii=False, indent=2)
        
        # 解析 JSON 响应
        try:
            resolutions = json.loads(response)
            if isinstance(resolutions, list):
                all_resolutions.extend(resolutions)
        except json.JSONDecodeError:
            import re
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                try:
                    resolutions = json.loads(json_match.group())
                    if isinstance(resolutions, list):
                        all_resolutions.extend(resolutions)
                except:
                    pass
    
    return all_resolutions


# CLI 入口
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="冲突项AI裁决工具")
    parser.add_argument("--input", required=True, help="冲突项文件路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--direction", required=True, choices=["pub", "sub"])
    parser.add_argument("--config", required=True, help="配置文件路径")
    
    args = parser.parse_args()
    
    with open(args.config, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    resolutions = batch_ai_judge_conflicts(
        input_path=args.input,
        output_dir=args.output_dir,
        direction=args.direction,
        config=config
    )
    
    print(f"✓ AI裁决完成: {len(resolutions)} 个冲突项")
