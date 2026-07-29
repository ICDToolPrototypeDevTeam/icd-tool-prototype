# V4 多智能体真实实现 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 V4 多智能体裁判 + Review Agent 从 mock 切换为真实 API 调用，增加 asyncio 并行和星评机制。

**Architecture:** 扩展 V4 `get_llm()` 工厂，新增 MiniMax/Qwen 真实客户端（与 DeepSeekClient 同模式）；改造 `multi_judge.py` 为 asyncio 并行；增强 `review_agent.py` 星评规则；增强 `consensus.md` prompt。

**Tech Stack:** Python 3.11, asyncio, requests, Pydantic v2, FastAPI

## Global Constraints

- 国产模型（DeepSeek/MiniMax/Qwen），不引入 CrewAI 框架
- matching/、parsers/、doc_generators/、semantic_judge.py、case_builder.py、report_generator.py、reverse_judge.md、forward_judge.md、config.py 不动
- USE_MOCK_LLM=1 仍然全走 mock（保留 mock 调试能力）
- Review Agent 固定用 DeepSeek

---

### Task 1: 新增 MiniMaxClient (`llm/minimax_client.py`)

**Files:**
- Create: `backend/app/v4/llm/minimax_client.py`

**Interfaces:**
- Consumes: 无
- Produces: `MiniMaxClient(api_key, base_url, model)` with `chat(messages, temperature, max_tokens, max_retries) -> ChatResponse`

- [ ] **Step 1: 创建 minimax_client.py**

以 `deepseek_client.py` 为模板，仅改类名和默认值：

```python
# backend/app/v4/llm/minimax_client.py
# -*- coding: utf-8 -*-
"""MiniMax API client via OpenAI-compatible chat/completions."""

from __future__ import annotations

import time
import requests


class MiniMaxClient:
    """Calls MiniMax API with timeout, basic retry, and JSON extraction."""

    def __init__(self, api_key: str, base_url: str, model: str):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int = 1024,
        timeout: int = 60,
        max_retries: int = 2,
    ) -> "ChatResponse":
        from app.v4.llm.factory import ChatResponse

        base = self._base_url
        if base.endswith("/v1"):
            base = base[:-3]
        url = f"{base}/v1/chat/completions"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=timeout,
                )
                resp.raise_for_status()
                body = resp.json()
                content = body["choices"][0]["message"]["content"]
                usage = body.get("usage", {})
                return ChatResponse(content=content, usage=usage)
            except requests.RequestException as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(2.0)
                continue

        raise last_error
```

- [ ] **Step 2: 验证导入**

```bash
cd backend && python -c "from app.v4.llm.minimax_client import MiniMaxClient; print('OK')"
```

---

### Task 2: 新增 QwenClient (`llm/qwen_client.py`)

**Files:**
- Create: `backend/app/v4/llm/qwen_client.py`

**Interfaces:**
- Consumes: 无
- Produces: `QwenClient(api_key, base_url, model)` with `chat(messages, temperature, max_tokens, max_retries) -> ChatResponse`

- [ ] **Step 1: 创建 qwen_client.py**

```python
# backend/app/v4/llm/qwen_client.py
# -*- coding: utf-8 -*-
"""Qwen API client via DashScope OpenAI-compatible endpoint."""

from __future__ import annotations

import time
import requests


class QwenClient:
    """Calls Qwen API (DashScope) with timeout, basic retry, and JSON extraction."""

    def __init__(self, api_key: str, base_url: str, model: str):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int = 1024,
        timeout: int = 60,
        max_retries: int = 2,
    ) -> "ChatResponse":
        from app.v4.llm.factory import ChatResponse

        base = self._base_url
        if base.endswith("/v1"):
            base = base[:-3]
        url = f"{base}/v1/chat/completions"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=timeout,
                )
                resp.raise_for_status()
                body = resp.json()
                content = body["choices"][0]["message"]["content"]
                usage = body.get("usage", {})
                return ChatResponse(content=content, usage=usage)
            except requests.RequestException as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(2.0)
                continue

        raise last_error
```

- [ ] **Step 2: 验证导入**

```bash
cd backend && python -c "from app.v4.llm.qwen_client import QwenClient; print('OK')"
```

---

### Task 3: 扩展 factory.py

**Files:**
- Modify: `backend/app/v4/llm/factory.py`

**Interfaces:**
- Consumes: `MiniMaxClient` (Task 1), `QwenClient` (Task 2)
- Produces: `get_llm(provider)` 支持 minimax/qwen 真实客户端；`get_available_providers() -> list[str]`

- [ ] **Step 1: 改造 get_llm() — 将 minimax/qwen 从 mock 改为真实客户端**

将 `factory.py` 第 56-59 行：

