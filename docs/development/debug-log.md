# 问题排查记录

本文档用于记录 **ICD工具原型Ver2.0** 开发和验证过程中出现的问题、原因分析、修复方式和验证结果。

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

