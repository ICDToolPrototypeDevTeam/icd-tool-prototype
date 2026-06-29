import mammoth from 'mammoth'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

export interface AnalyzeResponse {
  job_id: string
  status: string
  message: string
}

export interface JobStatusResponse {
  job_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  message?: string
  created_at: string
  updated_at: string
}

export interface JobResultSummary {
  requirement_count: number
  difference_count: number
}

export interface JobOutputs {
  requirements_docx: boolean
  difference_report_docx: boolean
  minimax_docx: boolean
  deepseek_docx: boolean
}

export interface JobResultResponse {
  job_id: string
  status: string
  summary: JobResultSummary
  outputs: JobOutputs
}

export async function analyzeFiles(
  eoicdWordFile: File | null,
  eoicdExcelFiles: File[],
  softwareRequirementFile: File
): Promise<AnalyzeResponse> {
  const formData = new FormData()
  if (eoicdWordFile) {
    formData.append('eoicd_word_file', eoicdWordFile)
  }
  eoicdExcelFiles.forEach((f) => formData.append('eoicd_excel_files', f))
  formData.append('software_requirement_file', softwareRequirementFile)

  const res = await fetch(`${API_BASE}/eoicd/analyze`, {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getJobResult(jobId: string): Promise<JobResultResponse> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}/result`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export type DownloadType =
  | 'requirements'
  | 'difference-report'
  | 'minimax-requirements'
  | 'deepseek-requirements'

export function getDownloadUrl(jobId: string, type: DownloadType): string {
  return `${API_BASE}/jobs/${jobId}/outputs/${type}`
}

export async function getPreviewHtml(
  jobId: string,
  type: 'requirements' | 'difference-report'
): Promise<string> {
  const url = getDownloadUrl(jobId, type)
  const res = await fetch(url)
  if (!res.ok) throw new Error('Failed to fetch preview')
  const buf = await res.arrayBuffer()
  const result = await mammoth.convertToHtml({ arrayBuffer: buf })
  return result.value
}