```python
    # Phase 2-3 scaffolding: minimax and qwen return mock for now
    if provider in ("minimax", "qwen"):
        from app.v4.llm.mock_llm import MockLLMClient
        return MockLLMClient()
```

替换为：

```python
    if provider == "minimax":
        from app.v4.llm.minimax_client import MiniMaxClient
        api_key = os.getenv("MINIMAX_API_KEY", "")
        if not api_key:
            raise ValueError(
                "MINIMAX_API_KEY not set. Check your .env file "
                "or set USE_MOCK_LLM=1 for offline development."
            )
        return MiniMaxClient(
            api_key=api_key,
            base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimax.chat"),
            model=os.getenv("MINIMAX_MODEL", "abab6.5s-chat"),
        )

    if provider == "qwen":
        from app.v4.llm.qwen_client import QwenClient
        api_key = os.getenv("QWEN_API_KEY", "")
        if not api_key:
            raise ValueError(
                "QWEN_API_KEY not set. Check your .env file "
                "or set USE_MOCK_LLM=1 for offline development."
            )
        return QwenClient(
            api_key=api_key,
            base_url=os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            model=os.getenv("QWEN_MODEL", "qwen-plus"),
        )
```

- [ ] **Step 2: 在文件末尾新增 get_available_providers()**

```python
def get_available_providers() -> list[str]:
    """Return providers whose API keys are configured in env.

    Used for auto-degradation: if only 1 provider is available,
    skip review agent (no consensus needed).
    """
    key_map = {
        "deepseek": "DEEPSEEK_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "qwen": "QWEN_API_KEY",
    }
    available = []
    for provider, env_key in key_map.items():
        if os.getenv(env_key):
            available.append(provider)
    return available
```

- [ ] **Step 3: 验证**

```bash
cd backend && python -c "
from app.v4.llm.factory import get_llm, get_available_providers
print('get_available_providers:', get_available_providers())
# 检查 deepseek 仍然可用
client = get_llm('deepseek')
print('deepseek OK:', type(client).__name__)
"
```

---

### Task 4: 增强 models.py

**Files:**
- Modify: `backend/app/v4/models.py`

**Interfaces:**
- Consumes: 无
- Produces: `AgentJudgment`（新增）；`MultiJudgeResult.judgments` 弱类型 → 强类型；`ConsensusResult` 补全字段

- [ ] **Step 1: 在 ConsensusResult 类中追加字段**

在 `ConsensusResult`（约第 304 行）末尾新增两个字段，加在 `confidence` 之后：

```python
    # 在 confidence: float = 0.0 之后追加：
    consistent_agents: list[str] = Field(default_factory=list)   # 语义一致的 agent 列表
    divergent_agents: list[str] = Field(default_factory=list)     # 偏离的 agent 列表
```

- [ ] **Step 2: 在 ConsensusOutput 之前新增 AgentJudgment**

```python
class AgentJudgment(BaseModel):
    """单个对比 agent 的输出。替代现有 MultiJudgeResult.judgments 中的裸 dict。"""

    agent_name: str = ""           # "deepseek" | "minimax" | "qwen"
    coverage_status: str = ""      # covered|partial|missing|inconsistent|needs_review|error
    difference_type: str = ""
    missing_points: list[str] = Field(default_factory=list)
    inconsistent_points: list[str] = Field(default_factory=list)
    analysis: str = ""
    suggested_action: str = ""
    confidence: float = 0.0
    raw_response: str = ""
```

- [ ] **Step 3: 验证导入**

```bash
cd backend && python -c "
from app.v4.models import AgentJudgment, ConsensusResult, MultiJudgeResult
print('AgentJudgment:', AgentJudgment.model_fields.keys())
print('ConsensusResult has consistent_agents:', 'consistent_agents' in ConsensusResult.model_fields)
"
```

---

### Task 5: 改造 multi_judge.py（同步 → asyncio 并行）

**Files:**
- Modify: `backend/app/v4/comparison/multi_judge.py`

**Interfaces:**
- Consumes: `get_llm` (factory), `AgentJudgment` (Task 4), `_build_reverse_user_prompt` / `_call_reverse_judge_api` (semantic_judge)
- Produces: `judge_with_panel()` 接口不变（仍返回 `MultiJudgeOutput`），内部改为 asyncio 并行

- [ ] **Step 1: 添加 import 和异步辅助函数**

在文件开头追加 import：

```python
import asyncio
```

- [ ] **Step 2: 添加 _judge_with_provider 异步函数**

在 `judge_with_panel()` 之前插入：

