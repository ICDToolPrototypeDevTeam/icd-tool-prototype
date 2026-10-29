"""
name_translator.py — HLR 中文名→英文名 翻译预通行（MT-then-token）

在身份匹配前，将 HLR 接口名中的"含中文"名批量翻译为英文，供 name_similarity
走拉丁 token 比对（排序 / 展示）；原始中文名一律保留，用于交给 AI 做最终比对。

设计要点：
- 仅翻译"含中文"的 name（纯英文名不调 API，省成本）。
- 按原始名去重 + 落磁盘缓存：缓存命中则重跑免费；仅首次付费。
- API 失败的名回退原名（name_en=None），相似度仍为 None → 交 LLM 判定，不硬失败。
- 翻译仅用于"算法排序 / AI 参考"，不对"原名含中文"的候选做脚本硬门控
  （缩写展开如 VLV→Valve、CMD→Command 会让 token 命中归零，脚本门控会误杀真匹配，
   故中文名统一交 LLM 定夺）。
"""

import json
import os
import re
import time

import requests

from utils import read_stream_with_stall_guard, record_token_usage
from name_ai_matcher import get_api_config

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
_DEFAULT_CACHE = os.path.join(_CACHE_DIR, "name_translation.json")

_HAS_CN_RE = re.compile(r"[一-鿿]")


def has_chinese(s):
    """判断字符串是否含中文（CJK 统一表意文字）。"""
    return bool(s) and bool(_HAS_CN_RE.search(str(s)))


def collect_chinese_names(hlr_units):
    """收集所有含中文的 HLR name（按原始字符串去重，稳定排序）。"""
    names = set()
    for u in hlr_units:
        n = u.get("name")
        if has_chinese(n):
            names.add(n)
    return sorted(names)


def load_translation_cache(cache_path=_DEFAULT_CACHE):
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_translation_cache(cache, cache_path=_DEFAULT_CACHE):
    """原子写：先写 .tmp 再 os.replace，避免并发/中断导致缓存文件损坏。"""
    parent = os.path.dirname(cache_path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cache_path)


def _build_translate_prompt(names):
    lines = [
        "你是航空电子领域的术语翻译助手。",
        "请将下列 HLR 接口需求名称翻译为英文，并额外给出其工程缩写。",
        "要求：",
        "1. 保留原有英文缩写与数字编号（如 EDP、RAT、VLV、005 原样保留）；",
        "2. 仅将中文部分翻译为对应的英文工程术语；",
        "3. 对每个名称，严格按以下一行格式输出（不要加解释、不要加序号前缀）：",
        "   EN: <英文翻译（完整单词）> ABBR: <工程缩写（大写缩写，空格分隔，如 VLV CMD）>",
        "   示例：通风阀门负载指令 -> EN: Vent Valve Load Command ABBR: VLV CMD",
        "",
        "输入名称：",
    ]
    for i, n in enumerate(names, 1):
        lines.append(f"{i}. {n}")
    return "\n".join(lines)


