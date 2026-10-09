"""
attr_mismatch_ai_matcher.py — 属性匹配：AI 复核 mismatch 数据

功能：
  复用身份匹配阶段的 AI 调用模式：
    - 分批处理（batch_size + fallback 降级）
    - OpenAI 兼容 API 格式
    - 配置文件驱动（model, api_key, retry 等）
    - API Key 从配置文件优先读取，环境变量回退

AI 判断标准：
  - 大小写差异 → 脚本已处理，不应出现
  - 单位差异 → 如单位已统一后数值仍不同，视为不等价
  - 语义等价 → 结合领域常识判断
  - 数值/内容不同 → 视为不等价

输出：二值结果 — "一致" / "不一致"
"""

from __future__ import annotations

import json
import os
import time

import requests
from utils import read_stream_with_stall_guard


def get_api_config(ai_cfg: dict) -> dict:
    """从配置中提取 API 参数（仅认 API_KEY / BASE_URL / AI_MODEL）"""
    # 模型：环境变量 AI_MODEL 优先于配置；均未设置则报错
    model = os.environ.get("AI_MODEL", "")
    if not model:
        model = ai_cfg.get("model", "")
    if not model:
        raise ValueError(
            "AI_MODEL 未配置：请在 .env 设置 AI_MODEL，"
            "或在配置中填写 ai_review.model"
        )

    # base_url：环境变量 BASE_URL 优先于配置；均未设置则报错（不再按模型名推断）
    base_url = os.environ.get("BASE_URL", "")
    if not base_url:
        base_url = ai_cfg.get("base_url", "")
    if not base_url:
        raise ValueError(
            "BASE_URL 未配置：请在 .env 设置 BASE_URL，"
            "或在配置中填写 ai_review.base_url"
        )

    # api_key：配置文件 api_key > 环境变量 API_KEY；均未设置则报错
    api_key = ai_cfg.get("api_key", "")
    if not api_key:
        api_key = os.environ.get(ai_cfg.get("api_key_env", "API_KEY"), "")
    if not api_key:
        raise ValueError(
            "API_KEY 未配置：请在 .env 设置 API_KEY，"
            "或在配置中填写 ai_key"
        )

    return {
        "model": model,
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "max_tokens": ai_cfg.get("max_tokens", 32000),
        "temperature": ai_cfg.get("temperature", 0.1),
        "timeout": ai_cfg.get("timeout_seconds", 300),
        "retry": ai_cfg.get("retry_times", 3),
        "min_interval": ai_cfg.get("min_interval_seconds", 0.1),
        # 抗停滞保护参数（防服务端 keepalive 滴流挂死）
        "stall_window_seconds": ai_cfg.get("stall_window_seconds", 30),
        "stall_min_bytes": ai_cfg.get("stall_min_bytes", 512),
        "overall_timeout_seconds": ai_cfg.get("overall_timeout_seconds", 900),
    }


def build_prompt(batch_pairs: list[dict]) -> str:
    """构建 AI Prompt"""
    # 检测是否有 CodedSet 属性
    has_codedset = any(p.get("attribute") == "DP.CodedSet" or p.get("attribute") == "RP.CodedSet" for p in batch_pairs)

    lines = [
        "你是航空电子领域的接口需求属性比对专家。",
        "你的任务是判断每组 EoICD 属性值与 HLR 属性值是否等价。",
        "",
        "判断标准：",
        "1. 如果两个值在数值上相等（忽略大小写、空格），回答：一致",
        "2. 如果两个值在语义上等价（如单位不同但数值已换算一致），回答：一致",
        "3. 如果两个值在数值或内容上确实不同，回答：不一致",
        "4. 不要过度推断，仅基于给出的值判断",
    ]

    if has_codedset:
        lines.extend([
            "",
            "【CodedSet 属性匹配说明】",
            "EoICD 格式:",
            '  - "[X通道] 编码值N=含义" 或 "[X通道] 编码值N(二进制:bitA=V,bitB=V)=含义"（已展开）',
            '  - "[X通道] 编码值N=含义 (bit信息待补充)"（无法自动展开）',
            '  - "[X通道] 编码值N=含义 (bit信息缺失)"（bit为null）',
            "",
            "HLR 格式不固定，可能为：",
            '  - 二进制规则表: "bit8=0,bit9=0,结果为...; ..."',
            '  - 十进制描述: "编码值3=..."',
            '  - 自然语言: "编码值为3时表示..."',
            '  - 通道映射: "bit8=0,bit9=0,通道位置为1A; ..."',
            "  - ……（其他未列出的格式）",
            "",
            "匹配原则:",
            "  1. 核心判断: 两个描述是否指向同一编码值的同一含义",
            "  2. 通道信息作为辅助，双方存在时应当一致",
            "     （一方有通道一方无时，不影响核心语义匹配）",
            "  3. 缺少 bit 信息时，优先从 HLR 描述中推导编码规则",
            "  4. 只要编码值与含义对应关系一致，即判定为匹配",
        ])

    lines.extend([
        "",
        "对每组，只需回答两个字：一致 / 不一致",
        "不要解释理由，不要输出任何其他内容。",
        "",
        "请按编号顺序输出结果，每行一个，格式如下：",
        "1. 一致",
        "2. 不一致",
        "...",
        "",
    ])

    for i, pair in enumerate(batch_pairs, 1):
        lines.append(f"--- 组 {i} ---")
        lines.append(f"属性: {pair['hlr_attr_name']}")
        lines.append(f"EoICD 值: {pair['eoicd_value']}")
        lines.append(f"HLR 值: {pair['hlr_value']}")
        lines.append("")

    return "\n".join(lines)


