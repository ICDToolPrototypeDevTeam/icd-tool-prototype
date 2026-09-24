# 问题排查记录

本文档用于记录 **ICD工具原型** 开发和验证过程中出现的问题、原因分析、修复方式和验证结果。

## 1. 记录原则

1. 每个明确问题单独记录；
2. 问题应尽量包含复现方式、原因分析、修复内容和验证结果；
3. 不记录与问题无关的开发过程；
4. 不记录大段无关日志；
5. 不在问题排查过程中顺手扩大修改范围；
6. 如果问题尚未定位，应明确标记为“未定位”；
7. 如果问题尚未验证，应明确标记为“尚未验证”。

## 2. 问题状态

建议问题状态包括：

| 状态              | 含义         |
| --------------- | ---------- |
| `open`          | 问题已发现，尚未解决 |
| `investigating` | 问题正在定位     |
| `fixed`         | 已完成修复      |
| `verified`      | 已验证修复有效    |
| `wontfix`       | 确认暂不修复     |
| `duplicate`     | 与已有问题重复    |

## 3. 问题编号规则

建议问题编号格式如下：

```text
BUG-YYYYMMDD-序号
```

示例：

```text
BUG-20260610-001
BUG-20260610-002
```

## 4. 记录模板

```text
## BUG-YYYYMMDD-001：问题标题

### 状态

open / investigating / fixed / verified / wontfix / duplicate

### 发现日期

YYYY-MM-DD

### 关联 Issue / PR

- Issue #编号
- PR #编号

### 问题现象

简要说明问题表现。

### 复现方式

1. 
2. 
3. 

### 影响范围

说明影响的功能、模块或文件。

### 原因分析

说明定位到的原因。  
如尚未定位，应写“尚未定位”。

### 修复方案

说明采用的最小修复方案。

### 修改文件

1. 
2. 

### 验证方式

1. 

### 验证结果

说明验证是否通过。  
如尚未验证，应写“尚未验证”。

### 遗留问题

1. 
```

## 5. 问题记录

### BUG-20260611-001：后端模块导入路径错误导致 uvicorn 启动失败

#### 状态

verified

#### 发现日期

2026-06-11

#### 关联 Issue / PR

- Issue #3

#### 问题现象

执行 `uvicorn app.main:app --reload --port 8000` 时报错：

```
ModuleNotFoundError: No module named 'models'
```

#### 复现方式

1. 进入 `backend/` 目录
2. 执行 `uvicorn app.main:app --host 127.0.0.1 --port 8000`
3. 访问 `/api/health` 报错

#### 影响范围

后端无法启动，所有 API 接口不可用。

#### 原因分析

`main.py`、`job_manager.py`、`pipeline.py` 中使用了 `from models import ...`、`from job_manager import ...`、`from pipeline import ...` 等导入路径。

当 uvicorn 从 `backend/` 目录启动时，Python 模块搜索路径为 `backend/` 目录本身，而非 `backend/app/`。因此 `models.py`（实际位于 `backend/app/models.py`）无法通过 `from models` 找到，必须使用 `from app.models` 路径。

#### 修复方案

统一修改所有后端模块内的导入路径，使用 `app.` 前缀：

- `from models import ...` → `from app.models import ...`
- `from job_manager import ...` → `from app.job_manager import ...`
- `from pipeline import ...` → `from app.pipeline import ...`

#### 修改文件

1. `backend/app/main.py`
2. `backend/app/job_manager.py`
3. `backend/app/pipeline.py`

#### 验证方式

1. `curl http://127.0.0.1:8000/api/health` 返回 `{"status":"ok"}`

#### 验证结果

已验证通过。后端正常启动，health 接口返回正常。

---

### BUG-20260611-002：pipeline.py 内部占位模块导入路径错误

#### 状态

verified

#### 发现日期

2026-06-11

#### 关联 Issue / PR

- Issue #3

#### 问题现象

通过 `POST /api/eoicd/analyze` 创建任务后，任务状态立即变为 `failed`，错误信息为：

```
任务处理失败: No module named 'parsers'
```

#### 复现方式

1. 启动后端 `uvicorn app.main:app --host 127.0.0.1 --port 8000`
2. 执行 `curl -X POST /api/eoicd/analyze` 上传 mock 文件
3. 查询任务状态 `GET /api/jobs/{job_id}` 返回 `failed`

#### 影响范围

端到端流程无法完成，pipeline 执行失败。

#### 原因分析

`pipeline.py` 中使用 `from parsers.placeholder import ...`、`from crew.placeholder import ...` 等导入路径，但 uvicorn 从 `backend/` 目录运行，`parsers/` 等目录实际位于 `backend/app/parsers/`。因此必须使用 `from app.parsers.placeholder import ...` 路径。

#### 修复方案

修改 `pipeline.py` 中所有模块占位导入路径：

- `from parsers.placeholder import ...` → `from app.parsers.placeholder import ...`
- `from crew.placeholder import ...` → `from app.crew.placeholder import ...`
- `from scoring.placeholder import ...` → `from app.scoring.placeholder import ...`
- `from docx.placeholder import ...` → `from app.docx.placeholder import ...`

#### 修改文件

1. `backend/app/pipeline.py`

#### 验证方式

1. `POST /api/eoicd/analyze` 创建任务
2. `GET /api/jobs/{job_id}` 查询状态，确认任务从 `pending` → `running` → `completed`
3. `GET /api/jobs/{job_id}/outputs/requirements` 下载文档返回 200

#### 验证结果

已验证通过。任务正常完成，下载接口返回 200，输出文件已生成。

---

### BUG-20260612-001：Docker Compose 环境下前端 Vite proxy 404

#### 状态

verified

#### 发现日期

2026-06-12

#### 关联 Issue / PR

- Issue #4

#### 问题现象

使用 Docker Compose 启动前后端服务后，前端点击"提交分析"按钮，浏览器返回 `404 Not Found`。Chrome F12 Network 显示：
- 请求网址：`http://localhost:3000/api/eoicd/analyze`
- 状态代码：404

#### 复现方式

1. `docker-compose up --build`
2. 浏览器访问 `http://localhost:3000`
3. 上传文件并点击"提交分析"
4. 浏览器返回 404

#### 影响范围

Docker Compose 环境下前端无法调用后端 API，端到端流程中断。

#### 原因分析

端口 3000 被**两个进程**同时监听：

| 进程 | PID | 地址 | 说明 |
|---|---|---|---|
| Docker 容器（Vite） | 29672 | `0.0.0.0:3000` | 正常 |
| 本地 Node 进程（旧的 Vite dev server） | 25996 | `[::1]:3000` | 未关闭 |

浏览器优先走 IPv6 `[::1]:3000`，连接到本地旧进程（PID 25996），该进程不是 Vite 开发服务器，返回 404。而 Docker 容器监听在 `0.0.0.0:3000`，浏览器 IPv4 连接正常，但 IPv6 被旧进程截获。

#### 修复方案

1. 杀掉占用 `[::1]:3000` 的本地 Node 进程：`taskkill /PID 25996 /F`
2. 以后每次启动 Docker Compose 前，确认没有其他 Node 进程占用 3000 端口
3. vite.config.ts 的 proxy 配置本身正确（`target: process.env.VITE_PROXY_TARGET || 'http://localhost:8000'`），Docker 中设置为 `http://host.docker.internal:8000`

#### 修改文件

1. `frontend/vite.config.ts`（proxy target 配置）
2. `docker-compose.yml`（VITE_PROXY_TARGET 环境变量）
3. `frontend/Dockerfile`（简化，仅保留基础镜像）

#### 验证方式

1. `docker-compose down`
2. 确认无本地 node 进程占用 3000 端口：`netstat -ano | grep ":3000"`
3. `docker-compose up --build`
4. `curl http://localhost:3000/api/health` 返回 HTML（Vite 页面，非 404）
5. 前端上传文件，任务状态变为 `completed`，下载链接可用

#### 验证结果

已验证通过。杀掉 PID 25996 后，端到端流程正常：任务 `pending` → `running` → `completed`，结果摘要显示需求条目数 3、差异条目数 2，两个 docx 下载链接可用。

### BUG-20260617-001：Docker Compose 启动时 `.env` 文件缺失导致启动失败

#### 状态

verified

#### 发现日期

2026-06-17

#### 关联 Issue / PR

- Issue #5

#### 问题现象

执行 `docker compose up --build` 时报错：

```
env file C:\Users\wdtjx\Desktop\icd-tool-prototype\backend\.env not found:
GetFileAttributesEx ...: The system cannot find the file specified.
```

#### 复现方式

1. 在项目根目录执行 `docker compose up --build`
2. 启动过程中抛出 `.env not found` 错误，容器未启动

#### 影响范围

Docker Compose 启动流程；Issue #5 提交前的阻塞性 bug。

#### 原因分析

`docker-compose.yml` 中 `env_file: ./backend/.env` 强制要求 `.env` 文件存在；但项目规则规定 `.env` 不入 Git，由用户本地创建（仅 `backend/.env.example` 作为占位）。本 Issue 引入 24 个真实 Provider 环境变量（MINIMAX_*/DEEPSEEK_*），用户可能还没填本地 `.env`，必须让 `.env` 可选。

#### 修复方案

把 `env_file: ./backend/.env` 改为 Docker Compose v2.24+ 支持的 optional 形式：

```yaml
env_file:
  - path: ./backend/.env
    required: false
```

`environment` 段已用 `${VAR:-default}` 形式给所有变量留好兜底，因此缺 `.env` 不会导致任何变量未定义。

#### 修改文件

1. `docker-compose.yml`

#### 验证方式

1. 故意不创建 `backend/.env`，执行 `docker compose up --build`
2. 容器应正常启动，`backend-1` 日志显示 `Uvicorn running on http://0.0.0.0:8000`
3. `GET /api/health` 返回 `{"status":"ok"}`
4. `USE_MOCK_LLM=1` 环境下端到端可 completed，4 个下载接口 200

#### 验证结果

已验证通过。删除 `backend/.env`（如果存在）后 `docker compose up --build` 直接成功；当前 Docker Compose 版本 v5.1.4 支持 `required: false` 语法。

#### 遗留问题

如未来 Docker Compose 降级到 < v2.24，需回退为 `env_file: ./backend/.env` 并要求用户本地创建 `.env`，或全部使用 `environment` 占位。

---

### BUG-20260617-002：Docker Compose 构建失败：uvicorn 版本与 crewai 间接依赖 mcp 冲突

#### 状态

verified

#### 发现日期

2026-06-17

#### 关联 Issue / PR

- Issue #5

#### 问题现象

`docker compose up --build` 后端镜像构建阶段失败：

```
ERROR: Cannot install crewai, uvicorn==0.27.1 and uvicorn[standard]==0.27.1
because these package versions have conflicting dependencies.

The conflict is caused by:
  - The user requested uvicorn==0.27.1
  - uvicorn[standard] 0.27.1 depends on uvicorn 0.27.1
  - chromadb 1.1.0 depends on uvicorn>=0.18.3
  - mcp 1.16.0+ depends on uvicorn>=0.31.1; sys_platform != "emscripten"

ERROR: ResolutionImpossible
```

#### 复现方式

1. 还原 `backend/requirements.txt` 中 `uvicorn[standard]==0.27.1`
2. `docker compose up --build`（全新环境，无 cache）
3. 后端镜像构建在 `pip install` 阶段失败，容器未启动

#### 影响范围

Docker Compose 启动；Issue #5 端到端 Docker 验证阻塞性 bug。本地 `uvicorn app.main:app` 不受影响（本地 mcp 旧版与 uvicorn 0.27.1 共存绕开了冲突）。

#### 原因分析

- `backend/requirements.txt` Issue #3 锁定 `uvicorn[standard]==0.27.1`；
- Issue #5 引入 `crewai>=1.0`，其间接依赖 `mcp>=1.16.0` 强制要求 `uvicorn>=0.31.1`；
- pip 解析器无法调和，抛出 `ResolutionImpossible`；
- 本地 `uvicorn` 直接启动能跑通，是因为本地已经装过 `mcp` 旧版 + `uvicorn 0.27.1` 形成的"既成"环境绕开了冲突；Docker 是全新环境，必须解决所有传递依赖。

#### 修复方案

按最小修改原则，**仅**把 `uvicorn` 范围放宽为 `>=0.31.1,<0.37`（与 `starlette<0.37,>=0.36.3` 兼容）：

```diff
- uvicorn[standard]==0.27.1
+ uvicorn[standard]>=0.31.1,<0.37
```

不修改 FastAPI / Starlette / CrewAI 任何版本。

#### 修改文件

1. `backend/requirements.txt`

#### 验证方式

1. `docker compose up --build` 重新构建
2. 后端镜像构建成功（pip 解析出 uvicorn 0.36.1）
3. 容器启动，`Uvicorn running on http://0.0.0.0:8000`
4. 端到端 4 个下载接口 200

#### 验证结果

已验证通过。`docker compose up --build` 构建成功，镜像缓存后 `docker compose up` 启动 2 秒内完成；端到端 5 个 API 全部 200，4 份 docx 在主机端可见。

#### 遗留问题

- 未来若 crewai 升级到 2.x，uvicorn / starlette 范围可能需要再次调整；
- sse-starlette 3.4.4 仍要 starlette>=0.49.1，与 fastapi 0.109.2 软冲突；当前通过锁定 starlette 0.36.3 绕过，未见运行期影响。

---

### BUG-20260617-003：Docker volume 路径不匹配，容器内输出文件不持久化到主机

#### 状态

verified

#### 发现日期

2026-06-17

#### 关联 Issue / PR

- Issue #5

#### 问题现象

`docker compose up --build` 启动后，后端容器内 `POST /api/eoicd/analyze` 创建任务并生成 4 份 docx，下载接口全部 HTTP 200；但主机端 `backend/app/output/{job_id}/` 下**看不到**任何文件。`docker exec` 进容器发现 docx 实际写到 `/app/app/output/{job_id}/`，而非 docker-compose.yml 中 volume 挂载的 `/app/output`。

#### 复现方式

