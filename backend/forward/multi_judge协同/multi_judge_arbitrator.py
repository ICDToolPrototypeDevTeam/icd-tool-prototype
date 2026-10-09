# -*- coding: utf-8 -*-
"""
multi_judge_arbitrator.py — 多 judge LLM 仲裁层（正向检查多智能体嫁接 · Step5 + Step5.5）

嫁接反向检查的两段智能层（详见 正向检查多judge嫁接方案.md §12/§13）：
  - Step5  仲裁者共识（review_agent，固定模型）：对聚合层（multi_judge_aggregate）产出的
           分歧/覆盖缺口/低置信信号，由独立固定模型再判一次，输出
           agreement_level / final_verdict / inconsistent_attributes(事实) /
           field_disagreements(KEY_FIELDS 二分) / confidence / final_analysis。
  - Step5.5 peer-aware 复查（re_review）：对星级 ≤2★ 的信号，把各 judge 结论匿名化为
           A/B/C（抹去具体模型名），交由仲裁者重新考量并产出修正结论，再触发一轮 Step5 重仲裁
           （对应反向 Step5.6）。

说明：反向 Step5.5 是「重新跑一遍 case 级 LLM 判定」；正向 judge=整条 EoICD→HLR 流水线，
重跑代价极高，故此处等价为「仲裁者用匿名化的全部 judge 结论再判一次」，语义一致、代价可控。

本模块由 runner 的复核路由（`run_review_route`）无条件调用——need_review=True 的信号必须
获得第二裁决；只有仲裁模型不可用 / 同源被拒 / 超预算时才降级，且一律记账 skipped。
LLM 调用失败（无密钥/网络）时优雅跳过，保留后端聚合结论。

用法（被 multi_judge_runner.py 调用）：
    from multi_judge_arbitrator import run_arbitration
    arb_cfg = dict(resolved); arb_cfg["re_review"] = cfg.get("re_review") or {}
    agg = run_arbitration(agg, shards, judge_meta, arb_cfg)
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
from collections import Counter

# 关键字段白名单（KEY_FIELDS，身份层 judge 结论骨架）—— 分歧即真分歧。
# 与 multi_judge_aggregate.KEY_FIELDS_IDENTITY 对齐，喂给仲裁 LLM 用于打 category='key'。
FORWARD_KEY_FIELDS = (
    "verdict", "matched_hlr", "name_match_status",
    "bus", "label", "bit", "direction",
    "attribute_class",  # 各被比对属性的结论字段
)


# ---------------------------------------------------------------------------
# 最小 OpenAI 兼容 LLM 客户端（标准库 urllib，无第三方依赖）
# ---------------------------------------------------------------------------

def call_llm(model: str, base_url: str, api_key: str, system: str, user: str,
             temperature: float = 0.1, timeout: int = 180) -> dict | None:
    """调用 OpenAI 兼容 /chat/completions，要求 JSON 输出，返回解析后的 dict；失败返回 None。"""
    if not (model and base_url and api_key):
        return None
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
            json.JSONDecodeError, Exception) as e:
        print(f"  [仲裁] LLM 调用失败（{type(e).__name__}: {e}），跳过智能层，保留后端聚合")
        return None


# ---------------------------------------------------------------------------
# 星级映射（与 multi_judge_aggregate._star_rating 一致）
# ---------------------------------------------------------------------------

def map_star(agreement: str, has_key: bool) -> int:
    if agreement == "full":
        return 4 if has_key else 5
    if agreement == "majority":
        return 2 if has_key else 3
    return 1  # split / single_source / no_consensus


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

CONSENSUS_SYSTEM = """你是一名航空软件需求一致性检查的独立仲裁者。
输入是多个独立 judge（可能不同模型）对同一 EoICD 信号做「EoICD→HLR 正向落实检查」的结论。
请综合各方意见，给出最终仲裁结论。严格按 JSON 输出，不要多余文字。

关键字段（KEY_FIELDS，身份层 judge 结论骨架）：
- verdict（落实结论：已落实/不一致/未落实/部分落实）
- matched_hlr（匹配到的 HLR 需求编号列表）
- name_match_status（EoICD name 是否语义命中某 HLR）
- bus/label/bit/direction（信号地址四元组）
- 各被比对属性 attribute_class 的结论 single_result（属性一致/属性不一致/无提及/无具体值）
仅当 judge 间在上述关键字段上出现分歧时，field_disagreements 的 category 才标 'key'；
若仅在次要字段（属性取值文本、自然语言分析、单位说明）上分歧，标 'non_key'。

输出 JSON 字段：
{
  "agreement_level": "full|majority|split|single_source|no_consensus",
  "final_verdict": "已落实|不一致|未落实|部分落实|待确认",
  "final_matched_hlr": ["需求编号", ...],
  "inconsistent_attributes": [{"attribute_class":"...","detail":"...","judges":["judge名",...]}],
  "field_disagreements": [{"field":"verdict|matched_hlr|name_match_status|bus|label|bit|direction|attr:属性类","category":"key|non_key","summary":"简要分歧说明"}],
  "final_analysis": "综合判定依据（中文，简洁）",
  "confidence": 0.0~1.0
}"""

CONSENSUS_USER_TMPL = """信号全名：{full_name}