def parse_response(response_text: str, expected_count: int) -> list[str]:
    """解析 AI 响应文本"""
    results = []
    lines = response_text.strip().split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if "不一致" in line:
            results.append("不一致")
        elif "一致" in line:
            results.append("一致")

    return results


def call_ai_api(prompt: str, api_cfg: dict) -> str:
    """调用 AI API，带重试机制"""
    headers = {
        "Authorization": f"Bearer {api_cfg['api_key']}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": api_cfg["model"],
        "messages": [
            {"role": "system", "content": "你是一个航空电子领域的接口需求属性比对专家。请严格按要求输出。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": api_cfg["max_tokens"],
        "temperature": api_cfg["temperature"],
    }

    url = f"{api_cfg['base_url']}/chat/completions"

    for attempt in range(api_cfg["retry"]):
        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=api_cfg["timeout"],
                stream=True,
            )
            resp.raise_for_status()
            body = read_stream_with_stall_guard(
                resp,
                window_seconds=api_cfg.get("stall_window_seconds", 30.0),
                min_bytes=api_cfg.get("stall_min_bytes", 512),
                overall_seconds=api_cfg.get("overall_timeout_seconds", 900.0),
            )
            return json.loads(body)["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt < api_cfg["retry"] - 1:
                wait = 2 ** attempt
                print(f"    API 调用失败（{e}），{wait}s 后重试（{attempt+1}/{api_cfg['retry']}）...")
                time.sleep(wait)
            else:
                raise


def process_batch(batch_pairs: list[dict], api_cfg: dict) -> list[dict]:
    """处理一批 pair"""
    prompt = build_prompt(batch_pairs)
    response = call_ai_api(prompt, api_cfg)
    results = parse_response(response, len(batch_pairs))

    if len(results) != len(batch_pairs):
        raise ValueError(
            f"解析结果数量不匹配: 期望 {len(batch_pairs)}, 实际 {len(results)}\n"
            f"AI 原始输出前 200 字: {response[:200]}"
        )

    return [
        {
            "pair_id": pair["pair_id"],
            "eoicd_cluster_id": pair["eoicd_cluster_id"],
            "req_id": pair["req_id"],
            "attribute": pair["attribute"],
            "ai_result": result,
        }
        for pair, result in zip(batch_pairs, results)
    ]


def batch_ai_review_mismatch(cropped_pairs: list[dict], ai_cfg: dict) -> list[dict]:
    """
    分批 AI 复核 mismatch 数据。

    返回：
      [
        {
          "pair_id": "attr_mismatch_00001",
          "eoicd_cluster_id": "...",
          "req_id": "...",
          "attribute": "DP.Units",
          "ai_result": "一致" / "不一致"
        },
        ...
      ]
    """
    api_cfg = get_api_config(ai_cfg)

    if not api_cfg["api_key"]:
        raise RuntimeError(
            f"API Key 未设置。请在配置文件中设置 ai_review.api_key，"
            f"或设置环境变量: export {ai_cfg.get('api_key_env', 'API_KEY')}=\"sk-xxx\""
        )

    batch_size = ai_cfg.get("batch_size", 300)
    fallback_size = ai_cfg.get("fallback_batch_size", 100)
    min_interval = ai_cfg.get("min_interval_seconds", 0.1)

    all_results = []
    total = len(cropped_pairs)
    total_batches = (total + batch_size - 1) // batch_size

    print(f"\n      共 {total} 对 mismatch，batch_size={batch_size}，预计 {total_batches} 批")

    for i in range(0, total, batch_size):
        batch = cropped_pairs[i:i + batch_size]
        batch_num = i // batch_size + 1

        print(f"      Batch {batch_num}/{total_batches}: {len(batch)} pairs...", end=" ")
        t0 = time.time()

        try:
            results = process_batch(batch, api_cfg)
            all_results.extend(results)
            print(f"✓ {time.time()-t0:.1f}s")
        except Exception as e:
            print(f"✗ 失败: {e}")
            print(f"        Fallback 降级: batch_size={fallback_size}")

            for j in range(0, len(batch), fallback_size):
                sub_batch = batch[j:j + fallback_size]
                sub_num = j // fallback_size + 1
                print(f"        Sub-batch {sub_num}: {len(sub_batch)} pairs...", end=" ")

                try:
                    results = process_batch(sub_batch, api_cfg)
                    all_results.extend(results)
                    print(f"✓")
                except Exception as e2:
                    print(f"✗ 也失败: {e2}")
                    print(f"        保守处理：全部标记为\"不一致\"")
                    for pair in sub_batch:
                        all_results.append({
                            "pair_id": pair["pair_id"],
                            "eoicd_cluster_id": pair["eoicd_cluster_id"],
                            "req_id": pair["req_id"],
                            "attribute": pair["attribute"],
                            "ai_result": "不一致",
                        })

        if min_interval > 0 and batch_num < total_batches:
            time.sleep(min_interval)

    return all_results
