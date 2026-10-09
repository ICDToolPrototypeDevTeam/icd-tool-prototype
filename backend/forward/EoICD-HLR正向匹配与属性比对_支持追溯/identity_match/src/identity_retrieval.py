"""
identity_retrieval.py — 含中文全 null unit 的 LLM 检索前置

问题：
  name 含中文且 bus/label/bit 全 null 的 HLR unit 会通配命中几乎所有 EoICD 簇，
  若直接送入 batch_ai_match 将产生海量逐对 LLM 调用（爆炸）。

做法（以准确性为前提）：
  在送入 batch_ai_match 之前，对这类 unit 先用"检索型"LLM 调用（按 chunk 分批，
  复用 name_ai_matcher 的 API 底座），让 LLM 从全部候选簇中语义选出与本 unit
  中文名相关的 cluster_id；其余判定为不相关，写入 rejected（reason=null_identity_retrieval），
  由下游 summary 兜底归为未落实/未识别。

  - 选哪一些由 LLM 语义判定，而非遍历序盲截断（盲截断会误标真匹配为未落实）。
  - 仅当某 unit 候选数 > top_k 才触发检索（普通数据到不了，零开销）。
  - 含中文但非全 null 的 unit：字段过滤后候选已很小，直接走现有 batch_ai_match，不检索。
"""

import re
from collections import defaultdict

from name_ai_matcher import call_ai_api, get_api_config

_CJK = re.compile(r"[\u4e00-\u9fff]")
_CHUNK = 200


def _has_cjk(s):
    return bool(s) and bool(_CJK.search(str(s)))


def build_retrieval_prompt(hlr_name, numbered_lines):
    """构造检索型 prompt：给定 HLR 中文名 + 编号候选列表，让 LLM 返回相关编号。"""
    header = [
        "你是航空电子领域的接口需求比对专家。",
        "",
        "【任务】",
        "以下是某条 HLR 软件高层需求信号的中文描述：",
        f"  HLR 信号名：{hlr_name}",
        "",
        "下面是一批候选的 EoICD 信号名称（已编号）。请判断其中哪些 EoICD 信号与",
        "上述 HLR 信号描述同一个物理信号。",
        "",
        "【匹配原则】",
        "1. 语义等价：两者指代同一物理信号，即使表述形式不同（中英混合/缩写）。",
        "2. 包含关系：EoICD 信号是 HLR 描述的具体成员，且关键字段不矛盾。",
        "3. 仅当明显无关时才排除。",
        "",
        "【候选列表】",
    ]
    footer = [
        "",
        "【输出格式】",
        "只输出相关的编号，以逗号分隔（例如：3,7,12）。若无相关项，输出 NONE。",
        "不要输出任何解释。",
    ]
    return "\n".join(header + numbered_lines + footer)


def parse_retrieval_response(text):
    """解析 LLM 返回的编号列表，返回 int 集合。"""
    text = (text or "").strip().upper()
    if not text or text == "NONE":
        return set()
    nums = set()
    for tok in re.split(r"[\s,，、;；]+", text):
        tok = tok.strip().lstrip("#").strip()
        if tok.isdigit():
            nums.add(int(tok))
    return nums


def retrieve_for_cn_fullnull(cropped_pairs, config):
    """对 cropped_pairs 中'含中文且全 null 且 候选>top_k'的 unit 做 LLM 检索缩减。

    参数：
      cropped_pairs: crop_name_input 产出的候选对列表
      config: 配置字典（读取 matching_rules.null_identity_top_k 与 ai_matching）

    返回：
      (reduced_pairs, retrieval_rejected)
    """
    rules = config.get("matching_rules", {})
    top_k = rules.get("null_identity_top_k", 30)

    # 1) 按 (req_id, interface_idx) 分组
    groups = defaultdict(list)
    for idx, p in enumerate(cropped_pairs):
        hlr = p.get("hlr", {})
        key = (hlr.get("req_id"), hlr.get("interface_idx"))
        groups[key].append(idx)

    # 2) 筛选需检索的 unit：候选>top_k 且 name 含中文 且 bus/label/bit 全 null
    need = {}
    meta = {}
    for key, idxs in groups.items():
        if len(idxs) <= top_k:
            continue
        hlr = cropped_pairs[idxs[0]].get("hlr", {})
        name = hlr.get("name")
        if not _has_cjk(name):
            continue
        if not (hlr.get("bus") is None and hlr.get("label") is None and hlr.get("bit") is None):
            continue
        need[key] = idxs
        meta[key] = (hlr.get("req_id"), hlr.get("interface_idx"), name)

    if not need:
        return cropped_pairs, []

    api_cfg = get_api_config(config)
    keep_idxs = set()
    rejected = []

    for key, idxs in need.items():
        req_id, iface_idx, name = meta[key]
        numbered = []
        id_map = {}  # num -> (idx, cluster_id, signal_short_name)
        for n, i in enumerate(idxs, 1):
            p = cropped_pairs[i]
            cid = p.get("eoicd_cluster_id")
            sname = (p.get("eoicd") or {}).get("signal_short_name") or cid
            numbered.append(f"{n}. {sname} (cluster_id={cid})")
            id_map[n] = (i, cid, sname)

        relevant_cids = set()
        for c0 in range(0, len(numbered), _CHUNK):
            chunk = numbered[c0:c0 + _CHUNK]
            prompt = build_retrieval_prompt(name, chunk)
            resp = call_ai_api(prompt, api_cfg)
            for num in parse_retrieval_response(resp):
                if num in id_map:
                    relevant_cids.add(id_map[num][1])

        # 以准确性为前提：保留 LLM 判定的所有相关簇（不盲截断到 top_k）。
        for i, cid, sname in id_map.values():
            if cid in relevant_cids:
                keep_idxs.add(i)
            else:
                rejected.append({
                    "eoicd_cluster_id": cid,
                    "eoicd_signal_name": sname,
                    "hlr_req_id": req_id,
                    "hlr_interface_idx": iface_idx,
                    "reason": "null_identity_retrieval",
                    "hlr_name": name,
                })

    # 保留未触发检索的 pair + 检索命中的 pair
    need_all = set()
    for idxs in need.values():
        need_all.update(idxs)
    reduced = [p for i, p in enumerate(cropped_pairs)
               if i not in need_all or i in keep_idxs]
    return reduced, rejected
