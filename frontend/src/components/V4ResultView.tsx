import { useState, useEffect } from 'react'
import { CircleCheck, CircleX, CircleHelp, CircleDashed, Table2, FileText } from 'lucide-react'
import { getDownloadUrlV4, getPreviewHtmlV4 } from '../api'
import type { V4JobResultResponse, V4DownloadKind } from '../types'

interface Props {
  data: V4JobResultResponse
  jobId: string
  onNewTask: () => void
}

const DOWNLOADS: { kind: V4DownloadKind; label: string; desc: string; icon: string }[] = [
  { kind: 'eoicd-xlsx', label: 'EoICD 条目化清单 (XLSX)', desc: '条目化需求清单', icon: '📊' },
  { kind: 'consistency/deepseek', label: '一致性报告 (DeepSeek)', desc: 'DeepSeek 模型分析', icon: '📄' },
  { kind: 'consistency/minimax', label: '一致性报告 (MiniMax)', desc: 'MiniMax 模型分析', icon: '📄' },
  { kind: 'consistency/qwen', label: '一致性报告 (Qwen)', desc: 'Qwen 模型分析', icon: '📄' },
  { kind: 'consensus-docx', label: '多模型共识报告', desc: '三模型共识分析', icon: '📋' },
]

const PREVIEWS: { kind: V4DownloadKind; title: string; desc: string; icon: string }[] = [
  { kind: 'eoicd-xlsx', title: 'EoICD 条目化清单', desc: '条目化需求 Excel 清单', icon: '📊' },
  { kind: 'consensus-docx', title: '多模型共识报告', desc: '三模型共识分析报告', icon: '📋' },
]

function outputAvailable(data: V4JobResultResponse, kind: V4DownloadKind): boolean {
  switch (kind) {
    case 'eoicd-xlsx': return data.outputs.eoicd_xlsx
    case 'consistency/deepseek': return data.outputs.consistency_deepseek_docx
    case 'consistency/minimax': return data.outputs.consistency_minimax_docx
    case 'consistency/qwen': return data.outputs.consistency_qwen_docx
    case 'consensus-docx': return data.outputs.consensus_docx
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
  title: string; desc: string; icon: string; jobId: string; kind: V4DownloadKind; isAvailable: boolean
}) {
  const [htmlContent, setHtmlContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!isAvailable) return
    setLoading(true)
    getPreviewHtmlV4(jobId, kind)
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
        ) : kind === 'eoicd-xlsx' ? (
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

const STATUS_META: Record<string, { label: string; color: string; Icon: React.ComponentType<{ size?: number }> }> = {
  covered: { label: '已覆盖', color: '#2e7d32', Icon: CircleCheck },
  inconsistent: { label: '不一致', color: '#c62828', Icon: CircleX },
  needs_review: { label: '待确认', color: '#e65100', Icon: CircleHelp },
}

function StarBar({ dist }: { dist: Record<string, number> }) {
  const total = Object.values(dist).reduce((a, b) => a + b, 0) || 1
  return (
    <div className="star-bar">
      {[3, 2, 1].map((star) => {
        const count = dist[String(star)] || 0
        const pct = Math.round((count / total) * 100)
        return (
          <div key={star} className="star-bar__item">
            <span className="star-bar__label">{'★'.repeat(star)}{'☆'.repeat(3 - star)}</span>
            <div className="star-bar__track">
              <div className="star-bar__fill" style={{ width: `${pct}%` }} />
            </div>
            <span className="star-bar__count">{count}</span>
          </div>
        )
      })}
    </div>
  )
}

export default function V4ResultView({ data, jobId, onNewTask }: Props) {
  const s = data.summary
  const statusDist = s.status_distribution || {}
  const starDist = s.star_distribution || {}

  const statCards = [
    { label: 'EoICD 条目', value: s.eoicd_count.toLocaleString(), Icon: Table2, color: '#1565c0' },
    { label: 'HLR 需求', value: String(s.hlr_count), Icon: FileText, color: '#1565c0' },
    ...Object.entries(STATUS_META).map(([key, meta]) => ({
      label: meta.label,
      value: String(statusDist[key] || 0),
      Icon: meta.Icon,
      color: meta.color,
    })),
    { label: '无匹配', value: String(s.unmatched_count), Icon: CircleDashed, color: '#6a1b9a' },
  ]

  return (
    <>
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

      {/* Star distribution + rating */}
      <div className="star-section">
        <div className="star-section__header">
          <span>共识质量</span>
          <span className="star-section__rating">
            平均星级 <strong>{s.average_star_rating?.toFixed(1)}</strong>
          </span>
        </div>
        <StarBar dist={starDist} />
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
              <a key={d.kind} href={getDownloadUrlV4(jobId, d.kind)} download className="download-card">
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
