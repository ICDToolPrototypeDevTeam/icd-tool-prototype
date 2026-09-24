import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CircleStop, TriangleAlert } from 'lucide-react'
import CompletenessFileUpload from '../components/CompletenessFileUpload'
import CompletenessResultView from '../components/CompletenessResultView'
import InterruptedTasks from '../components/InterruptedTasks'
import ProcessingView from '../components/ProcessingView'
import WorkflowSteps from '../components/WorkflowSteps'
import { useAnalysisJob } from '../hooks/useAnalysisJob'
import ErrorDiagnostics, { DiagnosticsCopyButton } from '../components/ErrorDiagnostics'
import type { ErrorDiagnosticsProps } from '../components/ErrorDiagnostics'
import JobLogPanel from '../components/JobLogPanel'
import { useJobLogs } from '../hooks/useJobLogs'
import { useMockMode } from '../hooks/useMockMode'
import { abandonJobV4, analyzeCompletenessV4, getForwardJobResultV4, resumeJobV4 } from '../api'
import type { FileItem, ForwardAnalysisMode, V4ForwardJobResultResponse, V4JobListItem } from '../types'

export default function CompletenessPage() {
  const job = useAnalysisJob<V4ForwardJobResultResponse>()
  const { mockMode } = useMockMode()
  const { lines: logLines, truncated: logTruncated } = useJobLogs(job.jobId)

  const [searchParams, setSearchParams] = useSearchParams()
  const attachRef = useRef(false)
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

  // 从工具入口页带 ?job=<id> 跳转过来时自动挂到该任务上（StrictMode 下 effect 会跑两次，用 ref 防重入）
  useEffect(() => {
    const resumeId = searchParams.get('job')
    if (!resumeId || attachRef.current) return
    attachRef.current = true
    setSearchParams({}, { replace: true })
    job.attach(resumeId, (id) => getForwardJobResultV4(id))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, setSearchParams])

  function handleOpenTask(item: V4JobListItem) {
    job.attach(item.job_id, (id) => getForwardJobResultV4(id))
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
    // 显式传值（而不是「不传即沿用容器配置」）：容器 .env 可能已开 mock，
    // 只有显式 false 才能让顶栏开关双向可用。
    formData.append('use_mock_llm', mockMode ? 'true' : 'false')

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

  /** 已终止的任务：就地续跑（后端按参数快照重启并复位取消标志），再挂回轮询 */
  function handleResume() {
    const id = job.jobId
    if (!id) return
    resumeJobV4(id)
      .then(() => job.attach(id, (jid) => getForwardJobResultV4(jid)))
      .catch((e) => {
        console.error(e)
        alert('继续任务失败，请返回上传页重试')
      })
  }

  /** 已终止的任务：放弃（终态，不删除输入与中间产物），收尾后回到上传页 */
  function handleAbandon() {
    const id = job.jobId
    if (!id) return
    abandonJobV4(id)
      .then(() => handleReset())
      .catch((e) => {
        console.error(e)
        alert('放弃任务失败，请返回上传页操作')
      })
  }

  // 错误视图的公共入参：诊断卡片与「复制诊断信息」按钮共用同一份
  const diagProps: ErrorDiagnosticsProps = {
    error: job.error,
    jobId: job.jobId,
    taskType: 'completeness',
    mockMode: job.mock,
    jobStatus: job.jobStatus,
    lines: logLines,
  }

  return (
    <div className="page">
      <WorkflowSteps pageState={job.pageState} />

      {job.pageState === 'upload' && (
        <>
          <InterruptedTasks taskType="completeness" onOpen={handleOpenTask} />
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
          resumed={job.resumed}
          reuse={job.reuse}
          resumedHint="中断恢复执行 · 已完成的判定结果将直接复用，不重复调用模型"
          onCancel={job.jobId !== null ? job.cancel : undefined}
          cancelRequested={job.cancelRequested}
          onForceCancel={job.jobId !== null ? job.forceCancel : undefined}
          forceCancelAvailable={job.forceCancelAvailable}
        >
          <JobLogPanel lines={logLines} truncated={logTruncated} defaultOpen={false} />
        </ProcessingView>
      )}

      {job.pageState === 'success' && job.resultData && (
        <>
          {job.mock && (
            <div className="mock-banner mock-banner--inline">
              本次任务运行于 MOCK 模式：以下为模拟数据，不可用于验收。
            </div>
          )}
          <CompletenessResultView
            data={job.resultData}
            jobId={job.resultData.job_id}
            onNewTask={handleReset}
          />
        </>
      )}

      {job.pageState === 'error' && (
        <div className="error-state">
          <div className="error-icon">
            {job.jobStatus === 'canceled' ? <CircleStop size={56} /> : <TriangleAlert size={56} />}
          </div>
          <h3 className="error-title">
            {job.jobStatus === 'canceled' ? '任务已终止' : '处理失败'}
          </h3>
          <p className="error-message">{job.errorMessage}</p>
          <ErrorDiagnostics {...diagProps} />
          <JobLogPanel lines={logLines} truncated={logTruncated} defaultOpen />
          <div className="error-state__actions">
            {job.jobStatus === 'canceled' ? (
              <>
                <button className="btn btn--primary" onClick={handleResume}>
                  继续执行
                </button>
                <button className="btn btn--secondary" onClick={handleAbandon}>
                  放弃
                </button>
              </>
            ) : (
              <button className="btn btn--new" onClick={handleReset}>
                重新尝试
              </button>
            )}
            <DiagnosticsCopyButton {...diagProps} />
          </div>
        </div>
      )}
    </div>
  )
}
