import { useEffect, useState } from 'react'
import {
  ClipboardList,
  FileSpreadsheet,
  FileText,
  RefreshCw,
  Trash2,
  type LucideIcon,
} from 'lucide-react'
import {
  ApiError,
  deleteHistoryV4,
  getDownloadUrlV4,
  getForwardDownloadUrlV4,
  listHistoryV4,
} from '../api'
import type { V4HistoryItem, V4HistoryOutputs } from '../types'

const TASK_LABEL: Record<string, string> = {
  correctness: '正确性分析',
  completeness: '完整性分析',
}

const STATUS_LABEL: Record<string, string> = {
  pending: '等待开始',
  running: '正在分析',
  completed: '已完成',
  failed: '失败',
  interrupted: '已中断',
  abandoned: '已放弃',
  canceled: '已终止',
  unknown: '记录缺失',
}

// 还在跑的任务不能删：目录仍会被写，后端也会 409。这里同步禁掉勾选框。
const UNDELETABLE = new Set(['pending', 'running'])

interface DownloadSpec {
  key: keyof V4HistoryOutputs
  label: string
  Icon: LucideIcon
  url: (jobId: string) => string
}

const REVERSE_DOWNLOADS: DownloadSpec[] = [
  {
    key: 'eoicd_xlsx',
    label: 'EoICD 条目化清单',
    Icon: FileSpreadsheet,
    url: (id) => getDownloadUrlV4(id, 'eoicd-xlsx'),
  },
  {
    key: 'consistency_deepseek_docx',
    label: '差异报告 (DeepSeek)',
    Icon: FileText,
    url: (id) => getDownloadUrlV4(id, 'consistency/deepseek'),
  },
  {
    key: 'consistency_minimax_docx',
    label: '差异报告 (MiniMax)',
    Icon: FileText,
    url: (id) => getDownloadUrlV4(id, 'consistency/minimax'),
  },
  {
    key: 'consistency_qwen_docx',
    label: '差异报告 (Qwen)',
    Icon: FileText,
    url: (id) => getDownloadUrlV4(id, 'consistency/qwen'),
  },
  {
    key: 'consensus_docx',
    label: '多模型差异报告',
    Icon: ClipboardList,
    url: (id) => getDownloadUrlV4(id, 'consensus-docx'),
  },
]

const FORWARD_DOWNLOADS: DownloadSpec[] = [
  {
    key: 'forward_xlsx',
    label: '正向完整性明细',
    Icon: FileSpreadsheet,
    url: (id) => getForwardDownloadUrlV4(id, 'forward-xlsx'),
  },
  {
    key: 'forward_docx',
    label: '正向完整性报告',
    Icon: ClipboardList,
    url: (id) => getForwardDownloadUrlV4(id, 'forward-docx'),
  },
]

/** 任务类型未知（上传失败留下的残留目录）时两类都列，实际只渲染存在的产物。 */
function downloadsFor(taskType: string): DownloadSpec[] {
  if (taskType === 'completeness') return FORWARD_DOWNLOADS
  if (taskType === 'correctness') return REVERSE_DOWNLOADS
  return [...REVERSE_DOWNLOADS, ...FORWARD_DOWNLOADS]
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { hour12: false })
}