1. `docker compose up --build`
2. `POST /api/eoicd/analyze` 上传样例文件
3. `GET /api/jobs/{id}/outputs/requirements` 返回 200 + 正确 docx
4. 检查主机端 `backend/app/output/{job_id}/` → 不存在该 job 目录
5. `docker exec icd-tool-prototype-backend-1 ls /app/output` → 没有该 job 目录
6. `docker exec icd-tool-prototype-backend-1 ls /app/app/output` → 该 job 目录存在

#### 影响范围

- Docker 部署下，所有任务生成的 docx / 上传的输入文件**仅存在于容器内**，容器重启即丢失；
- 端到端演示体验受损（用户找不到产物文件）；
- 实际功能正常（API + 下载都 200），所以**未**阻塞前两次 Issue #5 验证，但属于显著缺陷。

#### 原因分析

- `backend/app/main.py` 通过 `TASK_DIR = Path(__file__).parent / 'output'` 计算输出目录。在容器内 `__file__ = /app/app/main.py`，因此 `TASK_DIR = /app/app/output`；
- `docker-compose.yml` 原 volume 挂载 `./backend/app/output:/app/output`，把主机目录挂到了容器内**错误的位置** `/app/output`；
- 容器内 `main.py` 写到 `/app/app/output/...`（容器层），volume 不会拦截，导致文件不持久化到主机；
- 此 bug 早在 Issue #4 引入 docker-compose.yml 时就存在，Issue #5 之前没暴露是因为本地 uvicorn 测试时 `Path(__file__).parent = backend/app`，`backend/app/output` 与代码计算路径一致，未出现差异。

#### 修复方案

按最小修改原则，**仅**把 volume 挂载目标改为 `/app/app/output`：

```diff
volumes:
- - ./backend/app/output:/app/output
+ - ./backend/app/output:/app/app/output
```

不修改 `main.py` 路径计算逻辑（保持与 Issue #4 一致，避免扩大修改面）。

#### 修改文件

1. `docker-compose.yml`

#### 验证方式

1. `docker compose up --build` 重新启动
2. `POST /api/eoicd/analyze` 创建任务
3. `GET /api/jobs/{id}/outputs/{requirements,minimax-requirements,deepseek-requirements,difference-report}` 4 个接口全部 200
4. 检查主机端 `backend/app/output/{job_id}/` → **应能看到** 4 份 docx + 上传文件
5. `docker compose down` 销毁容器，再次 `docker compose up`
6. 重新创建新任务 → 新 docx 仍出现在主机 `backend/app/output/{新job_id}/`

#### 验证结果

已验证通过。任务 `642da9c0-b656-4af1-b748-be693e07f800` 生成的 4 份 docx 在主机端 `backend/app/output/642da9c0-.../` 可见，文件大小与 API content-length 一致（37632 / 37696 / 37806 / 38146 字节）。`docker compose down` 销毁容器后主机文件仍保留（volume 不会随容器销毁而删除）。

#### 遗留问题

- `main.py` 仍使用 `Path(__file__).parent / 'output'` 这种"隐式相对路径"约定，对打包/部署路径敏感；后续如做正式镜像（PyInstaller / wheel）需考虑改为显式配置项；本 Issue 不处理。
- 容器销毁**不会**删除主机端 output 目录（这是预期行为，但若希望"任务完成即清理"需另外加 cleanup 逻辑）。

---

### BUG-20260622-001：CrewAI Process.sequential 上下文污染导致双模型 scoring 输出完全一致

#### 状态

verified

#### 发现日期

2026-06-22

#### 关联 Issue / PR

- Issue #16

#### 问题现象

真实 MiniMax / DeepSeek 跑 scoring 阶段时，两个模型对同一 chunk 的 2 份候选的评分结果（score 值、recommended_is_best 标记、评语）完全一致，看不出任何区分度。无论在 Agent 定义中如何调整角色描述和 temperature，输出始终相同。

#### 复现方式

1. `USE_MOCK_LLM=0` 启动后端
2. 上传样例文件创建任务
3. 查看 scoring 输出：MiniMax 和 DeepSeek 的 `ChunkAgentScoreResult` 完全一致

#### 影响范围

Scoring 阶段失去多模型交叉验证意义；评分择优结果不可信。

#### 原因分析

CrewAI 的 `Process.sequential` 模式下，前序 Task 的 raw output 会被自动注入到后续 Task 的上下文中（即使未显式设置 `context` 参数）。scoring crew 中 4 个 scoring Task 顺序执行，第一个 Task 的输出（含完整评分 JSON）被注入第二个 Task，第二个被注入第三个……导致后续模型直接复读前序输出。

#### 修复方案

在所有 generation 和 scoring Task builder 中显式设置 `context=None`，阻止 CrewAI 自动将前序 Task 的 raw output 注入后续 Task 上下文。

#### 修改文件

1. `backend/app/crew/tasks.py`

#### 验证方式

1. 重新运行 scoring 流程，检查双模型评分结果是否有明显区分度
2. 确认不同模型的 score 值和 recommended_is_best 不再完全相同

#### 验证结果

已验证通过。`context=None` 后双模型 scoring 输出有明显区分度。

---

### BUG-20260622-002：多模型共用 OPENAI_API_KEY 导致凭证冲突

#### 状态

verified

#### 发现日期

2026-06-22

#### 关联 Issue / PR

- Issue #16

#### 问题现象

同时配置 MiniMax 和 DeepSeek 后，其中一个模型的 API 调用返回认证错误或路由到错误的 Base URL。

#### 复现方式

1. 在 `.env` 中同时配置 `MINIMAX_API_KEY`、`MINIMAX_BASE_URL`、`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`
2. 由于 litellm 默认读取 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL` 环境变量，两个模型共用同一组凭证导致冲突

#### 影响范围

真实 LLM 模式下，MiniMax 和 DeepSeek 无法同时正常工作。

#### 原因分析

LiteLLM 默认从 `OPENAI_API_KEY` / `OPENAI_BASE_URL` 环境变量读取凭证。两个模型配置不同的 API Key 和 Base URL 时，无法通过单一环境变量区分。

#### 修复方案

实现 `_provider_creds` 字典 + `_litellm_with_fallback` 函数：按模型名动态匹配凭证，在每次 API 调用时注入正确的 api_key 和 api_base，不依赖全局 `OPENAI_API_KEY` 环境变量。

#### 修改文件

1. `backend/app/llm/factory.py`

#### 验证结果

已验证通过。双模型各自使用正确的 API Key 和 Base URL，不再冲突。

---

### BUG-20260627-002：DeepSeek TOOLS mode 禁用 thinking 导致 scoring 质量下降

#### 状态

verified

#### 发现日期

2026-06-27

#### 关联 Issue / PR

- Issue #17-18-19

#### 问题现象

DeepSeek 在 TOOLS mode 下 `thinking=disabled`（CrewAI/Instructor 强制要求），导致 scoring 等复杂推理任务质量下降，评分结果缺乏区分度和合理理由。

#### 复现方式

1. DeepSeek TOOLS mode 下运行 scoring
2. 观察评分结果：分数分布集中、评语泛泛、缺乏横向对比理由

#### 影响范围

DeepSeek scoring 质量和可信度。

#### 原因分析

CrewAI 的 TOOLS mode 规范要求 `thinking=disabled`（否则 tool_calls 可能被 `<think>` 标签干扰）。但 DeepSeek V4 的 scoring 等复杂推理任务需要 thinking 能力才能产出有区分度的评分。

#### 修复方案

将 DeepSeek 路径从 `Mode.TOOLS` + `thinking=disabled` 切换为 `Mode.MD_JSON` + thinking 保留。新增 `extract_json_from_codeblock()` 函数，从 DeepSeek 的 markdown 代码块输出中自动跳过 `<think>` 标签提取 JSON，恢复 thinking 能力的同时确保结构化输出正确。

#### 修改文件

1. `backend/app/llm/factory.py`

#### 验证方式

1. DeepSeek MD_JSON 模式下运行 scoring
2. 检查评分结果是否有明显区分度
3. 确认 JSON 解析正确（markdown 代码块 → Pydantic 对象）

#### 验证结果

已验证通过。DeepSeek MD_JSON + thinking 模式下 scoring 质量明显恢复，JSON 解析正确。

---

### BUG-20260629-001：Excel 数据在 chunk → task → prompt 链路中字段错位

#### 状态

verified

#### 发现日期

2026-06-29

#### 关联 Issue / PR

- Issue #17-18-19 后续清理

#### 问题现象

`tasks.py` 中 generation task 构建时传入 `excel_data=chunk.tables`，但 `chunk.tables` 的语义是 Word 内嵌表格。Word+Excel 路径下 LLM 收到的是 Word 表格而非 Excel 数据；Excel-only 路径下碰巧正确（因为 `tables` 被误填了 `build_nested_sheets()` 的输出）。

同时 `chunk.excel_data`（类型为 `ParsedEoICDExcel`）虽然被 parser 赋值，但 tasks.py 从未读取，属于死字段。

#### 原因分析

`_excel_to_chunk()` 中把 `build_nested_sheets()` 结果放入 `tables` 字段（`list[dict]`），原始 `ParsedEoICDExcel` 放入 `excel_data`。tasks.py 读取 `chunk.tables` 当 Excel 数据传，恰好绕过了 `excel_data` 字段。两个字段语义和赋值都错位了。

#### 修复方案

1. `EoICDChunk.excel_data` 类型从 `Optional[ParsedEoICDExcel]` 改为 `list[dict]`，直接存 `build_nested_sheets()` 输出
2. `_excel_to_chunk()`: `tables=[]`，`excel_data=build_nested_sheets(parsed_excel)`
3. `parse_inputs()` Word+Excel 路径: `build_nested_sheets(eoicd_excel)` 替代原始 `ParsedEoICDExcel`
4. `tasks.py`: `excel_data=chunk.tables` → `excel_data=chunk.excel_data`

#### 修改文件

1. `backend/app/models.py`
2. `backend/app/parsers/__init__.py`
3. `backend/app/crew/tasks.py`

#### 验证方式

1. 容器内验证 `EoICDChunk.excel_data` 类型为 `list[dict]`
2. Excel-only 路径下 generation prompt 收到的 excel_data 为三层嵌套结构

#### 验证结果

代码验证已通过。实际 Excel 上传测试待进行。

---

### BUG-20260730-001：追溯表预筛选因数据覆盖不全导致可匹配 HLR 被误判为"无匹配"

#### 状态

verified

#### 发现日期

2026-07-30

#### 关联 Issue / PR

- Issue #43：追溯表预筛选兜底机制与协议开销字段过滤

#### 问题现象

上传包含追溯表的 V4 任务后，部分 HLR 在追溯表预筛选阶段被标记为"无匹配"，但经全量 EoICD 匹配验证后这些 HLR 实际存在可匹配的 EoICD Block。追溯表数据覆盖不全时，预筛选反而缩小了搜索范围导致漏判。

同时，A429 协议开销字段（如 `xxx/SDI`、`xxx/LABEL` 等）出现在追溯索引的有效候选列表中，这些 block_key 本身不应参与匹配却占用了索引空间。

#### 原因分析

1. 追溯 Excel 中标注的 ICD 映射关系（ERD → ICD Label）可能不完整，部分 HLR 的 label 在追溯表中没有对应映射条目，导致 `hlr_to_blocks` 索引中无该 HLR 的候选 block。
2. 旧流程中预筛选结果直接作为最终匹配结果，无兜底机制，导致预筛选失败的 HLR 直接判为"无匹配"。
3. `trace_parser.py` 在构建 block_key 映射时未过滤协议开销后缀（`/SDI`、`/LABEL`、`/PARITY`、`/SSM`、`/OCTLBL`），导致无意义的协议开销 block 混入候选列表。

#### 修复方案

1. `trace_parser.py`：新增 `_PROTOCOL_BLOCKKEY_SUFFIXES` 常量，在 `build_trace_index()` 的 block_key 映射阶段过滤以协议开销后缀结尾的条目。
2. `pipeline.py`：`_match_reverse_with_trace()` 中，Group A 预筛选完成后收集"无匹配"HLR，对它们触发全量 EoICD 匹配作为兜底；新增 `_count_match_types()` 辅助函数统计兜底前后匹配分布。

#### 修改文件

1. `backend/app/v4/matching/traceability/trace_parser.py`
2. `backend/app/v4/pipeline.py`

#### 验证方式

1. 上传包含追溯表的 V4 任务，检查后端日志确认兜底触发情况
2. 检查反向匹配结果中无匹配 HLR 数量是否合理减少
3. 验证协议开销 block_key 不出现在追溯索引中

#### 验证结果

已验证通过。兜底机制确保追溯表预筛选只缩小搜索范围而不引入误判。

---

## BUG-20260812-001：LLM 输出截断导致 JSON 解析失败

### 状态

fixed

### 发现日期

2026-08-12

### 问题现象

1. Multi-Judge 阶段偶发 `JSON parse error after retries: Expecting value: line 1 column 1 (char 0)`
2. DeepSeek 输出 `finish_reason=length, completion_tokens=1024` WARNING
3. HLR Labeler 阶段出现截断 WARNING，但无重试机制，直接进入 fallback 空标签

### 影响范围

- `semantic_judge.py`：`_call_judge_api` / `_call_reverse_judge_api`
- `review_agent.py`：`_call_review_api`
- `hlr_labeler.py`：`_call_label_api`（最严重，无任何重试兜底）

### 原因分析

1. **开启思考模式后 think 与 output 共享 `max_tokens`**：DeepSeek/ MiniMax / Qwen 默认开启思考模式，think block 消耗 1500-3000 tokens，留给 JSON 输出的空间不足，触发 `finish_reason=length`。
2. **初始方案只在业务层点状修补**：`_chat_with_truncation_retry` 仅覆盖 `semantic_judge.py` 和 `review_agent.py` 的 3 个调用点，`hlr_labeler.py`（`max_tokens=1024`）被遗漏。
3. **client 层未暴露截断状态**：最初尝试通过 `ChatResponse.truncated` 字段透传截断标记，但调用方需显式检查，容易遗漏。

### 修复方案

将截断自适应重试**下沉到三个 LLM client 的 `chat()` 方法内部**：

1. API 返回后检测 `finish_reason == "length"`
2. 截断时自动翻倍 `max_tokens` 重新 POST（4096→8192→16384，上限 16384）
3. 此重试独立于网络层 `max_retries`，不计入外层 retry 次数
4. 截断已在 client 内部消化，删除 `ChatResponse.truncated` 字段和 `_chat_with_truncation_retry` helper

### 修改文件

1. `backend/app/v4/llm/deepseek_client.py`
2. `backend/app/v4/llm/minimax_client.py`
3. `backend/app/v4/llm/qwen_client.py`
4. `backend/app/v4/llm/factory.py`
5. `backend/app/v4/llm/mock_llm.py`
6. `backend/app/v4/comparison/semantic_judge.py`
7. `backend/app/v4/comparison/review_agent.py`

### 验证方式

1. `python -c` import 全链路验证通过
2. 截断自适应重试效果待真实 LLM 端到端测试确认

### 验证结果

代码结构验证通过。真实 LLM 场景待后续端到端测试。

### 经验总结

1. **通用能力应放在最底层**：截断重试本质是 API 调用保障，与网络超时重试同级，应放在 client 层而非业务层。
2. **点状修补会制造盲区**：`_chat_with_truncation_retry` 覆盖了 judge/review 但漏了 labeler，导致 labeler 成为唯一无截断保护的调用方。
3. **开启思考模式后 `max_tokens` 预算需要更宽裕**：think block 消耗不可预测，初始 `max_tokens` 建议 ≥ 4096。

---

## BUG-20260828-001：minimax re-review 返回 markdown 分析 + ```json fence 时 JSON 解析失败

