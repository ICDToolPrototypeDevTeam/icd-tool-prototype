import { useState, useEffect } from 'react'
import V4FileUpload from './components/V4FileUpload'
import ProcessingView from './components/ProcessingView'
import V4ResultView from './components/V4ResultView'
import {
  analyzeFilesV4,
  getJobStatusV4,
  getJobResultV4,
  checkV4Health,
} from './api'
import type { PageState, FileItem, V4JobResultResponse } from './types'

export default function App() {
  const [pageState, setPageState] = useState<PageState>('upload')
  const [progress, setProgress] = useState('已提交，等待开始')
  const [v4ResultData, setV4ResultData] = useState<V4JobResultResponse | null>(null)
  const [isOnline, setIsOnline] = useState(true)
  const [v4Online, setV4Online] = useState(true)

  // V4 stage progress
  const [v4Stage, setV4Stage] = useState('')
  const [v4StageIndex, setV4StageIndex] = useState(0)
  const [v4StageTotal, setV4StageTotal] = useState(0)
  const [v4CaseIndex, setV4CaseIndex] = useState(0)
  const [v4CaseTotal, setV4CaseTotal] = useState(0)

  // V4 file state
  const [v4HlrWordFile, setV4HlrWordFile] = useState<FileItem | null>(null)
  const [v4PublisherFile, setV4PublisherFile] = useState<FileItem | null>(null)
  const [v4SubscriberFile, setV4SubscriberFile] = useState<FileItem | null>(null)
  const [v4TraceabilityFiles, setV4TraceabilityFiles] = useState<FileItem[]>([])
  const [v4SelectedPreviewFile, setV4SelectedPreviewFile] = useState<FileItem | null>(null)
  const [v4SystemType, setV4SystemType] = useState<string>('')

  // Health checks
  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.ok && setIsOnline(true))
      .catch(() => setIsOnline(false))
    checkV4Health()
      .then((ok) => setV4Online(ok))
      .catch(() => setV4Online(false))
  }, [])

  function resetAllState() {
    setPageState('upload')
    setProgress('已提交，等待开始')
    setV4ResultData(null)
    setV4Stage('')
    setV4StageIndex(0)
    setV4StageTotal(0)
    setV4CaseIndex(0)
    setV4CaseTotal(0)
    setV4HlrWordFile(null)
    setV4PublisherFile(null)
    setV4SubscriberFile(null)
    setV4TraceabilityFiles([])
    setV4SelectedPreviewFile(null)
    setV4SystemType('')
  }

  // Workflow step classes
  function getStepClass(step: number): string {
    if (step === 1 && pageState === 'upload') return 'active'
    if (step === 1 && pageState !== 'upload') return 'completed'
    if (step === 2 && pageState === 'processing') return 'active'
    if (step === 2 && (pageState === 'success' || pageState === 'error')) return 'completed'
    if (step === 3 && pageState === 'success') return 'active'
    if (step === 3 && pageState === 'error') return 'completed'
    return ''
  }

  // ===== V4 handlers =====
  function handleV4Start() {
    if (!v4HlrWordFile) {
      alert('请上传 HLR Word 文件')
      return
    }
    if (!v4PublisherFile && !v4SubscriberFile) {
      alert('请至少上传 Publisher Excel 或 Subscriber Excel')
      return
    }

    setPageState('processing')
    setProgress('已提交，等待开始')
    setV4Stage('')
    setV4StageIndex(0)
    setV4StageTotal(0)
    setV4CaseIndex(0)
    setV4CaseTotal(0)

    const formData = new FormData()
    if (v4HlrWordFile.file) formData.append('hlr_word_file', v4HlrWordFile.file)
    if (v4PublisherFile?.file) formData.append('eoicd_publisher_file', v4PublisherFile.file)
    if (v4SubscriberFile?.file) formData.append('eoicd_subscriber_file', v4SubscriberFile.file)
    if (v4SystemType) formData.append('system_type', v4SystemType)

    if (v4TraceabilityFiles.length > 0) {
      v4TraceabilityFiles.forEach((f) => {
        if (f.file) formData.append('traceability_files', f.file)
      })
      formData.append('enable_traceability_prefilter', 'true')
    }

    analyzeFilesV4(formData)
      .then((res) => pollV4Status(res.job_id))
      .catch((err) => {
        console.error(err)
        setPageState('error')
      })
  }

  function pollV4Status(id: string) {
    const maxRetries = 120
    let retries = 0

    const check = async () => {
      try {
        const status = await getJobStatusV4(id)
        if (status.message) setProgress(status.message)
        if (status.stage) setV4Stage(status.stage)
        if (status.stage_index !== undefined) setV4StageIndex(status.stage_index)
        if (status.stage_total !== undefined) setV4StageTotal(status.stage_total)
        if (status.case_index !== undefined) setV4CaseIndex(status.case_index)
        if (status.case_total !== undefined) setV4CaseTotal(status.case_total)

        if (status.status === 'completed') {
          try {
            const result = await getJobResultV4(id)
            setV4ResultData(result)
          } catch (e) {
            console.error('Failed to fetch V4 results:', e)
          }
          setPageState('success')
        } else if (status.status === 'failed') {
          setPageState('error')
        } else if (retries < maxRetries) {
          retries++
          setTimeout(check, 10000)
        } else {
          setPageState('error')
        }
      } catch {
        if (retries < maxRetries) {
          retries++
          setTimeout(check, 10000)
        } else {
          setPageState('error')
        }
      }
    }
    check()
  }

  // ===== Shared =====
  function handleNewTask() {
    resetAllState()
  }

  const v4JobId = v4ResultData?.job_id || ''

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className="header__left">
          <img src="/logo1.png" alt="Logo" className="header__logo-img" />
          <div>
            <div className="header__title">ICD工具平台</div>
            <div className="header__subtitle">ICD Tool Platform</div>
          </div>
        </div>
        <div className="header__status">
          <div className={`status-dot ${(!isOnline || !v4Online) ? 'status-dot--offline' : ''}`} />
          <span>{!isOnline ? '服务离线' : !v4Online ? 'V4 服务不可用' : '在线服务'}</span>
        </div>
      </header>

      {/* Main */}
      <main className="main">
        {/* Workflow Steps */}
        <div className="workflow">
          <div className={`step ${getStepClass(1)}`}>
            <div className="step__number">1</div>
            <div className="step__label">上传文件</div>
            <div className="step__arrow">›</div>
          </div>
          <div className={`step ${getStepClass(2)}`}>
            <div className="step__number">2</div>
            <div className="step__label">文件处理</div>
            <div className="step__arrow">›</div>
          </div>
          <div className={`step ${getStepClass(3)}`}>
            <div className="step__number">3</div>
            <div className="step__label">查看结果</div>
          </div>
        </div>

        {!v4Online && pageState === 'upload' && (
          <div className="error-state" style={{ marginBottom: 24 }}>
            <div className="error-icon">🔌</div>
            <h3 className="error-title">V4 后端服务不可用</h3>
            <p className="error-message">请确认后端已启动并支持 /api/v4</p>
          </div>
        )}

        {pageState === 'upload' && (
          <>
            <V4FileUpload
              hlrWordFile={v4HlrWordFile}
              eoicdPublisherFile={v4PublisherFile}
              eoicdSubscriberFile={v4SubscriberFile}
              traceabilityFiles={v4TraceabilityFiles}
              selectedPreviewFile={v4SelectedPreviewFile}
              onHlrWordChange={(f) => { setV4HlrWordFile(f); if (f) setV4SelectedPreviewFile(f) }}
              onEoicdPublisherChange={(f) => { setV4PublisherFile(f); if (f) setV4SelectedPreviewFile(f) }}
              onEoicdSubscriberChange={(f) => { setV4SubscriberFile(f); if (f) setV4SelectedPreviewFile(f) }}
              onTraceabilityChange={(files) => {
                setV4TraceabilityFiles(files)
                if (files.length > 0) setV4SelectedPreviewFile(files[files.length - 1])
              }}
              onPreviewSelect={setV4SelectedPreviewFile}
            />
            {/* System Type Selector */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
              <span style={{ fontSize: 14, color: '#555' }}>系统类型：</span>
              <select
                value={v4SystemType}
                onChange={(e) => setV4SystemType(e.target.value)}
                style={{
                  padding: '6px 12px',
                  borderRadius: 6,
                  border: '1px solid #ddd',
                  fontSize: 14,
                  minWidth: 140,
                }}
              >
                <option value="">自动识别</option>
                <option value="hvac">环控系统</option>
                <option value="fuel">燃油系统</option>
                <option value="hscu">液压系统</option>
              </select>
              <span style={{ fontSize: 12, color: '#888' }}>（默认自动识别，可手动选择）</span>
            </div>
            <div className="action-bar">
              <button className="btn btn--secondary btn--large" onClick={handleNewTask}>
                清空全部
              </button>
              <button className="btn btn--primary btn--large" onClick={handleV4Start}>
                开始处理
              </button>
            </div>
          </>
        )}

        {pageState === 'processing' && (
          <ProcessingView
            progress={progress}
            stage={v4Stage}
            stageIndex={v4StageIndex}
            stageTotal={v4StageTotal}
            caseIndex={v4CaseIndex}
            caseTotal={v4CaseTotal}
          />
        )}

        {pageState === 'success' && v4ResultData && (
          <V4ResultView data={v4ResultData} jobId={v4JobId} onNewTask={handleNewTask} />
        )}

        {pageState === 'error' && (
          <div className="error-state">
            <div className="error-icon">⚠️</div>
            <h3 className="error-title">处理失败</h3>
            <p className="error-message">请检查文件格式是否正确，或稍后重试</p>
            <button className="btn btn--new" onClick={handleNewTask}>
              重新尝试
            </button>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="footer">
        <div className="footer__left">
          <img src="/logo2.jpg" alt="AVIC" className="footer__logo-img" />
          <span className="footer__divider">|</span>
          <span>中航工业民机机载系统工程中心有限公司</span>
        </div>
        <div className="footer__right">
          <span>© 2026 中航工业民机机载系统工程中心有限公司</span>
        </div>
      </footer>
    </div>
  )
}
