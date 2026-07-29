import { useRef } from 'react'
import type { FileItem } from '../types'
import FilePreview from './FilePreview'

interface Props {
  hlrWordFile: FileItem | null
  eoicdPublisherFile: FileItem | null
  eoicdSubscriberFile: FileItem | null
  traceabilityFiles: FileItem[]
  selectedPreviewFile: FileItem | null
  onHlrWordChange: (file: FileItem | null) => void
  onEoicdPublisherChange: (file: FileItem | null) => void
  onEoicdSubscriberChange: (file: FileItem | null) => void
  onTraceabilityChange: (files: FileItem[]) => void
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

export default function V4FileUpload({
  hlrWordFile,
  eoicdPublisherFile,
  eoicdSubscriberFile,
  traceabilityFiles,
  selectedPreviewFile,
  onHlrWordChange,
  onEoicdPublisherChange,
  onEoicdSubscriberChange,
  onTraceabilityChange,
  onPreviewSelect,
}: Props) {
  const hlrWordInputRef = useRef<HTMLInputElement>(null)
  const publisherInputRef = useRef<HTMLInputElement>(null)
  const subscriberInputRef = useRef<HTMLInputElement>(null)
  const traceabilityInputRef = useRef<HTMLInputElement>(null)

  function handleSingleFile(
    e: React.ChangeEvent<HTMLInputElement>,
    onChange: (item: FileItem | null) => void
  ) {
    const f = e.target.files?.[0]
    if (f) {
      const item = makeFileItem(f)
      onChange(item)
      onPreviewSelect(item)
    }
  }

  function handleTraceability(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files
    if (!files) return
    const items = Array.from(files).map(makeFileItem)
    onTraceabilityChange([...traceabilityFiles, ...items])
  }

  function renderFileItem(
    file: FileItem | null,
    onChange: (f: FileItem | null) => void,
    icon: string
  ) {
    if (!file) return null
    return (
      <div className="file-list">
        <div
          className={`file-item ${selectedPreviewFile?.id === file.id ? 'selected' : ''}`}
          onClick={() => onPreviewSelect(file)}
        >
          <div className="file-item__icon">{icon}</div>
          <div className="file-item__info">
            <div className="file-item__name">{file.name}</div>
            <div className="file-item__meta">{formatSize(file.size)} · {file.type === 'excel' ? 'Excel' : 'Word'}</div>
          </div>
          <button
            className="file-item__remove"
            onClick={(ev) => {
              ev.stopPropagation()
              onChange(null)
              if (selectedPreviewFile?.id === file.id) onPreviewSelect(null)
            }}
          >
            ✕
          </button>
        </div>
      </div>
    )
  }

  function renderUploadButton(
    label: string,
    inputRef: React.RefObject<HTMLInputElement>,
    accept: string,
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => void,
    multiple = false
  ) {
    return (
      <div className="upload-btn-wrapper">
        <label className="upload-btn">
          <input
            ref={inputRef}
            type="file"
            accept={accept}
            multiple={multiple}
            onChange={onChange}
            style={{ display: 'none' }}
          />
          <span>+</span> {label}
        </label>
      </div>
    )
  }

  return (
    <div className="content-grid">
      <div className="card" style={{ height: 480 }}>
        <div className="card__header">
          <div className="card__icon card__icon--blue">📁</div>
          <div>
            <div className="card__title">文件上传</div>
            <div className="card__subtitle">上传 HLR Word、EoICD Pub/Sub Excel 与追溯表</div>
          </div>
        </div>
        <div className="card__body" style={{ height: 'calc(100% - 81px)', overflow: 'auto' }}>
          {/* HLR Word (required) */}
          <div className="file-section">
            <div className="file-section__title">
              📄 HLR Word 文件 <span className="file-section__required">*必填</span>
            </div>
            {renderFileItem(hlrWordFile, onHlrWordChange, '📄')}
            {!hlrWordFile &&
              renderUploadButton('上传 HLR Word 文件', hlrWordInputRef, '.docx', (e) =>
                handleSingleFile(e, onHlrWordChange)
              )}
          </div>

          {/* EoICD Publisher Excel */}
          <div className="file-section">
            <div className="file-section__title">
              📊 EoICD Publisher Excel <span className="file-section__required">*至少填一</span>
            </div>
            {renderFileItem(eoicdPublisherFile, onEoicdPublisherChange, '📊')}
            {!eoicdPublisherFile &&
              renderUploadButton('上传 Publisher Excel', publisherInputRef, '.xlsx,.xls', (e) =>
                handleSingleFile(e, onEoicdPublisherChange)
              )}
          </div>

          {/* EoICD Subscriber Excel */}
          <div className="file-section">
            <div className="file-section__title">
              📊 EoICD Subscriber Excel <span className="file-section__required">*至少填一</span>
            </div>
            {renderFileItem(eoicdSubscriberFile, onEoicdSubscriberChange, '📊')}
            {!eoicdSubscriberFile &&
              renderUploadButton('上传 Subscriber Excel', subscriberInputRef, '.xlsx,.xls', (e) =>
                handleSingleFile(e, onEoicdSubscriberChange)
              )}
          </div>

          {/* Traceability files (optional, multi) */}
          <div className="file-section">
            <div className="file-section__title">
              📎 追溯表 <span className="file-section__optional">选填（0-N）</span>
            </div>
            {traceabilityFiles.length > 0 && (
              <div className="file-list">
                {traceabilityFiles.map((f) => (
                  <div
                    key={f.id}
                    className={`file-item ${selectedPreviewFile?.id === f.id ? 'selected' : ''}`}
                    onClick={() => onPreviewSelect(f)}
                  >
                    <div className="file-item__icon">📎</div>
                    <div className="file-item__info">
                      <div className="file-item__name">{f.name}</div>
                      <div className="file-item__meta">{formatSize(f.size)} · Excel</div>
                    </div>
                    <button
                      className="file-item__remove"
                      onClick={(ev) => {
                        ev.stopPropagation()
                        const next = traceabilityFiles.filter((x) => x.id !== f.id)
                        onTraceabilityChange(next)
                        if (selectedPreviewFile?.id === f.id) {
                          onPreviewSelect(next.length > 0 ? next[0] : null)
                        }
                      }}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}
            {renderUploadButton(
              '添加追溯表',
              traceabilityInputRef,
              '.xlsx,.xls',
              handleTraceability,
              true
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
