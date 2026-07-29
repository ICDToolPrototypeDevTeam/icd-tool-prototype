# V4 多智能体真实实现 — 设计文档

**日期：** 2026-07-28
**状态：** 待实施
**关联：** 方案 A — 扩展 V4 Factory 模式

---

## 1. 背景与目标

### 1.1 现状

V4 反向管线的 Step 6-7（多智能体裁判 + Review 共识）当前为 mock 实现：
- `llm/factory.py`: MiniMax 和 Qwen 固定返回 `MockLLMClient`
- `comparison/multi_judge.py`: 同步 for 循环，3 个 provider 中仅 DeepSeek 走真实 API
- `comparison/review_agent.py`: Review Agent 无星评机制，仅做 agreement_level 判定

V3 的 CrewAI 方案因对国产模型（DeepSeek/MiniMax）需要 monkey patch 且维护成本高，**不引入 V4**。

### 1.2 目标

在不动已验证模块（matching/、parsers/、doc_generators/、semantic_judge.py、case_builder.py、report_generator.py、reverse_judge.md、forward_judge.md、config.py）的前提下，仅改造多智能体调用层：

1. MiniMax/Qwen 从 MockLLMClient 切换为真实 API 客户端
2. 多智能体裁判从同步 for 循环改为 asyncio 并行
3. Review Agent 增加星评机制（1-3 星 + 语义一致性判断）
4. 支持自动降级：API Key 不全时回退单模型模式

### 1.3 非目标

- 不修改 matching/ 匹配逻辑
- 不修改 parsers/ 解析逻辑
- 不修改 doc_generators/ 输出格式
- 不修改已验证的 prompt 文件（reverse_judge.md、forward_judge.md）
- 不引入 CrewAI 框架依赖
- 不改变现有 API 接口契约

---

## 2. 核心设计决策

| 决策 | 结论 | 理由 |
|------|------|------|
| 编排方式 | V4 `get_llm()` + asyncio | 零框架依赖，Docker 友好，国产模型原生兼容 |
| 对比 Agent 关系 | 完全独立并行，互不知晓对方存在 | 保证结果多样性，差异仅来源于底层模型 |
| Agent 角色定义 | 复用已有 `reverse_judge.md` prompt，不区分 Agent role | 已验证 prompt 不动 |
| 并行方式 | `asyncio.to_thread()` + `asyncio.gather()` | LLM 调用是 I/O 密集型，asyncio 足够 |
| Review 权威性 | Review 结论即为终审 | 避免无限循环，保证管线收敛 |

---

## 3. 改造范围

### 3.1 改造文件

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `llm/factory.py` | 扩展 | registry 增加 minimax/qwen 真实客户端入口；增加 `get_available_providers()` |
| `llm/minimax_client.py` | **新增** | MiniMax OpenAI-compatible 客户端 |
| `llm/qwen_client.py` | **新增** | Qwen 客户端（通过阿里云 DashScope 或 OpenAI-compatible endpoint） |
| `comparison/multi_judge.py` | 改造 | 同步 for 循环 → asyncio 并行真实调用 |
| `comparison/review_agent.py` | 增强 | 增加星评机制（1-3 星 + 语义一致性判断 + 打星规则） |
| `models.py` | 增强 | 新增 `AgentJudgment`；`MultiJudgeResult.judgments` 弱类型 → 强类型；`ConsensusResult` 补全字段 |
| `pipeline.py` | 微调 | Step 6-7 改为 asyncio 调用方式 |
| `prompts/consensus.md` | 增强 | 追加星评规则到已有 prompt |

### 3.2 不动文件

`matching/`、`parsers/`、`doc_generators/`、`semantic_judge.py`、`case_builder.py`、`report_generator.py`、`reverse_judge.md`、`forward_judge.md`、`config.py`

---

## 4. LLM 层设计

### 4.1 factory.py 扩展

