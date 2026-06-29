import { useState, useEffect } from 'react'
import { getDownloadUrl, getPreviewHtml } from '../api'
import type { DownloadType } from '../api'
import type { ResultData } from '../types'

interface Props {
  data: ResultData
  onNewTask: () => void
}

const DOWNLOADS: { type: DownloadType; label: string; desc: string; icon: string }[] = [
  { type: 'requirements', label: '最优条目化需求', desc: '综合评分最优的结果', icon: '🏆' },
  { type: 'minimax-requirements', label: 'MiniMax 条目化需求', desc: 'MiniMax 模型生成', icon: '📄' },
  { type: 'deepseek-requirements', label: 'DeepSeek 条目化需求', desc: 'DeepSeek 模型生成', icon: '📄' },
  { type: 'difference-report', label: '差异比对报告', desc: 'EoICD 与软件高层需求差异', icon: '📊' },
]

function PreviewCard({
  title,
  desc,
  icon,
  jobId,
  type,
  isAvailable,
}: {
  title: string
  desc: string
  icon: string
  jobId: string
  type: 'requirements' | 'difference-report'
  isAvailable: boolean
}) {
  const [htmlContent, setHtmlContent] = useState('')

  useEffect(() => {
    if (!isAvailable) return
    getPreviewHtml(jobId, type)
      .then(setHtmlContent)
      .catch(() => setHtmlContent('<p style="color:#6b7280">暂无法加载预览</p>'))
  }, [jobId, type, isAvailable])

  return (
    <div className="card" style={{ height: 480 }}>
      <div className="card__header">
        <div className="card__icon card__icon--blue">{icon}</div>
        <div>
          <div className="card__title">{title}</div>
          <div className="card__subtitle">{desc}</div>
        </div>
      </div>
      <div
        className="card__body"
        style={{ height: 'calc(100% - 81px)', overflow: 'auto' }}
      >
        {!isAvailable ? (
          <div className="preview-content">
            <div className="preview-empty">
              <div className="preview-empty__icon">📄</div>
              <p>暂未生成</p>
            </div>
          </div>
        ) : htmlContent ? (
          <div
            className="preview-content"
            dangerouslySetInnerHTML={{ __html: htmlContent }}
          />
        ) : (
          <div className="preview-content">
            <div className="preview-empty">
              <div className="preview-empty__icon">⏳</div>
              <p>加载中...</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function ResultView({ data, onNewTask }: Props) {
  return (
    <>
      {/* Summary */}
      <div className="result-summary">
        <span className="result-summary__text">需求条目数：</span>
        <span className="result-summary__highlight">{data.summary.requirement_count}</span>
        <span className="result-summary__text">· 差异条目数：</span>
        <span className="result-summary__highlight">{data.summary.difference_count}</span>
      </div>

      {/* Preview cards */}
      <div className="content-grid" style={{ marginTop: 24 }}>
        <PreviewCard
          title="最优条目化需求"
          desc="综合评分最优的 EoICD 条目化需求"
          icon="🏆"
          jobId={data.job_id}
          type="requirements"
          isAvailable={data.outputs.requirements_docx}
        />
        <PreviewCard
          title="差异分析报告"
          desc="EoICD 条目化需求与软件高层需求的差异比对"
          icon="📊"
          jobId={data.job_id}
          type="difference-report"
          isAvailable={data.outputs.difference_report_docx}
        />
      </div>

      {/* Downloads */}
      <div className="downloads-section">
        <div className="downloads-section__title">📥 下载输出文档</div>
        <div className="downloads-grid">
          {DOWNLOADS.map((d) => {
            const isAvailable =
              d.type === 'requirements'
                ? data.outputs.requirements_docx
                : d.type === 'difference-report'
                  ? data.outputs.difference_report_docx
                  : d.type === 'minimax-requirements'
                    ? data.outputs.minimax_docx
                    : data.outputs.deepseek_docx

            return isAvailable ? (
              <a key={d.type} href={getDownloadUrl(data.job_id, d.type)} download className="download-card">
                <span className="download-card__icon">{d.icon}</span>
                <div className="download-card__info">
                  <div className="download-card__name">{d.label}</div>
                  <div className="download-card__hint">{d.desc}</div>
                </div>
              </a>
            ) : (
              <div key={d.type} className="download-card" style={{ opacity: 0.4 }}>
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
          🔄 处理新文件
        </button>
      </div>
    </>
  )
}
