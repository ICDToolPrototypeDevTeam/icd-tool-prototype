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
import threading
import urllib.request
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from multi_judge_aggregate import (KEY_FIELDS_IDENTITY, _consensus_label,
                                 _display_matched_full, build_analysis_summary,
                                 derive_sub_classification, needs_review)

# 关键字段白名单（KEY_FIELDS，身份层 judge 结论骨架）—— 分歧即真分歧。
# 单一来源：直接复用 multi_judge_aggregate.KEY_FIELDS_IDENTITY（身份层 7 元组，
# verdict+matched_hlr+name_match_status+地址四元组），仲裁层在此之上追加
# attribute_class（各被比对属性的结论字段），喂给仲裁 LLM 打 category='key'。
FORWARD_KEY_FIELDS = KEY_FIELDS_IDENTITY + ("attribute_class",)


# ---------------------------------------------------------------------------
# Token 用量采集（仲裁进程内；runner 结束时经 get_token_total() 汇总）
# ---------------------------------------------------------------------------
# 每次成功响应后打印增量标记 [tokens] delta=N（由父进程实时汇总），并由
# runner 用 get_token_total() 写入总结。只对正增量打印，MOCK/无 usage 不刷屏。
_TOKEN_TOTAL = 0
_TOKEN_LOCK = threading.Lock()