各 judge 结论（judge 名 → 结论）：
{judges_block}

后端聚合提示：{backend_note}

请输出仲裁 JSON。"""


REVIEW_SYSTEM = """你是一名航空软件需求一致性检查的复查仲裁者。
下面把多个 judge 对同一 EoICD 信号的结论匿名化为若干「判断 A / 判断 B / 判断 C…」（已抹去具体模型名与 judge 名）。
其中「判断 A」是初判参考，请结合其余判断的证据反思并给出最终 reconciled 结论。

重要：每个判断对应一个独立视角，其后的「共 N 份重复」表示该视角用同一模型重复跑了 N 次——
重复份数只代表采样次数，不代表独立共识；若标注「结论完全一致」，说明这些份数之间摇摆为零。
请据此判断证据强度，不要把重复份数误当作多方支持。
严格按 JSON 输出。

输出 JSON 字段：
{
  "final_verdict": "已落实|不一致|未落实|部分落实|待确认",
  "final_matched_hlr": ["需求编号", ...],
  "attribute_reconciled": [{"attribute_class":"...","single_result":"属性一致|属性不一致|无提及|无具体值","reason":"..."}],
  "analysis": "复查分析（中文，指出 A/B/C 核心分歧与你的裁定理由）",
  "confidence": 0.0~1.0
}"""

# 复查侧的用户 prompt 不再写死「判断 A/B/C」三槽（#9 修复：按实际独立视角数动态拼装，
# 见下方 _independent_groups / _build_review_user）。


# ---------------------------------------------------------------------------
# 构造 judge 展示块
# ---------------------------------------------------------------------------

def _judge_block(shard_entry: dict, name: str) -> str:
    e = shard_entry
    attrs = e.get("attributes") or []
    attr_lines = "; ".join(
        f"{a.get('attribute_class')}={a.get('single_result')}"
        f"(EoICD:{a.get('eoicd_value')}/HLR:{a.get('hlr_value')})"
        for a in attrs
    ) or "无属性"
    return (f"- {name}: verdict={e.get('verdict')}, matched_hlr={e.get('matched_hlr')}, "
            f"name_match={e.get('name_match_status')}, identity={e.get('hlr_identity')}, "
            f"属性=[{attr_lines}]")


def _build_consensus_user(full: str, shard: dict, backend_note: str) -> str:
    block = "\n".join(_judge_block(e, j) for j, e in shard.items())
    return CONSENSUS_USER_TMPL.format(full_name=full, judges_block=block, backend_note=backend_note)


# ---------------------------------------------------------------------------
# 独立视角分组（#9-B：匿名化按「视角」而非「judge 个数」）
# ---------------------------------------------------------------------------

def _independent_groups(names: list[str], judge_meta: dict | None) -> list[list[str]]:
    """把 judge 名列表按独立视角分组：相同 (model, base_url) 判为同一视角（同一模型重复采样）。

    同模型重复（模式2）不是第二个独立视角，合并进一个判断并注明重复份数，
    避免仲裁者把同源重复当成两票独立共识（伪共识放大 confidence）。
    judge 缺 model/base_url meta 时退化为「每个 judge 单独一组」。
    """
    key_of: dict[str, tuple] = {}
    for n in names:
        m = (judge_meta or {}).get(n) or {}
        model = m.get("model") or ""
        base = (m.get("base_url") or "").rstrip("/")
        key_of[n] = (model, base) if (model or base) else (f"__solo_{n}__", "")

    groups: list[list[str]] = []
    idx: dict[tuple, list[str]] = {}
    for n in names:
        k = key_of[n]
        if k in idx:
            idx[k].append(n)
        else:
            g = [n]
            groups.append(g)
            idx[k] = g
    return groups


def _entry_body(entry: dict) -> str:
    """匿名化的单份结论正文（不含 judge 名/模型名，避免仲裁者锚定到具体来源）。"""
    attrs = entry.get("attributes") or []
    attr_lines = "; ".join(
        f"{a.get('attribute_class')}={a.get('single_result')}"
        f"(EoICD:{a.get('eoicd_value')}/HLR:{a.get('hlr_value')})"
        for a in attrs
    ) or "无属性"
    return (f"verdict={entry.get('verdict')}, matched_hlr={entry.get('matched_hlr')}, "
            f"name_match={entry.get('name_match_status')}, identity={entry.get('hlr_identity')}, "
            f"属性=[{attr_lines}]")


def _render_group(entries: list[tuple[str, dict]], label: str, first: bool) -> str:
    """把一个视角（可能含同模型 N 份重复）渲染成一条匿名判断。entries = [(judge名, shard entry)]。"""
    no = ord(label) - 64
    scope = f"视角 {no}，仅 1 份" if len(entries) == 1 else f"视角 {no}，共 {len(entries)} 份同模型重复"
    head = f"判断 {label}（{scope}，初判参考）" if first else f"判断 {label}（{scope}）"
    lines = [head]
    for k, (_, entry) in enumerate(entries, 1):
        lines.append(f"  第{k}份 {_entry_body(entry)}")
    if len(entries) > 1:
        verdicts = {e.get("verdict") for _, e in entries}
        if len(verdicts) <= 1:
            lines.append(f"  注：这 {len(entries)} 份结论完全一致——重复不构成独立共识，证据强度按单一视角计。")
        else:
            lines.append(f"  注：这 {len(entries)} 份结论不完全一致——该模型对这路信号自身摇摆，稳定性存疑。")
    return "\n".join(lines)


def _build_review_user(full: str, groups: list[list[str]], shard: dict) -> str:
    """按实际独立视角数动态拼装复查 prompt（替代写死的 A/B/C 三槽模板）。"""
    parts = [_render_group([(j, shard[j]) for j in g], chr(65 + i), first=(i == 0))
             for i, g in enumerate(groups)]
    return "信号全名：{0}\n\n{1}\n\n请匿名化反思并输出最终 reconciled JSON。".format(
        full, "\n\n".join(parts))


# ---------------------------------------------------------------------------
# 合并工具：仲裁层对后端结论「合并而非覆盖」
# ---------------------------------------------------------------------------

def _merge_field_disagreements(backend_fd, llm_fd):
    """合并后端 KEY_FIELDS 分歧与 LLM 仲裁分歧。

    后端结论（含 `values` 真实冲突值）为基准，必须保留；
    LLM 仅补充/覆盖 `category` 与 `summary`，不丢弃后端 values；
    LLM 独有 field（语义层新发现的分歧）也纳入，但无 values（由 summary 说明）。
    """
    backend_by_field = {d.get("field"): dict(d) for d in (backend_fd or [])}
    llm_by_field = {d.get("field"): d for d in (llm_fd or [])}
    merged = []
    # 1) 先放后端条目（保 values），再用 LLM 覆盖 category/summary
    for field, bd in backend_by_field.items():
        entry = dict(bd)
        ld = llm_by_field.get(field)
        if ld:
            entry["category"] = ld.get("category", entry.get("category", "non_key"))
            if ld.get("summary"):
                entry["summary"] = ld["summary"]
        merged.append(entry)
    # 2) 再补 LLM 独有 field（无 values）
    for field, ld in llm_by_field.items():
        if field not in backend_by_field:
            merged.append({
                "field": field,
                "category": ld.get("category", "non_key"),
                "summary": ld.get("summary", ""),
            })
    return merged


def _ia_key(d):
    return (d.get("attribute_class") or d.get("field") or "", str(d.get("description", ""))[:40])


def _merge_inconsistent_attributes(backend_ia, llm_ia):
    """事实不一致：后端为准，LLM 可增补语义层事实（去重，不丢后端事实）。"""
    base = [dict(d) for d in (backend_ia or [])]
    seen = {_ia_key(d) for d in base}
    for d in (llm_ia or []):
        k = _ia_key(d)
        if k not in seen:
            base.append(dict(d))
            seen.add(k)
    return base


# ---------------------------------------------------------------------------
# 复核进度汇报（串行长阶段的可见性）
# ---------------------------------------------------------------------------

# Step5/Step5.5 是逐条串行 LLM 调用（数百条 × 单条十几秒，总时长可达小时级），
# 期间没有任何输出会被误判成卡死（实测：界面长时间无日志，用户把健康任务终止）。
# 每 PROGRESS_EVERY 条打一行进度；文案前缀固定，便于日志面板与排障时 grep。
PROGRESS_EVERY = 20


def _fmt_dur(sec: float) -> str:
    """秒 → 「XmYYs」/「XhYYm」，供进度行与预计剩余使用。"""
    sec = int(round(sec))
    if sec >= 3600:
        return "{0}h{1:02d}m".format(sec // 3600, (sec % 3600) // 60)
    return "{0}m{1:02d}s".format(sec // 60, sec % 60)


def _progress_line(tag: str, done: int, total: int, executed_word: str,
                   executed: int, skipped: int, failed: int, t0: float) -> None:
    """打一行复核进度（done>=total 时为收尾行，带总耗时）。"""
    parts = "{0} {1} / 跳过 {2} / 失败 {3}".format(executed_word, executed, skipped, failed)
    elapsed = time.monotonic() - t0
    if done >= total:
        print("  [{0}进度] {1}/{2} 完成（{3}；总用时 {4}）".format(
            tag, done, total, parts, _fmt_dur(elapsed)))
        return
    eta = elapsed / done * (total - done)
    print("  [{0}进度] {1}/{2}（{3}；已用 {4}，约剩 {5}）".format(
        tag, done, total, parts, _fmt_dur(elapsed), _fmt_dur(eta)))


def _review_progress(tag: str, total: int, executed_word: str):
    """返回 (start, tick, finish) 三个进度回调，供 arbitrate / peer_aware_review 使用。

    tick(kind) 的 kind ∈ {executed, skipped, failed}；累计到 PROGRESS_EVERY 的倍数
    打一行进度，loop 结束后 finish() 打收尾行（total==0 时不产生任何输出）。
    """
    t0 = time.monotonic()
    state = {"done": 0, "executed": 0, "skipped": 0, "failed": 0}

    def start() -> None:
        if total:
            print("  [{0}进度] 0/{1} 开始（串行执行，每 {2} 条汇报一次）".format(
                tag, total, PROGRESS_EVERY))

    def tick(kind: str) -> None:
        state["done"] += 1
        if kind == "executed":
            state["executed"] += 1
        elif kind == "failed":
            state["failed"] += 1
        else:
            state["skipped"] += 1
        if total and state["done"] % PROGRESS_EVERY == 0 and state["done"] < total:
            _progress_line(tag, state["done"], total, executed_word,
                           state["executed"], state["skipped"], state["failed"], t0)

    def finish() -> None:
        if total:
            _progress_line(tag, total, total, executed_word,
                           state["executed"], state["skipped"], state["failed"], t0)

    return start, tick, finish


# ---------------------------------------------------------------------------
# Step5 仲裁
# ---------------------------------------------------------------------------

def arbitrate(agg: dict, shards: dict, arb_cfg: dict, only: set[str] | None = None,
              stage: str = "step5") -> dict:
    """对 agg 中的信号跑 Step5 仲裁；only 限定信号名集合（Step5.5 重仲裁时用）。

    stage: trace 的阶段名（Step5.6 重仲裁传 "step5.6"，避免把复查结论盖成仲裁结论）。
    """
    model = arb_cfg.get("model")
    base_url = arb_cfg.get("base_url")
    api_key = os.environ.get(arb_cfg.get("api_key_env", ""), "")
    if not (model and base_url and api_key):
        print("  [仲裁] 未配置有效 arbitrator（model/base_url/api_key），跳过 Step5")
        return agg

    n_judges = len(shards)
    targets = [f for f in agg if (only is None or f in only)]
    prog_start, prog_tick, prog_finish = _review_progress(
        "仲裁" if stage == "step5" else "重仲裁", len(targets), "裁决")
    prog_start()
    for full in targets:
        item = agg[full]
        a = item["aggregation"]
        shard = {j: shards[j][full] for j in shards if full in shards[j]}
        if not shard:
            prog_tick("skipped")
            continue
        n_valid = sum(1 for jv in item["judges"].values() if jv.get("coverage") == "ok")

        # 单 judge / 0 存活：仲裁者无法升级共识，保留后端结论（降级优先于 LLM）。
        if n_valid <= 1:
            # 0 存活 provider 强制压星（反向 zero_provider 规则）
            if n_valid == 0:
                a["star_rating"] = 1
                a["agreement"] = "no_consensus"
                a["final_verdict"] = "待确认"
                a["need_review"] = True
            a["review_trace"] = {
                "stage": stage, "applied": False,
                "reason": "存活 judge 数 {0}，无第二裁决对象".format(n_valid),
            }
            a["arbitrated"] = True
            prog_tick("skipped")
            continue

        # 后端已完全共识（full）且无缺口/无需复核：无需 LLM 仲裁，保留后端结论。
        if a.get("agreement") == "full" and not a.get("need_review") and not a.get("coverage_gap"):
            a["review_trace"] = {"stage": stage, "applied": False,
                                 "reason": "后端已完全共识且无需复核，跳过 Step5"}
            a["arbitrated"] = True
            prog_tick("skipped")
            continue

        # 成本护栏（路线三）：超出复核预算的本次不跑，如实记账而非静默跳过。
        cell = arb_cfg.get("_review_budget")
        if cell is not None and cell["left"] <= 0:
            a["review_trace"] = {
                "stage": stage, "applied": False,
                "reason": "超出复核预算（MULTI_JUDGE_REVIEW_BUDGET），本次未调用 LLM",
            }
            a["arbitrated"] = True
            prog_tick("skipped")
            continue
        if cell is not None:
            cell["left"] -= 1

        user = _build_consensus_user(full, shard, a.get("note", ""))
        out = call_llm(model, base_url, api_key, CONSENSUS_SYSTEM, user)
        if not isinstance(out, dict):
            a["review_trace"] = {"stage": stage, "applied": False,
                                 "reason": "Step5 仲裁 LLM 调用失败或返回非 JSON"}
            a["arbitrated"] = True
            prog_tick("failed")
            continue
        # 共识等级 / 结论：以 LLM 为准（仲裁者职责）
        agreement = out.get("agreement_level") or a.get("agreement")
        final_verdict = out.get("final_verdict") or a.get("final_verdict")

        # field_disagreements：合并而非覆盖——后端 values 为基准，LLM 仅补 category/summary
        merged_fd = _merge_field_disagreements(
            a.get("field_disagreements"), out.get("field_disagreements")
        )
        # KEY_FIELDS 二分：合并后 key 类分歧才触发降级
        has_key = any(d.get("category") == "key" for d in merged_fd)
        star = map_star(agreement, has_key)
        need_review = (star <= 2) or bool(a.get("need_review"))

        a["agreement"] = agreement
        a["star_rating"] = star
        a["final_verdict"] = final_verdict
        # 事实不一致：后端事实不丢，LLM 可增补（去重）
        a["inconsistent_attributes"] = _merge_inconsistent_attributes(
            a.get("inconsistent_attributes"), out.get("inconsistent_attributes")
        )
        a["field_disagreements"] = merged_fd
        a["final_analysis"] = out.get("final_analysis", "")
        a["confidence"] = out.get("confidence")
        a["need_review"] = need_review
        # 保留已写入的 peer_review 子字段：重仲裁(stage=step5.6)生效不代表复查也生效，
        # 直接整体赋值会把「复查因独立视角不足被跳过」这条事实抹掉。
        a["review_trace"] = {
            "stage": stage, "applied": True,
            "reason": "仲裁已生效（对全部 judge 结论做第二次裁决，stage={0}）".format(stage),
            "peer_review": (a.get("review_trace") or {}).get("peer_review"),
        }
        a["arbitrated"] = True
        prog_tick("executed")
    prog_finish()
    return agg


# ---------------------------------------------------------------------------
# Step5.5 peer-aware 复查（匿名化 A/B/C 重判）
# ---------------------------------------------------------------------------

def _low_star_signals(agg: dict) -> list[str]:
    """本轮需要复查的低星信号（≤2★）。与 Step5 的 low 选取口径一致。"""
    return [f for f, it in agg.items() if it["aggregation"].get("star_rating", 5) <= 2]


def peer_aware_review(agg: dict, shards: dict, arb_cfg: dict,
                      judge_meta: dict | None = None) -> dict:
    """对低星级信号做匿名化复查，返回更新后的 agg。

    #10 精简签名：低星选取由本函数自行按 star_rating 计算，调用方不再传 low_signals。

    #9 修复：匿名化按「独立视角」分组（同 model/base_url 的重复 judge 合并为一个判断并注明份数），
    不再把同一 judge 复制成「判断 C」充当假的第二方；独立视角不足 2 个时跳过且不记为已复查。
    """
    model = arb_cfg.get("model")
    base_url = arb_cfg.get("base_url")
    api_key = os.environ.get(arb_cfg.get("api_key_env", ""), "")
    if not (model and base_url and api_key):
        return agg

    judge_meta = judge_meta or {}

    def _mark_skipped(a: dict, reason: str, n_view: int, groups: list[list[str]] | None = None) -> None:
        """复查未生效时如实记账（不得留 peer_reviewed=True 的假象）。"""
        a["peer_reviewed"] = False
        # 记 peer_review 子字段：Step5.6 重仲裁会刷新 trace 的 stage/applied，
        # 但「复查到底生效没有」必须留痕，否则记账会把复查跳过误记成复核生效。
        a["review_trace"] = {"stage": "step5.5", "applied": False, "reason": reason,
                             "peer_review": {"applied": False, "reason": reason}}
        a["peer_review_info"] = {
            "applied": False,
            "reason": reason,
            "independent_views": n_view,
            "groups": groups or [],
        }

    low = _low_star_signals(agg)
    prog_start, prog_tick, prog_finish = _review_progress("复查", len(low), "复查")
    prog_start()
    for full in low:
        item = agg.get(full)
        if item is None:
            prog_tick("skipped")
            continue
        shard = {j: shards[j][full] for j in shards if full in shards[j]}
        names = list(shard.keys())
        groups = _independent_groups(names, judge_meta)
        n_view = len(groups)
        if n_view < 2:
            _mark_skipped(item["aggregation"],
                          "独立视角数 {0} < 2（同模型重复不构成独立视角），跳过 peer 复查".format(n_view)
                          if n_view >= 1 else "无可用 judge 结论，跳过 peer 复查",
                          n_view, groups)
            prog_tick("skipped")
            continue
        cell = arb_cfg.get("_review_budget")
        if cell is not None and cell["left"] <= 0:
            _mark_skipped(item["aggregation"],
                          "超出复核预算（MULTI_JUDGE_REVIEW_BUDGET），跳过 peer 复查",
                          n_view, groups)
            prog_tick("skipped")
            continue
        if cell is not None:
            cell["left"] -= 1
        user = _build_review_user(full, groups, shard)
        out = call_llm(model, base_url, api_key, REVIEW_SYSTEM, user)
        if not isinstance(out, dict):
            _mark_skipped(item["aggregation"], "复查 LLM 调用失败或返回非 JSON", n_view, groups)
            prog_tick("failed")
            continue
        a = item["aggregation"]
        # 复查结论覆盖 final_verdict / matched_hlr / 属性结论 / analysis / confidence
        if out.get("final_verdict"):
            a["final_verdict"] = out["final_verdict"]
        if isinstance(out.get("final_matched_hlr"), list):
            a["identity_consensus"] = f"{out['final_verdict']}→" + ",".join(out["final_matched_hlr"])
        reconciled = {r.get("attribute_class"): r for r in (out.get("attribute_reconciled") or [])}
        for d in a.get("structured_diff", []):
            cls = d.get("attribute_class")
            if cls in reconciled:
                d["diff_type"] = reconciled[cls].get("single_result", d["diff_type"])
        if out.get("analysis"):
            a["final_analysis"] = out["analysis"]
        if out.get("confidence") is not None:
            a["confidence"] = out["confidence"]
        a["peer_reviewed"] = True
        a["review_trace"] = {
            "stage": "step5.5", "applied": True,
            "reason": "对 {0} 个独立视角做匿名化复查".format(n_view),
            "peer_review": {"applied": True, "reason": "对 {0} 个独立视角做匿名化复查".format(n_view)},
        }
        a["peer_review_info"] = {
            "applied": True,
            "independent_views": n_view,
            "judges_by_view": [{"view": chr(65 + i), "judges": g} for i, g in enumerate(groups)],
            "reason": "对 {0} 个独立视角做匿名化复查".format(n_view),
        }
        prog_tick("executed")
    prog_finish()
    return agg


# ---------------------------------------------------------------------------
# 编排：Step5 + (Step5.5 循环 + Step5.6 重仲裁)
# ---------------------------------------------------------------------------

def run_arbitration(agg: dict, shards: dict, judge_meta: dict, arb_cfg: dict) -> dict:
    """完整智能层：Step5 仲裁 →（可选）Step5.5 复查循环 → 重仲裁。

    #10 精简签名：复查配置并入 arb_cfg 的 "re_review" 子键（`run_review_route` 负责组装），
    不再单独传 re_review_cfg——两者都是同一次复核路由的上下文，拆成两个位置参数没有意义。
    """
    if not arb_cfg:
        return agg
    re_review_cfg = arb_cfg.get("re_review") or {}
    agg = arbitrate(agg, shards, arb_cfg)
    if not re_review_cfg.get("enabled", False):
        return agg
    max_rounds = int(re_review_cfg.get("max_rounds", 1))
    for _ in range(max_rounds):
        low = _low_star_signals(agg)
        if not low:
            break
        agg = peer_aware_review(agg, shards, arb_cfg, judge_meta)
        # Step5.6 仅对低星重仲裁；stage 传 step5.6，避免把复查结论盖成仲裁结论（记账失真）
        agg = arbitrate(agg, shards, arb_cfg, only=set(low), stage="step5.6")
    return agg


# ---------------------------------------------------------------------------
# 离线自测（不依赖 LLM）
# ---------------------------------------------------------------------------

def _self_test() -> None:
    # 1) map_star 对照反向星级表
    assert map_star("full", False) == 5
    assert map_star("full", True) == 4
    assert map_star("majority", False) == 3
    assert map_star("majority", True) == 2
    assert map_star("split", False) == 1
    assert map_star("single_source", False) == 1

    # 2) 0 存活降级路径（通过 arbitrate 注入假 call_llm）
    fake = {
        "agreement_level": "full", "final_verdict": "已落实",
        "final_matched_hlr": ["R1"], "inconsistent_attributes": [],
        "field_disagreements": [{"field": "verdict", "category": "key", "summary": "x"}],
        "final_analysis": "ok", "confidence": 0.9,
    }
    import multi_judge_aggregate as agg_mod
    shards = {"j1": {"S1": {"verdict": "已落实", "matched_hlr": ["R1"], "name_match_status": True,
                            "hlr_identity": None, "coverage": "ok", "attributes": []}}}
    agg = agg_mod.aggregate(shards, {"j1": {}})
    # 单 judge 配置：后端给 5★（正常路径，#1 退化兼容），仲裁器不覆盖 LLM 的 full（降级优先）
    global call_llm
    orig = call_llm
    os.environ["X"] = "dummy-key"  # 通过 guard，call_llm 已被 mock 替换
    call_llm = lambda *a, **k: fake  # noqa: E731
    try:
        agg2 = arbitrate(agg, shards, {"model": "m", "base_url": "u", "api_key_env": "X"})
        # 多 judge 配置但仅 1 存活（降级）：后端压 1★，仲裁器仍保留（不升级）
        agg_deg = agg_mod.aggregate({"j1": shards["j1"], "j2": {}},
                                    {"j1": {}, "j2": {}}, {}, n_configured=2)
        agg_deg2 = arbitrate(agg_deg, {"j1": shards["j1"], "j2": {}},
                             {"model": "m", "base_url": "u", "api_key_env": "X"})
    finally:
        call_llm = orig
        os.environ.pop("X", None)
    # 单 judge 配置 → single_source + 5★（正常路径，仲裁器不升级）
    assert agg2["S1"]["aggregation"]["agreement"] == "single_source"
    assert agg2["S1"]["aggregation"]["star_rating"] == 5
    assert agg2["S1"]["aggregation"]["arbitrated"] is True
    # 多 judge 仅 1 存活（降级）→ 1★ 且需复核（仲裁器不升级）
    assert agg_deg2["S1"]["aggregation"]["star_rating"] == 1
    assert agg_deg2["S1"]["aggregation"]["need_review"] is True

    # 2b) #3 合并式 field_disagreements：后端 values 必须保留，LLM 仅补 category/summary
    sh = {
        "j1": {"S1": {"verdict": "已落实", "matched_hlr": ["R1"], "name_match_status": True,
                      "hlr_identity": {"bus": "A"}, "coverage": "ok",
                      "attributes": [{"attribute_class": "刷新率", "single_result": "属性不一致",
                                     "eoicd_value": "60", "hlr_value": "30"}]}},
        "j2": {"S1": {"verdict": "不一致", "matched_hlr": ["R2"], "name_match_status": True,
                      "hlr_identity": {"bus": "A"}, "coverage": "ok",
                      "attributes": [{"attribute_class": "刷新率", "single_result": "属性一致",
                                     "eoicd_value": "30", "hlr_value": "30"}]}},
    }
    meta = {"j1": {"model": "m", "base_url": "u", "key_hash": "k", "frozen": True},
            "j2": {"model": "m", "base_url": "u", "key_hash": "k", "frozen": True}}
    agg3 = agg_mod.aggregate(sh, meta, {}, n_configured=2)
    fd_before = {d["field"]: d for d in agg3["S1"]["aggregation"]["field_disagreements"]}
    assert "matched_hlr" in fd_before and "verdict" in fd_before and any(
        f.startswith("attr:") for f in fd_before), "后端应已产出含 values 的分歧"
    # LLM 只回 verdict 一个字段（降为非 key + 加 summary），故意遗漏 matched_hlr/attr
    fake3 = {
        "agreement_level": "majority", "final_verdict": "不一致",
        "inconsistent_attributes": [{"attribute_class": "分辨率", "description": "LLM 新发现的事实不一致"}],
        "field_disagreements": [{"field": "verdict", "category": "non_key", "summary": "语义同属未落实类"}],
        "final_analysis": "merged", "confidence": 0.8,
    }
    orig3 = call_llm
    os.environ["X"] = "dummy-key"
    call_llm = lambda *a, **k: fake3  # noqa: E731
    try:
        agg3r = arbitrate(agg3, sh, {"model": "m", "base_url": "u", "api_key_env": "X"})
    finally:
        call_llm = orig3
        os.environ.pop("X", None)
    fd_after = {d["field"]: d for d in agg3r["S1"]["aggregation"]["field_disagreements"]}
    # 1) 后端 matched_hlr 仍保留（LLM 没提但不得丢）
    assert "matched_hlr" in fd_after, "合并后不得丢失后端 matched_hlr 分歧"
    assert "values" in fd_after["matched_hlr"], "后端 values 必须保留"
    # 2) verdict 被 LLM 覆盖 category + 注入 summary，values 仍在
    assert fd_after["verdict"]["category"] == "non_key"
    assert fd_after["verdict"]["summary"] == "语义同属未落实类"
    assert fd_after["verdict"]["values"], "verdict 的 values 必须保留"
    # 3) attr 分歧（后端有）合并后仍保留
    assert any(f.startswith("attr:") for f in fd_after), "attr 分歧不得丢失"
    # 4) 事实不一致：后端 + LLM 合并去重（=2 项）
    assert len(agg3r["S1"]["aggregation"]["inconsistent_attributes"]) == 2, "事实应合并去重"
    print("✅ #3 合并式 field_disagreements / inconsistent_attributes 通过")

    # 3) #9 peer 复查：按独立视角分组 + 动态 prompt（不再有克隆 C）
    def _e9(v: str) -> dict:
        return {"verdict": v, "matched_hlr": ["R1"], "name_match_status": True,
                "hlr_identity": {"bus": "1"}, "coverage": "ok",
                "attributes": [{"attribute_class": "周期", "single_result": "属性不一致",
                                "eoicd_value": "1", "hlr_value": "2"}]}

    shards9 = {"judge_ds": _e9("不一致"), "judge_ds_stability": _e9("不一致"), "judge_qwen": _e9("已落实")}
    meta9 = {
        "judge_ds": {"model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"},
        "judge_ds_stability": {"model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"},
        "judge_qwen": {"model": "qwen-max", "base_url": "https://dashscope.aliyuncs.com/v1"},
    }
    # 3a) 同 model/base_url 的 judge 合并为一个视角，异模型各自成组
    g9 = _independent_groups(list(shards9), meta9)
    assert [len(x) for x in g9] == [2, 1], g9
    # meta 缺失时退化为逐个独立视角
    assert [len(x) for x in _independent_groups(["j1", "j2"], {})] == [1, 1]

    # 3b) prompt 按实际视角数拼装：只有 A/B，绝不出现克隆的「判断 C」
    u9 = _build_review_user("S3", g9, shards9)
    assert "判断 A" in u9 and "判断 B" in u9, u9
    assert "判断 C" not in u9, "不得出现克隆的第三个判断（#9）"
    assert "共 2 份同模型重复" in u9, u9
    assert "重复不构成独立共识" in u9, u9

    # 3c) 独立视角 < 2（两个 judge 同模型）：跳过复查，且如实记 False（不留「已复查」假象）
    # verdict 两派不同 → split 1★，确保落在复查的选取口径内（star<=2）。
    sh_same = {"a": {"S3": _e9("不一致")}, "b": {"S3": _e9("已落实")}}
    meta_same = {"a": meta9["judge_ds"], "b": meta9["judge_ds_stability"]}
    agg_same = agg_mod.aggregate(sh_same, meta_same, {}, n_configured=2)
    orig9 = call_llm
    os.environ["AR9"] = "dummy-key"
    call_llm = lambda *a, **k: {"final_verdict": "已落实", "final_matched_hlr": ["R9"],
                                "attribute_reconciled": [], "analysis": "x", "confidence": 0.9}
    try:
        out_same = peer_aware_review(agg_same, sh_same,
                                     {"model": "m", "base_url": "u", "api_key_env": "AR9"},
                                     meta_same)
        # 3d) 正常路径：3 个 judge / 2 个独立视角 → 复查生效并记账
        sh_ok = {"a": {"S3": _e9("不一致")},
                 "b": {"S3": _e9("不一致")},
                 "c": {"S3": _e9("已落实")}}
        meta_ok = {"a": meta9["judge_ds"],
                   "b": meta9["judge_ds_stability"],
                   "c": meta9["judge_qwen"]}
        agg_ok = agg_mod.aggregate(sh_ok, meta_ok, {}, n_configured=3)
        out_ok = peer_aware_review(agg_ok, sh_ok,
                                   {"model": "m", "base_url": "u", "api_key_env": "AR9"},
                                   meta_ok)
    finally:
        call_llm = orig9
        os.environ.pop("AR9", None)

    a_same = out_same["S3"]["aggregation"]
    assert a_same["peer_reviewed"] is False
    assert "独立视角数 1" in a_same["peer_review_info"]["reason"], a_same["peer_review_info"]
    a_ok = out_ok["S3"]["aggregation"]
    assert a_ok["peer_reviewed"] is True
    assert a_ok["peer_review_info"]["independent_views"] == 2, a_ok["peer_review_info"]
    assert a_ok["peer_review_info"]["judges_by_view"][0]["judges"] == ["a", "b"]

    # 3e) #10 精简签名：复查配置并入 arb_cfg["re_review"]，4 参数即可跑完整链路
    orig10 = call_llm
    os.environ["AR10"] = "dummy-key"
    call_llm = lambda *a, **k: {"final_verdict": "不一致", "final_matched_hlr": ["R1"],
                                "attribute_reconciled": [], "analysis": "x", "confidence": 0.9}
    try:
        cfg10 = {"model": "m", "base_url": "u", "api_key_env": "AR10",
                 "re_review": {"enabled": True, "max_rounds": 1}}
        out10 = run_arbitration(agg_mod.aggregate(sh_ok, meta_ok, {}, n_configured=3),
                                sh_ok, meta_ok, cfg10)
    finally:
        call_llm = orig10
        os.environ.pop("AR10", None)
    a10 = out10["S3"]["aggregation"]
    # 复查确实跑过（2 个独立视角），且 Step5.6 重仲裁后复查留痕不被抹掉
    assert a10["peer_review_info"]["independent_views"] == 2, a10["peer_review_info"]
    assert (a10.get("review_trace") or {}).get("peer_review", {}).get("applied") is True, a10["review_trace"]

    blk = _build_consensus_user("S2", {"j1": shards9["judge_ds"]}, "note")
    assert "S2" in blk and "j1" in blk

    print("✅ #9 独立视角分组匿名化复查通过（无克隆 C / 同模型合并 / 视角不足跳过并记账）")
    print("✅ #10 run_arbitration 精简签名通过（4 参数；re_review 并入 arb_cfg 照常驱动复查链路）")


if __name__ == "__main__":
    _self_test()
