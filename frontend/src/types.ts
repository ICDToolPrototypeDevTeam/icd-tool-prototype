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
