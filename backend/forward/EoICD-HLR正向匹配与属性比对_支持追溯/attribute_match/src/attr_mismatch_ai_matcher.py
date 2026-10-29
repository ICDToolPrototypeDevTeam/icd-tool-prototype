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

try:
    from utils import read_stream_with_stall_guard, record_token_usage
except ImportError:  # pragma: no cover
    # 独立运行（自测）时 utils 不在搜索路径 —— 生产路径由 run_attribute_match.py 引导，
    # 这里仅兜底补上 shared 目录后重试。
    import sys as _sys
    import os as _os
    _shared = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__)))), "shared")
    if _os.path.isdir(_shared) and _shared not in _sys.path:
        _sys.path.insert(0, _shared)
    from utils import read_stream_with_stall_guard, record_token_usage


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
        # 单批 prompt 字符上限（2026-10-28）：补上下文后体积约 3.3×，
        # 固定条数分批可能超小窗口模型的上下文，故加字符上限约束。
        "max_batch_chars": ai_cfg.get("max_batch_chars", 60000),
    }


def build_prompt(batch_pairs: list[dict]) -> str:
    """构建 AI Prompt

    ★2026-10-28 补充上下文：原先每组只给「属性名 / EoICD 值 / HLR 值」三项，
    AI 无法判断"这是表述差异还是真冲突"——因为它既不知道这两个值说的是哪个信号，
    也不知道需求的原始定义。现在每组额外给出：
      - EoICD 信号身份（短名 + bit 区间）
      - HLR 侧接口身份（中文接口名 + bit）
      - 需求原文（截断）
    这样 AI 才能把"EoICD 的枚举含义"与"HLR 的 bit 规则含义"对齐到同一个信号上比较。
    """
    # 检测是否有 CodedSet 属性
    has_codedset = any(p.get("attribute") == "DP.CodedSet" or p.get("attribute") == "RP.CodedSet" for p in batch_pairs)
    # 是否带有上下文（上下文缺失时退回旧的三项式排版，保持向后兼容）
    has_ctx = any(p.get("req_text") or p.get("hlr_signal_name") or p.get("eoicd_bit")
                  for p in batch_pairs)

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

    if has_ctx:
        lines.extend([
            "",
            "【重要：如何理解同一编码的不同表述】",
            "每组都给出了该信号的完整身份信息（EoICD 侧信号名与 bit、HLR 侧接口名与 bit）",
            "以及需求原文。请先确认两侧描述的是**同一个信号、同一个 bit**，再比较取值含义。",
            "常见情形（这些应判「一致」）：",
            "  - EoICD 给出编码枚举（如 0=Not Failed;1=Failed），HLR 给出该 bit 的中文规则",
            "    （如「…故障有效标志为有效→bit22=1」）——二者是同一套编码的英文/中文两种表述；",
            "  - EoICD 的枚举项与 HLR 规则中的含义指向同一状态（用信号名与原文核对确认）。",
            "常见情形（这些应判「不一致」）：",
            "  - 同一个 bit 上，两侧给出的含义确实不同（如一侧说 bit22=1 表示有效、",
            "    另一侧说 bit22=1 表示无效）；",
            "  - 数值确实不等且无法用上下文解释（如 EoICD=2 vs HLR=1）；",
            "  - 需求原文根本没有涉及该属性，无法支撑任何等价结论。",
            "若两侧不是同一信号（bit 或接口名对不上），判「不一致」。",
        ])

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
        if has_ctx:
            # 上下文（2026-10-28）：让 AI 能把两侧值对齐到同一个信号上比较
            sig = pair.get("eoicd_signal_short_name") or ""
            ebit = pair.get("eoicd_bit") or ""
            hname = pair.get("hlr_signal_name") or ""
            hbit = pair.get("hlr_bit") or ""
            if sig or ebit:
                lines.append(f"EoICD 信号: {sig}" + (f"（bit {ebit}）" if ebit else ""))
            if hname or hbit:
                lines.append(f"HLR 接口: {hname}" + (f"（bit {hbit}）" if hbit else ""))
        lines.append(f"属性: {pair['hlr_attr_name']}")
        lines.append(f"EoICD 值: {pair['eoicd_value']}")
        lines.append(f"HLR 值: {pair['hlr_value']}")
        if has_ctx and pair.get("req_text"):
            lines.append(f"需求原文: {pair['req_text']}")
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
            data = json.loads(body)
            # 先计数再取 content：解析成功即是一次真实计费调用
            record_token_usage(data.get("usage"))
            return data["choices"][0]["message"]["content"]
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


