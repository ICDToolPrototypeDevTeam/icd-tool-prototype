export type PageState = 'upload' | 'processing' | 'success' | 'error';

export interface FileItem {
  id: string;
  name: string;
  size: number;
  type: 'excel' | 'word';
  file?: File;
}

// ========== V4 Types ==========

export type V4DownloadKind =
  | 'eoicd-xlsx'
  | 'consistency/deepseek'
  | 'consistency/minimax'
  | 'consistency/qwen'
  | 'consensus-docx'

export type V4JobStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'interrupted'
  | 'abandoned'
  | 'canceled'

export type V4TaskType = 'correctness' | 'completeness'

/** 结构化失败信息（后端按异常分类，前端据此区分根因）。 */
export interface V4JobError {
  category: string
  title: string
  stage: string
  stage_index: number | null
  error_type: string
  message: string
  detail: string
  traceback_tail: string
  hint: string
  at: string
}

/** 任务日志单行。seq 在单个任务内单调递增，用于增量拉取。 */
export interface V4LogLine {
  seq: number
  ts: string
  level: string
  text: string
}

export interface V4JobLogsResponse {
  job_id: string
  lines: V4LogLine[]
  next_offset: number
  truncated: boolean
}

export interface V4JobStatusResponse {
  job_id: string
  status: V4JobStatus
  stage: string
  // 以下 5 个字段（含末尾的 message）后端为 Optional[...]，会回 null：新任务
  // 在第一步上报进度之前即为此状态；老 manifest 的进度由后端从 message 回落
  // 解析（_parse_progress），通常仍有值
  stage_index: number | null
  stage_total: number | null
  case_index: number | null
  case_total: number | null
  message: string | null
  /** 本次运行是否为中断后的恢复运行 */
  resumed?: boolean
  /** 恢复运行的实时复用计数（按模型调用次数计：缓存复用 / 接续调用） */
  reuse?: { reused: number; rerun: number } | null
  mock_models: string[]
  /** 本次运行是否为 MOCK 模式（结果页据此提示「模拟数据，不可用于验收」） */
  mock: boolean
  /** 已请求终止，等待管线在检查点停止 */
  cancel_requested: boolean
  /** 结构化失败信息；成功 / 运行中为 null */
  error: V4JobError | null
  created_at: string
  updated_at: string
}

export interface V4JobListItem {
  job_id: string
  task_type: V4TaskType
  status: V4JobStatus
  message: string | null
  created_at: string
  updated_at: string
  input_files: string[]
}

export interface V4JobResultResponse {
  job_id: string
  status: string
  summary: {
    eoicd_count: number
    hlr_count: number
    pending_count: number
    unmatched_count: number
    star_distribution: Record<string, number>
    status_distribution: Record<string, number>
    average_star_rating: number
    agreement_distribution?: Record<string, number>
  }
  outputs: {
    eoicd_xlsx: boolean
    consistency_deepseek_docx: boolean
    consistency_minimax_docx: boolean
    consistency_qwen_docx: boolean
    consensus_docx: boolean
  }
  mock_models: string[]
  errors: string[]
}

// ========== V4 Forward (Completeness) Types ==========

export type ForwardAnalysisMode = 'full' | 'trace'

export type V4ForwardDownloadKind = 'forward-xlsx' | 'forward-docx'

export interface V4ForwardJobResultResponse {
  job_id: string
  status: string
  summary: {
    analysis_mode: ForwardAnalysisMode
    total_blocks: number
    covered_direct: number
    covered_aggregate: number
    parent_referenced: number
    possible: number
    uncovered: number
    unsupported: number
    input_error: number
    ai_reviewed: number
    eoicd_count: number
    hlr_count: number
  }
  outputs: {
    forward_xlsx: boolean
    forward_docx: boolean
  }
  errors: string[]
}
