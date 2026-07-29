export type PageState = 'upload' | 'processing' | 'success' | 'error';

export interface FileItem {
  id: string;
  name: string;
  size: number;
  type: 'excel' | 'word';
  file?: File;
}

export interface ResultData {
  job_id: string;
  summary: {
    requirement_count: number;
    difference_count: number;
  };
  outputs: {
    requirements_docx: boolean;
    difference_report_docx: boolean;
    minimax_docx: boolean;
    deepseek_docx: boolean;
  };
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
  stage: 'parse' | 'label' | 'match' | 'multi_judge' | 'review' | 'report' | 'done'
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