```python
# 扩展后的 get_llm()
_PROVIDER_REGISTRY = {
    "deepseek": lambda: DeepSeekClient(
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
    ),
    "minimax": lambda: MiniMaxClient(
        api_key=os.getenv("MINIMAX_API_KEY", ""),
        base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimax.chat"),
        model=os.getenv("MINIMAX_MODEL", "abab6.5s-chat"),
    ),
    "qwen": lambda: QwenClient(
        api_key=os.getenv("QWEN_API_KEY", ""),
        base_url=os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        model=os.getenv("QWEN_MODEL", "qwen-plus"),
    ),
}


def get_llm(provider: str):
    """统一入口。USE_MOCK_LLM=1 时全走 mock；否则查 registry。"""
    if os.getenv("USE_MOCK_LLM", "0") == "1":
        return MockLLMClient()

    if provider not in _PROVIDER_REGISTRY:
        return MockLLMClient()  # 降级兜底

    client = _PROVIDER_REGISTRY[provider]()
    return client


def get_available_providers() -> list[str]:
    """检查哪些 provider 的 API Key 已配置。用于自动降级决策。"""
    key_map = {
        "deepseek": "DEEPSEEK_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "qwen": "QWEN_API_KEY",
    }
    return [p for p, env_key in key_map.items() if os.getenv(env_key)]
```

### 4.2 新增客户端接口（duck typing，无需基类）

所有客户端实现相同签名：

```python
class MiniMaxClient:
    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        max_retries: int = 2,
    ) -> ChatResponse:
        """OpenAI-compatible /v1/chat/completions endpoint"""
        ...

class QwenClient:
    def chat(self, messages, temperature=0.3, max_tokens=4096, max_retries=2) -> ChatResponse:
        ...
```

`ChatResponse` 为简单 dataclass：
```python
@dataclass
class ChatResponse:
    content: str
    model: str = ""
    usage: dict = field(default_factory=dict)
```

---

## 5. Judge 层设计（multi_judge.py 改造）

### 5.1 核心变化

**改造前（mock 同步循环）：**
```python
def judge_with_panel(cases, providers):
    for case in cases:
        for provider in providers:
            client = get_llm(provider)
            result = client.chat(...)  # 同步阻塞
```

**改造后（asyncio 并行）：**
```python
async def _judge_with_provider(case, provider: str) -> AgentJudgment:
    """单个 provider 对单条 case 的异步裁判"""
    client = get_llm(provider)
    prompt = _build_reverse_user_prompt(case)  # 复用现有 prompt 构建函数
    try:
        response = await asyncio.to_thread(
            client.chat,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return _parse_to_judgment(response.content, provider, case.case_id)
    except Exception as e:
        return AgentJudgment(
            agent_name=provider,
            coverage_status="error",
            confidence=0.0,
            analysis=f"调用失败: {str(e)}",
        )


async def judge_single_case(case, providers: list[str]) -> MultiAgentResult:
    """单条 case 的两阶段编排"""
    # Phase 1: 并行对比（所有 provider 同时调用，互不知晓）
    phase1_results = await asyncio.gather(
        *[_judge_with_provider(case, p) for p in providers],
        return_exceptions=True,
    )
    judgments = [r for r in phase1_results if not isinstance(r, Exception)]

    # Phase 2: Review 仲裁
    review = await _call_review_agent(case, judgments)

    return MultiAgentResult(
        case_id=case.case_id,
        agent_results=judgments,
        review=review,
        final_coverage_status=_derive_final_status(judgments, review),
    )


async def multi_agent_judge(cases, providers=None):
    """批量入口。自动降级：API Key 不全则只用可用 provider。"""
    if providers is None:
        providers = get_available_providers()
    if not providers:
        raise RuntimeError("没有可用的 LLM Provider（检查 .env 中的 API Key）")

    results = []
    for case in cases:
        result = await judge_single_case(case, providers)
        results.append(result)
    return results
```

