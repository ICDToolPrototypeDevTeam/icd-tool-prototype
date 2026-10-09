"""
name_ai_matcher.py — AI Name 匹配工具（v4：并行 + 精简 Prompt + 抗停滞流式读取）

特性：
- 配置文件驱动（batch_size, fallback_batch_size, model, retry 等）
- 分批处理，Fallback 降级机制
- 并行 AI 调用（ThreadPoolExecutor + 多 Key 轮询）
- 精简 Prompt（保留背景，去除冗余 Bus/Label 信息）
- 真实 API 调用（OpenAI 兼容格式）

注：同名对去重由 name_pair_deduper 在分批前完成（结构性去重），
本模块不再维护进程内缓存。
"""

import json
import os
import time

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from utils import read_stream_with_stall_guard


# ------------------------------------------------------------------
# API 配置
# ------------------------------------------------------------------

def get_api_config(config, api_key=None):
    """从配置中提取 API 参数。支持传入指定 api_key（用于并行时轮询分配）。

    环境变量统一约定（与 run_ai.bat / .env 一致，单一真源）：
      API_KEY   - AI 服务密钥
      BASE_URL  - OpenAI 兼容 base_url
      AI_MODEL  - 模型名
    不再兼容 DEEPSEEK_*/HLR_AI_*/OPENAI_* 等别名，也不再按模型名推断 base_url。
    """
    ai_cfg = config["ai_matching"]

    # model：环境变量 AI_MODEL 优先，其次配置文件 model 字段；都缺失则报错
    model = os.environ.get("AI_MODEL", "")
    if not model:
        model = ai_cfg.get("model", "")
    if not model:
        raise ValueError(
            "未配置 AI 模型：请在 .env 设置 AI_MODEL，或在 match_config.json 的 "
            "ai_matching.model 中指定"
        )

    # base_url：仅认 BASE_URL 环境变量，其次配置 base_url 字段；都缺失则报错
    base_url = os.environ.get("BASE_URL", "")
    if not base_url:
        base_url = ai_cfg.get("base_url", "")
    if not base_url:
        raise ValueError("未配置 AI base_url：请在 .env 设置 BASE_URL")

    # api_key：环境变量 API_KEY 优先，其次配置 api_key 字段；都缺失则报错
    if api_key:
        pass  # 使用并行轮询传入的 key
    else:
        api_key = os.environ.get("API_KEY", "")
        if not api_key:
            api_key = ai_cfg.get("api_key", "")
    if not api_key:
        raise ValueError("未配置 AI api_key：请在 .env 设置 API_KEY")

    return {
        "model": model,
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "max_tokens": ai_cfg["max_tokens"],
        "temperature": ai_cfg["temperature"],
        "timeout": ai_cfg["timeout_seconds"],
        "retry": ai_cfg["retry_times"],
        "min_interval": ai_cfg["min_interval_seconds"],
        # 抗停滞保护参数（防服务端 keepalive 滴流挂死）
        "stall_window_seconds": ai_cfg.get("stall_window_seconds", 30),
        "stall_min_bytes": ai_cfg.get("stall_min_bytes", 512),
        "overall_timeout_seconds": ai_cfg.get("overall_timeout_seconds", 900),
    }


# ------------------------------------------------------------------
# Prompt 构建（精简版：保留背景，去除冗余 Bus/Label）
# ------------------------------------------------------------------

