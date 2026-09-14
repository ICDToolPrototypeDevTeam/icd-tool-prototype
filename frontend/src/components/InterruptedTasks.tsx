import { useEffect, useState } from 'react'
import { abandonJobV4, listJobsV4, resumeJobV4 } from '../api'
import type { V4JobListItem, V4TaskType } from '../types'

interface Props {
  /** 不传 = 显示全部类型（工具入口页） */
  taskType?: V4TaskType
  /** 点「继续 / 查看进度」：调用方负责挂上轮询或跳转到对应页面 */
  onOpen: (job: V4JobListItem) => void
}

// 只展示「还没跑完」的任务：运行中（重开后重新连上）与被中断（可选择继续/放弃）
const VISIBLE_STATUSES = new Set(['pending', 'running', 'interrupted'])

const TASK_LABEL: Record<V4TaskType, string> = {
  correctness: '正确性分析',
  completeness: '完整性分析',
}

const STAGE_LABEL: Record<string, string> = {
  pending: '等待开始',
  running: '正在分析',
  interrupted: '已中断',
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { hour12: false })
}

export default function InterruptedTasks({ taskType, onOpen }: Props) {
  const [jobs, setJobs] = useState<V4JobListItem[]>([])
  const [busyId, setBusyId] = useState('')
  const [error, setError] = useState('')

  function load() {
    // 列表不可用时静默失败：不能影响正常上传流程
    listJobsV4({ taskType })
      .then((all) => setJobs(all.filter((j) => VISIBLE_STATUSES.has(j.status))))
      .catch(() => setJobs([]))
  }

  useEffect(load, [taskType])

  async function handleContinue(item: V4JobListItem) {
    // 被中断的任务需要先请后端按参数快照重启，再挂上轮询
    if (item.status === 'interrupted') {
      setBusyId(item.job_id)
      setError('')
      try {
        await resumeJobV4(item.job_id)
      } catch {
        setError('继续任务失败，请重试')
        setBusyId('')
        load()
        return
      }
      setBusyId('')
    }
    onOpen(item)
  }

  async function handleAbandon(id: string) {
    setBusyId(id)
    setError('')
    try {
      await abandonJobV4(id)
      load()
    } catch {
      setError('放弃任务失败，请重试')
    } finally {
      setBusyId('')
    }
  }

  if (jobs.length === 0) return null

  return (
    <div className="interrupted">
      <div className="interrupted__header">
        <span className="interrupted__title">未完成的任务（{jobs.length}）</span>
        <span className="interrupted__hint">
          「继续」将按原参数重新执行分析，已上传的文件无需再次提供
        </span>
      </div>

      <div className="interrupted__list">
        {jobs.map((j) => (
          <div key={j.job_id} className="interrupted__item">
            <div className="interrupted__info">
              <div className="interrupted__files">
                {!taskType && (
                  <span className="interrupted__tag">{TASK_LABEL[j.task_type]}</span>
                )}
                <span className={`interrupted__status interrupted__status--${j.status}`}>
                  {STAGE_LABEL[j.status] || j.status}
                </span>
                {j.input_files.length > 0 ? j.input_files.join('、') : j.job_id}
              </div>
              <div className="interrupted__meta">
                {j.message || '—'} · 最后更新：{formatTime(j.updated_at)}
              </div>
            </div>

            <div className="interrupted__actions">
              <button
                className="btn btn--primary"
                disabled={busyId === j.job_id}
                onClick={() => void handleContinue(j)}
              >
                {j.status === 'interrupted' ? '继续' : '查看进度'}
              </button>
              {j.status === 'interrupted' && (
                <button
                  className="btn btn--secondary"
                  disabled={busyId === j.job_id}
                  onClick={() => void handleAbandon(j.job_id)}
                >
                  放弃
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {error && <div className="interrupted__error">{error}</div>}
    </div>
  )
}