### 5.2 保持不变的接口

- `_build_reverse_user_prompt(case)` — 复用现有函数
- `_call_reverse_judge_api()` — 逻辑合并到 `_judge_with_provider()`
- 输出写入 `multi_judge_results.json` — 路径和格式不变

---

## 6. Review 层设计（review_agent.py 增强）

### 6.1 星评机制

采纳用户 2026-07-23 设计文档中的打星规则，整合到 `consensus.md` prompt 和 `review_agent.py` 中：

```
★★★（完全一致）：
三位专家的结论在语义上一致 —— 即使措辞不同或 coverage_status 标签不同，
各自 analysis 描述的核心判断是同一回事。

★★☆（部分分歧）：
两位专家结论语义一致，另一位有实质性分歧。
分歧不是措辞差异，而是对覆盖性的判断方向不同。

★☆☆（严重分歧）：
三位专家的结论互不一致，各自表达了实质性不同的判断。
```

### 6.2 review_agent.py 改造

```python
async def _call_review_agent(case, judgments: list[AgentJudgment]) -> ReviewResult:
    """调用 Review Agent。固定使用 DeepSeek。"""
    prompt = _build_review_user_prompt(case, judgments)
    client = get_llm("deepseek")
    try:
        response = await asyncio.to_thread(
            client.chat,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return _parse_review_result(response.content)
    except Exception as e:
        return ReviewResult(
            star_rating=0,
            consensus="error",
            analysis=f"Review 调用失败: {str(e)}",
        )


def _derive_final_status(judgments, review) -> str:
    """根据星评和 judgment 推导最终 coverage_status"""
    if review.star_rating == 3:
        # 完全一致：取任意一致方的结果
        return judgments[0].coverage_status
    elif review.star_rating == 2:
        # 部分分歧：取 majority 方的结果
        for j in judgments:
            if j.agent_name in review.consistent_agents:
                return j.coverage_status
    else:
        # 严重分歧或错误：标记 needs_review
        return "needs_review"
```

### 6.3 consensus.md prompt 增强

在已有 prompt 末尾追加星评规则段，不改已有内容。

---

## 7. 数据模型（models.py 增强现有模型 + 新增 AgentJudgment）

V4 已有 `MultiJudgeResult` / `MultiJudgeOutput` / `ConsensusResult` / `ConsensusOutput`（第 286-324 行），本次**增强**现有类型，不删不改已有字段。

### 7.1 新增 `AgentJudgment`（强类型替换 `dict`）

```python
class AgentJudgment(BaseModel):
    """单个对比 agent 的输出。替代现有 MultiJudgeResult.judgments 中的 dict。"""
    agent_name: str              # "deepseek" | "minimax" | "qwen"
    coverage_status: str          # covered|partial|missing|inconsistent|needs_review|error
    difference_type: str = ""
    missing_points: list[str] = []
    inconsistent_points: list[str] = []
    analysis: str = ""
    suggested_action: str = ""
    confidence: float = 0.0
    raw_response: str = ""
```

### 7.2 `MultiJudgeResult` — 弱类型 → 强类型

```python
# 改造前
class MultiJudgeResult(BaseModel):
    case_id: str
    judgments: dict[str, dict] = {}   # provider_name → judgment dict（弱类型）

# 改造后
class MultiJudgeResult(BaseModel):
    case_id: str
    judgments: dict[str, AgentJudgment] = {}  # provider_name → AgentJudgment（强类型）
```

### 7.3 `ConsensusResult` — 补全 Review 字段

```python
# 改造前（已有字段保留，新增带 + 的字段）
class ConsensusResult(BaseModel):
    case_id: str
    model_results: dict[str, dict]     # 保留
    agreement_level: str = ""          # 保留
    star_rating: int = 0               # 保留
    final_coverage_status: str = ""    # 保留
    final_analysis: str = ""           # 保留
    confidence: float = 0.0            # 保留
    # ↓ 新增字段
    consistent_agents: list[str] = []  # + 语义一致的 agent 列表
    divergent_agents: list[str] = []   # + 偏离的 agent 列表

# MultiJudgeOutput / ConsensusOutput 保持现有结构不变
```

