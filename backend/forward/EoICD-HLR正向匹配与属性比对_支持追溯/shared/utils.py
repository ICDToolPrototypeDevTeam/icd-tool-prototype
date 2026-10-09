"""
utils.py — 身份匹配阶段通用工具函数
"""

import json
import os
import re

# 名称相似度抽取英文/字母段的正则（兼容"中文+英文缩写"，如 BBSOV1 → BBSOV）
_TOKEN_RE = re.compile(r"[A-Za-z]+")

# --- 复用集成项目公共包 common/：自动向上定位到含 common/ 的根目录 ---
import sys as _sys
from pathlib import Path as _Path

_p = _Path(__file__).resolve()
while not (_p / "common").is_dir() and _p != _p.parent:
    _p = _p.parent
if str(_p) not in _sys.path:
    _sys.path.insert(0, str(_p))

from common import safe_load_json, save_json as _common_save_json  # noqa: E402


# ------------------------------------------------------------------
# 0. AI 流式响应防停滞读取（抗服务端 keepalive 滴流挂死）
# ------------------------------------------------------------------

class AIStreamStalledError(Exception):
    """AI 流式响应停滞：滑动窗口内收到的字节数低于阈值。

    服务端卡死后每几秒漏 1 个 keepalive 字节，requests 的 read timeout
    （两次收字节的最大间隔）会被不断重置、永不触发，导致进程永久挂死。
    按"窗口内实际字节数"判断即可识破这种滴流。本异常是普通 Exception，
    调用方的重试/降级逻辑零改动即可接住。
    """


def read_stream_with_stall_guard(
    resp,
    window_seconds=30.0,
    min_bytes=512,
    overall_seconds=900.0,
):
    """流式读取响应体，带"低速停滞"与"整体上限"双重保护。

    参数：
      resp:            requests.Response（发起请求时必须 stream=True）
      window_seconds:  滑动窗口时长（秒）；窗口内累计字节 < min_bytes 判停滞
      min_bytes:       窗口内最低字节数（正常生成远超此值，滴流骗不过）
      overall_seconds: 单次读取整体上限（秒）；防"极慢但不停"的拖死

    返回：完整响应体 bytes
    异常：AIStreamStalledError（停滞/超整体上限，底层连接已关闭）
          requests/urllib3 的既有异常（如 read timeout）原样抛出
    """
    import time as _t
    start = _t.time()
    window_start = start
    window_bytes = 0
    chunks = []
    try:
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                continue
            now = _t.time()
            chunks.append(chunk)
            window_bytes += len(chunk)
            if now - window_start >= window_seconds:
                if window_bytes < min_bytes:
                    raise AIStreamStalledError(
                        f"流式响应停滞：{window_seconds:.0f}s 窗口内仅收到 {window_bytes} 字节"
                    )
                window_start, window_bytes = now, 0
            if now - start > overall_seconds:
                raise AIStreamStalledError(
                    f"流式响应超整体上限 {overall_seconds:.0f}s"
                )
        return b"".join(chunks)
    finally:
        resp.close()


# ------------------------------------------------------------------
# 1. 配置加载
# ------------------------------------------------------------------

def load_config(config_path="config/match_config.json"):
    """加载 JSON 配置文件（支持 // 注释）。路径相对于项目根目录。

    注释剥离统一委托 common.safe_load_json：
    旧实现用 line.split("//")[0]，会连字符串值里的 //（如 URL）一起截断，
    属于「配置被静默损坏」的 bug，故不再在本文件保留重复实现。
    """
    # 获取项目根目录（本文件位于 shared/ 下，向上回退一级）
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_path = os.path.join(project_root, config_path)

    return safe_load_json(full_path)


# ------------------------------------------------------------------
# 2. JSON 读写
# ------------------------------------------------------------------

def load_json(path, project_root=None):
    """加载 JSON 文件。如果 path 是绝对路径则直接使用，否则拼接 project_root。

    统一走 common.safe_load_json：对纯 JSON 行为一致，并额外支持注释。
    """
    if project_root is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_path = path if os.path.isabs(path) else os.path.join(project_root, path)
    return safe_load_json(full_path)


