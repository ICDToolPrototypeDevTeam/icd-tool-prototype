import { useRef } from 'react'
import type { FileItem } from '../types'
import FilePreview from './FilePreview'

interface Props {
  eoicdWordFile: FileItem | null
  eoicdExcelFiles: FileItem[]
  swReqFile: FileItem | null
  selectedPreviewFile: FileItem | null
  onEoicdWordChange: (file: FileItem | null) => void
  onEoicdExcelsChange: (files: FileItem[]) => void
  onSwReqChange: (file: FileItem | null) => void
  onPreviewSelect: (file: FileItem | null) => void
}

function makeFileItem(file: File): FileItem {
  const isExcel = file.name.endsWith('.xlsx') || file.name.endsWith('.xls')
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    name: file.name,
    size: file.size,
    type: isExcel ? 'excel' : 'word',
    file,
  }
}

function formatSize(bytes: number) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

export default function FileUpload({
  eoicdWordFile,
  eoicdExcelFiles,
  swReqFile,
  selectedPreviewFile,
  onEoicdWordChange,
  onEoicdExcelsChange,
  onSwReqChange,
  onPreviewSelect,
}: Props) {
  const eoicdWordInputRef = useRef<HTMLInputElement>(null)
  const eoicdExcelInputRef = useRef<HTMLInputElement>(null)
  const swReqInputRef = useRef<HTMLInputElement>(null)

  function handleEoicdWord(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (f) {
      const item = makeFileItem(f)
      onEoicdWordChange(item)
      onPreviewSelect(item)
    }
  }

  function handleEoicdExcels(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files
    if (!files) return
    const items = Array.from(files).map(makeFileItem)
    onEoicdExcelsChange([...eoicdExcelFiles, ...items])
  }

  function handleSwReq(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (f) {
      const item = makeFileItem(f)
      onSwReqChange(item)
      onPreviewSelect(item)
    }
  }

  return (
    <div className="content-grid">
      <div className="card" style={{ height: 480 }}>
        <div className="card__header">
          <div className="card__icon card__icon--blue">📁</div>
          <div>
            <div className="card__title">文件上传</div>
            <div className="card__subtitle">上传 EoICD 源文件与软件高层需求文件</div>
          </div>
        </div>
        <div className="card__body" style={{ height: 'calc(100% - 81px)', overflow: 'auto' }}>
          {/* EoICD Word 主文件 */}
          <div className="file-section">
            <div className="file-section__title">
              📄 EoICD Word 主文件
            </div>
            {eoicdWordFile ? (
              <div className="file-list">
                <div
                  className={`file-item ${selectedPreviewFile?.id === eoicdWordFile.id ? 'selected' : ''}`}
                  onClick={() => onPreviewSelect(eoicdWordFile)}
                >
                  <div className="file-item__icon">📄</div>
                  <div className="file-item__info">
                    <div className="file-item__name">{eoicdWordFile.name}</div>
                    <div className="file-item__meta">{formatSize(eoicdWordFile.size)} · Word</div>
                  </div>
                  <button
                    className="file-item__remove"
                    onClick={(ev) => {
                      ev.stopPropagation()
                      onEoicdWordChange(null)
                      if (selectedPreviewFile?.id === eoicdWordFile.id) onPreviewSelect(null)
                    }}
                  >
                    ✕
                  </button>
                </div>
              </div>
            ) : (
              <div className="upload-btn-wrapper">
                <label className="upload-btn">
                  <input
                    ref={eoicdWordInputRef}
                    type="file"
                    accept=".docx"
                    onChange={handleEoicdWord}
                    style={{ display: 'none' }}
                  />
                  <span>+</span> 上传 EoICD Word 文件
                </label>
              </div>
            )}
          </div>

          {/* EoICD Excel 附件 */}
          <div className="file-section">
            <div className="file-section__title">
              📊 EoICD Excel 附件
            </div>
            {eoicdExcelFiles.length > 0 && (
              <div className="file-list">
                {eoicdExcelFiles.map((f) => (
                  <div
                    key={f.id}
                    className={`file-item ${selectedPreviewFile?.id === f.id ? 'selected' : ''}`}
                    onClick={() => onPreviewSelect(f)}
                  >
                    <div className="file-item__icon">📊</div>
                    <div className="file-item__info">
                      <div className="file-item__name">{f.name}</div>
                      <div className="file-item__meta">{formatSize(f.size)} · Excel</div>
                    </div>
                    <button
                      className="file-item__remove"
                      onClick={(ev) => {
                        ev.stopPropagation()
                        const next = eoicdExcelFiles.filter((x) => x.id !== f.id)
                        onEoicdExcelsChange(next)
                        if (selectedPreviewFile?.id === f.id)
                          onPreviewSelect(next.length > 0 ? next[0] : eoicdWordFile || swReqFile)
                      }}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="upload-btn-wrapper">
              <label className="upload-btn">
                <input
                  ref={eoicdExcelInputRef}
                  type="file"
                  accept=".xlsx,.xls"
                  multiple
                  onChange={handleEoicdExcels}
                  style={{ display: 'none' }}
                />
                <span>+</span> 添加 Excel 附件
              </label>
            </div>
          </div>

          {/* 软件高层需求文件 */}
          <div className="file-section">
            <div className="file-section__title">
              📋 软件高层需求文件
            </div>
            {swReqFile ? (
              <div className="file-list">
                <div
                  className={`file-item ${selectedPreviewFile?.id === swReqFile.id ? 'selected' : ''}`}
                  onClick={() => onPreviewSelect(swReqFile)}
                >
                  <div className="file-item__icon">📋</div>
                  <div className="file-item__info">
                    <div className="file-item__name">{swReqFile.name}</div>
                    <div className="file-item__meta">{formatSize(swReqFile.size)} · Word</div>
                  </div>
                  <button
                    className="file-item__remove"
                    onClick={(ev) => {
                      ev.stopPropagation()
                      onSwReqChange(null)
                      if (selectedPreviewFile?.id === swReqFile.id) onPreviewSelect(null)
                    }}
                  >
                    ✕
                  </button>
                </div>
              </div>
            ) : (
              <div className="upload-btn-wrapper">
                <label className="upload-btn">
                  <input
                    ref={swReqInputRef}
                    type="file"
                    accept=".docx"
                    onChange={handleSwReq}
                    style={{ display: 'none' }}
                  />
                  <span>+</span> 上传软件高层需求文件
                </label>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Preview Panel */}
      <div className="card" style={{ height: 480 }}>
        <div className="card__header">
          <div className="card__icon card__icon--blue">👁️</div>
          <div>
            <div className="card__title">文件预览</div>
            <div className="card__subtitle">查看选中文件的内容</div>
          </div>
        </div>
        <div className="card__body" style={{ height: 'calc(100% - 81px)', overflow: 'auto' }}>
          <FilePreview file={selectedPreviewFile?.file || null} />
        </div>
      </div>
    </div>
  )
}
