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

export interface V4JobStatusResponse {
  job_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  stage: string
  stage_index: number
  stage_total: number
  case_index: number
  case_total: number
  message: string
  mock_models: string[]
  created_at: string
  updated_at: string
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