def _extract_total_tokens(usage) -> int:
    """从 OpenAI 兼容的 usage 提取总 token；无效值返回 0。"""
    if not isinstance(usage, dict):
        return 0

    def _int(value) -> int:
        if value is None or isinstance(value, bool):
            return 0
        try:
            number = int(float(value)) if isinstance(value, str) else int(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        return max(0, number)

    total = _int(usage.get("total_tokens"))
    if total > 0:
        return total
    for prompt_key, completion_key in (
        ("prompt_tokens", "completion_tokens"),
        ("input_tokens", "output_tokens"),
    ):
        if prompt_key in usage or completion_key in usage:
            summed = _int(usage.get(prompt_key)) + _int(usage.get(completion_key))
            if summed > 0:
                return summed
    return total


def record_token_usage(usage) -> int:
    """累加一次仲裁调用的 token 并打印增量标记；绝不抛异常。"""
    try:
        delta = _extract_total_tokens(usage)
        if delta <= 0:
            return 0
        global _TOKEN_TOTAL
        with _TOKEN_LOCK:
            _TOKEN_TOTAL += delta
        print(f"[tokens] delta={delta}", flush=True)
        return delta
    except Exception:  # noqa: BLE001 — 统计是旁路，绝不影响模型调用
        return 0


def get_token_total() -> int:
    """返回本进程仲裁调用累计 token。"""
    with _TOKEN_LOCK:
        return _TOKEN_TOTAL


def reset_token_usage() -> None:
    """复位本进程累计值（runner 每轮开始时调用 / 单测用）。"""
    global _TOKEN_TOTAL
    with _TOKEN_LOCK:
        _TOKEN_TOTAL = 0


class _CircuitBreaker:
    """复核路由熔断器（第2点优化）：LLM 调用累计失败达阈值即整体跳闸，跳过剩余 needed，
    避免上游服务挂掉时每条信号逐个撞超时、把复核路由拖成几十分钟甚至数小时。

    串行下等价于「连续失败 N 次」；并发下为「累计失败 N 次」（用 Lock 保护计数）。
    任意一次成功即清零计数，允许偶发抖动后自动恢复。

    阈值默认 15（2026-10-15 由 5 调大）：实测 4 次 TimeoutError + 1 次 JSON 解析噪声即
    恰好踩到阈值 5，导致 270 条待复核信号一次性全被跳过（executed=0）。瞬时抖动/端点排队
    不应触发全局止损；15 次累计失败才判定为「端点系统性故障」。
    """
    def __init__(self, threshold: int = 15):
        self.threshold = max(1, int(threshold))
        self._failures = 0
        self._tripped = False
        self._trip_reason = ""
        self._lock = threading.Lock()

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.threshold:
                self._tripped = True
                if not self._trip_reason:
                    self._trip_reason = "累计 LLM 失败达阈值 {0} 次".format(self.threshold)

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0

    def trip(self, reason: str) -> None:
        """立即跳闸（永久性全局错误：欠费 / 鉴权失效 / 模型不存在）。

        与「累计失败达阈值」不同：这类错误**重试无意义**（请求本身不合法，或账户
        根本不可用），再撞下去只会白白发请求、把整批拖到超时。2026-10-29 实测：
        仲裁 key 欠费导致服务端对每条请求瞬时返回 HTTP 400（Arrearage），原逻辑
        仍按「可重试」处理，372 条信号各撞 3 次，直到最后两分钟才凑够熔断阈值。
        """
        with self._lock:
            self._tripped = True
            self._trip_reason = reason or "永久性错误"

    @property
    def tripped(self) -> bool:
        return self._tripped

    @property
    def trip_reason(self) -> str:
        with self._lock:
            return self._trip_reason


# ---------------------------------------------------------------------------
# LLM 调用失败落盘（纯诊断，零行为影响）
# ---------------------------------------------------------------------------
# 目的：call_llm 失败时把「为什么失败」记入 JSON Lines，供事后判断
#   ① 超时是「同一时刻集中爆发」（端点限流/排队特征）还是「零星散布」（链路抖动特征）；
#   ② 是否集中在少数「prompt 特别大」的信号上（输入侧问题）。
# 记录范围：**每一次尝试失败都记**（`final: false` = 中间失败、随后会重试；`final: true` = 重试耗尽）。
# 这样即使该次调用最终成功，也能统计「尝试失败率 / 失败时刻分布」，而不只有最终失败。
# 纯追加日志，不改返回值/重试次数/熔断逻辑；落盘自身异常一律吞掉，绝不影响主流程。
_FAIL_LOG_LOCK = threading.Lock()
_FAIL_LOG_STATE = {"written": 0}
_FAIL_LOG_MAX = 2000         # 最多记录条数（防端点系统性故障时磁盘暴涨；超出后静默不再记）
                             # 2026-10-15 由 200 提到 2000：改为「每次尝试失败」都记（含随后重试成功的），
                             # 单轮日志量放大（294 信号 × 最多 3 次尝试 ≈ 900 条），故放宽上限（约 2MB）
_FAIL_LOG_SNIP = 500         # 原始响应对头/尾各截取的字符数


def _arb_fail_log_path() -> Path:
    """落盘路径：<项目根>/runs/integration_workspace/logs/arb_fail.jsonl。
    放 runs 内部工作区，不进 output/ 交付物（不污染交付、业务数据不外泄）。"""
    return (Path(__file__).resolve().parents[1]
            / "runs" / "integration_workspace" / "logs" / "arb_fail.jsonl")


def _snip(s, n: int = _FAIL_LOG_SNIP):
    """取字符串对头/尾各 n 字符；未超长则原样返回。"""
    if not s:
        return None
    s = str(s)
    if len(s) <= 2 * n:
        return s
    return s[:n] + " ……<snip>…… " + s[-n:]


# ---------------------------------------------------------------------------
# HTTP 错误分类（2026-10-29）
# ---------------------------------------------------------------------------
# 为什么要有分类：原实现把 400 与 5xx 一视同仁地退避重试，实测暴露两个问题——
#   ① 400(Arrearage 欠费) 是**全局性永久错误**：重试无意义（请求不合法/账户不可用），
#      且后续每一条信号都会撞同样的墙 → 应立即止损，而不是 372 条各撞 3 次；
#   ② HTTPError 的响应体不被读取，原因被丢进黑洞（只剩 "HTTP Error 400: Bad Request"），
#      导致「欠费」这个关键原因排查了很久才定位。

# 判为「全局性永久错误」的响应体特征（大小写不敏感）
_FATAL_BODY_PATTERNS = (
    "arrearage",          # 阿里云百炼：账户欠费 / 账户状态异常
    "overdue",
    "insufficient",
    "access denied",
    "model_not_found",
    "model not exist",
    "invalid_api_key",
    "unauthorized",
)


def _classify_http_error(code, body: str | None) -> str:
    """把 HTTP 错误分成三类，决定「是否重试」与「是否立即止损」。

    - "fatal"    全局性永久错误：欠费 / 鉴权失效 / 模型不存在。重试无意义，且后续
                 每条信号都会撞同样的墙 → 不重试，并立即跳闸止损。
    - "request"  单条请求本身的问题（其余 400）：只影响这一条 → 不重试（省下无效
                 请求），但不跳闸，其余信号照常处理。
    - "transient" 瞬时 / 服务端问题（5xx 等）：维持原有退避重试。
    """
    try:
        c = int(code or 0)
    except (TypeError, ValueError):
        c = 0
    low = (body or "").lower()
    if c in (401, 403, 404):
        return "fatal"
    if any(p in low for p in _FATAL_BODY_PATTERNS):
        return "fatal"
    if c == 400:
        return "request"
    return "transient"


def _err_brief(body: str | None, limit: int = 160) -> str:
    """从错误响应体里挤出一句人能读的短原因（优先取 error.message，其次 error.type）。"""
    if not body:
        return ""
    try:
        data = json.loads(body)
        err = (data or {}).get("error") or {}
        msg = err.get("message") or err.get("type") or ""
        if msg:
            return str(msg)[:limit]
    except Exception:
        pass
    return str(body)[:limit]


def _record_llm_failure(record: dict) -> None:
    """追加一条失败记录到 JSONL（加锁 + 限量 + 截断）；任何异常静默忽略。"""
    try:
        with _FAIL_LOG_LOCK:
            if _FAIL_LOG_STATE["written"] >= _FAIL_LOG_MAX:
                return
            path = _arb_fail_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            _FAIL_LOG_STATE["written"] += 1
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 最小 OpenAI 兼容 LLM 客户端（标准库 urllib，无第三方依赖）
# ---------------------------------------------------------------------------

def call_llm(model: str, base_url: str, api_key: str, system: str, user: str,
             temperature: float = 0.1, timeout: int | None = None,
             max_retries: int | None = None, ctx: dict | None = None) -> dict | None:
    """调用 OpenAI 兼容 /chat/completions，要求 JSON 输出，返回解析后的 dict；失败返回 None。

    timeout 默认从 env MULTI_JUDGE_REVIEW_TIMEOUT 读取（默认 60s），避免单条调用卡满 180s；
    调用方可显式传参覆盖（per-signal 超时可控）。
    max_retries 默认从 env MULTI_JUDGE_REVIEW_RETRIES 读取（默认 2），在瞬时网络/代理抖动
    （连接重置、远程断开、DNS 失败、超时、5xx、429）时指数退避重试（1s/2s…），避免单次抖动
    即触发上层熔断器、导致整批 249 条全部跳过。注意：401/403 等鉴权/额度错误为永久错误，
    不重试（重试无意义，且会无意义消耗重试额度）。

    ctx（可选）：诊断上下文，如 {"full": 信号全名, "stage": "step5|step5.5"}；
    仅用于失败落盘（_record_llm_failure），不影响请求内容与返回值。
    """
    if timeout is None:
        timeout = int(os.environ.get("MULTI_JUDGE_REVIEW_TIMEOUT", "60"))
    if max_retries is None:
        max_retries = int(os.environ.get("MULTI_JUDGE_REVIEW_RETRIES", "2"))
    if not (model and base_url and api_key):
        return None
    ctx = ctx or {}
    _t0 = time.time()

    def _fail(err_name: str, err_msg, attempt: int, http_code=None,
              raw_body=None, raw_content=None, final: bool = True) -> None:
        """落盘一条失败记录（诊断用）。

        final=True  = 重试耗尽后的最终失败（该次调用作废）；
        final=False = 中间某次尝试失败、随后仍会重试（用于统计失败率与时间分布）。
        """
        _record_llm_failure({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "final": final,                            # 是否最终失败（False=中间失败、后有重试）
            "full": ctx.get("full"),
            "stage": ctx.get("stage"),
            "model": model,
            "base_url": base_url,
            "err": err_name,
            "err_msg": str(err_msg)[:500],
            "attempt": attempt,                        # 第几次尝试失败（1 = 首次）
            "max_attempts": max_retries + 1,           # 总尝试次数上限（= 首次 + max_retries）
            "elapsed_s": round(time.time() - _t0, 1),  # 进入函数→本次失败的总耗时
            "timeout": timeout,
            "system_chars": len(system or ""),
            "user_chars": len(user or ""),
            "prompt_chars": len(system or "") + len(user or ""),  # 判断是否「大 prompt 信号」
            "http_code": http_code,
            "resp_body": _snip(raw_body),              # 信封原文（若已读到）
            "resp_content": _snip(raw_content),        # 模型 content 原文（解析失败时最有价值）
        })

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
    raw_body = None      # 最近一次成功读到的 HTTP 响应原文
    raw_content = None   # 最近一次取到的模型 content 原文（json 解析失败时供取证）
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_body = resp.read().decode("utf-8")
            data = json.loads(raw_body)
            # 先计数再取 content：解析成功即是一次真实计费调用
            record_token_usage(data.get("usage"))
            raw_content = data["choices"][0]["message"]["content"]
            return json.loads(raw_content)
        except urllib.error.HTTPError as e:
            code = getattr(e, "code", None)
            # HTTPError 的响应体要单独读（urllib 不会自动读它）——不读就只剩
            # "HTTP Error 400: Bad Request"，把「欠费 / 模型不存在」这类真因丢掉
            # （2026-10-29 实测：因未记录响应体，欠费这一关键原因排查了很久）。
            err_body = None
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            kind = _classify_http_error(code, err_body)
            if kind == "fatal":
                # 全局性永久错误：重试无意义，且后续每条信号都会撞同样的墙
                # → 立即止损（跳闸），不再白白发请求把整批拖到超时。
                brief = _err_brief(err_body)
                reason = "永久性错误（HTTP {0}）：{1}".format(code, brief or "服务不可用")
                _fail("HTTPError", e, attempt + 1, http_code=code, raw_body=err_body)
                _bre = ctx.get("_breaker")
                if _bre is not None:
                    try:
                        _bre.trip(reason)
                    except Exception:
                        pass
                ctx["_perm_error"] = reason
                print(f"  [仲裁] LLM 调用失败（HTTP {code}，永久性错误）：{brief or '服务不可用'}")
                print("  [仲裁] 立即停止本轮智能层调用（后续信号不再重复发送请求），保留后端聚合结论")
                return None
            if kind == "request":
                # 单条请求本身的问题（其余 400）：重试纯属浪费 → 不重试；但不跳闸，
                # 其余信号照常处理。
                brief = _err_brief(err_body)
                _fail("HTTPError", e, attempt + 1, http_code=code, raw_body=err_body)
                print(f"  [仲裁] LLM 调用失败（HTTP {code}，请求被拒，不重试）"
                      + ("：{0}".format(brief) if brief else ""))
                return None
            # transient（5xx 等）：维持原有退避重试
            if attempt < max_retries:
                wait = 2 ** attempt
                print(f"  [仲裁] LLM 调用失败（HTTP {getattr(e, 'code', '?')}），"
                      f"{wait}s 后重试（{attempt + 1}/{max_retries}）")
                # 中间失败也落盘（final=False）：即便随后重试成功，也要能统计失败率与时间分布
                _fail("HTTPError", e, attempt + 1, http_code=code,
                      raw_body=err_body, final=False)
                time.sleep(wait)
                continue
            print(f"  [仲裁] LLM 调用失败（HTTP {getattr(e, 'code', '?')}），"
                  f"重试耗尽，跳过智能层，保留后端聚合")
            _fail("HTTPError", e, attempt + 1, http_code=code, raw_body=err_body)
            return None
        except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError) as e:
            # 连接重置 / 远程断开 / DNS 失败 / 超时 / 解析失败 → 退避重试
            if attempt < max_retries:
                wait = 2 ** attempt
                print(f"  [仲裁] LLM 调用失败（{type(e).__name__}: {e}），"
                      f"{wait}s 后重试（{attempt + 1}/{max_retries}）")
                # 中间失败也落盘（final=False）：即便随后重试成功，也要能统计失败率与时间分布
                _fail(type(e).__name__, e, attempt + 1,
                      raw_body=raw_body, raw_content=raw_content, final=False)
                time.sleep(wait)
                continue
            print(f"  [仲裁] LLM 调用失败（{type(e).__name__}: {e}），"
                  f"重试耗尽，跳过智能层，保留后端聚合")
            # raw_content 非空 ⇒ 是「模型 content 解析失败」（如 Extra data）；
            # raw_content 为空 ⇒ 是「连接/超时/信封解析失败」。二者据此可区分。
            _fail(type(e).__name__, e, attempt + 1,
                  raw_body=raw_body, raw_content=raw_content)
            return None
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