---

## 8. Pipeline 集成（pipeline.py 微调）

Step 6-7 调用方式从同步改为异步：

```python
# 改造前（pipeline.py Step 6-7）
# multi_results = judge_with_panel(cases, providers)
# consensus = review_judgments(multi_results)

# 改造后
import asyncio

async def _step_multi_agent_judge(cases, providers=None):
    if providers is None:
        providers = get_available_providers()
    return await multi_agent_judge(cases, providers)

# 在 run_reverse_pipeline() 中：
# multi_results = asyncio.run(_step_multi_agent_judge(cases, providers))
# consensus = multi_results  # MultiAgentResult 已包含 review 结果
```

---

## 9. 自动降级策略

```python
def resolve_providers(requested: list[str] | None = None) -> list[str]:
    """
    1. 未指定 providers → 用所有已配置 API Key 的 provider
    2. 指定了 providers → 过滤出已配置 Key 的
    3. 全部不可用 → 抛异常
    4. 仅 1 个可用 → 跳过 Review Agent（单模型不需要共识）
    """
    available = get_available_providers()
    if requested:
        available = [p for p in requested if p in available]
    if not available:
        raise RuntimeError("没有可用的 LLM Provider")
    return available
```

---

## 10. 输出文件

| 文件 | 格式 | 内容 |
|------|------|------|
| `multi_judge_results.json` | 现有格式兼容 | per-provider judgment + 追加 `agent_name` 字段 |
| `consensus_results.json` | 扩展 | 追加 `star_rating`、`consistent_agents`、`divergent_agents` |
| 其他 5 类对外下载 | 不变 | doc_generators 不改 |

---

## 11. 配置

`.env` 新增（MiniMax 和 Qwen 的 Key）：

```bash
# ── MiniMax ──
MINIMAX_API_KEY=sk-xxx
MINIMAX_BASE_URL=https://api.minimax.chat
MINIMAX_MODEL=abab6.5s-chat

# ── Qwen (阿里云 DashScope) ──
QWEN_API_KEY=sk-xxx
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
```

已有 `DEEPSEEK_*` 和 `USE_MOCK_LLM` 保持不变。

---

## 12. 不纳入本次范围

| 项目 | 说明 |
|------|------|
| Case 级并行 | 多个 case 同时走两阶段编排 |
| 自查重审 | 低星触发对 divergent agent 的二次审查 |
| 正向管线 | 本次仅改反向管线 Step 6-7 |
| CrewAI 框架 | 不引入 |
| matching/ 匹配逻辑 | 不动 |
| doc_generators/ 输出格式 | 不动 |

---

## 13. 与 V3 CrewAI 设计的映射

| 用户 2026-07-23 CrewAI 设计 | V4 Factory 实现 |
|---|---|
| `Agent(role, goal, backstory)` | 已有 `reverse_judge.md` prompt（复用，不动） |
| `Task(description, expected_output)` | `_build_reverse_user_prompt(case)` 函数（复用，不动） |
| `crew.kickoff_async()` | `asyncio.to_thread(client.chat, ...)` |
| `output_pydantic=AgentJudgment` | `_parse_to_judgment()` → `AgentJudgment.model_validate()` |
| `context=[task_a, task_b]` | `_build_review_user_prompt(case, judgments)` |
| 星评规则 §5.2 | 直接采纳，写入 `consensus.md` + `review_agent.py` |
| 降级策略 | `get_available_providers()` + `resolve_providers()` |
| 数据模型 §7 | 直接采纳 `AgentJudgment` / `ReviewResult` / `MultiAgentResult` |
