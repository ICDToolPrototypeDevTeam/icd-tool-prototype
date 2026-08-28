import { useState, useEffect } from 'react'
import { CircleCheck, CircleX, CircleHelp, CircleDashed, CircleAlert, Table2, type LucideIcon } from 'lucide-react'
import { getForwardDownloadUrlV4, getForwardPreviewHtmlV4 } from '../api'
import type { V4ForwardJobResultResponse, V4ForwardDownloadKind } from '../types'

interface Props {
  data: V4ForwardJobResultResponse
  jobId: string
  onNewTask: () => void
}

const DOWNLOADS: { kind: V4ForwardDownloadKind; label: string; desc: string; icon: string }[] = [
  { kind: 'forward-xlsx', label: '正向完整性分析明细 (XLSX)', desc: '覆盖结果明细', icon: '📊' },
  { kind: 'forward-docx', label: '正向完整性分析报告 (DOCX)', desc: '完整性分析报告', icon: '📋' },
]

const PREVIEWS: { kind: V4ForwardDownloadKind; title: string; desc: string; icon: string }[] = [
  { kind: 'forward-xlsx', title: '正向完整性分析明细', desc: '覆盖结果 Excel 明细', icon: '📊' },
  { kind: 'forward-docx', title: '正向完整性分析报告', desc: '完整性分析报告', icon: '📋' },
]

function outputAvailable(data: V4ForwardJobResultResponse, kind: V4ForwardDownloadKind): boolean {
  switch (kind) {
    case 'forward-xlsx': return data.outputs.forward_xlsx
    case 'forward-docx': return data.outputs.forward_docx
    default: return false
  }
}

function PreviewCard({
  title,
  desc,
  icon,
  jobId,
  kind,
  isAvailable,
}: {
  title: string; desc: string; icon: string; jobId: string; kind: V4ForwardDownloadKind; isAvailable: boolean
}) {
  const [htmlContent, setHtmlContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!isAvailable) return
    setLoading(true)
    getForwardPreviewHtmlV4(jobId, kind)
      .then((html) => { setHtmlContent(html); setLoading(false) })
      .catch(() => { setError('暂无法加载预览'); setLoading(false) })
  }, [jobId, kind, isAvailable])

  return (
    <div className="card" style={{ height: 480 }}>
      <div className="card__header">
        <div className="card__icon card__icon--blue">{icon}</div>
        <div>
          <div className="card__title">{title}</div>
          <div className="card__subtitle">{desc}</div>
        </div>
      </div>
      <div className="card__body" style={{ height: 'calc(100% - 81px)', overflow: 'auto' }}>
        {!isAvailable ? (
          <div className="preview-content">
            <div className="preview-empty">
              <div className="preview-empty__icon">📄</div>
              <p>暂未生成</p>
            </div>
          </div>
        ) : loading ? (
          <div className="preview-content">
            <div className="preview-empty">
              <div className="preview-empty__icon">⏳</div>
              <p>加载中...</p>
            </div>
          </div>
        ) : error ? (
          <div className="preview-content">
            <div className="preview-empty">
              <div className="preview-empty__icon">⚠️</div>
              <p>{error}</p>
            </div>
          </div>
        ) : kind === 'forward-xlsx' ? (
          <div
            className="preview-content"
            style={{ maxHeight: 320, overflow: 'auto' }}
            dangerouslySetInnerHTML={{ __html: htmlContent }}
          />
        ) : (
          <div
            className="preview-content"
            dangerouslySetInnerHTML={{ __html: htmlContent }}
          />
        )}
      </div>
    </div>
  )
}

export default function CompletenessResultView({ data, jobId, onNewTask }: Props) {
  const s = data.summary
  const covered = s.covered_direct + s.covered_aggregate
  const pending = s.possible + s.parent_referenced
  const modeLabel = s.analysis_mode === 'trace' ? '追溯范围分析' : '全量分析'

  const statCards: { label: string; value: string; Icon: LucideIcon; color: string }[] = [
    { label: '业务对象', value: String(s.total_blocks), Icon: Table2, color: '#1565c0' },
    { label: '已覆盖', value: String(covered), Icon: CircleCheck, color: '#2e7d32' },
    { label: '待确认', value: String(pending), Icon: CircleHelp, color: '#e65100' },
    { label: '未覆盖', value: String(s.uncovered), Icon: CircleX, color: '#c62828' },
    { label: '输入异常', value: String(s.input_error), Icon: CircleAlert, color: '#6a1b9a' },
    { label: '不支持', value: String(s.unsupported), Icon: CircleDashed, color: '#9e9e9e' },
  ]

  return (
    <>
      {/* Mode + AI review stats */}
      <div className="result-summary">
        <span className="result-summary__text">
          分析模式：<span className="result-summary__highlight">{modeLabel}</span>
          <span style={{ margin: '0 12px' }}>·</span>
          AI 复核对象：<span className="result-summary__highlight">{s.ai_reviewed.toLocaleString()}</span>
        </span>
      </div>

      {/* Stat cards row */}
      <div className="stat-cards">
        {statCards.map((sc) => (
          <div key={sc.label} className="stat-card">
            <sc.Icon size={22} className="stat-card__icon" style={{ color: sc.color }} />
            <div className="stat-card__value" style={{ color: sc.color }}>{sc.value}</div>
            <div className="stat-card__label">{sc.label}</div>
          </div>
        ))}
      </div>

      {/* Preview cards grid */}
      <div className="content-grid" style={{ marginTop: 24 }}>
        {PREVIEWS.map((p) => (
          <PreviewCard
            key={p.kind}
            title={p.title}
            desc={p.desc}
            icon={p.icon}
            jobId={jobId}
            kind={p.kind}
            isAvailable={outputAvailable(data, p.kind)}
          />
        ))}
      </div>

      {/* Downloads */}
      <div className="downloads-section">
        <div className="downloads-section__title">下载输出文档</div>
        <div className="downloads-grid">
          {DOWNLOADS.map((d) => {
            const available = outputAvailable(data, d.kind)
            return available ? (
              <a key={d.kind} href={getForwardDownloadUrlV4(jobId, d.kind)} download className="download-card">
                <span className="download-card__icon">{d.icon}</span>
                <div className="download-card__info">
                  <div className="download-card__name">{d.label}</div>
                  <div className="download-card__hint">{d.desc}</div>
                </div>
              </a>
            ) : (
              <div key={d.kind} className="download-card" style={{ opacity: 0.4 }}>
                <span className="download-card__icon">{d.icon}</span>
                <div className="download-card__info">
                  <div className="download-card__name">{d.label}</div>
                  <div className="download-card__hint">暂未生成</div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      <div className="action-bar">
        <button className="btn btn--new" onClick={onNewTask}>
          处理新文件
        </button>
      </div>
    </>
  )
}