def _dedup_key(pair: dict) -> tuple:
    """去重分组键：AI 可见输入 + 判定必需的最小上下文。

    ★2026-10-28：分组键必须与 build_prompt 实际送出的内容一致 —— 只有"AI 看到的
    输入完全相同"的项，判定结果才必然一致，合并才安全。

    包含的字段：
      - EoICD 侧信号身份：短名 + bit 区间
      - HLR 侧接口身份：中文接口名 + bit
      - 属性类、双方值

    刻意**不包含** req_text：同一组内 req_id 与 req_text 恒定（实测 73 组 0 例外），
    加它不增加区分度、只增加对自由文本格式的耦合。
    刻意**不包含** eoicd_cluster_id：EoICD 侧同一物理信号会因路径前缀不同
    （AMSC1/AMSC2 × OA/OB）被拆成多个 cluster，但 identity 与属性完全相同，
    比对结果必然相同 —— 这是冗余的根源。
    """
    return (
        pair.get("eoicd_signal_short_name"),
        pair.get("eoicd_bit"),
        pair.get("hlr_signal_name"),
        pair.get("hlr_bit"),
        pair.get("hlr_attr_name"),
        str(pair.get("eoicd_value")),
        str(pair.get("hlr_value")),
    )


def select_representatives(cropped_pairs: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """按去重键分组，每组取首个代表。

    返回 (代表列表, 辐射映射)：
      - reps：每组一个代表（保持原 pair 对象，按首次出现顺序）
      - radiation_map：{代表 pair_id: [成员 pair, ...]}（成员含代表自身）
    """
    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for p in cropped_pairs:
        k = _dedup_key(p)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(p)

    reps: list[dict] = []
    radiation_map: dict[str, list[dict]] = {}
    for k in order:
        members = groups[k]
        rep = members[0]
        reps.append(rep)
        radiation_map[rep["pair_id"]] = members
    return reps, radiation_map


def radiate_results(rep_results: list[dict], radiation_map: dict[str, list[dict]]) -> list[dict]:
    """把代表的判定结论辐射回全组成员，恢复为与原始条目一一对应的完整结果列表。

    ★2026-10-28 关键：成员自己的 pair_id / eoicd_cluster_id / req_id / attribute /
    双方值可能与代表不同（同一信号的不同需求、不同属性、不同 cluster），
    因此**只用成员的字段**，只共享 ai_result 结论。

    字段完整性：输出字段集必须与 process_batch 的返回**完全一致**——下游
    `attr_result_integrator._build_final_data` 会直接取 item["eoicd_value"] /
    item["hlr_value"] / item["hlr_identity"] 等，缺字段会 KeyError。

    兜底：代表结果缺失时，该组成员一律判"不一致"（保守，且不静默丢条目）。
    """
    verdict_by_rep = {r["pair_id"]: r.get("ai_result", "不一致") for r in rep_results}

    out: list[dict] = []
    for rep_id, members in radiation_map.items():
        verdict = verdict_by_rep.get(rep_id)
        for m in members:
            out.append({
                "pair_id": m["pair_id"],
                "eoicd_cluster_id": m.get("eoicd_cluster_id", ""),
                "eoicd_signal_name": m.get("eoicd_signal_name", ""),
                "eoicd_signal_short_name": m.get("eoicd_signal_short_name", ""),
                "req_id": m.get("req_id", ""),
                "hlr_identity": m.get("hlr_identity", {}),
                "attribute": m.get("attribute", ""),
                "hlr_attr_name": m.get("hlr_attr_name", ""),
                "eoicd_value": m.get("eoicd_value"),
                "hlr_value": m.get("hlr_value"),
                "rule": m.get("rule", "default"),
                "ai_result": verdict if verdict else "不一致",
            })
    return out


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
    # ★2026-10-28：单批 prompt 字符上限。
    # 补了上下文后 prompt 体积约为原来的 3.3 倍（实测最大单批 77K 字符），
    # 固定条数分批可能超出小窗口模型的上下文。改为「条数上限 + 字符上限」双约束，
    # 取更严格者分批，保证单批体积有界。
    max_batch_chars = ai_cfg.get("max_batch_chars", 60000)

    # ★2026-10-28 去重：EoICD 侧同一物理信号会因路径前缀不同（AMSC1/AMSC2 × OA/OB）
    # 被拆成多个 cluster，导致同一问题被判多次（实测 409 项里 73.2% 是这类冗余）。
    # 这里按「AI 可见输入 + 最小上下文」分组，每组只送 1 个代表，判完辐射回全组。
    enable_dedup = ai_cfg.get("enable_dedup", True)
    if enable_dedup and cropped_pairs:
        reps, radiation_map = select_representatives(cropped_pairs)
    else:
        reps, radiation_map = list(cropped_pairs), {}

    total = len(reps)
    # 先按条数切，再按字符上限细分（字符上限优先，保证单批不超）
    batches: list[list[dict]] = []
    for i in range(0, total, batch_size):
        chunk = reps[i:i + batch_size]
        cur: list[dict] = []
        cur_len = 0
        for p in chunk:
            # 单条 prompt 长度（该条自身 + 少量固定开销）
            plen = len(build_prompt([p]))
            if cur and (cur_len + plen > max_batch_chars or len(cur) >= batch_size):
                batches.append(cur)
                cur, cur_len = [], 0
            cur.append(p)
            cur_len += plen
        if cur:
            batches.append(cur)

    all_results = []
    total_batches = len(batches)

    if radiation_map:
        print(f"\n      共 {len(cropped_pairs)} 对 mismatch，去重后 {len(reps)} 组待判"
              f"（省 {len(cropped_pairs) - len(reps)} 次），batch_size={batch_size}，"
              f"字符上限={max_batch_chars}，预计 {total_batches} 批")
    else:
        print(f"\n      共 {total} 对 mismatch，batch_size={batch_size}，"
              f"字符上限={max_batch_chars}，预计 {total_batches} 批")

    for bi, batch in enumerate(batches):
        batch_num = bi + 1

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

    # ★2026-10-28 辐射：把代表的判定结论还原回全部原始条目。
    # 下游 feed_ai_results 按 pair_id 精确匹配，且缺 pair_id 会**静默默认"不一致"**
    # （`ai_map.get(pair_id, "不一致")`）——所以必须保证条目数与 pair_id 集合完全一致，
    # 否则会静默产生假不一致。
    if radiation_map:
        all_results = radiate_results(all_results, radiation_map)

    # 完整性断言：辐射后必须与原始条目一一对应
    _expected_ids = [p["pair_id"] for p in cropped_pairs]
    _got_ids = [r.get("pair_id") for r in all_results]
    assert len(all_results) == len(cropped_pairs), (
        f"AI 复核结果条数与原始 mismatch 数不一致: 期望 {len(cropped_pairs)}, "
        f"实际 {len(all_results)}（辐射遗漏）")
    assert sorted(_got_ids) == sorted(_expected_ids), (
        "AI 复核结果的 pair_id 集合与原始 mismatch 不一致（辐射错位）")

    return all_results


# ============================ 自测 ============================

def _self_test() -> None:
    """自测：属性比对 AI 复核的去重 + 辐射。

    覆盖的高频回归点：
      - 同键项被合并成一个代表（EoICD 同物理信号的不同 cluster 副本）。
      - 不同键项不被合并（不同信号 / 不同属性类 必须分开）。
      - 辐射后条目数与 pair_id 集合与原始完全一致（下游按 pair_id 精确匹配，
        缺一个会静默默认"不一致"）。
      - 辐射时用**成员自己的**字段（cluster_id / req_id / attribute 可能与代表不同）。
      - 代表结果缺失时兜底判"不一致"（不静默丢条目）。
    """
    def mk(i, short, ebit, hname, hbit, attr, ev, hv, cluster=None, req="R1"):
        return {
            "pair_id": f"attr_mismatch_{i:05d}",
            "eoicd_cluster_id": cluster or f"C{i}",
            "eoicd_signal_name": f"SIG.{short}",
            "eoicd_signal_short_name": short,
            "req_id": req,
            "attribute": f"DP.{attr}",
            "hlr_attr_name": attr,
            "eoicd_value": ev,
            "hlr_value": hv,
            "rule": "default",
            "hlr_bit": hbit,
            "hlr_signal_name": hname,
            "eoicd_bit": ebit,
            "req_text": "原文",
        }

    # 4 项同键（同一物理信号的 4 个 cluster 副本） + 2 项不同键
    pairs = [
        mk(1, "SIG_A", "22-22", "接口甲", "22", "CodedSet", "0=X;1=Y", "H1",
           cluster="C1", req="R1"),
        mk(2, "SIG_A", "22-22", "接口甲", "22", "CodedSet", "0=X;1=Y", "H1",
           cluster="C2", req="R1"),
        mk(3, "SIG_A", "22-22", "接口甲", "22", "CodedSet", "0=X;1=Y", "H1",
           cluster="C3", req="R2"),
        mk(4, "SIG_A", "22-22", "接口甲", "22", "CodedSet", "0=X;1=Y", "H1",
           cluster="C4", req="R2"),
        # 不同属性类 → 不同组
        mk(5, "SIG_A", "22-22", "接口甲", "22", "OneState", "X", "H2",
           cluster="C1", req="R1"),
        # 不同信号 → 不同组
        mk(6, "SIG_B", "23-23", "接口乙", "23", "CodedSet", "0=X;1=Y", "H3",
           cluster="C5", req="R1"),
    ]

    reps, rmap = select_representatives(pairs)
    # 4 项同键合并成 1 组；另 2 项各自成组 → 共 3 组
    assert len(reps) == 3, f"应得 3 个代表，实际 {len(reps)}"
    assert len(rmap) == 3, rmap
    # 第 1 组有 4 个成员
    sizes = sorted(len(v) for v in rmap.values())
    assert sizes == [1, 1, 4], sizes

    # 辐射：代表结果 → 全部成员
    rep_results = [{"pair_id": r["pair_id"], "ai_result": "一致"} for r in reps]
    out = radiate_results(rep_results, rmap)
    # 条数与 pair_id 集合必须与原始完全一致
    assert len(out) == len(pairs), f"辐射后条数 {len(out)} != {len(pairs)}"
    assert sorted(o["pair_id"] for o in out) == sorted(p["pair_id"] for p in pairs)
    # 结论全部继承
    assert all(o["ai_result"] == "一致" for o in out), out

    # ★辐射时用成员自己的字段（cluster/req/attribute 可能与代表不同）
    by_id = {o["pair_id"]: o for o in out}
    assert by_id["attr_mismatch_00003"]["eoicd_cluster_id"] == "C3", by_id["attr_mismatch_00003"]
    assert by_id["attr_mismatch_00003"]["req_id"] == "R2", by_id["attr_mismatch_00003"]
    assert by_id["attr_mismatch_00005"]["attribute"] == "DP.OneState", by_id["attr_mismatch_00005"]
    assert by_id["attr_mismatch_00005"]["hlr_attr_name"] == "OneState"

    # ★字段集必须与 process_batch 的返回一致（下游 _build_final_data 直接取
    #   item["eoicd_value"] / item["hlr_value"] / item["hlr_identity"]，缺字段会 KeyError）
    _pb_fields = {"pair_id", "eoicd_cluster_id", "req_id", "attribute", "ai_result"}
    _rad_fields = set(out[0].keys())
    assert _pb_fields <= _rad_fields, f"辐射结果缺字段: {_pb_fields - _rad_fields}"
    for _k in ("eoicd_value", "hlr_value", "hlr_attr_name", "hlr_identity",
               "eoicd_signal_name", "eoicd_signal_short_name", "rule"):
        assert _k in _rad_fields, f"辐射结果缺字段: {_k}"

    # 代表结果缺失 → 兜底判"不一致"，且不丢条目
    out2 = radiate_results([{"pair_id": reps[0]["pair_id"], "ai_result": "一致"}], rmap)
    assert len(out2) == len(pairs), len(out2)
    miss = [o for o in out2 if o["ai_result"] == "不一致"]
    assert len(miss) == len(pairs) - len(rmap[reps[0]["pair_id"]]), (len(miss),)

    # 分组键不含 cluster_id / req_id / req_text：同物理信号副本必须同组
    assert _dedup_key(pairs[0]) == _dedup_key(pairs[3]), "cluster/req 不同不应影响分组"
    # 不同属性类 / 不同信号必须不同组
    assert _dedup_key(pairs[0]) != _dedup_key(pairs[4])
    assert _dedup_key(pairs[0]) != _dedup_key(pairs[5])

    print("attr_mismatch_ai_matcher 自测通过（去重分组/同物理信号副本合并/不同键不合并/"
          "辐射条数与pair_id完整性/成员字段正确/代表缺失兜底 均符合预期）")


if __name__ == "__main__":  # pragma: no cover
    # 独立运行时补齐 utils 的搜索路径（生产路径由 run_attribute_match.py 引导）
    import sys as _sys
    import os as _os
    _shared = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__)))), "shared")
    if _os.path.isdir(_shared) and _shared not in _sys.path:
        _sys.path.insert(0, _shared)
    _self_test()