关键字段（KEY_FIELDS，身份层 judge 结论骨架，由 FORWARD_KEY_FIELDS 单一来源驱动）：
__KEY_FIELDS_LIST__
仅当 judge 间在上述关键字段上出现分歧时，field_disagreements 的 category 才标 'key'；
若仅在次要字段（属性取值文本、自然语言分析、单位说明）上分歧，标 'non_key'。

【inconsistent_attributes 填写约束（务必遵守）】
attribute_class 只能填「属性比对」中真实出现的属性类名（如 RP.SDIExpected、LogicalPort.SamplePeriod 等
EoICD/HLR 属性清单里的名字）。严禁把下列内容写进本字段：
  - 身份层字段（bus / label / bit / direction）——身份层分歧请写进 field_disagreements；
  - 自造的描述性类别（如 "属性覆盖完整性"、"属性核验"、"身份属性"、"属性覆盖情况" 等）。
若某属性只是"证据不足/覆盖不全"，请不要写入 inconsistent_attributes，可在 final_analysis 说明。

【结构化纠错：attribute_reconciled（重要，务必使用）】
如果你在综合判断时发现某个 judge 对某项属性判错了（典型：把 "1000 vs 1000ms" 这类单位等价的
情况误判为不一致；或未正确识别 bit 位与信号的对应关系而误判），**不要只写在 final_analysis 的文字里**，
必须逐属性写入 attribute_reconciled，让裁定可被程序执行：
  - attribute_class：属性类名（同上，必须是真实属性类）；
  - single_result：你裁定的**正确**结论（属性一致|属性不一致|无提及|无具体值）；
  - wrong_judges：判错的 judge 名列表（可多个；若无人判错则留空数组）；
  - reason：一句话说明错因。
**token 约束**：凡已在 attribute_reconciled 中声明过的逐属性裁定，final_analysis 里不要再重复展开
（同一理解不要既写散文又写结构化字段）；final_analysis 只写整体判定依据与未被该字段覆盖的要点。