```python
async def _judge_with_provider(
    case: ReverseCase,
    provider: str,
    system_prompt: str,
) -> dict:
    """单个 provider 对单条 case 的异步裁判。失败时返回 error judgment。"""
    from app.v4.models import AgentJudgment

    llm = get_llm(provider)
    user_prompt = _build_reverse_user_prompt(case)
    try:
        result = await asyncio.to_thread(
            _call_reverse_judge_api,
            llm=llm,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            case=case,
        )
        return AgentJudgment(
            agent_name=provider,
            coverage_status=result.coverage_status,
            difference_type=result.difference_type,
            missing_points=result.missing_points,
            inconsistent_points=result.inconsistent_points,
            analysis=result.analysis,
            suggested_action=result.suggested_action,
            confidence=result.confidence,
            raw_response="",
        ).model_dump()
    except Exception as e:
        return {
            "agent_name": provider,
            "coverage_status": "error",
            "difference_type": "",
            "missing_points": [],
            "inconsistent_points": [],
            "analysis": f"调用失败: {str(e)}",
            "suggested_action": "",
            "confidence": 0.0,
            "raw_response": "",
        }
```

- [ ] **Step 3: 改造 judge_with_panel 核心循环**

将 `judge_with_panel()` 中的同步 for 循环替换为 asyncio：

```python
def judge_with_panel(
    cases: list[ReverseCase],
    providers: list[str] | None = None,
) -> MultiJudgeOutput:
    """Judge each ReverseCase with multiple LLM providers in parallel."""
    if providers is None:
        providers = JUDGE_PROVIDERS

    total = len(cases)
    results: list[MultiJudgeResult] = []
    system_prompt = _load_reverse_prompt()

    for idx, case in enumerate(cases):
        # Phase 1: 并行调用所有 provider
        async def _gather():
            return await asyncio.gather(
                *[_judge_with_provider(case, p, system_prompt) for p in providers],
                return_exceptions=True,
            )

        gathered = asyncio.run(_gather())
        case_judgments: dict[str, dict] = {}
        for provider, result in zip(providers, gathered):
            if isinstance(result, Exception):
                case_judgments[provider] = {
                    "agent_name": provider,
                    "coverage_status": "error",
                    "confidence": 0.0,
                    "analysis": f"gather 异常: {str(result)}",
                }
            else:
                case_judgments[provider] = result

        results.append(MultiJudgeResult(
            case_id=case.case_id,
            judgments=case_judgments,
        ))

        statuses = {p: j.get("coverage_status", "?") for p, j in case_judgments.items()}
        print(
            f"  [multi] {case.case_id} ({idx + 1}/{total}) {statuses}",
            file=sys.stderr,
        )

    return MultiJudgeOutput(
        total_cases=total,
        providers=providers,
        results=results,
    )
```

- [ ] **Step 4: 验证导入和基本调用**

```bash
cd backend && python -c "
from app.v4.comparison.multi_judge import judge_with_panel
print('judge_with_panel imported OK')
"
```

---

### Task 6: 增强 review_agent.py（星评机制）

**Files:**
- Modify: `backend/app/v4/comparison/review_agent.py`

**Interfaces:**
- Consumes: `MultiJudgeResult` (增强后), `ConsensusResult` (增强后), `load_prompt("consensus")`
- Produces: `review_judgments()` 接口不变；`ConsensusResult` 现在包含 `consistent_agents` / `divergent_agents`

- [ ] **Step 1: 追加 _derive_consensus_details() 函数**

在 `_build_summary()` 之前插入：

```python
def _derive_consensus_details(
    data: dict,
    judgments: dict[str, dict],
) -> tuple[list[str], list[str]]:
    """从 LLM 返回的 JSON 或多数投票中推导 consistent/divergent agents。

    优先用 LLM 返回的字段；若缺失则根据 coverage_status 多数投票推导。
    """
    consistent = data.get("consistent_agents", [])
    divergent = data.get("divergent_agents", [])

    # Fallback: 多数投票推导
    if not consistent and not divergent:
        statuses = [
            j.get("coverage_status", "") for j in judgments.values()
        ]
        # 找出多数 status
        from collections import Counter
        counts = Counter(statuses)
        majority_status = counts.most_common(1)[0][0] if counts else ""
        for provider, j in judgments.items():
            if j.get("coverage_status", "") == majority_status:
                consistent.append(provider)
            else:
                divergent.append(provider)

    return consistent, divergent
```

- [ ] **Step 2: 修改 _call_review_api 的 ConsensusResult 构造**

将 `_call_review_api` 中构造 `ConsensusResult` 的部分（约第 105-113 行）替换为：

```python
            consistent, divergent = _derive_consensus_details(data, model_results)
            return ConsensusResult(
                case_id=case_id,
                model_results=model_results,
                agreement_level=data.get("agreement_level", "split"),
                star_rating=int(data.get("star_rating", 1)),
                final_coverage_status=data.get("final_coverage_status", "needs_review"),
                final_analysis=data.get("final_analysis", ""),
                confidence=float(data.get("confidence", 0.5)),
                consistent_agents=consistent,
                divergent_agents=divergent,
            )
```

