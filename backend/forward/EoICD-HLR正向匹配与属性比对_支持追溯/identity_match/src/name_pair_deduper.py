"""
name_pair_deduper.py — Name AI 匹配前的同名对去重辐射（结构性去重）

背景：
  name_ai_matcher 的 prompt 中，AI 每对只能看到三项信息：
    HLR name、EoICD identity.name、锚点 Bus/Label（取自 EoICD 侧）。
  因此 AI 可见输入完全相同的匹配对（同 HLR 名 + 同 EoICD 名 + 同锚点），
  判定结果必然一致，只需送 1 个代表，结果辐射给全组即可。

  本模块取代原 name_ai_matcher 内的进程内存缓存：
  旧缓存的预筛在空缓存上空转、分批执行不再查缓存，单次运行从未命中（死代码）。
  本模块为纯函数、无全局状态、运行间完全独立，符合黑盒要求。

接口（两个纯函数，无 I/O）：
  - select_representatives(cropped_pairs) -> (rep_pairs, radiation_map)
  - radiate_results(ai_results, radiation_map) -> full_results
"""


def _pair_key(pair):
    """
    类 key = AI 可见输入组合 (HLR name, EoICD name, EoICD bus, EoICD label)。

    EoICD 名取 identity.name，缺失时兜底 signal_short_name；
    两者都缺失（或 HLR 名缺失）时不做聚类，该对自成一类（退化为逐对判定，
    与去重前行为一致，避免把不同信号合并）。
    """
    idt = pair["eoicd"].get("identity", {})
    eoicd_name = idt.get("name") or pair["eoicd"].get("signal_short_name")
    hlr_name = pair["hlr"].get("name")
    if eoicd_name is None or hlr_name is None:
        return ("__singleton__", pair["pair_id"])
    return (hlr_name, eoicd_name, idt.get("bus"), idt.get("label"))


def select_representatives(cropped_pairs):
    """
    按类 key 分组，每组保留首个代表（确定性）。

    参数：
    - cropped_pairs: name_cropper 输出的 pair 列表

    返回：
    - rep_pairs: 代表 pair 列表（保持原 pair 对象，按首次出现顺序）
    - radiation_map: {代表 pair_id: [成员 pair, ...]}（成员含代表自身，保持原顺序）
    """
    groups = {}
    order = []
    for p in cropped_pairs:
        k = _pair_key(p)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(p)

    rep_pairs = []
    radiation_map = {}
    for k in order:
        members = groups[k]
        rep = members[0]
        rep_pairs.append(rep)
        radiation_map[rep["pair_id"]] = members

    return rep_pairs, radiation_map


def radiate_results(ai_results, radiation_map):
    """
    把代表结果辐射给全组成员，恢复为完整结果列表。

    参数：
    - ai_results: batch_ai_match 返回的代表结果列表
                  [{pair_id, eoicd_cluster_id, hlr_req_id, hlr_interface_idx, result}, ...]
    - radiation_map: select_representatives 的返回

    返回：
    - 完整结果列表，条目数 = 原始 cropped_pairs 数；
      每条用成员自己的 pair_id / eoicd_cluster_id / hlr_req_id / hlr_interface_idx，
      feeder（按 cluster_id+req_id+interface_idx 建映射）零感知。
    """
    result_by_rep = {r["pair_id"]: r["result"] for r in ai_results}
    full = []
    for rep_id, members in radiation_map.items():
        res = result_by_rep.get(rep_id)
        if res is None:
            # 代表缺失结果（极端异常）时整组跳过：与去重前"该对无结果即不反哺"行为一致
            continue
        for m in members:
            full.append({
                "pair_id": m["pair_id"],
                "eoicd_cluster_id": m["eoicd_cluster_id"],
                "hlr_req_id": m["hlr_req_id"],
                "hlr_interface_idx": m["hlr"].get("interface_idx"),
                "result": res,
            })
    return full