def save_json(data, path, project_root=None, indent=2):
    """保存数据为 JSON 文件。自动创建父目录。

    统一委托 common.save_json，避免多份「建目录 + json.dump」的重复实现。
    """
    if project_root is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_path = path if os.path.isabs(path) else os.path.join(project_root, path)
    return _common_save_json(data, full_path, indent=indent)


def get_cluster_id(eoicd):
    """从 EoICD 数据中提取 cluster_id（兼容 _cluster 嵌套和顶层字段）。"""
    if "cluster_id" in eoicd and eoicd["cluster_id"] is not None:
        return eoicd["cluster_id"]
    cluster_meta = eoicd.get("_cluster", {})
    return cluster_meta.get("cluster_id", "unknown")
# ------------------------------------------------------------------

def extract_label(hlr_label, strip_prefix="L"):
    """
    从 HLR label 中提取纯数字。
    例如："L11" → "11"；"L126" → "126"。
    如果 hlr_label 为 null 或不以指定前缀开头，则原样返回。
    """
    if hlr_label is None:
        return None
    if isinstance(hlr_label, str) and strip_prefix and hlr_label.startswith(strip_prefix):
        return hlr_label[len(strip_prefix):]
    return hlr_label


def name_similarity(hlr_name, eoicd_name):
    """含 null 字段 unit 的名称门控相似度（containment，分母取 HLR token 数）。

    用于替代 null_is_wildcard 对"全 null/部分 null"信号单元触发的全匹配爆炸：
    仅保留名称被 HLR token 覆盖比例 ≥ 阈值的候选簇，大幅减少送 AI 的候选对。

    返回 0.0~1.0，或 None：
      - 任一为 None/空 → 0.0
      - 抽不出拉丁子串（纯中文/无英文 token）→ None（哨兵：不可判定，交 LLM，不门控丢弃）
      - 下划线分词改为正则抽取 [A-Za-z]+ 段（兼容"中文+英文缩写"如 BBSOV1→BBSOV），
        containment = |交集| / |HLR token 集|
        （用 containment 而非纯 Jaccard，避免单 token HLR 名被误删：
         例：HLR "CMD" vs EoICD "RX_Drain_VLV_RPDU_ESW_CMD" → 1.0 仍保留）
    """
    if not hlr_name or not eoicd_name:
        return 0.0
    a = set(_TOKEN_RE.findall(str(hlr_name).upper()))   # 通风BBSOV1 → {BBSOV}
    b = set(_TOKEN_RE.findall(str(eoicd_name).upper()))  # BBSOV_X → {BBSOV, X}
    if not a or not b:
        return None   # 纯中文/无拉丁子串 → 不可判定，不门控丢弃
    inter = a & b
    if not inter:
        return 0.0
    return len(inter) / len(a)


# ------------------------------------------------------------------
# 4. 字段匹配（支持 null 通配符）
# ------------------------------------------------------------------

def match_field(eoicd_val, hlr_val, null_is_wildcard=True):
    """
    判断两个字段值是否匹配。

    规则：
    1. 如果 null_is_wildcard=True，任一方为 null → 视为匹配
    2. 双方非 null 且相等 → 匹配
    3. 双方非 null 且不等 → 不匹配
    """
    if null_is_wildcard and (eoicd_val is None or hlr_val is None):
        return True
    # 统一转为字符串比较（避免 int vs str 问题）
    return str(eoicd_val) == str(hlr_val)


# ------------------------------------------------------------------
# 5. Bit 范围解析与比对
# ------------------------------------------------------------------

