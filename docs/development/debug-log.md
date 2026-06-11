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
