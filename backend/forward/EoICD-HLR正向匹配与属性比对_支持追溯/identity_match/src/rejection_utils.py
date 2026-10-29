"""
rejection_utils.py — 身份识别阶段拒绝记录的聚合工具

背景
----
匹配对爆炸时，rejected_{stage}_{direction}.json 原本是「每个 EoICD 信号 × 每个被拒
HLR 单元」的扁平记录（N×M），体量随需求条目数平方级膨胀。为消除爆炸并保持可读性，
在写盘前按 eoicd_cluster_id 聚合成「每信号一条」，文件体积从 O(N×M) 降到 O(信号数)。

聚合结果同时是单judge结果汇总 summary.load_identity_rejections 的输入：该函数改为
直接读聚合 dict 并跨阶段合并（见 summary.py）。本模块是聚合逻辑的唯一来源，确保
writer 落盘格式与 reader 期望格式严格一致。

聚合格式（一个信号 cid 的条目）：
    {
      "hlrs": [req_id, ...],            # 该信号在身份识别阶段比对过的 HLR 需求编号集合
      "reasons": [reason, ...],         # 被拒原因集合
      "name": <全名优先，否则短名>,      # 用于汇总展示的信号名
      "name_full": <全名或 None>,        # 全名优先标记，供跨阶段合并时复刻"全名优先"规则
      "params": {                       # 仅对 _REASON_TO_PARAM 命中原因生成
          <param>: {"eoicd": [...], "hlr": [...]}  # 双方具体取值集合
      }
    }
"""

# 身份识别拒绝原因 → 具体未通过的参数（信号名称/方向/总线/位/标签）
_REASON_TO_PARAM = {
    "name mismatch (AI)": "name",
    "name_identity_gate": "name",
    "direction mismatch": "direction",
    "bus mismatch": "bus",
    "bit mismatch": "bit",
    "label mismatch": "label",
}
_PARAM_ORDER = ["name", "direction", "bus", "bit", "label"]
_PARAM_EOICD_KEY = {"name": "eoicd_signal_short_name", "direction": "eoicd_direction",
                    "bus": "eoicd_bus", "bit": "eoicd_bit", "label": "eoicd_label"}
_PARAM_HLR_KEY = {"bus": "hlr_bus", "bit": "hlr_bit", "direction": "hlr_direction"}


def _hlr_value_of(param, obj):
    """取 HLR 侧期望取值：标签优先 hlr_label_raw（缺失回退 extracted），
    信号名称取 hlr_name，其余取对应键。"""
    if param == "label":
        hv = obj.get("hlr_label_raw")
        if not hv or hv == "None":
            hv = obj.get("hlr_label_extracted")
        return hv
    if param == "name":
        return obj.get("hlr_name")
    return obj.get(_PARAM_HLR_KEY.get(param))


def aggregate_rejections(rejections):
    """按 eoicd_cluster_id 聚合拒绝记录。

    与单judge结果汇总旧版 load_identity_rejections 的内联逻辑逐字等价：
    相同 cid 合并 reasons/hlrs，按原因归并各参数双方取值；信号名遵循
    「全名优先、可覆盖已存短名」规则（name_full 记录是否见过全名，供跨阶段合并复刻）。
    """
    agg = {}
    for obj in rejections:
        cid = obj.get("eoicd_cluster_id")
        if not cid:
            continue
        d = agg.setdefault(cid, {"hlrs": set(), "reasons": set(),
                                 "name": None, "name_full": None, "params": {}})
        h = obj.get("hlr_req_id")
        if h:
            d["hlrs"].add(h)
        r = obj.get("reason")
        if r:
            d["reasons"].add(r)
            param = _REASON_TO_PARAM.get(r)
            if param:
                p = d["params"].setdefault(param, {"eoicd": set(), "hlr": set()})
                ev = obj.get(_PARAM_EOICD_KEY.get(param))
                if ev not in (None, ""):
                    p["eoicd"].add(str(ev))
                hv = _hlr_value_of(param, obj)
                if hv not in (None, ""):
                    p["hlr"].add(str(hv))
        full = obj.get("eoicd_signal_name")
        short = obj.get("eoicd_signal_short_name")
        if full:
            d["name_full"] = full
            d["name"] = full
        elif short and not d["name"]:
            d["name"] = short
    return agg


def rejections_to_jsonable(agg):
    """将 aggregate_rejections 的 set 结构转为可 JSON 序列化的 dict（集合→排序列表）。"""
    out = {}
    for cid, info in agg.items():
        params = {
            pk: {"eoicd": sorted(pv["eoicd"]), "hlr": sorted(pv["hlr"])}
            for pk, pv in info["params"].items()
        }
        out[cid] = {
            "hlrs": sorted(info["hlrs"]),
            "reasons": sorted(info["reasons"]),
            "name": info["name"],
            "name_full": info["name_full"],
            "params": params,
        }
    return out
