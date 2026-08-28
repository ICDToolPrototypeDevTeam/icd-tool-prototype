import { useRef } from 'react'
import type { FileItem, ForwardAnalysisMode } from '../types'
import FilePreview from './FilePreview'

interface Props {
  hlrWordFile: FileItem | null
  eoicdPublisherFile: FileItem | null
  eoicdSubscriberFile: FileItem | null
  deviceIcdTraceFile: FileItem | null
  systemDeviceTraceFile: FileItem | null
  analysisMode: ForwardAnalysisMode
  selectedPreviewFile: FileItem | null
  onHlrWordChange: (file: FileItem | null) => void
  onEoicdPublisherChange: (file: FileItem | null) => void
  onEoicdSubscriberChange: (file: FileItem | null) => void
  onDeviceIcdTraceChange: (file: FileItem | null) => void
  onSystemDeviceTraceChange: (file: FileItem | null) => void
  onAnalysisModeChange: (mode: ForwardAnalysisMode) => void
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

export default function CompletenessFileUpload({
  hlrWordFile,
  eoicdPublisherFile,
  eoicdSubscriberFile,
  deviceIcdTraceFile,
  systemDeviceTraceFile,
  analysisMode,
  selectedPreviewFile,
  onHlrWordChange,
  onEoicdPublisherChange,
  onEoicdSubscriberChange,
  onDeviceIcdTraceChange,
  onSystemDeviceTraceChange,
  onAnalysisModeChange,
  onPreviewSelect,
}: Props) {
  const hlrWordInputRef = useRef<HTMLInputElement>(null)
  const publisherInputRef = useRef<HTMLInputElement>(null)
  const subscriberInputRef = useRef<HTMLInputElement>(null)
  const deviceIcdTraceInputRef = useRef<HTMLInputElement>(null)
  const systemDeviceTraceInputRef = useRef<HTMLInputElement>(null)

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
          {/* Analysis mode toggle */}
          <div className="version-tabs" style={{ marginBottom: 20 }}>
            <button
              className={`version-tab ${analysisMode === 'full' ? 'version-tab--active' : ''}`}
              onClick={() => onAnalysisModeChange('full')}
            >
              全量分析
            </button>
            <button
              className={`version-tab ${analysisMode === 'trace' ? 'version-tab--active' : ''}`}
              onClick={() => onAnalysisModeChange('trace')}
            >
              追溯范围分析
            </button>
          </div>

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

          {/* Trace files (trace mode only) */}
          {analysisMode === 'trace' && (
            <>
              <div className="file-section">
                <div className="file-section__title">
                  📎 设备→ICD 追溯表 <span className="file-section__required">*追溯模式必填</span>
                </div>
                {renderFileItem(deviceIcdTraceFile, onDeviceIcdTraceChange, '📎')}
                {!deviceIcdTraceFile &&
                  renderUploadButton('上传 设备→ICD 追溯表', deviceIcdTraceInputRef, '.xlsx,.xls', (e) =>
                    handleSingleFile(e, onDeviceIcdTraceChange)
                  )}
              </div>

              <div className="file-section">
                <div className="file-section__title">
                  📎 设备→高层需求 追溯表 <span className="file-section__required">*追溯模式必填</span>
                </div>
                {renderFileItem(systemDeviceTraceFile, onSystemDeviceTraceChange, '📎')}
                {!systemDeviceTraceFile &&
                  renderUploadButton('上传 设备→高层需求 追溯表', systemDeviceTraceInputRef, '.xlsx,.xls', (e) =>
                    handleSingleFile(e, onSystemDeviceTraceChange)
                  )}
              </div>
            </>
          )}
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
