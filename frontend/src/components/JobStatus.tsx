import { useEffect, useState } from 'react'
import { getJobStatus, getJobResult, getDownloadUrl } from '../api'
import type { JobStatusResponse, JobResultResponse } from '../api'

interface Props {
  jobId: string
}

export default function JobStatus({ jobId }: Props) {
  const [status, setStatus] = useState<JobStatusResponse | null>(null)
  const [result, setResult] = useState<JobResultResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const poll = setInterval(async () => {
      try {
        const s = await getJobStatus(jobId)
        setStatus(s)
        if (s.status === 'completed' || s.status === 'failed') {
          clearInterval(poll)
          if (s.status === 'completed') {
            const r = await getJobResult(jobId)
            setResult(r)
          }
        }
      } catch (err) {
        setError(String(err))
        clearInterval(poll)
      }
    }, 2000)
    return () => clearInterval(poll)
  }, [jobId])

  if (error) return <p style={{ color: 'red' }}>错误: {error}</p>
  if (!status) return <p>加载中...</p>

  return (
    <div>
      <h2>任务状态</h2>
      <p>任务ID: {jobId}</p>
      <p>状态: {status.status}</p>
      {status.message && <p>消息: {status.message}</p>}

      {status.status === 'completed' && result && (
        <div>
          <h3>结果摘要</h3>
          <p>需求条目数: {result.summary.requirement_count}</p>
          <p>差异条目数: {result.summary.difference_count}</p>

          <div style={{ marginTop: '1rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <a href={getDownloadUrl(jobId, 'requirements')} download>
              下载 EoICD条目化需求.docx
            </a>
            <a href={getDownloadUrl(jobId, 'difference-report')} download>
              下载 EoICD与软件高层需求差异报告.docx
            </a>
          </div>
        </div>
      )}

      {status.status === 'failed' && (
        <p style={{ color: 'red' }}>任务失败，请检查输入文件或稍后重试。</p>
      )}
    </div>
  )
}