def parse_bit_range(bit_value):
    """
    解析 bit 值为集合（用于范围比对）。

    支持格式：
    - null / None          → None（表示无约束）
    - 整数 15              → {15}
    - 字符串 "15"          → {15}
    - 字符串 "15-16"       → {15, 16}
    - 字符串 "15,16,17"    → {15, 16, 17}
    - 负值 "-1"            → {-1}
    """
    if bit_value is None:
        return None

    s = str(bit_value).strip()
    if not s:
        return None

    bits = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue

        # 负数单值（如 "-1"）—— 只有开头有 -，中间没有
        if part.startswith("-") and part.count("-") == 1:
            bits.add(int(part))
        # 范围（如 "15-16"）—— 中间有 -
        elif "-" in part and not part.startswith("-"):
            start, end = part.split("-", 1)
            start = start.strip()
            end = end.strip()
            if start and end:
                bits.update(range(int(start), int(end) + 1))
        # 普通单值
        else:
            bits.add(int(part))

    return bits


def is_bit_contained(eoicd_bit, hlr_bit, strategy="subset"):
    """
    判断 EoICD bit 与 HLR bit 的包含关系。

    参数：
    - eoicd_bit: EoICD 的 bit（单值/范围）
    - hlr_bit: HLR 的 bit（单值/范围）
    - strategy: "subset"（默认，严格）或 "intersect"（宽松）

    返回：
    - "high"    : EoICD bit ⊆ HLR bit（subset 策略）
    - "medium"  : 任一方为 null，无约束
    - "low"     : 其他情况（不满足包含关系）
    """
    eoicd_set = parse_bit_range(eoicd_bit)
    hlr_set = parse_bit_range(hlr_bit)

    # 任一方为 null → 中置信度
    if eoicd_set is None or hlr_set is None:
        return "medium"

    if strategy == "subset":
        if eoicd_set <= hlr_set:  # EoICD 是 HLR 的子集
            return "high"
        else:
            return "low"
    elif strategy == "intersect":
        if eoicd_set & hlr_set:   # 有交集
            return "high"
        else:
            return "low"
    else:
        raise ValueError(f"Unknown bit match strategy: {strategy}")


def load_hlr_dedup(dedup_path, project_root=None):
    """
    加载 HLR 去重结果，将 interfaces 展开为匹配单元列表。

    每个匹配单元包含：
      - req_id: 需求唯一标识
      - bus: req_id 级别总线
      - label: 接口级别 label
      - name: 接口级别 name
      - bit: 接口级别 bit
      - direction: 接口方向 (pub/sub)
      - interface_idx: 接口在原始 interfaces 数组中的索引

    返回：匹配单元列表 [{req_id, bus, label, name, bit, direction, interface_idx}, ...]
    """
    hlr_dedup = load_json(dedup_path, project_root)
    dedup_reqs = hlr_dedup.get("dedup_requirements", [])

    expanded = []
    for req in dedup_reqs:
        req_id = req.get("req_id")
        req_bus = req.get("bus")
        interfaces = req.get("interfaces", [])

        for idx, iface in enumerate(interfaces):
            expanded.append({
                "req_id": req_id,
                "bus": req_bus,
                "label": iface.get("label"),
                "name": iface.get("name"),
                "bit": iface.get("bit"),
                "direction": iface.get("direction"),
                "interface_idx": idx,
            })

    return expanded


