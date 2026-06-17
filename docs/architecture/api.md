# API 设计说明

本文档用于说明 **ICD工具原型Ver2.0** 的目标 API 设计。
当前版本处于原型初始化阶段，本文档中的接口为初始设计草案，后续可根据前后端实现和任务模型调整。

## 1. API 设计原则

API 设计应遵守以下原则：

1. 前端通过 API 与后端交互，不直接访问后端文件系统；
2. 文件上传、任务状态查询、结果查询和文件下载应分离；
3. 后端应通过任务标识维护一次分析过程；
4. API 返回结果应便于前端展示任务状态和下载结果；
5. 当前版本优先满足本地演示原型，不追求完整生产级接口设计；
6. API 字段可在最小可运行工程实现过程中继续细化。

## 2. 目标接口概览

当前规划的核心接口如下：

| 接口                                             | 方法     | 说明                              |
| ---------------------------------------------- | ------ | ------------------------------- |
| `/api/health`                                  | `GET`  | 后端健康检查                          |
| `/api/eoicd/analyze`                           | `POST` | 上传输入文件并创建分析任务                   |
| `/api/jobs/{job_id}`                           | `GET`  | 查询任务状态                          |
| `/api/jobs/{job_id}/result`                    | `GET`  | 查询任务处理结果摘要                      |
| `/api/jobs/{job_id}/outputs/requirements`      | `GET`  | 下载"最优 EoICD 条目化需求"（语义见 §7）      |
| `/api/jobs/{job_id}/outputs/minimax-requirements` | `GET`  | 下载 MiniMax 条目化需求文档            |
| `/api/jobs/{job_id}/outputs/deepseek-requirements` | `GET`  | 下载 DeepSeek 条目化需求文档           |
| `/api/jobs/{job_id}/outputs/difference-report` | `GET`  | 下载差异报告文档                        |

## 3. 健康检查接口

### 3.1 接口定义

```text
GET /api/health
```

### 3.2 接口用途

用于检查后端服务是否正常运行。

### 3.3 预期返回

```json
{
  "status": "ok"
}
```

## 4. 创建分析任务接口

### 4.1 接口定义

```text
POST /api/eoicd/analyze
```

### 4.2 接口用途

用于上传 EoICD 源文件和软件高层需求文件，并创建一次分析任务。

### 4.3 输入文件

该接口应支持上传以下文件：

1. EoICD Word 主文件；
2. 一个或多个 EoICD Excel 附件；
3. 软件高层需求文件。

### 4.4 初始请求字段草案

字段命名可在实现阶段进一步细化。

```text
eoicd_word_file
eoicd_excel_files
software_requirement_file
```

### 4.5 预期返回

接口成功后应返回任务标识和初始任务状态。

```json
{
  "job_id": "example-job-id",
  "status": "pending",
  "message": "分析任务已创建"
}
```

### 4.6 说明

该接口只负责创建任务，不要求同步返回完整分析结果。
实际分析过程可由后端 pipeline 执行，并通过任务状态接口查询进度。

## 5. 查询任务状态接口

### 5.1 接口定义

```text
GET /api/jobs/{job_id}
```

### 5.2 接口用途

用于查询指定分析任务的当前状态。

### 5.3 任务状态

建议状态包括：

| 状态          | 含义           |
| ----------- | ------------ |
| `pending`   | 任务已创建，尚未开始处理 |
| `running`   | 任务正在处理       |
| `completed` | 任务处理完成       |
| `failed`    | 任务处理失败       |

### 5.4 预期返回

```json
{
  "job_id": "example-job-id",
  "status": "running",
  "message": "任务正在处理",
  "created_at": "2026-01-01T10:00:00",
  "updated_at": "2026-01-01T10:01:00"
}
```

字段可根据实际实现进行调整。

## 6. 查询任务结果摘要接口

### 6.1 接口定义

```text
GET /api/jobs/{job_id}/result
```

### 6.2 接口用途

用于查询任务完成后的结果摘要。

该接口不直接返回完整 Word 文档内容，只返回前端展示所需的摘要信息和输出文件状态。

### 6.3 预期返回

```json
{
  "job_id": "example-job-id",
  "status": "completed",
  "summary": {
    "requirement_count": 0,
    "difference_count": 0
  },
  "outputs": {
    "requirements_docx": true,
    "difference_report_docx": true
  }
}
```

字段可根据实际实现进行调整。

## 7. 下载 EoICD 条目化需求文档接口

### 7.1 接口定义

```text
GET /api/jobs/{job_id}/outputs/requirements
```

### 7.2 接口用途

用于下载任务生成的条目化需求文档。

### 7.3 输出文件

预期下载文件名：

```text
EoICD条目化需求.docx
```

### 7.4 说明

- 物理文件 `EoICD条目化需求.docx` 的内容**与"最优条目化需求"相同**（同一份 docx 落两份文件名）。
- 本接口保留向后兼容，**不**强制前端切换文案。
- 如果任务尚未完成或文件不存在，接口应返回明确错误信息。

## 7.5 下载 MiniMax 条目化需求文档接口

```text
GET /api/jobs/{job_id}/outputs/minimax-requirements
```

预期下载文件名：`MiniMax条目化需求.docx`。该文件是 MiniMax generation agent 在所有 chunk 上的全量候选合并。

## 7.6 下载 DeepSeek 条目化需求文档接口

```text
GET /api/jobs/{job_id}/outputs/deepseek-requirements
```

预期下载文件名：`DeepSeek条目化需求.docx`。该文件是 DeepSeek generation agent 在所有 chunk 上的全量候选合并。

## 8. 下载差异报告接口

### 8.1 接口定义

```text
GET /api/jobs/{job_id}/outputs/difference-report
```

### 8.2 接口用途

用于下载任务生成的 EoICD 与软件高层需求差异报告。

### 8.3 输出文件

预期下载文件名：

```text
EoICD与软件高层需求差异报告.docx
```

### 8.4 说明

如果任务尚未完成或文件不存在，接口应返回明确错误信息。

## 9. 错误响应草案

当前阶段可采用统一错误响应结构。

```json
{
  "detail": "错误说明"
}
```

后续可根据需要扩展为：

```json
{
  "error_code": "FILE_PARSE_FAILED",
  "message": "文件解析失败",
  "details": {}
}
```

当前版本暂不强制完整错误码体系。

## 10. 待实现阶段细化内容

以下内容可在最小可运行工程或端到端原型实现过程中进一步细化：

1. 文件上传字段名称；
2. 任务状态模型；
3. 任务结果摘要字段；
4. 错误码体系；
5. 文件下载响应头；
6. 前端轮询任务状态的时间间隔；
7. 是否采用同步任务、后台任务或队列；
8. 是否保留中间结果预览接口。

## 11. API 变更原则

如 API 发生变化，应同步更新本文档。

以下变化必须更新本文档：

1. 新增或删除接口；
2. 修改接口路径；
3. 修改请求字段；
4. 修改响应字段；
5. 修改任务状态定义；
6. 修改输出文件下载方式；
7. 修改错误响应结构。

本文档是前后端接口协作的初始事实源，但在原型阶段允许随实现过程进行合理调整。
