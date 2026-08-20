import mammoth from 'mammoth'
import * as XLSX from 'xlsx'
import type { V4JobStatusResponse, V4JobResultResponse, V4DownloadKind } from '../types'

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
