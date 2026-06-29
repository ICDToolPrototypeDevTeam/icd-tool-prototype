import { useEffect, useState } from 'react'
import * as XLSX from 'xlsx'
import mammoth from 'mammoth'

interface Props {
  file: File | null
}

export default function FilePreview({ file }: Props) {
  const [loading, setLoading] = useState(false)
  const [htmlContent, setHtmlContent] = useState('')
  const [sheets, setSheets] = useState<{ name: string; headers: string[]; rows: string[][] }[]>([])
  const [activeSheet, setActiveSheet] = useState(0)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!file) {
      setHtmlContent('')
      setSheets([])
      setError(null)
      return
    }

    setLoading(true)
    setError(null)

    if (file.name.endsWith('.xlsx') || file.name.endsWith('.xls')) {
      const reader = new FileReader()
      reader.onload = (e) => {
        try {
          const data = new Uint8Array(e.target?.result as ArrayBuffer)
          const workbook = XLSX.read(data, { type: 'array' })
          const validNames = workbook.SheetNames.filter(
            (n) => !n.startsWith('WpsReserved_') && !n.startsWith('_') && !n.includes('CellImg')
          )
          const parsed = validNames.map((name) => {
            const sheet = workbook.Sheets[name]
            const json = XLSX.utils.sheet_to_json(sheet, { header: 1 }) as string[][]
            return {
              name,
              headers: (json[0] || []) as string[],
              rows: json.slice(1).filter((row) => row.some((cell) => cell !== '')),
            }
          })
          setSheets(parsed)
          setActiveSheet(0)
          setLoading(false)
        } catch {
          setError('无法解析 Excel 文件')
          setLoading(false)
        }
      }
      reader.onerror = () => {
        setError('读取文件失败')
        setLoading(false)
      }
      reader.readAsArrayBuffer(file)
    } else if (file.name.endsWith('.docx')) {
      const reader = new FileReader()
      reader.onload = async (e) => {
        try {
          const buf = e.target?.result as ArrayBuffer
          const result = await mammoth.convertToHtml({ arrayBuffer: buf })
          setHtmlContent(result.value)
          setLoading(false)
        } catch {
          setError('无法解析 Word 文件')
          setLoading(false)
        }
      }
      reader.onerror = () => {
        setError('读取文件失败')
        setLoading(false)
      }
      reader.readAsArrayBuffer(file)
    } else {
      setError('不支持的文件格式')
      setLoading(false)
    }
  }, [file])

  if (!file) {
    return (
      <div className="preview-content">
        <div className="preview-empty">
          <div className="preview-empty__icon">📄</div>
          <p>选择文件后即可预览内容</p>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="preview-content">
        <div className="preview-empty">
          <div className="preview-empty__icon">⏳</div>
          <p>正在加载预览...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="preview-content">
        <div className="preview-empty">
          <div className="preview-empty__icon">⚠️</div>
          <p>{error}</p>
        </div>
      </div>
    )
  }

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  }

  const isExcel = file.name.endsWith('.xlsx') || file.name.endsWith('.xls')

  return (
    <div>
      <div className="file-info">
        <div className="file-icon">{isExcel ? '📊' : '📄'}</div>
        <div className="file-details">
          <div className="file-name">{file.name}</div>
          <div className="file-size">{formatSize(file.size)} · 已加载</div>
        </div>
      </div>

      {isExcel && sheets.length > 0 ? (
        <>
          <div className="sheet-tabs">
            {sheets.map((s, i) => (
              <button
                key={s.name}
                className={`sheet-tab ${i === activeSheet ? 'active' : ''}`}
                onClick={() => setActiveSheet(i)}
              >
                {s.name}
              </button>
            ))}
          </div>
          <div className="preview-content" style={{ maxHeight: 320, overflow: 'auto' }}>
            <table className="preview-excel-table">
              <thead>
                <tr>
                  {sheets[activeSheet].headers.map((h, i) => (
                    <th key={i}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sheets[activeSheet].rows.slice(0, 100).map((row, ri) => (
                  <tr key={ri}>
                    {row.map((cell, ci) => (
                      <td key={ci}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div
          className="preview-content"
          style={{ maxHeight: 320, overflow: 'auto' }}
          dangerouslySetInnerHTML={{ __html: htmlContent }}
        />
      )}
    </div>
  )
}