### 状态

verified

### 发现日期

2026-08-28

### 关联 Issue / PR

- 无（用户口头反馈）

### 问题现象

跑 `故障注入-test-minimax_api_error.docx` 时，Step 5.5 re-review 阶段 minimax 的 judgment 在 `re_review_results.json` 中始终为 `coverage_status="error"`、`analysis="API error after retries: Expecting value: line 1 column 1 (char 0)"`。deepseek / qwen 在同 case 上正常返回有效 JSON，只有 minimax 报错。导致最终 consensus 的 minimax 维度缺失，少数意见复核的可靠性下降。

### 复现方式

1. `docker compose up backend`
2. `curl -X POST /api/v4/coverage-analysis` 上传 `故障注入-test-minimax_api_error.docx` + EoICD Pub/Sub Excel，`judge_providers=minimax/deepseek/qwen`
3. 等 Step 5 完成 → Step 5.5 re-review 触发
4. 查看 `backend/output/v4/{job_id}/output/re_review_results.json`，minimax 的 re_review_judgments 为 error

### 影响范围

- `backend/app/v4/comparison/semantic_judge.py::_extract_json`：仅 re-review 路径（`_call_re_review_api` 调用）触发
- 不影响 multi-judge（`_call_judge_api`）和 review agent（`_call_review_api`），因为这两种调用 minimax 的 content 始终以 ```json fence 开头

### 原因分析

定位经过多轮：

1. **第一轮假设（错误）**：minimax 返回 HTTP 200 + empty body。验证后排除——通过 `minimax_client.py` 临时 debug print 抓到 raw response 是有 content 的（`[minimax raw] content='<think>...'`，10KB+）。
2. **第二轮定位**：raw content 形如 `<long <think> block>...</think>\n\n<markdown 分析段>\n\n```json\n{...JSON...}\n````。`_extract_json` 的 fence 检查逻辑只处理 `text.startswith("```")` 的情况；当 text 以 `<think>` 或 markdown 段落开头时直接跳到「找首个 `{`」，由于分析段中常出现零散花括号（中文括号、表格分隔），截取的不是 JSON 起点，导致 `json.loads` 失败。
3. **真实根因**：minimax re-review 调用 system prompt 较短 + 用户 prompt 中带反思规则，minimax 倾向于先输出大段 markdown 分析再附加 JSON，与 multi-judge（system prompt 强制 JSON 输出）行为不同。`_extract_json` 没有处理「先分析后 fence」这种 markdown 排版。

### 修复方案

按最小修改原则，**仅**给 `_extract_json` 增加 else 分支——text 不以 ``` 开头时，先在 text 内部用非贪婪正则搜索 ```json fence 并提取其中的 `{...}`：

```python
else:
    # text 不以 ``` 开头（minimax re-review 场景）：```json fence 前可能有
    # 大段 markdown 分析。先在 text 内部搜索 ```json fence，提取其中的 {...}；
    # 若没有 fence，再退到找首个 { 的位置。
    fence_match = re.search(
        r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```',
        text, flags=re.DOTALL,
    )
    if fence_match:
        text = fence_match.group(1).strip()
    else:
        brace_idx = text.find("{")
        if brace_idx > 0:
            text = text[brace_idx:]
```

think 块剥离、markdown fence 移除（以 ``` 开头场景）、JSON 截断修复均保留不动。

### 修改文件

1. `backend/app/v4/comparison/semantic_judge.py`

### 验证方式

1. `docker compose build backend` 重新构建
2. 跑两次 `故障注入-test-minimax_api_error.docx`（job `a57a5e68` 和 `0416ddf6`），均应正常完成 Step 5.5/5.6/6
3. 检查两次的 `re_review_results.json` 中 minimax 不再为 `error`，而是返回完整 judgment（`coverage_status` / `difference_type` / `missing_points` / `inconsistent_points` / `analysis` / `confidence` / `suggested_action` 全字段非空）

### 验证结果

已验证通过。job `a57a5e68` 和 `0416ddf6` 均正常完成，minimax re-review 返回 `coverage_status="inconsistent"` / `difference_type="不一致"` / `confidence=0.85`，与 qwen / deepseek 的判断进入共识计算。

修复完成后清理：
- `backend/app/v4/llm/minimax_client.py` 临时 debug print（成功路径 + 异常路径）全部移除
- `backend/debug_minimax_rereview.py`（独立调试脚本，201 行）删除

### 经验总结

1. **不同 prompt 模板下同一 provider 行为可能差异显著**：minimax 在 multi-judge（system prompt 强制 JSON）下返回纯 fence JSON，在 re-review（system prompt 短、prompt 鼓励反思）下返回「先 markdown 分析 + 后 fence JSON」。任何 JSON 解析逻辑都应假设 provider 不按预期排版。
2. **LLM 临时 debug print 必须覆盖 try/except 两路径**：本次修复前最初只成功路径 print，异常路径无 raw body，导致 `requests.RequestException` 抛出时无法定位是 empty body 还是其他问题。修复后两个路径都 print，才确认是 HTTP 200 + 有 content + JSON 解析失败。
3. **非贪婪正则 + `re.DOTALL` 是 fence 提取的最低成本方案**：re-review 的 markdown 分析段可能含有零散 `{`（表格、数学公式），`re.findall` 非贪婪 + DOTALL 即可正确锚定 ```json fence 内 JSON 起点。

---

### BUG-20260903-001：RPDU refine 整合后 FastAPI 入口未透传 `refine` 形参导致真实 E2E 走原 pipeline

#### 状态

verified

#### 发现日期

2026-09-03

#### 关联 Issue / PR

- Issue RPDU 适配续
- job `10c3d635`

#### 问题现象

整合同事 RPDU 优化代码到 V4 主线、`pipeline.run_reverse_pipeline` 已正确支持 `refine=True` 精化分支后，跑真实 E2E（job `10c3d635`，上传 RPDU HLR + EoICD Pub/Sub Excel）：

- `output/reverse_matches.json` 中每条 HLR 反向匹配数仍为 top_k=50 全量候选（未做无关 block 过滤、未做精确/同义词补采）；
- 与同事代码 reference case04 对照，11 条 HLR 匹配数差距大（应为 `8/11/1/5/7/9/4/4/4/6/7`，实测仍是全量 50/50/50/...）；
- `output/consensus_results.json` 5 星分布与参考结果差距大（参考 9×5★ + 1×3★ + 1×1★，实测分布偏移）。

但跑 CLI `python -m app.v4.cli reverse-analyze ...`（CLI 子命令路径）时，`pipeline.run_reverse_pipeline(refine=True)` 正确触发，与 case04 一致。证明问题出在 HTTP API 入口链路，而非 `refine` 子包或 pipeline 本身。

#### 复现方式

1. 后端启动（`uvicorn app.main:app --reload --port 8000` 或 `docker compose up`）
2. 通过 `POST /api/v4/coverage-analysis` 上传 RPDU HLR + EoICD Pub/Sub Excel，`controller_profile=rpdu`，不传 `no_refine`
3. 任务完成后检查 `backend/output/v4/{job_id}/output/reverse_matches.json`：matched_blocks 应已被 refine 过滤 + 补采（与 case04 对齐），但实测未被处理
4. 同时跑 CLI `python -m app.v4.cli reverse-analyze --controller-profile rpdu ...` 对照，CLI 输出与 case04 一致

#### 影响范围

仅影响通过 HTTP API 入口创建的 RPDU 任务；CLI 入口不受影响。所有其他 profile（AMS / FGMC / HSCU / FSECU）不受影响（`refine=False` 路径不被触发）。

#### 原因分析

Pipeline CLI 入口路径：`cli._cmd_reverse_analyze` → `profile.profile_id == "rpdu" and not args.no_refine` 判定 → `run_reverse_pipeline(refine=refine)`：CLI 链路完整传 `refine`。

Pipeline FastAPI 入口路径：`api/v4/coverage.py::coverage_analysis` → `launch_v4_pipeline(...)` → `api/v4/runner.py::run_v4_pipeline_thread(...)` → `run_reverse_pipeline(...)`：调用点漏补 `refine=...` 形参。

具体定位：`backend/app/api/v4/runner.py:241` 调用 `run_reverse_pipeline(...)` 时未传 `refine`，导致 pipeline 默认走 `refine=False` 分支，与同事代码 reference 行为差异。

代码追溯：`pipeline.run_reverse_pipeline(refine: bool = False)` 形参定义在 `backend/app/v4/pipeline.py:738`（新增），调用形参链路为：

```text
cli._cmd_reverse_analyze      → run_reverse_pipeline(refine=refine)    [CLI ✅]
api/v4/coverage.coverage_analysis → launch_v4_pipeline                [HTTP ❌ 缺 no_refine 形参]
launch_v4_pipeline           → run_v4_pipeline_thread(args=...)        [HTTP ❌ args 元组缺 no_refine]
run_v4_pipeline_thread       → run_reverse_pipeline(...)                [HTTP ❌ refine=refine 缺]
```

整条 HTTP 入口链路上 `no_refine` 形参缺失，导致 `refine` 判定逻辑无法运行。

#### 修复方案

按 debug-rules §5 最小修改原则，仅补透传链路，不顺手重构：

1. `backend/app/api/v4/coverage.py::coverage_analysis` 形参新增 `no_refine: bool = Form(False)`；
2. `launch_v4_pipeline(...)` 调用点追加 `no_refine=no_refine`；
3. `launch_v4_pipeline(...)` 函数签名新增 `no_refine: bool = False`；
4. `launch_v4_pipeline(...)` 内 `args=(job, ..., controller_profile, no_refine)` 元组追加 `no_refine`；
5. `run_v4_pipeline_thread(...)` 函数签名新增 `no_refine: bool = False`；
6. `run_v4_pipeline_thread(...)` 内计算 `refine = (profile.profile_id == "rpdu") and (not no_refine)`；
7. `run_v4_pipeline_thread(...)` 调用 `run_reverse_pipeline(...)` 时追加 `refine=refine`。

修改后 HTTP 路径与 CLI 路径行为对齐：

- `cli._cmd_reverse_analyze`：`refine = (profile.profile_id == "rpdu") and (not getattr(args, "no_refine", False))`
- `api/v4/runner.run_v4_pipeline_thread`：`refine = (profile.profile_id == "rpdu") and (not no_refine)`

两处判定表达式结构一致。

#### 修改文件

1. `backend/app/api/v4/coverage.py`（新增 `no_refine` form 字段 + 透传）
2. `backend/app/api/v4/runner.py`（`launch_v4_pipeline` 与 `run_v4_pipeline_thread` 双函数补 `no_refine` 形参与透传 + `refine` 判定）

#### 验证方式

1. `docker compose build backend` 重新构建
2. 真实 E2E：`POST /api/v4/coverage-analysis` 上传 RPDU HLR + EoICD Pub/Sub Excel（job `8e6498ab`）
3. 检查 `output/reverse_matches.json`：11 条 HLR 反向匹配数与 case04 完全一致（8/11/1/5/7/9/4/4/4/6/7）
4. 检查 `output/consensus_results.json`：5 星分布平均 4.45（9×5★ + 1×3★ + 1×1★）
5. 检查 `output/re_review_results.json`：REV-0008 触发 split → 待确认
6. AMS / FGMC / HSCU 回归：HTTP API 上传不传 `no_refine`（默认 false），行为与 RPDU 整合前字节一致

#### 验证结果

已验证通过。job `8e6498ab` 全部数据与同事代码 reference case04 完全对齐；AMS / FGMC / HSCU 回归测试不变。

#### 经验总结

1. **新增 pipeline 形参必须穿透整条调用链**：本次 `refine: bool` 形参从 `pipeline.run_reverse_pipeline` 入口补到 CLI 入口，但 HTTP API 入口的 4 层调用（`coverage_analysis` → `launch_v4_pipeline` → `run_v4_pipeline_thread` → `run_reverse_pipeline`）漏补；正确做法是改造 `pipeline.run_reverse_pipeline` 形参时同步审计所有调用点（CLI + API + 测试）。
2. **多入口架构的形参审计清单**：本期 V4 后端有 3 个 pipeline 入口（CLI `cli.py` / HTTP API `api/v4/runner.py` / 单元测试 `tests/`）。新增/修改 pipeline 形参时必须同步审计这 3 个入口，否则会出现「CLI 行为正确、HTTP 行为错」的隐蔽问题。
3. **真实 E2E + reference 对照是发现此类问题的最低成本手段**：单元测试覆盖率未覆盖 HTTP API → CLI 入口一致性，本次问题只在「同事代码 reference 对照」时才暴露。下次类似集成建议加 E2E 用例做入口一致性 diff。

---

### BUG-20260911-001：HTTP 入口 Form 默认值覆盖 .env，导致 USE_MOCK_LLM=1 时反向/正向分析不走 Mock

#### 状态

fixed（exe 端到端验证待用户提供样例文件后进行）

#### 发现日期

2026-09-11

#### 问题现象

exe（及一切走 HTTP API 的场景）中，`.env` 设置 `USE_MOCK_LLM=1` 不生效：反向分析任务实际走真实 LLM——无 API Key 时任务直接 failed（`DEEPSEEK_API_KEY not set...`），有 Key 时真实调用 API。正向完整性分析同款问题。

#### 复现方式

1. exe 同级 `.env` 设置 `USE_MOCK_LLM=1`
2. 前端上传文件发起反向分析（前端不发送 `use_mock_llm` 字段）
3. 任务不走 Mock，报缺 API Key 或真实调用 API

#### 影响范围

- `backend/app/api/v4/coverage.py`（反向入口）
- `backend/app/api/v4/completeness.py`（正向入口）
- `backend/app/api/v4/runner.py`（env 写入链路）
- CLI / MockLLMClient / factory 均不受影响

#### 原因分析

Mock 的设计意图是**只由 `.env` 的 `USE_MOCK_LLM` 控制**（前端无 mock 开关）。但 HTTP 入口的 `use_mock_llm` Form 字段定义为 `bool = Form(False)` 且前端从不发送该字段 → 恒为 `False`（永不为 `None`）→ `run_v4_pipeline_thread` 中 `if use_mock_llm is not None:` 判断恒真 → 线程启动时把 `.env` 加载好的 `USE_MOCK_LLM=1` 强制覆盖为 `"0"`。

runner 里 `is not None` 判断原本就是为「未显式提供则不动 env」设计的，但 `bool = Form(False)` 的默认值使其形同虚设。`run_forward_pipeline_thread` 同款问题。

#### 修复方案

按最小修改原则，仅把两个端点的 Form 字段改为 `Optional[bool] = Form(None)`，runner 四处形参类型注解同步改为 `Optional[bool]`。`runner.py` 的 `is not None` 判断逻辑无需改动：

- 未显式传 → 不动 env（`.env` 权威，Mock 生效）；
- 显式传 true/false（curl 调试）→ 仍按显式值覆盖 env。

#### 修改文件

1. `backend/app/api/v4/coverage.py`（Form 字段类型/默认值）
2. `backend/app/api/v4/completeness.py`（Form 字段类型/默认值）
3. `backend/app/api/v4/runner.py`（4 处形参类型注解）
4. `backend/tests/test_api_mock_env_default.py`（新增回归测试）
5. `docs/architecture/api.md`（字段契约说明）

#### 验证方式

1. `cd backend && python -m pytest tests/test_api_mock_env_default.py -v`
2. 修复前该测试 2 个用例 RED（端点默认捕获到 `False`），修复后 5/5 GREEN
3. `USE_MOCK_LLM=1` 下 `get_llm('deepseek')` 返回 `MockLLMClient`（工厂链路 sanity）

#### 验证结果

已验证通过。回归测试 5/5 通过；工厂链路 sanity 通过。exe 端到端（上传样例文件跑完整反向任务）待用户用真实样例验证。

#### 遗留问题

1. `backend/tests/` 中 10 个测试文件收集失败（2026-09-24 复核；本条原记为 9 个，且未写明缺失模块）：它们导入的是 `app.v4.reverse.*`（`base` / `ctx` / `pipeline` / `judging.multi_judge` / `controllers.*`）与 `app.v4.shared.parsing.hlr_parser_config`，而这些模块在当前工作区已不存在——`app/v4/reverse/` 下 0 个 `.py`，目录里只剩 `__pycache__`（`controller.cpython-310.pyc` 的时间戳为 2026-09-16）；`git log --all -- app/v4/reverse` 与 `app/v4/shared` 均为 0 条，即从未进入版本库；`backend/tests/` 自身也在 `.gitignore:50`，24 个测试文件同样未纳管。为既有状态，与本次修复无关。
2. `config.JUDGE_PROVIDERS` 为 import-time 常量，runner 线程内 `os.environ["JUDGE_PROVIDERS"]` 覆盖对 `pipeline.py` 引用的模块级常量不生效（provider 白名单实际由 `.env` 决定）；与 Mock 无关，本次不动。

---

### BUG-20260922-001：长时运行后任务失败但界面无法判断根因（失败原因取值链路缺失）

#### 状态

fixed（前端运行时行为尚未验证：无浏览器环境，仅经代码审查 + `tsc` / `build` 通过）

#### 发现日期

2026-09-22

#### 问题现象

服务器部署形态下（不能 SSH 看容器日志、也不能在前端看日志），任务跑久后进入错误页，但页面只有一个笼统的「处理失败」，看不出根因：分不清是输入文件缺失、模型 Key 失效、模型返回格式异常，还是**前端自己**等待超时；同时没有任何后端日志可看。

#### 复现方式

1. 在服务器形态下提交一次必然失败的任务（把一个纯文本文件改名为 `publisher.xlsx`，与一个正常的 HLR 文件一起提交）；
2. 任务失败后观察错误页：只有兜底文案「请检查文件格式是否正确，或稍后重试」，无分类、无建议；
3. 另可断开网络让前端持续取不到状态（或让任务长时间不返回）：进入的是**同一个**错误页、同样没有有效文案。

#### 影响范围

- `backend/app/api/v4/jobs.py`（`/result` 非 `completed` 即 409，失败任务的 `errors` 前端拿不到）
- `backend/app/job_manager.py`（失败原因此前只写入一句 `message`，前端无从消费）
- `frontend/src/hooks/useAnalysisJob.ts`（`failed` 分支不写 `errorMessage`；轮询超时与后端失败同形）
- `frontend/src/pages/CorrectnessPage.tsx` / `frontend/src/pages/CompletenessPage.tsx`（错误页永远渲染兜底文案）

#### 原因分析

1. **失败原因没有可读通道**：后端**有**失败信息，但 `GET /jobs/{id}/result` 仅在 `status == completed` 时返回 200、其余一律 409，失败任务的 `result["errors"]` 前端根本读不到；`job.message` 只有一句 `V4 pipeline failed: <类型>: <信息>`，不稳定也没有面向用户的建议。
2. **前端没有消费失败原因**：`useAnalysisJob` 的 `status === 'failed'` 分支只 `setPageState('error')`，**不写 `errorMessage`**，错误页因此永远渲染兜底文案。
3. **两条完全不同的路径在 UI 上同形**：前端轮询原实现为 `MAX_RETRIES=120 × 10s = 20 分钟`，超时后同样进错误页且 `errorMessage` 为空 ——「后端真的失败」与「前端放弃轮询」在界面上完全一样。
4. **关联（不是本次新增的缺陷）**：MOCK 开关此前无法从界面双向切换，即「前端**要能**发送 `use_mock_llm` 字段」这一环缺失。这是 `BUG-20260911-001` 的**下游**：09-11 修的是「前端不发送该字段时 `.env` 被 Form 默认值（`bool = Form(False)`）覆盖为 `"0"`」，本次修的是「前端**显式**发送该字段（`Optional[bool] = Form(None)` 的契约因而真正生效），从而能在容器 `.env` 已配 MOCK 的环境下双向切换」。两者是同一条链路的上下游，不是两个独立缺陷。

#### 修复方案

后端新增结构化失败信息并下发给前端，前端把两类原因分开呈现：

1. 新增 `backend/app/v4/errors.py`：把管线异常映射为 12 类 category + title + 可执行 hint；LLM 层细分复用 `degradation.fallback.classify_exception`，并按异常类型把关，避免与模型无关的失败（网络盘写盘 `OSError`、文档解析 `KeyError`）被贴成模型故障。
2. `Job` 新增 `error` 字段（连同 `mock`、`cancel_requested`），`GET /api/v4/jobs/{job_id}` 新增 `error: {category, title, stage, stage_index, error_type, message, detail, traceback_tail, hint, at} | null`；`/result` 的 409 契约**不变**，失败原因改从状态接口读取。
3. `useAnalysisJob` 的 `failed` / `canceled` 分支写入结构化错误；前端等待超时改按**时间上限**（6 小时）并在文案里明确「任务可能仍在服务器上运行」，与后端失败彻底分开。
4. 前端错误页展示分类 + 建议，并提供「一键复制诊断信息」（含任务 ID、失败分类、堆栈尾部与日志尾部）。

#### 修改文件

1. `backend/app/v4/errors.py`（新增）
2. `backend/app/job_manager.py`（`Job.error` / `mock` / `cancel_requested` 与取消检查点）
3. `backend/app/api/v4/schemas.py`（`V4JobError` + 状态响应新字段）
4. `backend/app/api/v4/jobs.py`（状态接口回填新字段）
5. `backend/app/api/v4/runner.py`（失败 / 取消时写入分类结果）
6. `frontend/src/hooks/useAnalysisJob.ts`、`frontend/src/components/ErrorDiagnostics.tsx`（新增）、`frontend/src/pages/CorrectnessPage.tsx`、`frontend/src/pages/CompletenessPage.tsx`、`frontend/src/types.ts`
7. `docs/architecture/api.md`（字段契约同步）

#### 验证方式

1. `cd backend && python -m pytest tests/ -q --continue-on-collection-errors`
2. `cd frontend && npx tsc --noEmit && npm run build`
3. 浏览器端人工核对：失败任务显示分类与建议、「复制诊断信息」粘贴出的文本完整（本条**未执行**）

#### 验证结果

1. 后端回归 **80 passed, 10 errors**（10 个 collection error 为既有、与本次无关，同 BUG-20260911-001 遗留问题第 1 条）—— **已验证**。
2. 前端类型检查与构建通过 —— **已验证**（仅静态层面）。
3. 浏览器端行为（错误页分类渲染、明文 HTTP 下复制走 `execCommand` 兜底）**尚未验证**：无浏览器环境、未新增前端测试运行器；需按 `docs/testing/服务器部署验收清单.md` 第 6 / 7 行手工验收。

#### 遗留问题

1. 取消后错误页标题为「任务已终止」（前端按 `jobStatus === 'canceled'` 判定），而结构化错误的 `error.title` 为「任务已取消」（后端 `CATEGORY_TITLES['CANCELLED']`）。两者用词不同但语义各自正确（前者说这次运行终止了，后者说这次失败的性质是取消），**不是不一致**，无需统一、也不要为此改代码。
2. 错误页与「一键复制诊断信息」的运行时行为未在浏览器中实测（见验证结果 3）。
3. 本条目涉及的服务端到端验收未执行，清单见 `docs/testing/服务器部署验收清单.md`。

#### 经验总结

1. **「有数据」不等于「可诊断」**：后端一直持有失败信息，但取值通道（`/result` 409 + 前端不写 `errorMessage`）断在两端，UI 上就等价于「没有」。定位时应顺着「这条信息从产生到渲染，中间有哪几跳」逐跳核对，而不是只看末端页面的表现。
2. **不同原因必须有不同的呈现**：「后端失败」与「前端等待超时」是两类事件，混用同一个兜底文案会把用户的判断引向错误方向（本轮实测中曾因此误判为模型故障）。
3. **跨会话缺陷要写清上下游**：本次的开关能力是 09-11 修复的下游，若把它写成两个独立缺陷，后续会话会重复排查同一条链路。

### BUG-20260922-002：错误页「失败步骤」在反向 Step 1–3 失败时为空，Step 4 之后显示未翻译 token

#### 状态

fixed

#### 发现日期

2026-09-22

#### 关联 Issue / PR

本次「服务器可观测与任务控制」Issue（5 个问题合并；纪要见 development-log 同名条目）

#### 问题现象

正确性分析在反向 Step 1–3（解析 / HLR 标注 / 反向匹配）失败时，错误页**不显示**「失败步骤」行，「复制诊断信息」的文本里写成 `失败步骤: (未知)`；Step 4 之后失败时该行虽有值，显示的却是 `multi_judge` 这类未翻译 token（同屏处理页显示的是中文标签）。失败分类本身是正确的。

#### 复现方式

把 `.txt` 改名成 `publisher.xlsx` 与正常 HLR 一起提交一次正确性分析（失败发生在 Step 1 解析）：错误页有「失败分类：文件无法解析」，但不显示「失败步骤」行，复制出的诊断文本里是 `失败步骤: (未知)`。

#### 影响范围

错误页与「一键复制诊断信息」的文本（`frontend/src/components/ErrorDiagnostics.tsx`）；`GET /api/v4/jobs/{job_id}` 的 `error.stage` / `error.stage_index` 取值。

#### 原因分析

同一个逻辑字段（失败发生在第几步）有**两个不同来源**：状态查询（`backend/app/api/v4/jobs.py`）先取结构化 `job.progress`、再回落到 `_parse_progress(job.message)` 的正则解析；而 `_fail_job`（`backend/app/api/v4/runner.py`）**只**取结构化进度。结构化 `stage` 只在反向 Step 4 与正向 Step 7 才被写入，因此反向 Step 1–3 失败时 `error.stage` 为空 —— 不是「没有数据」，而是「两个来源不一致」。

#### 修复方案

抽出 `_merged_progress(job)`（`runner.py`）作为 stage 的唯一推导入口，`_fail_job` 与 `jobs.get_v4_job_status` 共用；前端 `ErrorDiagnostics` 改用 `ProcessingView` 导出的 `STAGE_LABELS` 翻译 token（未知值保留原文），复制文本写成「中文（token）」两种形式都给。

#### 修改文件

`backend/app/api/v4/runner.py`、`backend/app/api/v4/jobs.py`、`frontend/src/components/ProcessingView.tsx`、`frontend/src/components/ErrorDiagnostics.tsx`、`backend/tests/test_job_api_ext.py`

#### 验证方式

`cd backend && python -m pytest tests/test_job_api_ext.py -v` —— 新增用例 `test_fail_job_stage_shared_with_status_response`（修复前 `error.stage` 为空，必红；修复后为 `multi_judge`）。

#### 验证结果

**已验证（单元层面）**：该文件 11 passed；本计划 8 个测试文件 76 passed（`test_job_log` / `test_pipeline_errors` / `test_job_cancel` / `test_job_api_ext` / `test_job_log_pool` / `test_runner_cancel` / `test_pipeline_checkpoints` / `test_hlr_labeler_throttle`）；全量套件 81 passed / 10 errors（修复轮前为 80 passed，+1 为本轮新增用例；10 个 error 与本轮无关）；`npx tsc --noEmit` exit=0。**尚未验证**：错误页在浏览器中的实际渲染（本仓库无浏览器环境，未新增前端测试运行器）。

#### 遗留问题

1. `_fail_job` 先算 stage、再用失败文案覆盖 `job.message`，故失败后状态查询的顶层 `stage` 为空、而同响应 `error.stage` 有值 —— 这是本次修复**引入**的、方向相反的不一致；前端两个状态互斥渲染，用户不可见。列为跟进项（修法：改 message 写法，或让查询侧读 `progress['message']`）。
2. 同轮最终审查另发现 `I-2`（MOCK 开关是进程级环境变量、无单飞保护）与 `M-1` / `M-3` / `M-4` / `M-5(c)` / `M-6` 代码半，均为跟进 Issue 候选，详见 development-log 同名条目的「最终定审与修复轮」。

### BUG-20260922-003：续跑已终止的任务时 Step 1 文件解析整段重跑（约 38s）

#### 状态

fixed（2026-09-22 用户改判「现在做」，按下方方案 1+2 实施）

#### 发现日期

2026-09-22

#### 关联 Issue / PR

本次「服务器可观测与任务控制」Issue 的后续（终止任务可续跑能力上线后的用户反馈）

#### 问题现象

正确性分析任务在反向 Step 1（`Parsing input files`）跑完后被终止，点「继续执行」续跑时，Step 1 整段重跑：两次日志给出完全相同的 `Generated (before any dedup): 634794` → `After global dedup: 122674`，`[timing] Step 1/6: Parsing input files` 两次都是 38.2s / 35s 量级。从用户视角就是「终止之后还是从第一步重新执行」。

#### 复现方式

提交一次正确性分析 → 等日志出现 `[timing] Step 1/6: Parsing input files: 38.2s` → 终止 → 点「继续执行」→ 日志再次出现完整 Step 1 解析。

#### 影响范围

仅影响**续跑的耗时**（每条续跑多花 Step 1 的时间，本次实测约 38s：EoICD 解析约 26s + HLR 解析与《EoICD条目化清单.xlsx》生成合计约 12s）。不影响结果正确性：解析产物与首跑逐字节一致。

#### 原因分析

1. 「继续执行」的语义是**重跑整条管线 + 复用 LLM 结果**，不是「从第 N 步接着跑」。管线内具备复用能力的只有 LLM 调用层：`output_dir/llm_cache.jsonl`（内容寻址）与 `output_dir/hlr_labels.json`。
2. Step 1 的文件解析**本来也有复用路径**，但 API 侧从不使用它：`run_reverse_pipeline` 支持 `eoicd_json`（命中则打印 `[skip] Using cached EoICD JSON`）与 `hlr.suffix == ".json"`（命中则 `[skip] Using cached HLR JSON`）（`backend/app/v4/pipeline.py:923-942`），而 `backend/app/api/v4/runner.py:381-391` 硬传 `eoicd_json=None`（注释：缓存路径不在 API 暴露）并把原始 `.docx` / `.xlsx` 传给 `hlr` / `publisher` / `subscriber`，因此每次都落到重解析分支；正向管线 `runner.py:647-651` 同理。
3. 本次案例中「没有任何东西可复用」被放大成「看起来从零开始」：取消是在 Step 1 的计时标记之后、Step 2 的第一个检查点（`pipeline.py:959` 的 `raise_if_cancelled()`）抛出的，此时一条 LLM 调用都还没发生 —— 日志里 `[cache] llm_cache.jsonl not found, starting empty` 与「Step 2/6 横幅已打印、stage 仍显示 `parse`」的组合（`job.update` 在检查点之后，故 stage 未推进）都是这个落点的证据。

#### 修复方案（已实施）

1. `runner.py` 反向与正向两处：新增 `_reuse_parse_inputs(job, output_dir, hlr_path)` —— 只有 `job.resumed` 为真**且** `output/` 下 `eoicd_requirements.json` 与 `hlr_requirements.json` 同时存在时，才改传这两份 JSON（走管线**已有的** `[skip]` 分支）；其余情况一律 `(hlr_path, None)`，与改动前逐字一致。两份 JSON 就是 `EoICDOutput` / `HLROutput` 的 `model_dump_json(indent=2)` 原样回读，判定逻辑不变；`hlr` 路径在 Step 1 之后无任何下游使用（已核对 `pipeline.py:880-1250`），输入文件名不会流进输出文档。首跑不复用是刻意的：首跑时同名文件可能是上一轮残留，当成解析结果用会让新上传的输入文件完全不生效。
2. 前置安全项：`pipeline.py` 新增 `_write_text_atomic(path, text)`（写 `<name>.tmp` + `Path.replace` 原子改名），`_parse_eoicd` / `_parse_hlr` 的落盘改走它。复用之后，「JSON 半截（容器在写盘窗口内被重启）」的后果会从「白跑 38s」升级为「该任务永远续跑失败、只能放弃重传」，故先堵住。
3. 预期效果（**推算，非本轮实测**）：Step 1 从约 38s 降到约 11s 量级 —— 剩下的只有 `EoICD条目化清单.xlsx` 的生成（交付物，仍须重新生成，原「影响范围」段测得约 11s）。两次解析（EoICD 约 26s、HLR 若干秒）都被跳过，其中 26s 可明确归因；HLR 解析与 xlsx 生成在原记录里合并计为 12s，未单独拆分，故只报「下降量级」不报精确秒数。

#### 修改文件

- `backend/app/v4/pipeline.py`（新增 `_write_text_atomic`；`_parse_eoicd`、`_parse_hlr` 落盘改原子写）
- `backend/app/api/v4/runner.py`（新增 `_reuse_parse_inputs`；`run_v4_pipeline_thread`、`run_forward_pipeline_thread` 两处调用点接线）
- `backend/tests/test_parse_artifact_reuse.py`（新增 6 个测试）
- `backend/tests/test_api_mock_env_default.py`（`_StubJob` 补 `resumed = False`，见下方验证结果）

#### 验证方式

```bash
cd backend && PYTHONIOENCODING=utf-8 python -m pytest tests/ --continue-on-collection-errors -q
```

端到端（未执行，需服务器环境）：提交正确性分析 → Step 1 跑完后终止 → 「继续执行」→ 日志应打印 `[skip] Using cached EoICD JSON` / `[skip] Using cached HLR JSON`，`[timing] Step 1/6` 应降到约 11s。

#### 验证结果

单元测试通过：`94 passed, 10 errors`（10 项为既有的 `app.v4.reverse.*` / `app.v4.shared.parsing.*` collection error，数量与本次修改前一致；新增 6 个测试，全量通过数 88 → 94）。红/绿证据：

- `test_reverse_launcher_reuses_parsed_json_on_resume`：把 `git show HEAD:backend/app/api/v4/runner.py` 载入为独立模块、只替换 profile 注册表（临时路径下旧模块以自身 `__file__` 定位 profiles 目录）后跑同一入参，记录到 `hlr = <原始 hlr.docx>`、`eoicd_json = None` —— 断言在旧代码上不成立；当前代码为 `hlr = output/hlr_requirements.json`、`eoicd_json = output/eoicd_requirements.json`。
- `test_pipeline_skips_parsing_when_given_json`：钉住管线侧契约（`eoicd_json` 存在 + `hlr` 为 `.json` ⇒ 必须走 `[skip]`，`_parse_eoicd`/`_parse_hlr` 被替换为「一调用就 AssertionError」的桩）。选择这条契约是因为复用若静默失效只会「变慢、不报错」，没有任何其它测试能发现。
- `_write_text_atomic` 两条：写盘中途抛错时旧文件逐字节不变；成功路径不留 `.tmp`。

回归发现与处理：`test_api_mock_env_default.py::test_runner_thread_env_authoritative_when_none` 因新增的 `job.resumed` 读取而失败（`_StubJob` 只实现了 `result` / `update`，缺该属性时 `_fail_job` 又因缺 `progress` 二次抛错，报错点落在 `runner.py:238`）。`Job.__init__` 必设 `resumed = False`（`job_manager.py:87`），故真实 Job 不受影响，判定为测试替身与真实接口不同构，仅补 `_StubJob.resumed = False`；未在生产代码加 `getattr` 兜底（那会把「Job 接口残缺」静默当成「非续跑」，正是本 Bug 的失效模式）。

端到端 `[skip]` 实跑尚未验证，原因是：需在服务器/Docker 环境提交真实输入并触发一次「终止 → 继续执行」。

#### 遗留问题

「从第 N 步断点续跑」是另一件事：即便按上述方案复用解析产物，Step 2 之后仍会在每个 `raise_if_cancelled()` 处重跑非 LLM 的确定性计算（匹配、构例、聚合），只是 LLM 判定不会重复调用。

### BUG-20260922-004：HLR 标注的取消检查点按「每 20 条」落位，十几条的批次内没有检查点

#### 状态

fixed

#### 发现日期

2026-09-22

#### 关联 Issue / PR

本次「服务器可观测与任务控制」Issue 的后续（用户提问：「只打了 10/14 个标签，不能立刻终止吗？」）

#### 问题现象

Step 2（HLR AI 标注）点「终止」后不立刻生效：HLR 只有十几条时，界面会一直停在「正在终止…」，直到整批标注跑完、走到 Step 2 之后的检查点才转「已终止」。

#### 复现方式

`backend/tests/test_pipeline_checkpoints.py::test_label_hlrs_stops_on_cancel_between_items`（修复前必红）：第 1 条标完后 `request_cancel()`，观察是否还会继续标第 2、3 条 —— 修复前仍会标注完（用例捕获的 stdout 为 `1/3 H1` / `2/3 H2` / `3/3 H3`）。

#### 影响范围

终止任务的响应粒度（反向 Step 2 / 正向 Step 4 共用 `label_hrls`）。只影响「多久停下」，不影响结果与已标注条目的复用（每条标完即追加进 `llm_cache.jsonl`，续跑逐条命中）。

#### 原因分析

检查点写成步长式：`if idx % 20 == 0: raise_if_cancelled()`。索引 0 已含在批次内，因此**少于 20 条的批次在整个批次内只有这一次检查**，之后要等 Step 2 结束（`pipeline.py` 中 Step 2 之后的检查点）才消费取消事件。逐条标注本身是秒级调用，用步长省下的检查成本（一次 Event 读取）远小于「终止不生效」的代价。

#### 修复方案

改为**每条**一个检查点（`hlr_labeler.py` 循环内无条件 `raise_if_cancelled()`）。语义：终止在下一条开始前生效，正在跑的那一条跑完并把结果写入缓存（不浪费）。不做「掐断正在飞的那次模型调用」—— 需要 LLM 客户端支持中止，是另一件事。

#### 修改文件

`backend/app/v4/matching/hlr_labeler.py`、`backend/tests/test_pipeline_checkpoints.py`

#### 验证方式

`cd backend && PYTHONIOENCODING=utf-8 python -m pytest tests/test_pipeline_checkpoints.py tests/test_hlr_labeler_throttle.py -v`

#### 验证结果

**已通过（单元层面）**：新增用例修复前 `Failed: DID NOT RAISE JobCancelled`（且 stdout 显示 3 条全被标注），修复后 7 passed；本计划 8 个测试文件 83 passed（修复前 82，+1 为本轮新增用例）；全量套件 88 passed / 10 errors（10 个 error 为 `app.v4.reverse.*` 采集错误，与本轮无关）。**尚未验证**：容器内在真实模型调用下点终止的实际观感。

#### 遗留问题

同类「步长式检查点」若在别处出现，需一并核对：`coverage_reviewer`（每个 block 边界）与 `pipeline.py` 各步边界已是较细粒度。**2026-09-24 复核：本条原写「HLR 标注是唯一一处按索引取模的」，现已不成立**——此后按同一手法在多处补了点：EoICD Excel 解析器（`eoicd_excel_parser.py` 的物料化循环与解析循环，`_CANCEL_CHECK_ROWS = 5000`）与四个报告生成器（`word_generator.py:229`、`consensus_word_generator.py:457`、`forward_word_generator.py:145`、`excel_generator.py:67`）。

### BUG-20260923-001：WPS 重存的 Word/Excel 在 Linux 上必然解析失败（zip 条目名用反斜杠）

#### 状态

fixed

#### 发现日期

2026-09-23

#### 关联 Issue / PR

本次「Docker 服务器部署」反馈问题的第 1 项：Word 无法解析。

#### 问题现象

同一份 .docx 在不同机器上表现不同：用户本机、本机 Docker、云服务器都能解析；另一台云服务器上**所有** Word 都报「文件无法解析」；内网服务器上只有 AMSC 那一份失败。失败机器上传回来的那份问题件（`空气管理系统控制器控制通道控制软件高层需求规范_故障注入.docx`，23,858 字节）在用户本机上同样解析失败。

#### 复现方式

```python
import os
os.sep = "/"                       # 模拟 Linux（容器/服务器）
from app.v4.parsers.hlr_parser_factory import create_hlr_parser
create_hlr_parser(path, profile=ams).parse()   # KeyError → 任务报「文件无法解析」
```

同一份文件保持 `os.sep = "\\"`（Windows 默认）则解析出 16 条需求。回归用例见 `backend/tests/test_zip_entry_normalize.py`。

#### 影响范围

所有走 OOXML 包（.docx / .xlsx）的解析入口：HLR Word 解析、EoICD Excel 解析。表现为任务在 Step 1 直接失败。**与服务器配置、镜像构建路径、机器资源均无关** —— 同一份文件在任何 Linux 环境（含用户自己的容器）都会失败。

#### 原因分析

.docx / .xlsx 本身就是 zip 包，内部部件名按 OOXML 规范必须用正斜杠（`word/document.xml`）。WPS 等第三方 Office 存盘时会把条目名写成反斜杠（`word\document.xml`），实测该问题件 15 个条目中 14 个是反斜杠。

CPython 的 `zipfile` 对这类包有一个 Windows 专属兼容：`ZipInfo.__init__` 与 `_RealGetContents` 中都有 `if os.sep != "/" and os.sep in filename: filename = filename.replace(os.sep, "/")`。**只有 Windows 会替换**，Linux 原样保留 —— python-docx 按 `word/document.xml` 查关系部件时抛 `KeyError: no relationship of type '.../officeDocument'`，上层归类为「文件无法解析」。

由此：同一份文件 Windows 上解析正常、任何 Linux 上必挂。三台机器的差异因此不在系统、不在镜像，而在**文件是否经 WPS 这类工具重存过**：说「所有 Word 都失败」的那台，文件都过了一遍 WPS；「只有 AMSC 失败」的那台，只有那一份过过。

佐证：该问题件与用户原始的 Word 版本（27,235 字节）`word/document.xml` **逐字节相同**（解压后 166,329 字节、压缩后 9,275 字节），需求数同为 16、术语表同为 2 条；3,377 字节的体积差全部来自 zip 头与额外字段（WPS 不写 Word 的那些 extra field）。

#### 修复方案

新增 `backend/app/v4/parsers/zip_entry_normalize.py`：解析前按**中央目录的原始字节**检查条目名是否含反斜杠（不依赖 `os.sep` —— 依赖它会使检测本身随平台漂移，正是本 bug 的成因）；不规范就用标准库重写成一份规范包，落在 `<原目录>/_normalized/` 下并**保持同名**（解析结果里 `source_file` 用的是文件名，改名会让报告中的「来源文件」跟着变）。规范包原路径直通、零额外行为；读不出中央目录的（空文件、非 zip、截断）一律直通，把报错留给原本的解析路径。

`pipeline._parse_hlr` 与 `_parse_eoicd` 在调用解析器前接入该归一化。`_parse_hlr` 中同一路径也交给 profile hook（HSCU 的 hook 会重开源 Word），两者用的是同一份文件。发生归一化时日志打一行 `[fix] Non-standard package entry names normalized: <路径>`，前端日志面板可见。

未做：不把 `KeyError` 加入 `errors.py` 的格式异常集合（否则任意位置抛出的 `KeyError` 都会被误报成「文件无法解析」）；不改 `.doc`；不加依赖；不动判定逻辑。

#### 修改文件

`backend/app/v4/parsers/zip_entry_normalize.py`（新增）、`backend/app/v4/pipeline.py`、`backend/tests/test_zip_entry_normalize.py`（新增）

#### 验证方式

```bash
cd backend && PYTHONIOENCODING=utf-8 python -m pytest tests/test_zip_entry_normalize.py -v
```

另有真实问题件回归：把它复制到临时目录，在 `os.sep="/"` 下走真实 `_parse_hlr` 入口解析，并与 Word 原件的结果对比需求数。

#### 验证结果

**已通过（本机）**：新增用例 10 passed，其中核心契约用例在 `os.sep="/"` 下断言「原件读 `word/document.xml` 抛 `KeyError`、归一化副本读得出且内容逐字节相同」；检测用例在 `os.sep` 取 `"/"` 与 `"\\"` 两种值时结论一致。真实问题件走 `_parse_hlr` 在 `os.sep="/"` 下解析出 **16 条需求 / 2 条术语**（与 Word 原件一致）、耗时 0.21s、`source_file` 仍为原始文件名；Word 原件走同一入口不生成 `_normalized/` 目录（直通路径，耗时 0.13s）。**尚未验证**：容器内实际解析（本机未执行 Docker 构建/运行）；`.xlsx` 侧只有单元层面覆盖，没有真实的第三方重存 Excel 样例。

#### 遗留问题

1. 本模块只处理条目名分隔符；若将来出现其他形态的非规范包（绝对路径、`..` 等）需另行处理；
2. `.doc`（OLE 复合文档，不是 zip）仍不支持；按用户 2026-09-23 指示，`.doc` 适配不再做，本条不再是待办；
3. MOCK 中途报错尚未定位；正向 Step 8 卡死已定位并修复（见 BUG-20260923-009），与本问题无关。

### BUG-20260923-002：上传页「文件预览」对同一类包显示「无法解析 Word 文件」（浏览器端，与系统无关）

#### 状态

fixed

#### 发现日期

2026-09-23

#### 关联 Issue / PR

本次「Docker 服务器部署」反馈问题的第 1 项。与 BUG-20260923-001 **同根因、不同组件**：001 是后端管线在 Linux 上的失败，本条是纯浏览器端预览的失败。

#### 问题现象

上传 `空气管理系统控制器控制通道控制软件高层需求规范_故障注入.docx` 后，右侧「文件预览」面板显示「无法解析 Word 文件」。用户据此认为「重新构建镜像后这份文件还是无法解析」。

#### 复现方式

用前端预览所用的同一套库（mammoth 的 browser 构建 + JSZip）直接解析该文件：

```js
mammoth.convertToHtml({ arrayBuffer })   // FAIL: Could not find main document part. Are you sure this is a valid .docx file?
```

同一入口下 Word 原件正常（10034 字符 HTML）。

#### 影响范围

仅前端上传页的文件预览面板（Word 分支）。**不影响任务执行**，也不影响生成的需求文档。与 BUG-20260923-001 不同，本条**在任何平台的任何浏览器上都会复现**（包括 Windows）—— 浏览器的 zip 实现（JSZip）不做反斜杠归一化。

#### 原因分析

预览是纯浏览器端解析：`FilePreview.tsx` 用 `FileReader` 读本地 `File` 对象，交给 mammoth（内部用 JSZip）转 HTML，**全程不经过后端**。因此：

1. 重建后端镜像对这条报错零影响；
2. 后端也没有预览接口（`backend/app/api/` 下无 `preview`）。

报错本身的原因与 BUG-20260923-001 相同：该文件 15 个 zip 条目名中 14 个用反斜杠（JSZip 原样看到、不做归一化），mammoth 按 `word/document.xml` 查不到主文档部件。

补充一个容易误判的点：前端源码是**打进 frontend 镜像**的（`frontend/Dockerfile` 为 `COPY . .` + `npm run dev`，`docker-compose.yml` 的 frontend 服务没有源码卷），所以这条改动要生效必须**重建前端镜像**，与后端镜像无关。

#### 修复方案

新增 `frontend/src/utils/zipEntryNames.ts`，把后端那个做法搬到浏览器：只扫中央目录与局部头，把条目名里的反斜杠就地换成正斜杠。反斜杠与正斜杠都是单字节，等长替换后偏移、CRC、压缩数据全部原样保持 —— 不需要重新压缩，也不需要引入任何依赖。规范包返回同一个缓冲区（零开销直通）；读不出结构时原样返回，把报错留给原本的解析路径。

`FilePreview.tsx` 的 Word 与 Excel 两个分支在交给 `mammoth` / `XLSX.read` 之前各调用一次。

#### 修改文件

`frontend/src/utils/zipEntryNames.ts`（新增）、`frontend/src/components/FilePreview.tsx`

#### 验证方式

```bash
cd frontend && npx tsc --noEmit
```

外加临时 node 探针：以 `{ arrayBuffer }` 调用 mammoth 的 browser 构建（与 Vite 打包时的解析一致），对比归一化前后。

#### 验证结果

**已通过（本机）**：`tsc --noEmit` 退出码 0。探针结果 —— 问题件未归一化：FAIL `Could not find main document part...`；经 `normalizeZipEntryNames` 后：**OK，10034 字符**，与 Word 原件的预览内容**逐字符相同**；规范文件返回同一个缓冲区（`result === input`，确认零开销）；非 zip 缓冲区不抛异常。**尚未验证**：浏览器/容器内实际上传预览（需重建前端镜像后人工复测）。

#### 遗留问题

1. Excel 分支的归一化是**预防性**的：实测 SheetJS 对条目名反斜杠的包照读（人为造的 WPS 风格 xlsx，20/21 个条目名带反斜杠，`XLSX.read` 仍出 5 个 sheet），目前没有已知的 Excel 预览失败样例；
2. 探针是临时文件，验证后已删除，未留在仓库。

### BUG-20260923-003：提交后立即报 Internal Server Error，任务未创建（接口层自动识别）

#### 状态

fixed

#### 发现日期

2026-09-23

#### 关联 Issue / PR

本次「Docker 服务器部署」反馈问题第 1 项的**第三个表现**。与 BUG-20260923-001 同根因，是该问题的第三个调用点：001 修的是管线入口，002 修的是浏览器预览，本条在接口的处理请求阶段。

#### 问题现象

前端预览恢复正常后重新提交同一份文件，点提交立即得到「处理失败 / Internal Server Error / 任务未创建：提交失败，后端没有开始执行。」，且「执行日志」为空。

#### 复现方式

模拟容器（`os.sep="/"`）直接调用接口层的识别函数：

```python
from app.api.v4.coverage import _detect_system_type, _load_hlr_tables
_load_hlr_tables(BAD_DOCX)      # Linux 下 KeyError
_detect_system_type(BAD_DOCX)   # 同上；Windows 下返回 'ams'
```

#### 影响范围

系统类型选择「自动识别」时**无法创建任务**——不是任务失败，而是任务从未创建，所以没有任务日志可看，前端只能显示一条「任务未创建」。手动指定系统类型可绕过（该分支不读文件）。

#### 原因分析

`POST /api/v4/coverage-analysis` 在 `controller_profile is None` 时**在请求内同步**调用 `_detect_system_type` → `_load_hlr_tables` → `python-docx` 打开上传文件，用来比对各 profile 的 `auto_detect` 规则。这条路径：

1. 没有 `try/except` 兜底 —— `KeyError: no relationship of type '.../officeDocument...'`（反斜杠条目名的直接后果，同 001）冒泡成裸 HTTP 500；
2. 也不在后台线程里 —— 因而没有任务日志，用户看到的只有一句「任务未创建」。

BUG-20260923-001 的修复只包住了**管线解析入口**（`_parse_hlr` / `_parse_eoicd`），接口这条「为了自动识别而提前开一次文件」的路径当时没覆盖到。同类「直接开上传文件、读失败就是硬错误」的调用点还有追溯表侧：`traceability/trace_parser.py` 的 5 处 `openpyxl.load_workbook`、`traceability/forward_scope.py` 的表校验。

#### 修复方案

在 `zip_entry_normalize` 增加轻量包装 `ensure_standard_path(path) -> Path`（只要路径、不打 `[fix]` 日志；管线入口继续用 `ensure_standard_zip` 以保留日志），并在上述调用点各加一次归一化。接口侧 1 行、追溯表侧 6 处各 1 行，判定逻辑与解析结果格式均不变。

#### 修改文件

`backend/app/v4/parsers/zip_entry_normalize.py`（新增 `ensure_standard_path`）、`backend/app/api/v4/coverage.py`、`backend/app/v4/traceability/trace_parser.py`（5 处）、`backend/app/v4/traceability/forward_scope.py`

#### 验证方式

```bash
cd backend && PYTHONIOENCODING=utf-8 python -m pytest tests/test_zip_entry_normalize.py -v
cd backend && PYTHONIOENCODING=utf-8 python -m pytest tests/ -q --continue-on-collection-errors
```

外加识别探针（模拟 Linux 直接调用 `_detect_system_type`）。

#### 验证结果

**已通过（本机）**：识别探针在 `os.sep="/"` 下对问题件的 `_load_hlr_tables` 返回 17 张表、`_detect_system_type` 返回 `'ams'`（修复前同一调用抛 `KeyError`），与 Windows 行为一致；归一化副本识别结果同样为 `'ams'`。新增 3 个用例（`_load_hlr_tables` 读 .docx、`trace_parser._read_table2_erd_to_hlr` 读 .xlsx、`forward_scope._validate_trace_table` 读 .xlsx），xlsx 夹具是真的 openpyxl 工作簿再改条目名，其中校验用例含反证：把归一化换成直通后同一份文件只能落进 `trace_open_error` 分支。测试文件 13 passed；全量 107 passed / 10 errors（10 errors 与本问题无关，见遗留问题）。**尚未验证**：容器内用「自动识别」实际提交该文件（需重建**后端**镜像，本机未执行 Docker 构建/运行）。

#### 遗留问题

1. 全量测试的 10 个 collection error 是历史问题（`tests/test_*_controller.py` 等仍 import 已不存在的 `app.v4.shared.parsing.hlr_parser_config`），不是本次改动引入，也不受本次影响；
2. 追溯表侧 6 处同样只做了本机单元验证，没有真实的第三方重存追溯表样例；
3. MOCK 中途报错尚未定位；正向 Step 8 卡死已定位并修复（见 BUG-20260923-009），与本问题无关。

---

### BUG-20260923-004：上传失败留下永远停在「等待开始」的幽灵任务，且无法丢弃

#### 状态

fixed

#### 发现日期

2026-09-23

#### 关联 Issue / PR

本次「Docker 服务器部署」反馈：任务列表里有两条「等待开始」的记录，没有任何办法去掉。用户要求「这个等待开始的任务也需要可以丢弃」。本条与 BUG-20260923-003 是同一批反馈的两面：003 修的是「点提交就 500」，本条修的是「那两次 500 留下的残骸」。

#### 问题现象

工具入口页「未完成的任务」列表里躺着两条「等待开始」：点进去没有进度、没有日志，也没有「继续」和「放弃」——**没有任何前端操作能把它从列表里去掉**。

#### 复现方式

两条记录的时间戳与 BUG-20260923-003 的两次 500 提交完全吻合，即它们正是那两次失败请求留下的。也可直接构造：调用上传接口时让「自动识别」抛错（或让 `_save_upload` 抛 413、追溯表校验抛 422），随后 `GET /api/v4/jobs` 就能看到多出来的这条记录。

#### 影响范围

一切「任务已创建、线程还没启动」的失败请求：文件过大 413、追溯表格式错 422、识别不出系统类型 500、保存上传失败。每失败一次就在列表里多一条无人认领、也无法处理的记录。

#### 原因分析

1. 上传接口先 `create_job` 把任务登记进任务表，**再**保存上传文件、自动识别系统类型；后两步任一抛错，请求就结束了，管线线程从未启动。
2. 这条记录同时不满足两个出口：`cancel` 只置一个标志，需要有线程在检查点读取它——没有线程，终止无效；`abandon` 的门槛是 `interrupted` / `canceled`，`pending` 不在其中。
3. 它也等不到重启清理：内存任务表随进程消失，而它没有 `job_dir`、没写过 `job.json`，启动扫描（只扫已落盘的 manifest）找不到它。于是用户看到的是一份「重启才会消失、重启前一直挂着」的记录。

#### 修复方案

把「登记」与「承诺运行」绑定：

1. `JobManager.new_job()` 只构造、**不登记**；`launch_v4_pipeline` / `launch_forward_pipeline` 在 `set_dir()` 之后、`t.start()` 之前调用 `job_manager.register(job)`——登记点即「已承诺运行」点。
2. `abandon` 的门槛从「状态是 interrupted/canceled」改为「**没有任何线程在跑**」：放行 `pending` 且 `job_dir is None`（从未启动）的记录；`job_dir` 非空的 `pending` 落在启动窗口内，仍属运行中的任务，只能先终止。
3. 前端「未完成的任务」列表对 `pending` 也给出「放弃」按钮（`ABANDONABLE` 集合）；「继续」仍只对 `interrupted` / `canceled` 显示——`pending` 没有参数快照，也没有进度可续。

#### 修改文件

`backend/app/job_manager.py`（新增 `new_job` / `register`）、`backend/app/api/v4/runner.py`（两个 launch 函数各 1 行登记）、`backend/app/api/v4/coverage.py` 与 `backend/app/api/v4/completeness.py`（`create_job` → `new_job`）、`backend/app/api/v4/jobs.py`（abandon 门槛与说明）、`frontend/src/components/InterruptedTasks.tsx`（`ABANDONABLE`）

#### 验证方式

```bash
cd backend && PYTHONIOENCODING=utf-8 python -m pytest tests/test_job_orphan.py -v
cd backend && PYTHONIOENCODING=utf-8 python -m pytest tests/ -q --continue-on-collection-errors
cd frontend && npx tsc --noEmit
```

#### 验证结果

**已通过（本机）**：新增 5 个用例全通过——识别失败时任务表前后一致（修复前会多一条 pending）、成功路径照旧登记、`new_job` 构造的任务在 `launch` 之前查不到、未启动的 `pending` 可放弃成 `abandoned`、已 `set_dir` 的 `pending` 仍返回 409（守住「门槛是无线程、不是状态名」这条判据本身）。全量 112 passed / 10 errors（10 个 error 同 BUG-20260923-003 遗留问题第 1 条）。`tsc --noEmit` 退出码 0。**尚未验证**：容器内实际上传一个触发 413/422 的请求、确认列表不再新增记录（需重建镜像，本机未跑 Docker）。

#### 遗留问题

1. 服务器上那两条历史幽灵记录不在任何持久化文件里（无 `job.json`），重启容器即消失，无需手工清理；
2. 顺带核实并**排除**了一个此前怀疑的隐患：`profiles/fsecu/` 存在但不在系统类型白名单内，曾担心「自动识别会选中它」。实测（`init_registry` + `list_ids()`）fsecu 虽在注册表里，但 `auto_detect=False`，而 `_detect_system_type` 对未配置 `auto_detect` 的 profile 直接 `continue`，因此不可能被自动识别选中——白名单只管手动选择，两套口径不冲突，无需改动。
3. MOCK 中途报错尚未定位；正向 Step 8 卡死已定位并修复（见 BUG-20260923-009），与本问题无关。

---

### BUG-20260923-005：交付包部署脚本在部署成功之后仍以退出码 1 结束，且不打印结果汇总

#### 状态

fixed（交付前拦截，未随任何交付包发出）

#### 发现日期

2026-09-23

#### 关联 Issue / PR

本次「打包交付物」任务的实测环节：把构建出来的交付包整包拷到临时目录、按运维的方式执行 `bash deploy.sh`。

#### 问题现象

日志停在「前端首页：HTTP 200」，**没有打印「部署完成」汇总，脚本退出码 1**。而此前四步（sha256 校验、架构复核、镜像导入、容器启动与健康检查）全部正常——部署其实已经成功，运维看到的却是失败。

#### 复现方式

在 `hostname -I` 不可用的环境执行即可（BusyBox、Alpine 等精简发行版；本机 Git Bash 同样不支持该选项）：

```bash
cd <交付包目录> && bash deploy.sh; echo "exit=$?"
```

#### 影响范围

仅汇总段：容器已正常起来、自检也已通过，但脚本报错退出，运维容易误判为部署失败而重复执行或回滚。`hostname -I` 是 iproute2 的扩展，并非 POSIX 或 BusyBox 的标准选项。

#### 原因分析

脚本开头是 `set -euo pipefail`，而汇总前取本机 IP 的写法是命令替换赋值：

```bash
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
```

`hostname` 报「无效选项」→ 管道退出码经 `pipefail` 变成非 0 → **赋值语句的退出码即命令替换的退出码** → `set -e` 立即终止脚本。注意判据的边界：命令替换失败本身不触发 `set -e`（如 `info "$(false)"` 不会中断），只有「赋值 = 命令替换」这种形状才会。按此判据逐行排查，同类隐患还有两处：openssl 分支的 `GET_SHA`、以及 wget-only 环境下返回 127 的 `CODE`。

#### 修复方案

1. `IP` 改三级回退：`hostname -I` → `hostname -f` → `hostname`，每级都带 `|| true`；全空则汇总里打印占位 `<服务器IP>`；
2. `GET_SHA`（openssl 分支）加 `|| true`，并显式判空后 `die`，避免静默中止；
3. `CODE`（首页状态码）加 `|| true`，改用 `case` 区分 200 / 127（本机无 curl）/ 其他，不再把「没有 curl」误报成「首页异常」；
4. `docker load` 的 `gunzip` 兜底路径补 `die`，让失败有明确结论而不是静默退出。

自此 `deploy.sh` 中每一处命令替换赋值都有兜底或明确判空。

#### 修改文件

`scripts/deploy.sh`

#### 验证方式

```bash
bash -n scripts/deploy.sh
# 再按交付方式整包实测（临时目录内改端口，避免与开发容器抢 8000）：
#   sed -i "s/8000:8000/18000:8000/" docker-compose.server.yml
#   bash deploy.sh; echo "exit=$?"
```

#### 验证结果

**已通过（本机 Git Bash + Docker Desktop）**：`bash -n` 通过；用**重新构建出来的交付包**（`dist/icd-deploy-20260923/`，BUILD_INFO 中 sha256 = `864a6fc9…`）整包实测，退出码 **0**，四步全绿并打印「部署完成」汇总（IP 回退到 `hostname -f` 的结果）；返回的 `index-D3u_PtZc.js` / `index-DlK8Roxx.css` 与交付包内 `frontend/dist/assets/` 完全一致，且线上 JS 内含 27 处 lucide 图标特征，确认新图标已随包交付。测试容器、网络与临时目录均已清理。**尚未验证**：在真实 Linux 服务器上执行（本机只有 Git Bash + Docker Desktop）。

#### 遗留问题

1. 脚本要求 `bash`；纯 `sh`（dash）下 `BASH_SOURCE`、数组等不可用，属既定前提，已在「环境要求」表中说明。
2. MOCK 中途报错尚未定位；正向 Step 8 卡死已定位并修复（见 BUG-20260923-009），与本问题无关。

---

### BUG-20260923-006：点「终止」要等第一步（解析）跑完才生效

#### 状态

fixed

#### 发现日期

2026-09-23

#### 关联 Issue / PR

本次「Docker 服务器部署」反馈：「容器中第一步执行也超级慢，但终止只能等第一步执行完」。用户确认修复范围为**只修终止响应**，不动解析性能。

#### 问题现象

容器里跑分析，在 `Step 1/8: Parsing input files`（正向）或 `Step 1/6: Parsing input files`（反向）横幅出现后点「终止」：任务状态不变、日志停住，直到 Step 1 把 `Parsing EoICD…` / `Parsing HLR…` / `Output: …` 全部打印完，才跳到已终止。

#### 复现方式

上传一份十万行级的 EoICD 表，Step 1 期间点终止；或在 `tests/test_pipeline_checkpoints.py` 看新增的两个用例 —— 未修时解析器根本不引用 `raise_if_cancelled`，用例直接失败。

#### 影响范围

两个管线的 Step 1：反向（正确性）Step 1/6、正向（完整性）Step 1/8。第一步是本工具最长的非模型阶段（本机实测 AMSC 14.5MB→21s / 122,674 条，RPDU 24.1MB→36s / 225,825 条；服务器 CPU 更弱则成倍放大），用户在此期间**没有任何办法停下**，只能等。

#### 原因分析

1. 取消是协作式的：`request_cancel()` 只置 `cancel_event`，必须由管线在检查点调用 `raise_if_cancelled()` 才真正抛 `JobCancelled`。
2. 检查点策略（`pipeline.py` 顶部注释）原本只有「每个 Step 开头 + Step 4 / Step 5.5 的每个 case 边界」——**Step 1 内部零检查点**，其后的第一个检查点在 Step 1 结束处（反向约 987 行、正向约 1304 行）。于是 Step 1 期间置位的标志没人读。
3. 同类问题其实已修过一次：Step 2 的 HLR 标注曾按「每 20 条」检查、后改成「每条」（见 2026-09-22 的 Fixed 记录）——「按批量边界埋点」在长批量上必然表现为「终止迟迟不生效」。

#### 修复方案

1. `eoicd_excel_parser` 引入模块级检查点（与 `coverage_reviewer`、`hlr_labeler` 同一套机制，未绑定任务时静默 no-op）：**整表物化**循环与**逐行解析**循环各按 `_CANCEL_CHECK_ROWS = 5000` 行检查一次，另在每张表开头补一次（多张小表的文件里行间隔永远数不到，靠它兜底）。
2. 两个管线的 Step 1 子步骤边界各补检查点：解析发布方 → 解析订阅方 → 解析 HLR →（反向）生成条目化清单。
3. 尾部三处 O(n) 合并 / 去重循环（纯内存、秒级）不加点，避免为可忽略的收益增加噪音。

#### 修改文件

`app/v4/parsers/eoicd_excel_parser.py`（+1 import、+1 常量、+3 处检查点）、`app/v4/pipeline.py`（两个 Step 1 共 +5 处检查点，顶部策略注释同步）、`tests/test_pipeline_checkpoints.py`（+2 用例）

#### 验证方式

```bash
cd backend && PYTHONIOENCODING=utf-8 python -m pytest tests/test_pipeline_checkpoints.py -v
cd backend && PYTHONIOENCODING=utf-8 python -m pytest tests/ -q --continue-on-collection-errors
# 容器内实测：Step 1 横幅出现后点「终止」，期望数秒内变为已终止
```

#### 验证结果

**已通过（本机）**：新增 2 个用例通过——一个证明取消在**整表物化**途中即抛出，一个证明在**逐行解析**循环内即抛出（断言中断落在第几次检查点，不依赖线程时序）。全量 114 passed / 10 errors（10 个 error 同 BUG-20260923-003 遗留问题第 1 条，非本次引入）。**尚未验证**：容器 / 服务器上点终止的实际响应时间，以及「第一步本身仍然慢」的现状（需重建镜像，本机未跑 Docker）。

#### 遗留问题

1. 「第一步慢」未处理（用户明确只修终止响应）：解析已是 `read_only + values_only` 快路径，不引入新依赖则无数量级优化空间；若后续仍要提速，方向是换更快的 xlsx 读取器（新依赖，需单独决策）或把第一步改为可续的分片。**终止响应已解决**（本条检查点 + 未提交的 `/force-cancel`）。
2. **第一步在低内存机器上会「卡死」——实为进程被杀 + 容器自动重启**（2026-09-24 补充）：交付用的 `docker-compose.server.yml` 是 `restart: unless-stopped` 且未设内存限制、无 healthcheck；容器一旦被宿主 OOM 杀掉就会自动起来，启动扫描随即把仍为 `running` 的任务标成 `interrupted`（`job_manager.load_interrupted`），前台表现为「未完成的任务」里一条「已中断」——用户读作「程序被重启过」。任务恰好停在第一步时，观感就是「第一步卡死」。成因与 BUG-20260923-008 同源（第一步峰值 621MB、其中落盘那一步独占 +252MB）：该条目的分块落盘已把第一步峰值降到 391MB、落盘增量降到 0，但**解析期 387MB 对象图是地板**，更大输入（RPDU 22.5 万条）仍可能被杀。
   **现场确认手段（尚未在对方服务器执行）**：`dmesg -T | grep -iE "oom|killed process"`、`docker inspect -f '{{.RestartCount}} {{.State.OOMKilled}}' <容器>`，再比对 manifest 里 `updated_at`（中断前最后进度）与容器启动时刻。
3. HLR（Word）解析内部未埋点，只靠其后的边界检查点兜底：本轮无证据表明 Word 解析是瓶颈；若服务器日志显示 Step 1 停在 `Parsing HLR…`，再按同一手法补点。
4. MOCK 中途报错尚未定位；正向 Step 8 卡死已定位并修复（见 BUG-20260923-009），与本问题无关。若那条报错其实是任务变成了 `interrupted` / 提示「程序被重启过」，则属第 2 条（进程被杀），两者要分开看。

### BUG-20260923-008：大输入序列化与收尾反读把峰值推到服务器可用内存之上（整机假死 / 被 OOM 击杀）

#### 状态

fixed

#### 发现日期

2026-09-23

#### 关联 Issue / PR

本次「Docker 服务器部署」反馈：反向任务跑完后服务器整机无响应，只能重启。与 BUG-20260923-006 同属这一批。代码注释里此前引用的 `BUG-20260923-007`（Excel 生成改为复用已解析对象、省掉一次 410MB 重读）属同一类问题的另一处修复，本文档未单列条目。

#### 问题现象

1. 2026-09-23 17:46 服务器：反向任务（AMSC，122,674 条 EoICD + 16 条 HLR，MOCK）在 `17:46:41.954` 流水线完成、四份报告落盘之后**整机失去响应**。最后一次被服务的请求是 `17:46:40.636`，此后约 7 分钟零请求、零日志、零写盘；用户点的「终止」「强制终止」从未被处理，8082 也访问不了，只能重启服务器。
2. 2026-09-22 同一天：内核 OOM 击杀一次 —— `Out of memory: Killed process … (uvicorn) … anon-rss:732404kB`。
3. 两次是同一根因的两种结局：内存压力先表现为 OOM 击杀，后表现为换页假死（本次无 OOM 记录、容器 `restarts=0`、全程仅两次 uvicorn 启动）。

#### 复现方式

本机 Docker，同镜像 `41d819deacc9`、同输入、同 MOCK 路径（脚本在 `build/measure-scratch/`，未进 git）：

```bash
# Step 1 两法对照（旧：整份序列化；新：分块流式），各跑一次独立进程
docker run --rm --memory 3g --memory-swap 3g \
  -v …/data:/data:ro -v …/measure-step1-both.py:/mnt/m.py:ro \
  icd-tool-backend-v4.0:latest python -u /mnt/m.py old    # 再跑一次 new
# 收尾与全局峰值
docker run --rm --memory 3g --memory-swap 3g -e USE_MOCK_LLM=1 … python -u /mnt/measure-rev-tail-new.py
```

#### 影响范围

反向（正确性）与正向（完整性）两个管线的 Step 1 与收尾阶段。**峰值与 EoICD 条数成正比**：AMSC 122,674 条时 Step 1 峰值 630MB；按 BUG-20260923-006 记录的 RPDU 225,825 条（1.8 倍）推算，同样任务在 896MB 机器上必死。服务器自报可用内存仅 476MB。

#### 原因分析

1. **Step 1 落盘**：`_parse_eoicd` 原走 `model_dump_json(indent=2)` → `Path.write_text`。12 万条时先在堆上造 171MB 的 str（含中文时 CPython 按 UCS-2 存，2 字节/字符），再编出 88MB 的 bytes。实测构成：解析 387MB → 序列化 +170MB → 写盘 +84MB = 621~630MB。
2. **收尾反读**：`runner` 收尾调 `derive_eoicd_hlr_counts(output_dir)`，只为取 `total_after_dedup` 与 HLR 条数两个整数，把同一份 87.9MB JSON 整份 `read_text` + `json.loads`。实测该步 Δ +163/+247/+199MB（三次），峰值落到 421~498MB。这两个数在流水线里本来就在手上（`eoicd_out` / `hlr_out`），只是没往上传。
3. **缺口**：服务器 `available` 476MB，对需求 630MB（Step 1）/ 421~498MB（收尾），缺口约 150MB —— 由慢速云盘 swap 抖动（假死）或全局 OOM（击杀）消化。本机在 646m（≈896MB 宿主减系统与页缓存占用）下跑同样的任务**零 swap 通过**，说明这是**全局宿主内存**现象，不是容器限额本身触发的。

#### 修复方案

1. 新增 `_write_json_streaming(path, model, big_field)`：标量头部照常 `json.dumps`，超大列表按 `_JSON_CHUNK_ROWS = 2000` 分块序列化写入 `TextIOWrapper`（增量编码，不再整份编 bytes），仍走「写 `.tmp` 再 `replace`」保证原子性。**输出与旧写法逐字节一致**，对下游 `json.loads` / `model_validate_json` 完全透明。
2. `PipelineResult` 增 `eoicd_count` / `hlr_count`（均带默认值，向后兼容）；两个管线在 return 处用已在作用域的 `eoicd_out.total_after_dedup` / `len(hlr_out.requirements)` 填值。不复用现成的 `parsed_count`：反向管线里它是 HLR 条数、正向管线里是 `blocks.total_blocks`，含义不同。
3. 两处 runner 收尾改用回传值；`derive_eoicd_hlr_counts` 保留给旧任务兜底，并把正向结果接口里对它的**无条件调用**改成惰性（原写法每打开一次正向结果页就整份读一遍 87.9MB）。
4. `_parse_hlr` 保持原样（HLR JSON 只有 16 条，收益为零）；续跑分支（`pipeline.py` 中 `eoicd_data = json.loads(...)` 那处）的 410MB 峰值与新鲜解析同级，本次不动。

#### 修改文件

`app/v4/models.py`（`PipelineResult` +2 字段）、`app/v4/pipeline.py`（+`textwrap` import、+`_write_json_streaming`、`_parse_eoicd` 改用它、两处 `return PipelineResult(` 填值）、`app/api/v4/runner.py`（两处收尾改用回传值，`getattr` 容忍测试桩返回 `None`）、`app/api/v4/jobs.py`（正向结果接口兜底改惰性）

#### 验证方式

```bash
cd backend && PYTHONIOENCODING=utf-8 python -m pytest tests -q --continue-on-collection-errors
python build/measure-scratch/check-streaming-write.py       # 新旧写法等价（含造的小样本七组用例）
python build/measure-scratch/check-forward-result-lazy.py   # 结果接口兜底是否真的变惰性
# 容器内：measure-step1-both.py（Step 1 两法对照）、verify-step1-bytes.py（87.9MB 全尺寸比对）、
#         measure-rev-tail-new.py（收尾 + 全局峰值）、verify-pipelines-e2e.py（两条管线端到端）
```

#### 验证结果

**已通过（本机，同镜像 `41d819deacc9`、同输入、同 MOCK）**：

| 测点 | 改前 | 改后 |
| --- | --- | --- |
| Step 1 落盘那一步的 Δ | +252MB | **+0MB** |
| Step 1 进程 VmHWM | 621MB | **391MB** |
| 反向全局峰值 RSS | 630MB | **519MB** |
| 收尾 `derive_eoicd_hlr_counts` Δ | +216MB | **+0MB**（runner 已不调用） |
| 落盘 `eoicd_requirements.json` | 87,904,677B | 87,904,677B，**sha256 相同** |
| 与事故现场那份产物比对 | — | 摘掉 `generated_at` 后**完全一致** |
| 端到端（3g，两条管线） | — | 反向 5 类产物 + 正向 2 类产物齐全，`status=completed`，无 `.tmp` 残留 |
| 646m 且**禁用 swap** | 通过 | 通过 |
| 计数两条路径 | — | 回传 `(122674, 16)` == 反读落盘 `(122674, 16)` |
| 全量测试 | 114 passed / 10 errors | 114 passed / 10 errors（10 个 error 同 BUG-20260923-003 遗留，非本次引入） |

代价：Step 1 落盘墙钟 0.8s → 3.6s（+2.8s，约占 Step 1 总时长 6%）。

**尚未验证**：服务器上反向任务完整跑完且全程服务可响应（需部署新包后实测）。

#### 遗留问题

1. **升内存仍是主解**：改后峰值 519MB 仍高于服务器可用 476MB。本机 646m/646m 下新旧代码**都能跑通**，说明本机无法复现服务器那种「宿主级」饥饿（服务器还有宿主系统占用、88MB 写盘的页缓存、慢速云盘 swap）。本改动是必要的风险削减，不替代扩容；且分块后峰值不再随文件大小放大。
   **2026-09-24 补充（他人服务器的「第一步卡死」）**：对方服务器上 AMSC 任务在第一步「卡死」并提示程序被重启过，与本次内存尖峰同源 —— 交付 compose 是 `restart: unless-stopped`，容器被宿主 OOM 杀掉后自动重启，启动扫描把仍为 `running` 的任务标成 `interrupted`（详见 BUG-20260923-006 遗留问题第 2 条）。本条改动把第一步峰值从 621MB 降到 391MB（落盘那一步的增量由 +252MB 降到 0），**直接削弱**这一路径；但解析期 387MB 对象图未动，低内存机器与更大输入（RPDU 22.5 万条）仍可能被杀，仍需现场 `dmesg` / `OOMKilled` 证据与升内存。
2. 结构性成本未动：解析期对象图 387MB、Excel 生成 +131MB。要再降需流式解析 / 流式 Excel，另开 Issue。
3. 续跑分支（`pipeline.py` 中 `eoicd_data = json.loads(...)` 那处）读 87.9MB JSON 的 410MB 峰值与新鲜解析同级，本次未处理。
4. 收尾的 `derive_eoicd_hlr_counts` 对**旧任务**仍会整份反读（结果接口惰性兜底），属可接受的兼容代价。
5. 编号说明：`BUG-20260923-007`（Excel 生成改为复用已解析对象、省掉一次 410MB 重读）在代码里有两处引用（`excel_generator.py`、`pipeline.py`）但本日志**仍无对应条目**；`docx_cells.py` 原先误用 `008` 标注 docx 卡死，2026-09-24 已归位为 `BUG-20260923-009`。
6. **同一批工作区改动尚未提交**：`/force-cancel`（`Job.bind_thread` / `hard_killed` / `_inject_cancel`）、前端「强制终止」按钮、`docx_cells.py` + 四个报告生成器。其中**强制终止那一层目前既无 debug-log 条目、也无 CHANGELOG 行**（本条与 009 的条目描述的只是其中的削峰与 docx 两部分）。

### BUG-20260923-009：正向 Step 8 的 Word 报告生成为 O(行数²)，表现为「卡死」

#### 状态

fixed

#### 发现日期

2026-09-23（定位与修复当日完成）；2026-09-24 补做同 N 对照实测并补齐本条记录

#### 关联 Issue / PR

本次「Docker 服务器部署」反馈：正向（完整性）任务停在 Step 8/8 长时间不出结果。与 BUG-20260923-008 同批。
编号说明：`docx_cells.py` 原先把这个缺陷标为 `BUG-20260923-008`，与内存峰值那条撞号，2026-09-24 归位为 009（代码注释已同步）。

#### 问题现象

1. 正向任务跑到 `Step 8/8: Consolidating coverage + generating reports` 后长时间无产出，CPU 单核跑满（服务器上观测「十余分钟仍在跑」）。
2. 行数越多越慢，呈平方级：2026-09-23 观测到 1618 行的正向报告卡住 5.8 分钟仍不产出文件，遂中止，未跑完。
3. 2026-09-24 在本机用同一份 1618 行数据复测：旧的取单元格方式 800 行 19.57s（按 O(n²) 外推 1618 行约 80s），新的方式 1618 行 2.73s。两者与第 2 条的差异属环境差异（当时在容器 / 服务器路径上，CPU 更慢），本条以 2026-09-24 可复现的数字为准。

#### 复现方式

```bash
cd build/measure-scratch
python -u verify-step8.py equiv        # 等价性：多规模 + 带横向合并的表
python -u verify-step8.py old 800      # 旧路径（把生成器模块里的 row_cells 换回 row.cells）
python -u verify-step8.py subset 800   # 新路径，同 N 对照
python -u verify-step8.py new          # 全量 1618 行
```

**注意 `old` 模式的实现前提**：生成器用的是 `from ... import row_cells`（直接绑定），因此必须替换**生成器模块里**的那个名字 —— 2026-09-24 修正前该脚本只改 `docx_cells.row_cells`，两条分支实际跑的都是新路径，故早先那对 `fwd-old-sub.docx` / `fwd-new-sub.docx` **不构成对照证据**（本轮实测已推翻等价性结论所依赖的这一步，正确对照见下方数字）。

#### 影响范围

Word 报告生成的逐行填表：`word_generator`（反向一致性报告）、`forward_word_generator`（正向完整性报告）、`consensus_word_generator`（共识报告）。行数越多越严重；Excel 生成走 openpyxl，不受影响。反向报告行数少、症状不明显，正向 1618 行是本次暴露点。

#### 原因分析

python-docx 的 `_Row.cells` 并不是「取本行单元格」：它转到 `Table.row_cells(idx)` → `Table._cells`，**每次调用都用 `iter_tcs()` 把整张表的单元格网格重建一遍**（含 vMerge / gridSpan 回溯）。逐行填表时每行取 6 次单元格、共 1618 行 —— 约 9,708 次全表重建，整体退化为 O(行数²)。所以「卡死」的真身是平方级重算，不是死锁：进程一直在算，只是产出时间被平方项支配。

#### 修复方案

1. 新增 `docx_cells.row_cells(row)`：直接读本行 `tr.tc_lst`，逐行开销 O(列数)，整表回到 O(行数·列数)。前提不满足（`tc` 个数 ≠ 网格列数，或任一 `grid_span != 1`，即存在横向合并）就回落 `list(row.cells)`，保证结果不因快路径而不同。
2. 三个 Word 生成器的逐行填表热循环改用它，共 11 处；表头行与小汇总表（状态 / 星级分布、汇总行）保持原样。
3. 顺带：四个生成器（含 Excel）埋协作式取消检查点（Word 200 行、Excel 5000 行）并在开头打印行数 —— Step 8 由此既能秒级响应终止，也能从日志看出停在哪个报告、多少行。

#### 修改文件

`app/v4/doc_generators/docx_cells.py`（新增）、`word_generator.py`、`forward_word_generator.py`、`consensus_word_generator.py`（改用 row_cells + 检查点 + 行数打印）、`excel_generator.py`（检查点 + 行数打印；其对象复用见 BUG-20260923-007）

#### 验证方式

见上方「复现方式」四条命令；另加 `cd backend && PYTHONIOENCODING=utf-8 python -m pytest tests -q --continue-on-collection-errors`。

#### 验证结果

**已通过（本机，2026-09-24）**：

| 测点 | 旧取单元格方式 | 新方式 |
| --- | --- | --- |
| 800 行同 N 对照（同进程、同数据） | 19.57s | **1.13s**（17.3×） |
| 800 行 `word/document.xml` | sha1 `68086896…`，604,172B | **与旧方式逐字节相同**（同 sha1、同字节数） |
| 1618 行全量（正向报告） | 本轮未跑完（见问题现象第 2 条） | **2.73s**，59,965B，sha1 `127ede6a…` |
| 可复现性 | — | 与 4 小时前同脚本产出的 `fwd-new-full.docx` sha1 一致 |
| 等价性 16 组（列 2/3/6/8 × 行 1/2/5/50，各含带横向合并的表） | — | 返回值与 `row.cells` 的 `tc` 身份序列完全一致；有合并时回落原路径且一致 |
| 全量测试 | — | 114 passed / 10 errors（10 个 error 为既有遗留，非本次引入） |

#### 遗留问题

1. 仍有少量 `.cells` 用在小表上（状态 / 星级分布表、汇总行、表头行）——行数是个位数，不构成热点，本次不动。
2. 「MOCK 中途报错」仍未定位（本批其它条目同样记为待定位），与本条无关。若那条报错其实是任务变成 `interrupted` / 提示「程序被重启过」，则属 BUG-20260923-008 的内存被杀路径（见其遗留问题第 1 条），两条要分开查。
3. 本批改动（`docx_cells.py` + 四个生成器）**尚未提交**，与 `/force-cancel`、强制终止前端按钮等同在工作区；后者目前无 debug-log 条目、无 CHANGELOG 行（见 BUG-20260923-008 遗留问题第 6 条）。