def build_prompt(batch_pairs):
    """构建 AI Prompt（v5：撤下 full name 展示，设备标识改从 EoICD 名称下划线全分段查找）。

    在 v4 基础上按主人意见调整：
    - 撤下每组 EoICD full name（点分层级串）输入，压缩 token 消耗
    - 【设备标识判别规则】查找对象从"完整名称全字符段"改为"名称全部下划线分段"
      （下划线字段段本身含全部设备标识，如 RX_Drain_VLV_RPDU_ESW_CMD 的 VLV 在第 3 段）
    - 四步判别流程、判断独立性声明、判定示例保持不变
    """
    lines = [
        "你是航空电子领域的接口需求比对专家。",
        "",
        "【任务】",
        "判断每组 HLR 需求名称与 EoICD 信号名称是否描述同一个物理信号。",
        "",
        "【名称格式说明】",
        "- HLR 名称：通常为中文或中英混合的自然语言描述",
        "- EoICD 名称：通常为英文缩写，由下划线分隔的多个字段组成，",
        "  设备标识可能出现在任意字段段，不限于首段",
        "",
        "【匹配原则（满足任一即匹配）】",
        "1. 语义等价：两者指代同一物理信号，即使表述形式不同",
        "2. 包含关系：EoICD 名称是 HLR 描述的具体成员，且关键字段不矛盾",
        "   - 仅当 HLR 描述是明确的上位概念时成立",
        "   - 不得因 HLR 描述笼统、宽泛而放宽标准",
        "3. 编号对应：缩写中的数字编号与描述中的数字编号一致，",
        "   且设备标识、系统归属等其他字段不矛盾",
        "",
        "【设备标识判别规则】",
        "判别步骤：",
        "1. 先提取 HLR 名称中明确提到的设备/部件标识（如 EDP、EVDU、VLV、RAT、RDP）",
        "2. 在 EoICD 名称的全部下划线分段中查找该设备标识",
        "   （如 RX_Drain_VLV_RPDU_ESW_CMD 分为 RX/Drain/VLV/RPDU/ESW/CMD 六段）：",
        "   - 找到 → 按该标识判断设备是否矛盾",
        "   - 未找到 → 再按“首段通常是设备位号”的经验方式提取；",
        "     仍无法确定时，按语义等价正常判断，",
        "     不要仅因设备标识位置不确定而拒绝",
        "3. HLR 名称明确出现的设备标识，在 EoICD 名称对应位置上是",
        "   另一个设备的 → 直接判不匹配，无论尾部编号是否相同",
        "   （例：HLR 提到 EDP，EoICD 对应位置是 EVDU → 不匹配）",
        "4. HLR 名称中没有出现任何设备标识时，本规则不生效，",
        "   按语义等价正常判断",
        "",
        "【非匹配情况】",
        "- 两者指代完全不同的物理量或系统",
        "- 缩写中的关键字段与描述中的关键词明显矛盾",
        "- 仅共有数字而无其他语义关联",
        "  （例：某名称仅与需求名共有“05”这个数字，不构成匹配依据）",
        "",
        "【判断独立性】",
        "每组判断相互独立。同批其他组的结论不构成任何一组的依据，",
        "不要因为同批出现过明确的匹配就放宽后续组的标准。",
        "",
        "【判定示例（仅示范判定标准，不是要判断的题目）】",
        "例：HLR “EDP_TCB_STATUS_005状态” ↔ EoICD “EDP_TCB_STATUS_005”",
        "    → 匹配（设备标识一致、编号一致、语义完整对应）",
        "例：HLR “<RAT状态” ↔ EoICD “RAT_DEPLOYED”",
        "    → 匹配（HLR 提到的 RAT 在 EoICD 名称中找到，DEPLOYED 属 RAT 状态）",
        "例：HLR “VLV负载指令” ↔ EoICD “RX_Drain_VLV_RPDU_ESW_CMD”",
        "    → 匹配（HLR 提到的 VLV 在 EoICD 名称第 3 段找到，",
        "            Drain 阀指令属于负载阀指令）",
        "例：HLR “<RAT状态” ↔ EoICD “RDP_TCB_STATUS_013”",
        "    → 不匹配（RDP 断路器状态与 RAT 无关，需求名笼统不是放宽理由）",
        "例：HLR “EDP_TCB_STATUS_005状态” ↔ EoICD “EVDU_TCB_STATUS_005”",
        "    → 不匹配（HLR 提到的 EDP 在 EoICD 对应位置是 EVDU，编号相同不成立）",
        "例：HLR “EDP_TCB_STATUS_005状态” ↔ EoICD “L_BPCU_CBITE_05_FAULT”",
        "    → 不匹配（仅共有数字 05，无语义关联）",
        "",
        "【锚点信息】",
        "以下每组已通过 Bus/Label 脚本匹配确认为同一总线、同一标签：",
        "（此信息仅用于辅助判断，不作为唯一依据）",
        "",
        "对每组，只需回答两个字：匹配 / 不匹配",
        "不要解释理由，不要输出任何其他内容。",
        "不要重复题目、示例或任何其他文字，只输出结果行。",
        "",
        "请按编号顺序输出结果，每行一个，格式如下：",
        "1. 匹配",
        "2. 不匹配",
        "...",
        "",
    ]

    for i, pair in enumerate(batch_pairs, 1):
        hlr = pair["hlr"]
        eoicd = pair["eoicd"]
        bus = eoicd.get("identity", {}).get("bus", "N/A")
        label = eoicd.get("identity", {}).get("label", "N/A")
        lines.append(f"--- 组 {i} ---")
        lines.append(f"HLR name：{hlr['name']}")
        lines.append(f"EoICD identity-name：{eoicd['identity']['name']}")
        lines.append(f"已确认锚点：Bus={bus}, Label={label}")
        lines.append("")

    return "\n".join(lines)