def generate_match_report_from_dedup(final_retained, hlr_clustered, config):
    """
    根据 Name 匹配后的保留结果，结合 HLR 聚类数据生成最终身份匹配报告。

    参数：
    - final_retained: Name 匹配后的保留列表
      [{eoicd_cluster, candidate_interfaces: [{unit, match_info}, ...]}, ...]
    - hlr_clustered: HLR 聚类结果（用于回填 attribute_class, attr_value, evidence）
    - config: 配置字典

    返回：
    - match_report: 最终报告（格式与旧版完全一致）
    """
    # 构建 req_id -> 聚类条目的快速查找表
    # 结构: {req_id: [{attribute_class, attr_value, evidence}, ...]}
    req_id_to_clustered = {}
    for attr_cls in hlr_clustered.get("attribute_classes", []):
        attr_name = attr_cls.get("attribute", "")
        for req in attr_cls.get("requirements", []):
            req_id = req.get("req_id")
            if req_id not in req_id_to_clustered:
                req_id_to_clustered[req_id] = []
            req_id_to_clustered[req_id].append({
                "attribute_class": attr_name,
                "attr_value": req.get("attr_value"),
                "evidence": req.get("evidence", []),
            })

    matches = []
    match_summary = []

    for item in final_retained:
        eoicd = item["eoicd_cluster"]
        cluster_id = get_cluster_id(eoicd)
        eoicd_identity = eoicd.get("identity", {})
        signal_name = eoicd.get("signal_name", "")

        matched_hlrs = []

        for candidate in item["candidate_interfaces"]:
            unit = candidate["unit"]
            hlr_identity = {
                "bus": unit.get("bus"),
                "label": unit.get("label"),
                "name": unit.get("name"),
                "bit": unit.get("bit"),
                "direction": unit.get("direction"),
            }

            req_id = unit.get("req_id")

            # 从聚类结果中查找该 req_id 的属性信息
            clustered_entries = req_id_to_clustered.get(req_id, [])
            if clustered_entries:
                # 收集该 req_id 涉及的所有属性类
                attribute_classes = [entry.get("attribute_class", "") for entry in clustered_entries if entry.get("attribute_class")]
                attr_value = clustered_entries[0].get("attr_value")
                evidence = clustered_entries[0].get("evidence", [])
            else:
                attribute_classes = []
                attr_value = None
                evidence = []

            matched_hlrs.append({
                "req_id": req_id,
                "hlr_identity": hlr_identity,
                "attribute_classes": attribute_classes,
                "attr_value": attr_value,
                "evidence": evidence,
                "match_pipeline": {
                    "bus_label_bit": {"status": "matched"},
                    "direction": {"status": "matched"},
                    "name": {"status": "matched"},
                },
            })

            # 身份主键字符串
            eoicd_id_key = _fmt_identity_key(eoicd_identity)
            hlr_id_key = _fmt_identity_key(hlr_identity)

            match_summary.append(
                f"{cluster_id}---{signal_name}---{eoicd_id_key}---{req_id}---{hlr_id_key}"
            )

        matches.append({
            "eoicd_cluster_id": cluster_id,
            "eoicd_signal_name": signal_name,
            "eoicd_signal_short_name": eoicd.get("signal_short_name"),
            "eoicd_identity": eoicd_identity,
            "eoicd_attributes": eoicd.get("attributes", {}),
            "matched_hlrs": matched_hlrs,
        })

    report = {
        "meta": {
            "stage": "identity_match_complete",
            "description": "EoICD → HLR 正向匹配 - 身份匹配阶段（含 Bus+Label+Bit + Direction + Name）",
            "total_clusters": len(matches),
            "match_summary": match_summary,
            "stages": ["bus_label_bit", "direction", "name"],
        },
        "matches": matches,
    }

    return report


def _fmt_identity_key(identity):
    """将身份字典格式化为 `bus-label-name-bit-direction` 字符串。"""
    bus = identity.get("bus") if identity.get("bus") is not None else "null"
    label = identity.get("label") if identity.get("label") is not None else "null"
    name = identity.get("name") if identity.get("name") is not None else "null"
    bit = identity.get("bit")
    if bit is not None:
        bit_str = str(bit)
    else:
        bit_range = identity.get("bit_range")
        bit_str = str(bit_range) if bit_range is not None else "null"
    # 新增 direction
    direction = identity.get("direction") if identity.get("direction") is not None else "null"
    return f"{bus}-{label}-{name}-{bit_str}-{direction}"


# ------------------------------------------------------------------
# 7. HLR 需求扁平化（兼容旧数据）
# ------------------------------------------------------------------

def flatten_hlr_requirements(hlr_data):
    """
    将 HLR 聚类数据扁平化为需求列表。

    输入：hlr_clustered.json 格式
    输出：[{req_id, bus, label, name, bit, attr_value, evidence, attribute_class}, ...]
    """
    reqs = []
    for attr_cls in hlr_data.get("attribute_classes", []):
        attr_name = attr_cls.get("attribute", "")
        for req in attr_cls.get("requirements", []):
            req_copy = dict(req)
            req_copy["attribute_class"] = attr_name
            reqs.append(req_copy)
    return reqs
