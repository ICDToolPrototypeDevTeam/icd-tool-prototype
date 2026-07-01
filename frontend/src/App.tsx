import { useState, useEffect } from 'react'
import FileUpload from './components/FileUpload'
import ProcessingView from './components/ProcessingView'
import ResultView from './components/ResultView'
import { analyzeFiles, getJobStatus, getJobResult } from './api'
import type { PageState, FileItem, ResultData } from './types'

export default function App() {
  const [pageState, setPageState] = useState<PageState>('upload')
  const [progress, setProgress] = useState('已提交，等待开始')
  const [resultData, setResultData] = useState<ResultData | null>(null)
  const [isOnline, setIsOnline] = useState(true)

  // File state
  const [eoicdWordFile, setEoicdWordFile] = useState<FileItem | null>(null)
  const [eoicdExcelFiles, setEoicdExcelFiles] = useState<FileItem[]>([])
  const [swReqFile, setSwReqFile] = useState<FileItem | null>(null)
  const [selectedPreviewFile, setSelectedPreviewFile] = useState<FileItem | null>(null)

  // Health check
  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.ok && setIsOnline(true))
      .catch(() => setIsOnline(false))
  }, [])

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

  function handleStart() {
    if ((!eoicdWordFile && eoicdExcelFiles.length === 0) || !swReqFile) {
      alert('请至少上传一个 EoICD 文件（Word 或 Excel）和软件高层需求文件')
      return
    }
    setPageState('processing')
    setProgress('已提交，等待开始')

    const wordFile = eoicdWordFile?.file || null
    const excelFiles = eoicdExcelFiles.map((f) => f.file).filter((f): f is File => f !== undefined)
    const swFile = swReqFile.file!

    analyzeFiles(wordFile, excelFiles, swFile)
      .then((res) => {
        pollStatus(res.job_id)
      })
      .catch((err) => {
        console.error(err)
        setPageState('error')
      })
  }

  function pollStatus(id: string) {
    const maxRetries = 600
    let retries = 0

    const check = async () => {
      try {
        const status = await getJobStatus(id)
        if (status.message) {
          setProgress(status.message)
        }

        if (status.status === 'completed') {
          try {
            const result = await getJobResult(id)
            setResultData({
              job_id: id,
              summary: result.summary,
              outputs: result.outputs,
            })
          } catch (e) {
            console.error('Failed to fetch results:', e)
          }
          setPageState('success')
        } else if (status.status === 'failed') {
          setPageState('error')
        } else if (retries < maxRetries) {
          retries++
          setTimeout(check, 2000)
        } else {
          setPageState('error')
        }
      } catch {
        if (retries < maxRetries) {
          retries++
          setTimeout(check, 2000)
        } else {
          setPageState('error')
        }
      }
    }

    check()
  }

  function handleNewTask() {
    setResultData(null)
    setProgress('已提交，等待开始')
    setEoicdWordFile(null)
    setEoicdExcelFiles([])
    setSwReqFile(null)
    setSelectedPreviewFile(null)
    setPageState('upload')
  }

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className="header__left">
          <img src="/logo1.png" alt="Logo" className="header__logo-img" />
          <div>
            <div className="header__title">ICD 需求生成器</div>
            <div className="header__subtitle">Interface Control Document Requirements Generator</div>
          </div>
        </div>
        <div className="header__status">
          <div className={`status-dot ${!isOnline ? 'status-dot--offline' : ''}`} />
          <span>{isOnline ? '在线服务' : '服务离线'}</span>
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

        {/* Page Content */}
        {pageState === 'upload' && (
          <>
            <FileUpload
              eoicdWordFile={eoicdWordFile}
              eoicdExcelFiles={eoicdExcelFiles}
              swReqFile={swReqFile}
              selectedPreviewFile={selectedPreviewFile}
              onEoicdWordChange={(f) => {
                setEoicdWordFile(f)
                if (f) setSelectedPreviewFile(f)
              }}
              onEoicdExcelsChange={(files) => {
                setEoicdExcelFiles(files)
              }}
              onSwReqChange={(f) => {
                setSwReqFile(f)
                if (f) setSelectedPreviewFile(f)
              }}
              onPreviewSelect={setSelectedPreviewFile}
            />
            <div className="action-bar">
              <button
                className="btn btn--secondary btn--large"
                onClick={handleNewTask}
              >
                ↻ 清空全部
              </button>
              <button className="btn btn--primary btn--large" onClick={handleStart}>
                ▶ 开始处理
              </button>
            </div>
          </>
        )}

        {pageState === 'processing' && <ProcessingView progress={progress} />}

        {pageState === 'success' && resultData && (
          <ResultView data={resultData} onNewTask={handleNewTask} />
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
