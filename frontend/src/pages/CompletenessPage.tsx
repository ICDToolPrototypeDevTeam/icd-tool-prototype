import { useState } from 'react'
import CompletenessFileUpload from '../components/CompletenessFileUpload'
import CompletenessResultView from '../components/CompletenessResultView'
import ProcessingView from '../components/ProcessingView'
import WorkflowSteps from '../components/WorkflowSteps'
import { useAnalysisJob } from '../hooks/useAnalysisJob'
import { analyzeCompletenessV4, getForwardJobResultV4 } from '../api'
import type { FileItem, ForwardAnalysisMode, V4ForwardJobResultResponse } from '../types'

export default function CompletenessPage() {
  const job = useAnalysisJob<V4ForwardJobResultResponse>()

  const [hlrWordFile, setHlrWordFile] = useState<FileItem | null>(null)
  const [publisherFile, setPublisherFile] = useState<FileItem | null>(null)
  const [subscriberFile, setSubscriberFile] = useState<FileItem | null>(null)
  const [deviceIcdTraceFile, setDeviceIcdTraceFile] = useState<FileItem | null>(null)
  const [systemDeviceTraceFile, setSystemDeviceTraceFile] = useState<FileItem | null>(null)
  const [analysisMode, setAnalysisMode] = useState<ForwardAnalysisMode>('full')
  const [selectedPreviewFile, setSelectedPreviewFile] = useState<FileItem | null>(null)

  function handleModeChange(mode: ForwardAnalysisMode) {
    setAnalysisMode(mode)
    // 切换到全量分析时清空追溯表（避免遗留单张追溯表导致后端 422）
    if (mode === 'full') {
      setDeviceIcdTraceFile(null)
      setSystemDeviceTraceFile(null)
    }
  }

  function handleStart() {
    if (!hlrWordFile) {
      alert('请上传 HLR Word 文件')
      return
    }
    if (!publisherFile && !subscriberFile) {
      alert('请至少上传 Publisher Excel 或 Subscriber Excel')
      return
    }
    // 追溯表必须两张齐全：只上传一张禁止提交（后端按是否上传自动判定模式）
    const hasTrace1 = !!deviceIcdTraceFile
    const hasTrace2 = !!systemDeviceTraceFile
    if (hasTrace1 !== hasTrace2) {
      alert('追溯范围分析需同时上传两张追溯表（设备→ICD 与 设备→高层需求）')
      return
    }

    const formData = new FormData()
    if (hlrWordFile.file) formData.append('hlr_word_file', hlrWordFile.file)
    if (publisherFile?.file) formData.append('eoicd_publisher_file', publisherFile.file)
    if (subscriberFile?.file) formData.append('eoicd_subscriber_file', subscriberFile.file)
    if (deviceIcdTraceFile?.file) formData.append('device_icd_trace_file', deviceIcdTraceFile.file)
    if (systemDeviceTraceFile?.file) formData.append('system_device_trace_file', systemDeviceTraceFile.file)
    // 注意：不向接口提交 analysis_mode，后端按追溯表上传情况自动判定

    job.start(
      () => analyzeCompletenessV4(formData),
      (id) => getForwardJobResultV4(id),
    )
  }

  function handleReset() {
    job.reset()
    setHlrWordFile(null)
    setPublisherFile(null)
    setSubscriberFile(null)
    setDeviceIcdTraceFile(null)
    setSystemDeviceTraceFile(null)
    setAnalysisMode('full')
    setSelectedPreviewFile(null)
  }

  return (
    <div className="page">
      <WorkflowSteps pageState={job.pageState} />

      {job.pageState === 'upload' && (
        <>
          <CompletenessFileUpload
            hlrWordFile={hlrWordFile}
            eoicdPublisherFile={publisherFile}
            eoicdSubscriberFile={subscriberFile}
            deviceIcdTraceFile={deviceIcdTraceFile}
            systemDeviceTraceFile={systemDeviceTraceFile}
            analysisMode={analysisMode}
            selectedPreviewFile={selectedPreviewFile}
            onHlrWordChange={(f) => { setHlrWordFile(f); if (f) setSelectedPreviewFile(f) }}
            onEoicdPublisherChange={(f) => { setPublisherFile(f); if (f) setSelectedPreviewFile(f) }}
            onEoicdSubscriberChange={(f) => { setSubscriberFile(f); if (f) setSelectedPreviewFile(f) }}
            onDeviceIcdTraceChange={(f) => { setDeviceIcdTraceFile(f); if (f) setSelectedPreviewFile(f) }}
            onSystemDeviceTraceChange={(f) => { setSystemDeviceTraceFile(f); if (f) setSelectedPreviewFile(f) }}
            onAnalysisModeChange={handleModeChange}
            onPreviewSelect={setSelectedPreviewFile}
          />
          <div className="action-bar">
            <button className="btn btn--secondary btn--large" onClick={handleReset}>
              清空全部
            </button>
            <button className="btn btn--primary btn--large" onClick={handleStart}>
              开始处理
            </button>
          </div>
        </>
      )}

      {job.pageState === 'processing' && (
        <ProcessingView
          progress={job.progress}
          stage={job.stage}
          stageIndex={job.stageIndex}
          stageTotal={job.stageTotal}
          caseIndex={job.caseIndex}
          caseTotal={job.caseTotal}
        />
      )}

      {job.pageState === 'success' && job.resultData && (
        <CompletenessResultView
          data={job.resultData}
          jobId={job.resultData.job_id}
          onNewTask={handleReset}
        />
      )}

      {job.pageState === 'error' && (
        <div className="error-state">
          <div className="error-icon">⚠️</div>
          <h3 className="error-title">处理失败</h3>
          <p className="error-message">请检查文件格式是否正确，或稍后重试</p>
          <button className="btn btn--new" onClick={handleReset}>
            重新尝试
          </button>
        </div>
      )}
    </div>
  )
}
