import mammoth from 'mammoth'
import * as XLSX from 'xlsx'
import type { V4JobStatusResponse, V4JobResultResponse, V4DownloadKind } from '../types'

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

// ========== V4 API ==========

const API_V4_BASE = '/api/v4'

export async function analyzeFilesV4(formData: FormData): Promise<{ job_id: string; status: string; message: string }> {
  const res = await fetch(`${API_V4_BASE}/coverage-analysis`, {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || 'Failed to create V4 job')
  }
  return res.json()
}

export async function getJobStatusV4(jobId: string): Promise<V4JobStatusResponse> {
  const res = await fetch(`${API_V4_BASE}/jobs/${jobId}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getJobResultV4(jobId: string): Promise<V4JobResultResponse> {
  const res = await fetch(`${API_V4_BASE}/jobs/${jobId}/result`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export function getDownloadUrlV4(jobId: string, kind: V4DownloadKind): string {
  return `${API_V4_BASE}/jobs/${jobId}/outputs/${kind}`
}

export async function checkV4Health(): Promise<boolean> {
  try {
    const res = await fetch(`${API_V4_BASE}/health`)
    return res.ok
  } catch {
    return false
  }
}

export async function getPreviewHtmlV4(jobId: string, kind: V4DownloadKind): Promise<string> {
  const url = getDownloadUrlV4(jobId, kind)
  const res = await fetch(url)
  if (!res.ok) throw new Error('Failed to fetch preview')

  const buf = await res.arrayBuffer()

  if (kind === 'eoicd-xlsx') {
    const data = new Uint8Array(buf)
    const workbook = XLSX.read(data, { type: 'array' })
    const sheet = workbook.Sheets[workbook.SheetNames[0]]
    const rows = XLSX.utils.sheet_to_json(sheet, { header: 1 })
    const truncated = rows.slice(0, 200)
    const newSheet = XLSX.utils.aoa_to_sheet(truncated)
    const json = XLSX.utils.sheet_to_html(newSheet)
    return json
  }

  // docx files
  const result = await mammoth.convertToHtml({ arrayBuffer: buf })
  return result.value
}