def _call_translate_api(prompt, api_cfg):
    """调用翻译 API（复用 name_ai_matcher 的流式防停滞底座），temperature=0 求确定。

    重试策略与 name_ai_matcher.call_llm 同款：
    - 指数退避（2**attempt 秒），最多 api_cfg['retry'] 次；
    - 每次退避前打印日志（含第几次/共几次），与 AI matcher 一致便于排查。
    对限流 429 额外识别并尊重服务端 Retry-After 头（针对本次防护目标）：
    缺失/越界时退化为指数退避，避免固定短退避期内反复触发限流。
    """
    headers = {
        "Authorization": f"Bearer {api_cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": api_cfg["model"],
        "messages": [
            {"role": "system", "content": "你是一个航空电子领域的术语翻译助手，只输出英文翻译结果，不加解释。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 2048,
        "temperature": 0.0,
    }
    url = f"{api_cfg['base_url']}/chat/completions"
    last_err = None
    for attempt in range(api_cfg["retry"]):
        try:
            resp = requests.post(
                url, headers=headers, json=payload,
                timeout=api_cfg["timeout"], stream=True,
            )
            # 限流 429：在 raise_for_status 之前识别，尊重 Retry-After（本次防护目标）
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                ra = None
                if retry_after is not None:
                    try:
                        ra = float(retry_after)
                        if ra <= 0 or ra > 120:
                            ra = None
                    except (TypeError, ValueError):
                        ra = None
                wait = ra if ra is not None else (2 ** attempt)
                last_err = RuntimeError(f"HTTP 429 (rate limited), Retry-After={retry_after}")
                try:
                    resp.close()
                except Exception:
                    pass
                if attempt < api_cfg["retry"] - 1:
                    print(f"    [翻译] 触发限流 429，{wait:.0f}s 后重试（{attempt+1}/{api_cfg['retry']}）...")
                    time.sleep(wait)
                    continue
                else:
                    raise last_err
            resp.raise_for_status()
            body = read_stream_with_stall_guard(
                resp,
                window_seconds=api_cfg.get("stall_window_seconds", 30),
                min_bytes=api_cfg.get("stall_min_bytes", 512),
                overall_seconds=api_cfg.get("overall_timeout_seconds", 900),
            )
            data = json.loads(body)
            # 翻译调用同样计入本进程 token（identity sidecar 汇总）
            record_token_usage(data.get("usage"))
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            last_err = e
            if attempt < api_cfg["retry"] - 1:
                wait = 2 ** attempt
                print(f"    [翻译] API 调用失败（{e}），{wait}s 后重试（{attempt+1}/{api_cfg['retry']}）...")
                time.sleep(wait)
            else:
                raise
    # 兜底（正常不会到达：循环内已 return 或 raise）
    if last_err is not None:
        raise last_err


def _parse_translate_response(text, names):
    """按行索引映射回 names（保留空行以维持对齐，避免某名为空导致错位串译）。

    每行期望格式：EN: <单词> ABBR: <缩写>。解析为 {原名: {"en":.., "abbr":..}}；
    若模型未给出 ABBR:（退化），则 abbr 留空字符串。
    行 i 对应 names[i]；剥离行首可能的 'N. ' 前缀；空行/仅空白的行不写入缓存。
    """
    raw_lines = (text or "").split("\n")
    out = {}
    for i, n in enumerate(names):
        if i < len(raw_lines):
            ln = re.sub(r"^\d+[.)、]\s*", "", raw_lines[i]).strip()
            if not ln:
                continue
            m = re.search(r"ABBR:\s*(.+)$", ln, re.IGNORECASE)
            if m:
                abbr = m.group(1).strip()
                en = ln[:m.start()].replace("EN:", "", 1).strip()
            else:
                en, abbr = ln, ""
            out[n] = {"en": en, "abbr": abbr}
    return out


def run_translation(hlr_units, config, cache_path=_DEFAULT_CACHE, enabled=None):
    """对含中文的 HLR name 做翻译预通行，原地给每个 unit 附 name_en。返回同一列表。

    关闭（enabled=False）或无可用 API 配置时，直接返回（不附 name_en）。
    """
    rules = config.get("matching_rules", {})
    if enabled is None:
        enabled = rules.get("name_translation_enabled", False)
    if not enabled:
        return hlr_units

    # 无可用 API 配置则安全跳过（不阻断流水线）
    try:
        api_cfg = get_api_config(config)
    except Exception as e:
        print(f"  [翻译] 跳过：无法获取 API 配置（{e}）")
        return hlr_units

    cache = load_translation_cache(cache_path)
    names = collect_chinese_names(hlr_units)
    to_translate = [n for n in names if n not in cache]

    if to_translate:
        batch_size = rules.get("translation_batch_size", 50)
        translated_total = 0
        for i in range(0, len(to_translate), batch_size):
            batch = to_translate[i:i + batch_size]
            try:
                prompt = _build_translate_prompt(batch)
                resp = _call_translate_api(prompt, api_cfg)
                translated = _parse_translate_response(resp, batch)
                if translated:
                    cache.update(translated)
                    translated_total += len(translated)
                    save_translation_cache(cache, cache_path)
            except Exception as e:
                print(f"  [翻译] 批次翻译失败（{e}），该批 {len(batch)} 个名回退原名（后续重跑可重试）")
        print(f"  [翻译] 新增翻译 {translated_total} 个中文名（缓存共 {len(cache)} 条，路径 {cache_path}）")
    else:
        print(f"  [翻译] 全部中文名已命中缓存（{len(cache)} 条），未发起 API 调用")

    # 富化：含中文名且有翻译时附 name_en（单词）/ name_abbr（缩写，非空才附）。
    # 兼容旧缓存：旧格式为字符串（仅 en），新格式为 {"en":.., "abbr":..}；
    # 旧缓存的名不会重译，故无 abbr（下游回退 en/原名），需删缓存重译方得 abbr。
    for u in hlr_units:
        n = u.get("name")
        if n and has_chinese(n):
            entry = cache.get(n)
            if isinstance(entry, str):
                en, abbr = entry, ""
            elif isinstance(entry, dict):
                en = entry.get("en", "") or ""
                abbr = entry.get("abbr", "") or ""
            else:
                en, abbr = "", ""
            if en:
                u["name_en"] = en
            if abbr:
                u["name_abbr"] = abbr

    return hlr_units