输出 JSON 字段：
{
  "agreement_level": "full|majority|split|single_source|no_consensus",
  "final_verdict": "已落实|不一致|未落实|部分落实|待确认",
  "final_matched_hlr": ["需求编号", ...],
  "inconsistent_attributes": [{"attribute_class":"...","detail":"...","judges":["judge名",...]}],
  "attribute_reconciled": [{"attribute_class":"...","single_result":"属性一致|属性不一致|无提及|无具体值","wrong_judges":["judge名",...],"reason":"..."}],
  "field_disagreements": [{"field":"verdict|matched_hlr|name_match_status|bus|label|bit|direction|attr:属性类","category":"key|non_key","summary":"简要分歧说明"}],
  "final_analysis": "综合判定依据（中文，简洁；不要重复 attribute_reconciled 已声明的逐属性裁定）",
  "confidence": 0.0~1.0
}"""

# 单一来源：prompt 字段清单由 FORWARD_KEY_FIELDS 驱动（__KEY_FIELDS_LIST__ 占位符在上方静态字符串里，
# 这里注入，避免与常量脱节；用 replace 而非 f-string，确保下方 JSON 示例里的花括号不受影响）。
_CONSENSUS_KEY_FIELDS_DOC = "\n".join(f"- {f}" for f in FORWARD_KEY_FIELDS)
CONSENSUS_SYSTEM = CONSENSUS_SYSTEM.replace("__KEY_FIELDS_LIST__", _CONSENSUS_KEY_FIELDS_DOC)

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

def _compress_attribute_lines(attrs):
    """压缩属性明细：仅逐条列出非「属性一致」的属性（事实不一致 / 各 judge 分歧），
    「属性一致」与「无提及」压缩为计数摘要，显著削减仲裁 prompt 的 token 体积
    （一致属性通常占多数时尤其明显）。后端聚合已对一致属性达成共识，仲裁裁决只需看分歧点，
    且后端 inconsistent_attributes / field_disagreements 为准、LLM 仅增补，故不丢事实。"""
    detail = []
    n_consist = 0
    n_notm = 0
    for a in (attrs or []):
        sr = a.get("single_result")
        if sr == "属性一致":
            n_consist += 1
        elif sr == "无提及":
            n_notm += 1
        else:
            detail.append(f"{a.get('attribute_class')}={sr}"
                          f"(EoICD:{a.get('eoicd_value')}/HLR:{a.get('hlr_value')})")
    return detail, n_consist, n_notm


def _judge_block(shard_entry: dict, name: str) -> str:
    e = shard_entry
    detail, n_consist, n_notm = _compress_attribute_lines(e.get("attributes"))
    pieces = list(detail)
    if n_consist:
        pieces.append(f"{n_consist}项一致")
    if n_notm:
        pieces.append(f"{n_notm}项无提及")
    attr_str = "[" + ("; ".join(pieces) if pieces else "无属性") + "]"
    return (f"- {name}: verdict={e.get('verdict')}, matched_hlr={e.get('matched_hlr')}, "
            f"name_match={e.get('name_match_status')}, identity={e.get('hlr_identity')}, "
            f"属性={attr_str}")


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
    detail, n_consist, n_notm = _compress_attribute_lines(entry.get("attributes"))
    pieces = list(detail)
    if n_consist:
        pieces.append(f"{n_consist}项一致")
    if n_notm:
        pieces.append(f"{n_notm}项无提及")
    attr_str = "[" + ("; ".join(pieces) if pieces else "无属性") + "]"
    return (f"verdict={entry.get('verdict')}, matched_hlr={entry.get('matched_hlr')}, "
            f"name_match={entry.get('name_match_status')}, identity={entry.get('hlr_identity')}, "
            f"属性={attr_str}")


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


def _merge_inconsistent_attributes(backend_ia, llm_ia, valid_classes=None, dropped_sink=None):
    """事实不一致：后端为准，LLM 可增补语义层事实（去重，不丢后端事实）。

    白名单约束（2026-10-15 新增）：LLM「新增」的属性类必须落在 valid_classes
    （= 真实属性比对类集合，即 attr_consensus_detail 的键）内。实测 LLM 常把两类东西
    当属性类写进本字段，污染报告「属性比对」列：
      ① 身份层字段（bus / label / bit / direction）——身份层分歧本有专用字段
         field_disagreements(category=key)，属重复/错位；
      ② 自造的描述性类别（"属性覆盖完整性" / "属性核验" / "属性覆盖情况" …）。
    越界条目不再并入，改记入 dropped_sink 留痕（供追溯，不进展示）。
    valid_classes=None 时不做白名单（兼容旧调用）。

    注意：仅约束「LLM 新增」条目；后端条目一律保留（后端属性类天然来自真实比对）。
    """
    base = [dict(d) for d in (backend_ia or [])]
    seen = {_ia_key(d) for d in base}
    for d in (llm_ia or []):
        k = _ia_key(d)
        if k in seen:
            continue
        cls = d.get("attribute_class") or d.get("field")
        if valid_classes is not None and cls not in valid_classes:
            if dropped_sink is not None:
                dropped_sink.append(dict(d))
            continue
        base.append(dict(d))
        seen.add(k)
    return base


def _record_review_failure(a: dict, stage: str, reason: str) -> None:
    """记录「某一阶段的复核调用失败」（累积，不覆盖 review_trace）。

    为什么不用 review_trace 表达失败：复核是多阶段的（step5 仲裁 → step5.5 复查 →
    step5.6 重仲裁），每个阶段成功都会**整体覆盖** review_trace，于是
    「step5 失败、step5.5 成功」的历史会被抹掉——统计上就出现
    「executed == needed」但实际存在调用失败的情况（2026-10-29 实测：38 条需复核
    全部计入 executed，而 logs/arb_fail.jsonl 里有 11 次调用失败）。
    这里用累积列表留住每一次失败，与 review_trace（只反映最后一次阶段）分工：
      review_failures      —— 发生过失败（无论后续是否成功）
      review_applied_stages —— 真正产出过裁决结论的阶段
    """
    fails = a.setdefault("review_failures", [])
    fails.append({
        "stage": stage,
        "reason": reason,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })


def _mark_review_applied(a: dict, stage: str) -> None:
    """记录「某一阶段真正产出了裁决结论」（累积）。"""
    stages = a.setdefault("review_applied_stages", [])
    if stage not in stages:
        stages.append(stage)


def _reconcile_and_recompute(a: dict, out: dict) -> None:
    """方案Y（配套）：仲裁 LLM 判为「属性一致」的属性，回写清除后端不一致标记，并一次性重算
    派生字段，保证一行（a 字典）自洽。JSON/CSV/Word 报告均现读 a，故只要这里改对整行即同步。

    - 清除：从 inconsistent_attributes 与 field_disagreements(attr:* key 项) 删除被仲裁判一致的属性类；
    - 重算：has_key / star_rating（map_star）/ need_review（修永真：不再 OR 旧值，仅保留
      no_valid_source / coverage_gap 等合法标记）。
    仅当 out["attribute_reconciled"] 显式标记 single_result=="属性一致" 才清除，避免 LLM 误删真实不一致。

    D1（全量）：LLM 逐属性裁定量整体留痕到 a["attribute_rulings"]（含 wrong_judges / reason），
    供报告与追溯查"哪个 judge 在哪项属性上判错"——这是"纠正 judge 错误"的唯一结构化落点，
    因为信号级 final_verdict 只有五档，无法承载 judge 级的对错裁定。
    """
    rulings = [dict(r) for r in (out.get("attribute_reconciled") or []) if r.get("attribute_class")]
    if rulings:
        a["attribute_rulings"] = rulings
    reconciled = {
        r.get("attribute_class"): r for r in (out.get("attribute_reconciled") or [])
        if r.get("single_result") == "属性一致"
    }
    if reconciled:
        a["inconsistent_attributes"] = [
            d for d in (a.get("inconsistent_attributes") or [])
            if d.get("attribute_class") not in reconciled
        ]
        a["field_disagreements"] = [
            d for d in (a.get("field_disagreements") or [])
            if not (d.get("category") == "key"
                    and isinstance(d.get("field"), str)
                    and d["field"].startswith("attr:")
                    and d["field"][len("attr:"):] in reconciled)
        ]

    has_key = any(d.get("category") == "key" for d in (a.get("field_disagreements") or []))
    agreement = a.get("agreement") or "split"
    a["star_rating"] = map_star(agreement, has_key)

    # 修 need_review 永真：不再 `or a.get("need_review")`。仅保留无有效源/覆盖缺口等合法标记。
    persistent = ("no_valid_source" in (a.get("need_review_rules") or [])) or bool(a.get("coverage_gap"))
    a["need_review"] = (
        (a["star_rating"] <= 2)
        or has_key
        or bool(a.get("inconsistent_attributes"))
        or persistent
    )

    # ---- 同步派生的标签与审计痕迹（2026-10-29）----
    # ① consensus_label：星级被改写后标签必须跟着变。原标签由聚合层按**改写前**的
    #    星级算出（aggregate.py 里 `_consensus_label(star, agreement)`），仲裁改写
    #    star_rating 后未同步 → 同一行会出现「star_rating=3★ + consensus_label=完全
    #    共识 + agreement=majority」的自相矛盾（2026-10-29 实测 334 条）。
    #    此处用与聚合层**同一个函数**重算，保证标签与星级恒同源。
    a["consensus_label"] = _consensus_label(a["star_rating"], agreement)

    # ② need_review 的审计链：仲裁层重算了 need_review，但聚合层写的
    #    need_review_rules/details 仍对应重算前的状态 → 出现「need_review=True 而
    #    审计记录全空」，读者无法解释为何要复核（2026-10-29 实测 372/372 全 True
    #    而两个数组全空）。此处用**同一张规则表**重算命中：输入取聚合层落盘的
    #    路由上下文快照（need_review_disabled_rules / need_review_on_split），
    #    以免把用户在 config 里逐条关闭的规则又加回来。
    _ctx = {
        # 缺字段时按「有 1 个有效源 / 配置 1 个 judge」兜底：若按 0 处理会误命中
        # no_valid_source 规则，把「字段缺失」说成「无有效源」。
        "n_valid": a.get("n_valid_sources", 1),
        "star": a["star_rating"],
        "n_configured": a.get("n_judges_configured", 1),
        "need_review_on_split": a.get("need_review_on_split", True),
    }
    _overrides = {k: False for k in (a.get("need_review_disabled_rules") or [])}
    _rules: list = []
    _details: list = []
    try:
        _, _hits = needs_review(_ctx, _overrides)
        for _h in _hits:
            _rules.append(_h["name"])
            _details.append(_h["detail"])
    except Exception:
        _rules, _details = [], []
    # 规则表之外的硬事实触发原因（不可配置关闭），补记以便解释 need_review=True
    _facts = []
    if a.get("coverage_gap"):
        _facts.append(("coverage_gap", "存在覆盖缺口（部分 judge 未产出有效结论）"))
    if has_key:
        _facts.append(("arbitration_key_disagreement", "仲裁后关键字段仍有分歧"))
    if a.get("inconsistent_attributes"):
        _facts.append(("inconsistent_attributes", "存在事实不一致属性"))
    for _n, _d in _facts:
        if _n not in _rules:
            _rules.append(_n)
            _details.append(_d)
    a["need_review_rules"] = _rules
    a["need_review_details"] = _details


# ---------------------------------------------------------------------------
# 自相矛盾兜底扫描（零成本：纯规则、不调 LLM）
# ---------------------------------------------------------------------------

# "纠正/否定既有判定"类措辞。命中说明 LLM 在叙述里已经推翻了某个 judge 的判定
# （甚至明确说某属性其实一致），但结构化回写（attribute_reconciled）没有落地——
# 于是报告会出现"自己说自己判错了，却仍按错判展示"的自相矛盾。
_CONTRADICTION_KEYWORDS = (
    "误判", "判错", "误将", "错误包含", "识别错误", "未精准识别", "未正确识别",
    "应为", "应当为", "更正为", "纠正", "其实一致", "实则一致", "实际一致",
    "并非不一致", "不应判", "错误地", "属于误判",
)


def _scan_self_contradiction(a: dict) -> dict | None:
    """零成本兜底：叙述已推翻、数据未回写 → 标记「待人工确认」。

    判据（三条同时满足才命中，避免误报）：
      ① 信号级 final_verdict **不是「不一致」**——若本来就判不一致，属性列在不一致清单里
         是自洽的（实测：verdict=不一致 的信号里"识别错误/未精准识别"等措辞多是在**确认**分歧，
         例如"判断C指出B的识别错误，结论仍为不一致"，若不加此条会大量误报）；
      ② final_analysis 或某个 field_disagreements[].summary 含纠正类措辞；
      ③ inconsistent_attributes 或 field_disagreements 的 attr:* 项**仍在**（没被结构化回写清掉）。

    命中时 attributes 只列"文本点名到、且仍在清单里"的属性（可为空——叙述未点名具体属性时
    只能给出信号级提示，由人工核对）。

    只做标记与留痕，**不改判定、不改星级、不改复核路由**（纯可观测性补丁）。
    """
    if (a.get("final_verdict") or "") == "不一致":
        return None

    texts = []
    if a.get("final_analysis"):
        texts.append(str(a["final_analysis"]))
    for d in (a.get("field_disagreements") or []):
        if isinstance(d, dict) and d.get("summary"):
            texts.append(str(d["summary"]))
    blob = " ".join(texts).strip()
    if not blob:
        return None
    if not any(k in blob for k in _CONTRADICTION_KEYWORDS):
        return None

    stuck: list[str] = []
    for d in (a.get("inconsistent_attributes") or []):
        cls = d.get("attribute_class") if isinstance(d, dict) else None
        if cls and str(cls) in blob and str(cls) not in stuck:
            stuck.append(str(cls))
    for d in (a.get("field_disagreements") or []):
        f = d.get("field") if isinstance(d, dict) else None
        if isinstance(f, str) and f.startswith("attr:"):
            cls = f[len("attr:"):]
            if cls in blob and cls not in stuck:
                stuck.append(cls)

    has_attr_key = any(
        isinstance(d, dict) and str(d.get("field", "")).startswith("attr:")
        for d in (a.get("field_disagreements") or [])
    )
    if not (stuck or a.get("inconsistent_attributes") or has_attr_key):
        return None

    return {
        "detected": True,
        "attributes": stuck,
        "evidence": blob[:200],
        "action": "待人工确认",
        "reason": "叙述含纠正类措辞、且信号级判定并非「不一致」，但「属性比对」仍保留不一致条目",
    }


# ---------------------------------------------------------------------------
# Step5 仲裁
# ---------------------------------------------------------------------------

def arbitrate(agg: dict, shards: dict, arb_cfg: dict, only: set[str] | None = None,
              stage: str = "step5") -> dict:
    """对 agg 中的信号跑 Step5 仲裁；only 限定信号名集合（Step5.5 重仲裁时用）。

    stage: trace 的阶段名（Step5.6 重仲裁传 "step5.6"，避免把复查结论盖成仲裁结论）。

    第1/2点优化：逐信号并发（ThreadPoolExecutor，并发度 arb_cfg["_concurrency"]），接入熔断器
    arb_cfg["_circuit_breaker"]——累计 LLM 失败达阈值即跳过剩余信号。各信号原地写回各自 agg[full]，
    无共享可变状态冲突（预算扣减与熔断计数已加锁）。
    """
    model = arb_cfg.get("model")
    base_url = arb_cfg.get("base_url")
    api_key = os.environ.get(arb_cfg.get("api_key_env", ""), "")
    if not (model and base_url and api_key):
        print("  [仲裁] 未配置有效 arbitrator（model/base_url/api_key），跳过 Step5")
        return agg

    breaker = arb_cfg.get("_circuit_breaker")
    budget = arb_cfg.get("_review_budget")
    budget_lock = arb_cfg.get("_budget_lock") or threading.Lock()
    concurrency = arb_cfg.get("_concurrency", 1)
    targets = [f for f in agg if (only is None or f in only)]
    label = "Step5 仲裁" if stage == "step5" else "Step5.6 重仲裁"
    if not targets:
        return agg
    print(f"  [复核] {label}：{len(targets)} 条信号（并发 {concurrency}）")

    def _process(full: str) -> None:
        item = agg[full]
        a = item["aggregation"]
        # 熔断器已跳闸 → 跳过剩余（第2点）
        if breaker is not None and breaker.tripped:
            # 跳闸原因要写进 trace：区分「累计失败达阈值」与「永久性错误（欠费/鉴权/
            # 模型不存在）」——两者的处置完全不同（前者等端点恢复，后者要换 key/模型）。
            _br = (getattr(breaker, "trip_reason", "") or "累计 LLM 失败达阈值")
            a["review_trace"] = {"stage": stage, "applied": False,
                                 "reason": "复核熔断器已跳闸（{0}），跳过本次仲裁".format(_br)}
            a["arbitrated"] = True
            return
        shard = {j: shards[j][full] for j in shards if full in shards[j]}
        if not shard:
            return
        n_valid = sum(1 for jv in item["judges"].values() if jv.get("coverage") == "ok")

        # 单 judge / 0 存活：仲裁者无法升级共识，保留后端结论（降级优先于 LLM）。
        if n_valid <= 1:
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
            return

        # 后端已完全共识（full）且无缺口/无需复核：无需 LLM 仲裁，保留后端结论。
        if a.get("agreement") == "full" and not a.get("need_review") and not a.get("coverage_gap"):
            a["review_trace"] = {"stage": stage, "applied": False,
                                 "reason": "后端已完全共识且无需复核，跳过 Step5"}
            a["arbitrated"] = True
            return

        # 成本护栏（路线三）：超出复核预算的本次不跑，如实记账而非静默跳过。
        if budget is not None:
            with budget_lock:
                if budget["left"] <= 0:
                    a["review_trace"] = {
                        "stage": stage, "applied": False,
                        "reason": "超出复核预算（MULTI_JUDGE_REVIEW_BUDGET），本次未调用 LLM",
                    }
                    a["arbitrated"] = True
                    return
                budget["left"] -= 1

        user = _build_consensus_user(full, shard, a.get("note", ""))
        # _breaker 传给 call_llm：遇「全局性永久错误」（欠费/鉴权/模型不存在）时
        # 由它立即跳闸止损，而不是让 372 条信号各撞 3 次。
        _ctx = {"full": full, "stage": stage, "_breaker": breaker}
        try:
            out = call_llm(model, base_url, api_key, CONSENSUS_SYSTEM, user, ctx=_ctx)
        except Exception:
            out = None
        if not isinstance(out, dict):
            # 第2点：降级 + 记录失败（触发熔断）
            _perm = _ctx.get("_perm_error")
            if not _perm and breaker is not None:
                breaker.record_failure()
            _reason = _perm or "Step5 仲裁 LLM 调用失败或返回非 JSON"
            _record_review_failure(a, stage, _reason)
            a["review_trace"] = {"stage": stage, "applied": False, "reason": _reason}
            a["arbitrated"] = True
            return
        # 第2点：成功复位熔断计数
        if breaker is not None:
            breaker.record_success()
        # 共识等级 / 结论：以 LLM 为准（仲裁者职责）
        agreement = out.get("agreement_level") or a.get("agreement")
        final_verdict = out.get("final_verdict") or a.get("final_verdict")

        # field_disagreements：合并而非覆盖——后端 values 为基准，LLM 仅补 category/summary
        merged_fd = _merge_field_disagreements(
            a.get("field_disagreements"), out.get("field_disagreements")
        )
        a["agreement"] = agreement
        a["final_verdict"] = final_verdict
        # 事实不一致：后端事实不丢，LLM 可增补（去重）；越界的 LLM 条目（身份层字段/自造类）不并入，
        # 白名单 = 真实属性比对类（attr_consensus_detail 的键），被丢弃的记入 llm_attributes_dropped 留痕。
        _dropped_ia: list = []
        a["inconsistent_attributes"] = _merge_inconsistent_attributes(
            a.get("inconsistent_attributes"), out.get("inconsistent_attributes"),
            valid_classes=set((a.get("attr_consensus_detail") or {}).keys()),
            dropped_sink=_dropped_ia,
        )
        if _dropped_ia:
            a["llm_attributes_dropped"] = _dropped_ia
        a["field_disagreements"] = merged_fd
        a["final_analysis"] = out.get("final_analysis", "")
        a["confidence"] = out.get("confidence")
        # 方案Y（配套）：仲裁判一致的属性回写清除 + 派生字段一次性重算（修 need_review 永真）
        _reconcile_and_recompute(a, out)
        # 保留已写入的 peer_review 子字段：重仲裁(stage=step5.6)生效不代表复查也生效，
        # 直接整体赋值会把「复查因独立视角不足被跳过」这条事实抹掉。
        _mark_review_applied(a, stage)
        a["review_trace"] = {
            "stage": stage, "applied": True,
            "reason": "仲裁已生效（对全部 judge 结论做第二次裁决，stage={0}）".format(stage),
            "peer_review": (a.get("review_trace") or {}).get("peer_review"),
        }
        a["arbitrated"] = True

    if concurrency <= 1 or len(targets) <= 1:
        for full in targets:
            _process(full)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = [ex.submit(_process, full) for full in targets]
            total = len(futures)
            step = max(1, total // 10)
            done = 0
            for fut in as_completed(futures):
                fut.result()
                done += 1
                if done % step == 0 or done == total:
                    print(f"    └ 进度 {done}/{total}（{done * 100 // total}%）")
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
    #9 修复：匿名化按「独立视角」分组。

    第1/2点优化：逐信号并发（ThreadPoolExecutor），接入熔断器与预算锁。
    """
    model = arb_cfg.get("model")
    base_url = arb_cfg.get("base_url")
    api_key = os.environ.get(arb_cfg.get("api_key_env", ""), "")
    if not (model and base_url and api_key):
        return agg

    judge_meta = judge_meta or {}
    breaker = arb_cfg.get("_circuit_breaker")
    budget = arb_cfg.get("_review_budget")
    budget_lock = arb_cfg.get("_budget_lock") or threading.Lock()
    concurrency = arb_cfg.get("_concurrency", 1)

    def _mark_skipped(a: dict, reason: str, n_view: int, groups: list[list[str]] | None = None) -> None:
        """复查未生效时如实记账（不得留 peer_reviewed=True 的假象）。"""
        a["peer_reviewed"] = False
        a["review_trace"] = {"stage": "step5.5", "applied": False, "reason": reason,
                             "peer_review": {"applied": False, "reason": reason}}
        a["peer_review_info"] = {
            "applied": False,
            "reason": reason,
            "independent_views": n_view,
            "groups": groups or [],
        }

    def _review_one(full: str) -> None:
        item = agg.get(full)
        if item is None:
            return
        a = item["aggregation"]
        n_view = 0
        groups: list = []
        try:
            # 熔断已跳闸 → 跳过（第2点）
            if breaker is not None and breaker.tripped:
                _br = (getattr(breaker, "trip_reason", "") or "累计 LLM 失败达阈值")
                _mark_skipped(a, "复核熔断器已跳闸（{0}），跳过 peer 复查".format(_br), 0, [])
                return
            shard = {j: shards[j][full] for j in shards if full in shards[j]}
            names = list(shard.keys())
            groups = _independent_groups(names, judge_meta)
            n_view = len(groups)
            if n_view < 2:
                _mark_skipped(a,
                              "独立视角数 {0} < 2（同模型重复不构成独立视角），跳过 peer 复查".format(n_view)
                              if n_view >= 1 else "无可用 judge 结论，跳过 peer 复查",
                              n_view, groups)
                return
            if budget is not None:
                with budget_lock:
                    if budget["left"] <= 0:
                        _mark_skipped(a, "超出复核预算（MULTI_JUDGE_REVIEW_BUDGET），跳过 peer 复查",
                                      n_view, groups)
                        return
                    budget["left"] -= 1
            user = _build_review_user(full, groups, shard)
            _ctx = {"full": full, "stage": "step5.5", "_breaker": breaker}
            try:
                # 本函数无 stage 变量（阶段名硬编码为 "step5.5"，同 _mark_skipped），
                # 诊断上下文用字面量，勿引用不存在的 stage。
                out = call_llm(model, base_url, api_key, REVIEW_SYSTEM, user, ctx=_ctx)
            except Exception:
                out = None
            if not isinstance(out, dict):
                # 第2点：降级 + 触发熔断
                _perm = _ctx.get("_perm_error")
                if not _perm and breaker is not None:
                    breaker.record_failure()
                _reason = _perm or "复查 LLM 调用失败或返回非 JSON"
                _record_review_failure(a, "step5.5", _reason)
                _mark_skipped(a, _reason, n_view, groups)
                return
            if breaker is not None:
                breaker.record_success()
            # 复查结论覆盖 final_verdict / matched_hlr / 属性结论 / analysis / confidence
            if out.get("final_verdict"):
                a["final_verdict"] = out["final_verdict"]
            # 第B点：final_matched_hlr 富结构为 [[req_id, iface], ...]（#5 修复），也可能退化为
            # 扁平 [req_id, ...]；复用聚合层 _display_matched_full 渲染，避免对嵌套列表直接 join
            # 触发 TypeError。空值/缺失则不覆盖身份共识（保留聚合阶段结论）。
            if out.get("final_matched_hlr"):
                a["identity_consensus"] = (
                    f"{out['final_verdict']}→" + _display_matched_full(out["final_matched_hlr"])
                )
            reconciled = {r.get("attribute_class"): r for r in (out.get("attribute_reconciled") or [])}
            for d in a.get("structured_diff", []):
                cls = d.get("attribute_class")
                if cls in reconciled:
                    d["diff_type"] = reconciled[cls].get("single_result", d["diff_type"])
            if out.get("analysis"):
                a["final_analysis"] = out["analysis"]
            if out.get("confidence") is not None:
                a["confidence"] = out["confidence"]
            # D1（最小）：Step5.5 此前只把 attribute_reconciled 用来重贴 structured_diff 标签，
            # 不回写清除、不重算派生字段——LLM 的裁定"能叙述不能行动"。此处补挂同一清除函数，
            # 使判为「属性一致」的属性同时从 inconsistent_attributes 与 field_disagreements(attr:*)
            # 移除，并重算 has_key / star_rating / need_review（零新增输出 schema，token 中性）。
            _reconcile_and_recompute(a, out)
            a["peer_reviewed"] = True
            _mark_review_applied(a, "step5.5")
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
        except Exception as exc:  # 第C点：任何意外异常（含 LLM 返回畸形 final_matched_hlr）局部降级，
            # 不连坐整次运行——保留 Step5 结论，标记 peer 复查跳过，由 review_trace 诚实记账。
            _record_review_failure(a, "step5.5",
                                   "复查过程异常（{0}），已跳过 peer 复查，保留 Step5 结论".format(type(exc).__name__))
            _mark_skipped(a, "复查过程异常（{0}），已跳过 peer 复查，保留 Step5 结论".format(type(exc).__name__),
                          n_view, groups)

    low = _low_star_signals(agg)
    print(f"  [复核] Step5.5 复查：{len(low)} 条低星信号（并发 {concurrency}）")
    if not low:
        return agg
    if concurrency <= 1 or len(low) <= 1:
        for full in low:
            _review_one(full)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = [ex.submit(_review_one, full) for full in low]
            total = len(futures)
            step = max(1, total // 10)
            done = 0
            for fut in as_completed(futures):
                fut.result()
                done += 1
                if done % step == 0 or done == total:
                    print(f"    └ 进度 {done}/{total}（{done * 100 // total}%）")
    return agg


# ---------------------------------------------------------------------------
# 编排：Step5 + (Step5.5 循环 + Step5.6 重仲裁)
# ---------------------------------------------------------------------------

def run_arbitration(agg: dict, shards: dict, judge_meta: dict, arb_cfg: dict) -> dict:
    """完整智能层：Step5 仲裁 →（可选）Step5.5 复查循环 → 重仲裁。

    #10 精简签名：复查配置并入 arb_cfg 的 "re_review" 子键。

    第1/2点优化：初始化熔断器与并发度（写入 arb_cfg 供 arbitrate/peer 共享，跨阶段并发安全）；
    第3点优化：Step5.5 复查后，仅当复查真正改变了裁决结论（final_verdict 变化）的信号才跑
    Step5.6 重仲裁，避免对「复查与原判一致」的信号重复调用 LLM（低星信号从 3 次降到 2 次）。
    """
    if not arb_cfg:
        return agg
    re_review_cfg = arb_cfg.get("re_review") or {}
    # 第2点：熔断器（跨 Step5/5.5/5.6 共享）
    # 阈值默认 15（2026-10-15 由 5 调大）：避免少量瞬时超时（实测 4~5 次）误触发全局跳闸，
    # 把整批复核信号（数百条）一次跳过；真·系统性故障仍由累计 15 次失败 + 预算兜底止损。
    threshold = int(os.environ.get("MULTI_JUDGE_REVIEW_FAILSTOP", "15"))
    arb_cfg.setdefault("_circuit_breaker", _CircuitBreaker(threshold))
    # 第1点：并发度（默认 8，上限 16，避免打爆 LLM 服务）
    # 2026-10-15：8→6 试行后未观察到超时改善（本次超时次数未记录），故按用户决定改回 8。
    concurrency = int(os.environ.get("MULTI_JUDGE_REVIEW_CONCURRENCY", "8"))
    concurrency = max(1, min(concurrency, 16))
    arb_cfg.setdefault("_concurrency", concurrency)
    # 预算锁（并发下原子扣减 _review_budget.left）
    if arb_cfg.get("_review_budget") is not None and "_budget_lock" not in arb_cfg:
        arb_cfg["_budget_lock"] = threading.Lock()

    agg = arbitrate(agg, shards, arb_cfg)
    if re_review_cfg.get("enabled", False):
        max_rounds = int(re_review_cfg.get("max_rounds", 1))
        for _ in range(max_rounds):
            low = _low_star_signals(agg)
            if not low:
                break
            # 第3点：记录 peer 前的裁决结论，用于判断是否真有改变
            before = {f: agg[f]["aggregation"].get("final_verdict") for f in low}
            agg = peer_aware_review(agg, shards, arb_cfg, judge_meta)
            # 仅对复查真正改变了 verdict 的信号跑 Step5.6 重仲裁
            changed = {f for f in low
                       if agg[f]["aggregation"].get("final_verdict") != before.get(f)}
            if changed:
                agg = arbitrate(agg, shards, arb_cfg, only=changed, stage="step5.6")

    # 方案 A：仲裁后重算 analysis_summary，使其与最终（仲裁后）判定口径一致。
    # 用仲裁后的 final_verdict / 合并后的 inconsistent_attributes 与 field_disagreements 重新生成；
    # 当仲裁推翻了 judge 多数判定时，build_analysis_summary 会自动切换为「仲裁判定」口径并标注原分布。
    # 未走仲裁（arb_cfg 为空）或仲裁跳过的信号，final_verdict 与共识字段均未变，重算结果与原摘要逐字一致。
    #
    # 零成本兜底：同步扫描「叙述已推翻、结构化未回写」的自相矛盾信号（纯规则，不调 LLM）。
    # 命中时标记 a["self_contradiction"]、写入 review_trace，并在摘要末尾补一句提示，
    # 避免报告出现"自己说自己判错了、却仍按错判展示"而不被察觉。
    for full, item in agg.items():
        a = item["aggregation"]
        # 子类重算（修缺陷：arbitrate() 只改 final_verdict，不更新 sub_classification）。
        # 若仲裁把 verdict 改判为「未落实」而该字段仍为 None/旧值，报告「未落实」的两个
        # 二级小节（报告按该字段分流）都会收不到它 ⇒ 漏项。此处与 analysis_summary 同处重算，
        # 用同一 derive_sub_classification 单一来源，保证字段与摘要、报告分组三者口径一致。
        # 未走仲裁、未改判的信号：verdict 与 judges 均未变，重算结果与原值逐字相同（幂等）。
        a["sub_classification"] = derive_sub_classification(
            a.get("final_verdict"), item.get("judges", {})
        )
        # 复核状态（2026-10-29）：把「没拿到第二裁决」与「模型判待确认」彻底分开。
        #   已裁决   —— 有阶段真正产出了裁决结论，且全程无失败
        #   部分裁决 —— 有阶段产出了结论，但过程中发生过调用失败（结论仍有效，但必须留痕）
        #   未获裁决 —— 没有阶段成功（LLM 调用失败 / 熔断 / 超预算 / 无独立视角）
        # 未进入复核路由的信号不写该字段（"无此状态"即表示未进入复核）。
        _applied = a.get("review_applied_stages") or []
        _failed = a.get("review_failures") or []
        _entered = bool(a.get("arbitrated")) or bool(a.get("review_trace"))
        if _applied and _failed:
            a["review_status"] = "部分裁决"
        elif _applied:
            a["review_status"] = "已裁决"
        elif _failed or _entered:
            a["review_status"] = "未获裁决"
        sc = _scan_self_contradiction(a)
        if sc:
            a["self_contradiction"] = sc
            _rt = dict(a.get("review_trace") or {})
            _rt["self_contradiction"] = sc
            a["review_trace"] = _rt
        a["analysis_summary"] = build_analysis_summary(
            final_verdict=a.get("final_verdict"),
            judges_with=item.get("judges", {}),
            attr_by_class=a.get("attr_consensus_detail") or {},
            inconsistent_attributes=a.get("inconsistent_attributes") or [],
            field_disagreements=a.get("field_disagreements") or [],
            divergence=a.get("divergence", False),
            n_judges=a.get("n_judges_participating", 1),
        )
        if sc:
            if sc["attributes"]:
                a["analysis_summary"] += (
                    "；该信号叙述显示存在被判错但仍列于「属性比对」的项（"
                    + "、".join(sc["attributes"]) + "），已标记待人工确认。"
                )
            else:
                a["analysis_summary"] += (
                    "；该信号叙述显示存在被判错的属性裁定，但「属性比对」仍保留不一致条目，"
                    "已标记待人工确认。"
                )
    return agg


# ---------------------------------------------------------------------------
# 离线自测（不依赖 LLM）
# ---------------------------------------------------------------------------
# 自测已迁出到 tests/test_arbitrator.py（生产文件瘦身、便于 pytest 化）。
# 旧 CLI 兼容：python multi_judge_arbitrator.py 仍可触发，转调 tests/ 入口。
if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path
    _root = _Path(__file__).resolve().parent.parent
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    from tests.test_arbitrator import run_all as _run_self_test
    _run_self_test()

