import mammoth from 'mammoth'
import * as XLSX from 'xlsx'
import type {
  V4JobStatusResponse,
  V4JobResultResponse,
  V4DownloadKind,
  V4ForwardJobResultResponse,
  V4ForwardDownloadKind,
} from '../types'

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

export async function analyzeCompletenessV4(formData: FormData): Promise<{ job_id: string; status: string; message: string }> {
  const res = await fetch(`${API_V4_BASE}/completeness-analysis`, {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || 'Failed to create completeness job')
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

export async function getForwardJobResultV4(jobId: string): Promise<V4ForwardJobResultResponse> {
  const res = await fetch(`${API_V4_BASE}/jobs/${jobId}/result`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export function getDownloadUrlV4(jobId: string, kind: V4DownloadKind): string {
  return `${API_V4_BASE}/jobs/${jobId}/outputs/${kind}`
}

export function getForwardDownloadUrlV4(jobId: string, kind: V4ForwardDownloadKind): string {
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

function xlsxToHtml(buf: ArrayBuffer): string {
  const data = new Uint8Array(buf)
  const workbook = XLSX.read(data, { type: 'array' })
  const sheet = workbook.Sheets[workbook.SheetNames[0]]
  const rows = XLSX.utils.sheet_to_json(sheet, { header: 1 }) as unknown[][]
  const truncated = rows.slice(0, 200)
  const newSheet = XLSX.utils.aoa_to_sheet(truncated)
  return XLSX.utils.sheet_to_html(newSheet)
}

export async function getPreviewHtmlV4(jobId: string, kind: V4DownloadKind): Promise<string> {
  const url = getDownloadUrlV4(jobId, kind)
  const res = await fetch(url)
  if (!res.ok) throw new Error('Failed to fetch preview')

  const buf = await res.arrayBuffer()

  if (kind === 'eoicd-xlsx') {
    return xlsxToHtml(buf)
  }

  // docx files
  const result = await mammoth.convertToHtml({ arrayBuffer: buf })
  return result.value
}

export async function getForwardPreviewHtmlV4(jobId: string, kind: V4ForwardDownloadKind): Promise<string> {
  const url = getForwardDownloadUrlV4(jobId, kind)
  const res = await fetch(url)
  if (!res.ok) throw new Error('Failed to fetch preview')

  const buf = await res.arrayBuffer()

  if (kind === 'forward-xlsx') {
    return xlsxToHtml(buf)
  }

  const result = await mammoth.convertToHtml({ arrayBuffer: buf })
  return result.value
}