# ------------------------------------------------------------------
# 响应解析
# ------------------------------------------------------------------

def parse_response(response_text, expected_count):
    """解析 AI 响应文本，提取匹配结果。"""
    results = []
    lines = response_text.strip().split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 匹配格式："1. 匹配" / "1. 不匹配"
        # 先检查"不匹配"再检查"匹配"，避免"不匹配"被误判为"匹配"
        if "不匹配" in line:
            results.append("不匹配")
        elif "匹配" in line:
            results.append("匹配")
        # 其他格式忽略

    return results


# ------------------------------------------------------------------
# API 调用
# ------------------------------------------------------------------

def call_ai_api(prompt, api_cfg):
    """调用 AI API，带重试机制。"""
    headers = {
        "Authorization": f"Bearer {api_cfg['api_key']}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": api_cfg["model"],
        "messages": [
            {"role": "system", "content": "你是一个航空电子领域的接口需求比对专家。请严格按要求输出。"},
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


# ------------------------------------------------------------------
# 单批处理
# ------------------------------------------------------------------

def process_batch(batch_pairs, api_cfg):
    """处理一批 pair，返回结果列表。"""
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
            "hlr_req_id": pair["hlr_req_id"],
            "hlr_interface_idx": pair["hlr"]["interface_idx"],
            "result": result,
        }
        for pair, result in zip(batch_pairs, results)
    ]


def process_batch_with_key(batch_pairs, api_key, config):
    """带指定 API Key 的单批处理（供并行调用使用）。"""
    api_cfg = get_api_config(config, api_key=api_key)
    return process_batch(batch_pairs, api_cfg)


# ------------------------------------------------------------------
# Fallback 降级处理
# ------------------------------------------------------------------

def _fallback_process(batch_pairs, fallback_size, config, api_key=None):
    """对失败批次进行 Fallback 降级处理（拆分为小批次串行重试）。"""
    api_cfg = get_api_config(config, api_key=api_key)
    all_results = []

    for j in range(0, len(batch_pairs), fallback_size):
        sub_batch = batch_pairs[j:j + fallback_size]
        sub_num = j // fallback_size + 1
        print(f"    Sub-batch {sub_num}: {len(sub_batch)} pairs...", end=" ")

        try:
            results = process_batch(sub_batch, api_cfg)
            all_results.extend(results)
            print("✓")
        except Exception as e2:
            print(f"✗ 也失败: {e2}")
            print(f"    保守处理：全部标记为\"不匹配\"")
            for pair in sub_batch:
                all_results.append({
                    "pair_id": pair["pair_id"],
                    "eoicd_cluster_id": pair["eoicd_cluster_id"],
                    "hlr_req_id": pair["hlr_req_id"],
                    "hlr_interface_idx": pair["hlr"]["interface_idx"],
                    "result": "不匹配",
                })

    return all_results


# ------------------------------------------------------------------
# 串行匹配（回退逻辑）
# ------------------------------------------------------------------

