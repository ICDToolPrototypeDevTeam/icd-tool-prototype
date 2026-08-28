import { useState } from 'react'
import CorrectnessFileUpload from '../components/CorrectnessFileUpload'
import CorrectnessResultView from '../components/CorrectnessResultView'
import ProcessingView from '../components/ProcessingView'
import WorkflowSteps from '../components/WorkflowSteps'
import { useAnalysisJob } from '../hooks/useAnalysisJob'
import { analyzeFilesV4, getJobResultV4 } from '../api'
import type { FileItem, V4JobResultResponse } from '../types'

export default function CorrectnessPage() {
  const job = useAnalysisJob<V4JobResultResponse>()

  const [hlrWordFile, setHlrWordFile] = useState<FileItem | null>(null)
  const [publisherFile, setPublisherFile] = useState<FileItem | null>(null)
  const [subscriberFile, setSubscriberFile] = useState<FileItem | null>(null)
  const [traceabilityFiles, setTraceabilityFiles] = useState<FileItem[]>([])
  const [selectedPreviewFile, setSelectedPreviewFile] = useState<FileItem | null>(null)
  // Reverse-pipeline only: AMS / FGMC / HSCU / RPDU controller profile.
  // Forward (Completeness) analysis does not use this selector.
  const [v4ControllerProfile, setV4ControllerProfile] = useState<string>('')

  function handleStart() {
    if (!hlrWordFile) {
      alert('请上传 HLR Word 文件')
      return
    }
    if (!publisherFile && !subscriberFile) {
      alert('请至少上传 Publisher Excel 或 Subscriber Excel')
      return
    }

    const formData = new FormData()
    if (hlrWordFile.file) formData.append('hlr_word_file', hlrWordFile.file)
    if (publisherFile?.file) formData.append('eoicd_publisher_file', publisherFile.file)
    if (subscriberFile?.file) formData.append('eoicd_subscriber_file', subscriberFile.file)
    if (v4ControllerProfile) formData.append('controller_profile', v4ControllerProfile)

    if (traceabilityFiles.length > 0) {
      traceabilityFiles.forEach((f) => {
        if (f.file) formData.append('traceability_files', f.file)
      })
      formData.append('enable_traceability_prefilter', 'true')
    }

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
    setSelectedPreviewFile(null)
  }

  return (
    <div className="page">
      <WorkflowSteps pageState={job.pageState} />

      {job.pageState === 'upload' && (
        <>
          <CorrectnessFileUpload
            hlrWordFile={hlrWordFile}
            eoicdPublisherFile={publisherFile}
            eoicdSubscriberFile={subscriberFile}
            traceabilityFiles={traceabilityFiles}
            selectedPreviewFile={selectedPreviewFile}
            onHlrWordChange={(f) => { setHlrWordFile(f); if (f) setSelectedPreviewFile(f) }}
            onEoicdPublisherChange={(f) => { setPublisherFile(f); if (f) setSelectedPreviewFile(f) }}
            onEoicdSubscriberChange={(f) => { setSubscriberFile(f); if (f) setSelectedPreviewFile(f) }}
            onTraceabilityChange={(files) => {
              setTraceabilityFiles(files)
              if (files.length > 0) setSelectedPreviewFile(files[files.length - 1])
            }}
            onPreviewSelect={setSelectedPreviewFile}
          />
          {/* Controller Profile selector — reverse pipeline only.
              Matches backend ALLOWED_CONTROLLER_PROFILES in
              backend/app/api/v4/coverage.py: ams / fgmc / hscu / rpdu. */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 16, marginBottom: 16 }}>
            <span style={{ fontSize: 14, color: '#555' }}>系统类型：</span>
            <select
              value={v4ControllerProfile}
              onChange={(e) => setV4ControllerProfile(e.target.value)}
              style={{
                padding: '6px 12px',
                borderRadius: 6,
                border: '1px solid #ddd',
                fontSize: 14,
                minWidth: 160,
              }}
            >
              <option value="">自动识别</option>
              <option value="ams">环控系统 (AMS)</option>
              <option value="fgmc">燃油系统 (FGMC)</option>
              <option value="hscu">液压系统 (HSCU)</option>
              <option value="rpdu">远程功率分配单元 (RPDU)</option>
            </select>
            <span style={{ fontSize: 12, color: '#888' }}>（默认自动识别；仅影响反向管线）</span>
          </div>
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
        <CorrectnessResultView
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