function formatSize(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${unit === 0 || value >= 10 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`
}

export default function HistoryList() {
  const [items, setItems] = useState<V4HistoryItem[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirming, setConfirming] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  function load() {
    setLoading(true)
    listHistoryV4()
      .then((all) => {
        setItems(all)
        setError('')
      })
      .catch(() => setError('历史结果加载失败，请重试'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const deletable = items.filter((i) => !UNDELETABLE.has(i.status))
  const selectedItems = items.filter((i) => selected.has(i.job_id))
  const selectedBytes = selectedItems.reduce((sum, i) => sum + i.size_bytes, 0)
  const totalBytes = items.reduce((sum, i) => sum + i.size_bytes, 0)
  const allSelected = deletable.length > 0 && deletable.every((i) => selected.has(i.job_id))

  function toggle(jobId: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(jobId)) next.delete(jobId)
      else next.add(jobId)
      return next
    })
    setNotice('')
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(deletable.map((i) => i.job_id)))
    setNotice('')
  }

  async function handleDelete() {
    const ids = [...selected]
    if (ids.length === 0) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const res = await deleteHistoryV4(ids)
      const failed = res.failed.length
        ? `；${res.failed.length} 个删除失败：${res.failed.map((f) => f.error).join('、')}`
        : ''
      setNotice(`已删除 ${res.deleted.length} 项，释放 ${formatSize(res.total_freed_bytes)}${failed}`)
      setSelected(new Set())
      load()
    } catch (e) {
      // 409 = 批次里有任务正在跑（后端整批不删）：如实说明，别让用户以为已经删了
      setError(
        e instanceof ApiError && e.status === 409
          ? '选中的任务里有正在分析的任务，本次未删除任何内容'
          : '删除失败，请重试',
      )
    } finally {
      setConfirming(false)
      setBusy(false)
    }
  }

  return (
    <div className="history">
      <div className="history__header">
        <span className="history__title">历史结果（{items.length}）</span>
        <span className="history__hint">
          结果保存在服务器上，共占用 {formatSize(totalBytes)}；可直接下载，也可删除以释放磁盘
        </span>
        <button className="btn btn--ghost" onClick={load} disabled={loading || busy}>
          <RefreshCw size={14} /> 刷新
        </button>
      </div>

      {items.length > 0 && (
        <div className="history__toolbar">
          <label className="history__check-all">
            <input
              type="checkbox"
              checked={allSelected}
              disabled={deletable.length === 0 || busy}
              onChange={toggleAll}
            />
            全选可删除项（{deletable.length}）
          </label>
          <button
            className="btn btn--danger"
            disabled={selected.size === 0 || busy}
            onClick={() => setConfirming(true)}
          >
            <Trash2 size={14} /> 删除选中（{selected.size}）
          </button>
        </div>
      )}

      {/* 二次确认：删除是整目录硬删除，必须把后果写清楚再让用户点第二下 */}
      {confirming && (
        <div className="history__confirm">
          <div className="history__confirm-text">
            将永久删除 <strong>{selected.size}</strong> 个历史结果（约 {formatSize(selectedBytes)}），
            包含这些任务<strong>上传的输入文件</strong>与全部中间产物，<strong>不可恢复</strong>。
          </div>
          <div className="history__confirm-actions">
            <button className="btn btn--secondary" disabled={busy} onClick={() => setConfirming(false)}>
              取消
            </button>
            <button className="btn btn--danger" disabled={busy} onClick={() => void handleDelete()}>
              {busy ? '删除中…' : '确认删除'}
            </button>
          </div>
        </div>
      )}

      {error && <div className="history__error">{error}</div>}
      {notice && <div className="history__notice">{notice}</div>}

      {!loading && items.length === 0 && !error && (
        <div className="history__placeholder">服务器上还没有历史结果</div>
      )}

      <div className="history__list">
        {items.map((item) => {
          const locked = UNDELETABLE.has(item.status)
          const available = downloadsFor(item.task_type).filter((s) => item.outputs[s.key])
          return (
            <div key={item.job_id} className="history__item">
              <label className="history__pick" title={locked ? '任务正在分析，不能删除' : '选中以删除'}>
                <input
                  type="checkbox"
                  checked={selected.has(item.job_id)}
                  disabled={locked || busy}
                  onChange={() => toggle(item.job_id)}
                />
              </label>

              <div className="history__info">
                <div className="history__files">
                  <span className="history__tag">{TASK_LABEL[item.task_type] || '未知类型'}</span>
                  <span className={`history__status history__status--${item.status}`}>
                    {STATUS_LABEL[item.status] || item.status}
                  </span>
                  {item.mock && <span className="history__tag history__tag--mock">MOCK</span>}
                  {item.input_files.length > 0 ? item.input_files.join('、') : item.job_id}
                </div>
                <div className="history__meta">
                  {formatTime(item.finished_at || item.created_at)} · {formatSize(item.size_bytes)}
                </div>

                {available.length > 0 ? (
                  <div className="history__downloads">
                    {available.map((spec) => (
                      <a key={spec.key} className="history__download" href={spec.url(item.job_id)} download>
                        <spec.Icon size={14} />
                        {spec.label}
                      </a>
                    ))}
                  </div>
                ) : (
                  <div className="history__missing">无可下载产物（任务未跑完或生成失败）</div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
