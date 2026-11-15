import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CircleStop, TriangleAlert } from 'lucide-react'
import CorrectnessFileUpload from '../components/CorrectnessFileUpload'
import CorrectnessResultView from '../components/CorrectnessResultView'
import HlrSchemaEditor from '../components/HlrSchemaEditor'
import InterruptedTasks from '../components/InterruptedTasks'
import ProcessingView from '../components/ProcessingView'
import WorkflowSteps from '../components/WorkflowSteps'
import { useAnalysisJob } from '../hooks/useAnalysisJob'
import ErrorDiagnostics, { DiagnosticsCopyButton } from '../components/ErrorDiagnostics'
import type { ErrorDiagnosticsProps } from '../components/ErrorDiagnostics'
import JobLogPanel from '../components/JobLogPanel'
import { useJobLogs } from '../hooks/useJobLogs'
import { useMockMode } from '../hooks/useMockMode'
import { abandonJobV4, analyzeFilesV4, getJobResultV4, resumeJobV4 } from '../api'
import type {
  FileItem,
  HlrSchemaPayload,
  V4JobListItem,
  V4JobResultResponse,
} from '../types'

export default function CorrectnessPage() {
  const job = useAnalysisJob<V4JobResultResponse>()
  const { mockMode } = useMockMode()
  const { lines: logLines, truncated: logTruncated } = useJobLogs(job.jobId)

  const [searchParams, setSearchParams] = useSearchParams()
  const attachRef = useRef(false)
  const [hlrWordFile, setHlrWordFile] = useState<FileItem | null>(null)
  const [publisherFile, setPublisherFile] = useState<FileItem | null>(null)
  const [subscriberFile, setSubscriberFile] = useState<FileItem | null>(null)
  const [traceabilityFiles, setTraceabilityFiles] = useState<FileItem[]>([])
  // 任务附件：只保存、不参与分析（仅管理员可在历史结果页下载）
  const [attachmentFiles, setAttachmentFiles] = useState<FileItem[]>([])
  const [selectedPreviewFile, setSelectedPreviewFile] = useState<FileItem | null>(null)
  // Reverse-pipeline only: AMS / FGMC / HSCU / RPDU / generic controller profile.
  // Forward (Completeness) analysis does not use this selector.
  const [v4ControllerProfile, setV4ControllerProfile] = useState<string>('')
  // 自定义样式是否处于激活状态：与 hlrSchemaPayload 分开，避免「配置不完整
  // （payload 为 null）」被误判为「未使用自定义样式」。
  const [hlrCustomActive, setHlrCustomActive] = useState(false)
  // 自定义 HLR 表格样式引用（完整配置 / template_id / schema_id），随请求提交。
  const [hlrSchemaPayload, setHlrSchemaPayload] = useState<HlrSchemaPayload | null>(null)
  // 「清空全部」时递增：即使当前没有 HLR 文件（hlrFile 保持 null、effect 不会重跑），
  // 也能强制重挂载样式编辑器，让组件内的选择/字段/试解析结果一并清空。
  const [hlrEditorResetKey, setHlrEditorResetKey] = useState(0)

  // 从工具入口页带 ?job=<id> 跳转过来时自动挂到该任务上（StrictMode 下 effect 会跑两次，用 ref 防重入）
  useEffect(() => {
    const resumeId = searchParams.get('job')
    if (!resumeId || attachRef.current) return
    attachRef.current = true
    setSearchParams({}, { replace: true })
    job.attach(resumeId, (id) => getJobResultV4(id))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, setSearchParams])

  function handleOpenTask(item: V4JobListItem) {
    job.attach(item.job_id, (id) => getJobResultV4(id))
  }

  function handleStart() {
    if (!hlrWordFile) {
      alert('请上传 SWHLR Word 文件')
      return
    }
    if (!publisherFile && !subscriberFile) {
      alert('请至少上传 Publisher Excel 或 Subscriber Excel')
      return
    }
    if (hlrCustomActive && !v4ControllerProfile) {
      alert('使用自定义样式时请选择系统类型')
      return
    }
    if (v4ControllerProfile === 'generic' && !hlrSchemaPayload) {
      alert('其他系统需要先完成表格样式配置')
      return
    }

    const formData = new FormData()
    if (hlrWordFile.file) formData.append('hlr_word_file', hlrWordFile.file)
    if (publisherFile?.file) formData.append('eoicd_publisher_file', publisherFile.file)
    if (subscriberFile?.file) formData.append('eoicd_subscriber_file', subscriberFile.file)
    if (v4ControllerProfile) formData.append('controller_profile', v4ControllerProfile)
    if (hlrSchemaPayload) formData.append('hlr_schema', JSON.stringify(hlrSchemaPayload))
    // 显式传值（而不是「不传即沿用容器配置」）：容器 .env 可能已开 mock，
    // 只有显式 false 才能让顶栏开关双向可用。
    formData.append('use_mock_llm', mockMode ? 'true' : 'false')

    if (traceabilityFiles.length > 0) {
      traceabilityFiles.forEach((f) => {
        if (f.file) formData.append('traceability_files', f.file)
      })
      formData.append('enable_traceability_prefilter', 'true')
    }

    // 附件只随任务保存，后端不会把它们交给管线。
    attachmentFiles.forEach((f) => {
      if (f.file) formData.append('attachment_files', f.file)
    })

    job.start(
      () => analyzeFilesV4(formData),
      (id) => getJobResultV4(id),
    )
  }

  function handleReset() {
    job.reset()
    setHlrWordFile(null)
    setPublisherFile(null)
    setSubscriberFile(null)
    setTraceabilityFiles([])
    setAttachmentFiles([])
    setSelectedPreviewFile(null)
    setHlrSchemaPayload(null)
    setHlrCustomActive(false)
    setHlrEditorResetKey((k) => k + 1)
  }

  /** 已终止的任务：就地续跑（后端按参数快照重启并复位取消标志），再挂回轮询 */
  function handleResume() {
    const id = job.jobId
    if (!id) return
    resumeJobV4(id)
      .then(() => job.attach(id, (jid) => getJobResultV4(jid)))
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
    taskType: 'correctness',
    mockMode: job.mock,
    jobStatus: job.jobStatus,
    lines: logLines,
  }

  return (
    <div className="page">
      <WorkflowSteps pageState={job.pageState} />

      {job.pageState === 'upload' && (
        <>
          <InterruptedTasks taskType="correctness" onOpen={handleOpenTask} />
          <CorrectnessFileUpload
            hlrWordFile={hlrWordFile}
            eoicdPublisherFile={publisherFile}
            eoicdSubscriberFile={subscriberFile}
            traceabilityFiles={traceabilityFiles}
            attachmentFiles={attachmentFiles}
            selectedPreviewFile={selectedPreviewFile}
            onHlrWordChange={(f) => { setHlrWordFile(f); if (f) setSelectedPreviewFile(f) }}
            onEoicdPublisherChange={(f) => { setPublisherFile(f); if (f) setSelectedPreviewFile(f) }}
            onEoicdSubscriberChange={(f) => { setSubscriberFile(f); if (f) setSelectedPreviewFile(f) }}
            onTraceabilityChange={(files) => {
              setTraceabilityFiles(files)
              if (files.length > 0) setSelectedPreviewFile(files[files.length - 1])
            }}
            onAttachmentChange={setAttachmentFiles}
            onPreviewSelect={setSelectedPreviewFile}
          />
          {/* 解析规则：系统类型与表格样式合并在同一张卡片内选择。
              controller_profile 取值与 backend/api/v4/coverage.py 的
              ALLOWED_CONTROLLER_PROFILES 一致：ams / fgmc / hscu / rpdu / generic。 */}
          <HlrSchemaEditor
            key={hlrEditorResetKey}
            hlrFile={hlrWordFile?.file ?? null}
            controllerProfile={v4ControllerProfile}
            onControllerProfileChange={setV4ControllerProfile}
            onChange={setHlrSchemaPayload}
            onCustomActiveChange={setHlrCustomActive}
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
          tokenUsage={job.tokenUsage}
          mock={job.mock}
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
          <CorrectnessResultView
            data={job.resultData}
            jobId={job.resultData.job_id}
            onNewTask={handleReset}
            mock={job.mock}
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