def _sequential_match(pairs, config):
    """串行执行 AI 匹配（回退逻辑）。"""
    batch_size = config["ai_matching"]["batch_size"]
    fallback_size = config["ai_matching"]["fallback_batch_size"]
    min_interval = config["ai_matching"]["min_interval_seconds"]

    api_cfg = get_api_config(config)

    all_results = []
    total = len(pairs)
    total_batches = (total + batch_size - 1) // batch_size

    print(f"\n  串行模式：共 {total} 对，batch_size={batch_size}，预计 {total_batches} 批")

    for i in range(0, total, batch_size):
        batch = pairs[i:i + batch_size]
        batch_num = i // batch_size + 1

        print(f"  Batch {batch_num}/{total_batches}: {len(batch)} pairs...", end=" ")
        t0 = time.time()

        try:
            results = process_batch(batch, api_cfg)
            all_results.extend(results)
            print(f"✓ {time.time()-t0:.1f}s")
        except Exception as e:
            print(f"✗ 失败: {e}")
            print(f"    Fallback 降级: batch_size={fallback_size}")
            results = _fallback_process(batch, fallback_size, config)
            all_results.extend(results)

        if min_interval > 0 and batch_num < total_batches:
            time.sleep(min_interval)

    return all_results


# ------------------------------------------------------------------
# 主入口：分批 AI 匹配（含缓存预筛 + 并行/串行自适应）
# ------------------------------------------------------------------

def batch_ai_match(cropped_pairs, config):
    """
    分批 AI 匹配主函数。

    流程：
    1. 判断并行/串行：根据 parallel 配置选择执行方式
    2. 执行匹配：并行（ThreadPoolExecutor）或串行
    3. 返回结果列表

    注：输入应为 name_pair_deduper 去重后的代表对列表。

    返回：
    - 结果列表 [{pair_id, eoicd_cluster_id, hlr_req_id, hlr_interface_idx, result}, ...]
    """
    ai_cfg = config["ai_matching"]
    parallel_cfg = ai_cfg.get("parallel", {})

    if not cropped_pairs:
        return []

    # ---------- 第1步：判断并行/串行 ----------
    # 收集可用 keys
    api_keys = parallel_cfg.get("api_keys", [])
    if not api_keys:
        api_keys = [ai_cfg.get("api_key", "")]

    max_workers = min(
        parallel_cfg.get("max_workers", 1),
        len(api_keys)
    )

    # 禁用并行或只有1个 worker → 串行
    if max_workers <= 1 or not parallel_cfg.get("enabled", False):
        return _sequential_match(cropped_pairs, config)

    # ---------- 第2步：并行执行 ----------
    batch_size = ai_cfg["batch_size"]
    fallback_size = ai_cfg["fallback_batch_size"]
    min_interval = ai_cfg["min_interval_seconds"]

    # 分批
    batches = [cropped_pairs[i:i + batch_size]
               for i in range(0, len(cropped_pairs), batch_size)]

    total_batches = len(batches)
    print(f"\n  并行模式：{len(cropped_pairs)} 对需 AI，batch_size={batch_size}，"
          f"并发数={max_workers}，共 {total_batches} 批")

    ai_results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        futures = {}
        for i, batch in enumerate(batches):
            key = api_keys[i % len(api_keys)]  # 轮询分配 Key
            future = executor.submit(process_batch_with_key, batch, key, config)
            futures[future] = i

        # 收集结果（as_completed 不保证顺序，但靠 pair_id 对应回原始数据）
        for future in as_completed(futures):
            batch_idx = futures[future]
            t0 = time.time()

            try:
                results = future.result()
                ai_results.extend(results)

                print(f"  Batch {batch_idx + 1}/{total_batches}: {len(results)} pairs ✓ {time.time()-t0:.1f}s")
            except Exception as e:
                print(f"  Batch {batch_idx + 1}/{total_batches}: ✗ 失败: {e}")
                print(f"    Fallback 降级处理...")

                batch = batches[batch_idx]
                key = api_keys[batch_idx % len(api_keys)]
                results = _fallback_process(batch, fallback_size, config, api_key=key)
                ai_results.extend(results)

            if min_interval > 0:
                time.sleep(min_interval)

    return ai_results