同样更新 fallback return（约第 123-131 行）追加：

```python
    return ConsensusResult(
        case_id=case_id,
        model_results=model_results,
        agreement_level="split",
        star_rating=1,
        final_coverage_status="needs_review",
        final_analysis="Review API error after retries",
        confidence=0.0,
        consistent_agents=[],
        divergent_agents=[],
    )
```

- [ ] **Step 3: 验证**

```bash
cd backend && python -c "
from app.v4.comparison.review_agent import review_judgments
print('review_judgments imported OK')
"
```

---

### Task 7: 增强 consensus.md prompt（追加星评规则）

**Files:**
- Modify: `backend/app/v4/prompts/consensus.md`

- [ ] **Step 1: 在已有 prompt 末尾追加星评语义规则**

保持已有内容不变，在文件末尾追加：

```markdown

## 星评语义规则（基于语义一致性，非字面一致性）

以下规则用于指导 star_rating 的判断：

★★★（完全一致，star_rating=3）：
三位专家的结论在语义上一致 —— 即使措辞不同或 coverage_status 标签不同，
各自 analysis 描述的核心判断指向同一事实。
例（语义一致）：
  专家A analysis："HLR描述了温度信号的采集功能，但缺少对数据格式、分辨率和量程的具体定义"
  专家B analysis："ICD要求该信号为BNR格式精度0.01，HLR中只写了采集温度信号，格式和精度要求未落实"
  → 两者都在说格式和精度定义缺失这同一个事实，视为一致。

★★☆（部分分歧，star_rating=2）：
两位专家结论语义一致，另一位有实质性分歧。
分歧不是措辞差异，而是对覆盖性的判断方向不同。
例（实质分歧）：
  专家A analysis："HLR完整覆盖了信号方向、数据类型和范围，是一致的实现"
  专家C analysis："HLR中信号方向为'接收'，但ICD定义为'发送'，存在方向性矛盾"
  → A认为覆盖了，C认为有矛盾，这是实质性分歧。

★☆☆（严重分歧，star_rating=1）：
三位专家的结论互不一致，各自表达了实质性不同的判断。

## 重要提示
- 不要只看 coverage_status 的字面值，要通过 analysis 理解每位专家的实际含义
- 不同措辞表达相同判断 → 视为一致
- 相同措辞表达不同判断 → 视为分歧（罕见但可能出现）
- 如果所有模型都标注为 needs_review，star_rating 仍可为 3 星（因为一致）
- 在输出中额外提供 consistent_agents 和 divergent_agents 列表
```

- [ ] **Step 2: 验证**

```bash
cd backend && python -c "
from app.v4.prompts import load_prompt
p = load_prompt('consensus')
assert '★★★' in p
assert 'consistent_agents' in p
print('consensus.md OK, len=', len(p))
"
```

---

### Task 8: 更新 pipeline.py（asyncio 调用适配）

**Files:**
- Modify: `backend/app/v4/pipeline.py`（仅 Step 3 调用方式）

**Interfaces:**
- Consumes: `judge_with_panel` (Task 5 改造后)
- Produces: 输出接口不变（JSON 文件路径和内容格式不变）

- [ ] **Step 1: 确认 pipeline.py 无需代码改动**

`pipeline.py` 中 Step 3 调用：

```python
multi_out = judge_with_panel(cases, providers=JUDGE_PROVIDERS)
```

`judge_with_panel()` 签名和返回值类型不变，内部已通过 `asyncio.run()` 处理异步。
因此 **pipeline.py 不需要改动**。

- [ ] **Step 2: 以 mock 模式验证端到端流程**

```bash
cd backend && USE_MOCK_LLM=1 python -c "
from app.v4.pipeline import run_reverse_pipeline
# 不实际运行完整 pipeline，仅验证导入和接口
print('pipeline imports OK')
"
```

- [ ] **Step 3: 运行现有反向管线 CLI 验证 mock 模式**

```bash
cd backend && USE_MOCK_LLM=1 python -m app.v4.cli reverse-analyze \
    --hlr "path/to/sample_hlr.docx" \
    --eoicd "path/to/sample_pub.xlsx" \
    --output-dir output/test_multi_agent
```

验证 `multi_judge_results.json` 和 `consensus_results.json` 正常生成。

---

### 任务依赖图

```
Task 1 (MiniMaxClient) ──┐
                          ├──→ Task 3 (factory.py) ──→ Task 5 (multi_judge.py)
Task 2 (QwenClient) ─────┘                                    │
                                                              ↓
Task 4 (models.py) ──────────────────────────────→ Task 6 (review_agent.py)
                                                              │
Task 7 (consensus.md) ────────────────────────────────────────┘
                                                              ↓
                                                     Task 8 (pipeline 验证)
```
