import { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { ApiError, getAuditLog } from '../../api'
import type { AuditLogDetail } from '../../types'
import {
  formatDateTime,
  formatDetailValue,
  formatDuration,
  formatMethod,
} from './format'

interface Props {
  logId: string | null
  onClose: () => void
}

/** 日志详情：右侧抽屉展示完整字段、失败原因与业务明细。 */
export function AuditDetailDrawer({ logId, onClose }: Props) {
  const [detail, setDetail] = useState<AuditLogDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const panelRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!logId) {
      setDetail(null)
      setError('')
      return
    }
    let cancelled = false
    setLoading(true)
    setError('')
    setDetail(null)
    getAuditLog(logId)
      .then((data) => {
        if (!cancelled) setDetail(data)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        if (err instanceof ApiError) {
          setError(err.status === 404 ? '日志不存在或已被清理' : '日志详情加载失败')
        } else {
          setError('日志详情加载失败，请稍后重试')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [logId])

  useEffect(() => {
    if (!logId) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [logId, onClose])

  useEffect(() => {
    if (!logId) return
    panelRef.current?.focus({ preventScroll: true })
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [logId])

  if (!logId) return null

  const failure = detail?.result === 'failure'

  const fields: { label: string; value: string }[] = detail
    ? [
        { label: '时间', value: formatDateTime(detail.ts) },
        { label: '操作人', value: detail.actor_username || '未登录 / 系统' },
        { label: '角色', value: detail.actor_role_label },
        { label: '模块', value: detail.module_label },
        { label: '操作类型', value: detail.action_type_label },
        { label: '动作', value: detail.action_label },
        { label: '对象', value: detail.target_label || detail.target_id || '—' },
        { label: '耗时', value: formatDuration(detail.duration_ms) },
        { label: 'IP 地址', value: detail.ip || '—' },
        { label: '客户端', value: detail.client || '—' },
        {
          label: '请求方式',
          value: detail.request_method ? formatMethod(detail.request_method) : '—',
        },
        { label: '接口地址', value: detail.request_path || '—' },
        { label: '日志类别', value: detail.category_label },
      ]
    : []

  return (
    <div className="audit-drawer" role="dialog" aria-modal="true" aria-label="日志详情">
      <div className="audit-drawer__backdrop" onClick={onClose} />
      <aside
        className={`audit-sheet${failure ? ' is-failure' : ''}`}
        ref={panelRef}
        tabIndex={-1}
      >
        <header className="audit-sheet__top">
          <span className="audit-sheet__kind">日志详情</span>
          <button
            type="button"
            className="audit-sheet__close"
            onClick={onClose}
            aria-label="关闭详情"
          >
            <X size={18} />
          </button>
        </header>

        <div className="audit-sheet__scroll">
          {loading && <div className="audit-empty">正在加载日志详情…</div>}
          {!loading && error && (
            <div className="audit-empty audit-empty--error">{error}</div>
          )}

          {!loading && !error && detail && (
            <>
              <div className="audit-sheet__meta">
                <span className="audit-sheet__time">{formatDateTime(detail.ts)}</span>
                <span className="audit-sheet__id">编号 {detail.id}</span>
              </div>

              <div className="audit-sheet__heading">
                <h2 className="audit-sheet__title">{detail.action_label}</h2>
                <span
                  className={`audit-badge ${
                    failure ? 'audit-badge--failure' : 'audit-badge--success'
                  }`}
                >
                  {detail.result_label}
                </span>
              </div>

              <p className={`audit-sheet__summary${failure ? ' is-failure' : ''}`}>
                {detail.summary}
              </p>

              <section className="audit-sheet__section">
                <h3 className="audit-sheet__section-title">记录信息</h3>
                <dl className="audit-sheet__fields">
                  {fields.map((row) => (
                    <div key={row.label} className="audit-sheet__row">
                      <dt>{row.label}</dt>
                      <dd>{row.value}</dd>
                    </div>
                  ))}
                </dl>
              </section>

              {detail.error && (
                <section className="audit-sheet__section">
                  <h3 className="audit-sheet__section-title">失败原因</h3>
                  <div className="audit-sheet__error">{detail.error}</div>
                </section>
              )}

              <section className="audit-sheet__section">
                <h3 className="audit-sheet__section-title">业务明细</h3>
                {Object.keys(detail.detail || {}).length === 0 ? (
                  <p className="audit-sheet__empty">本次操作没有附加明细字段。</p>
                ) : (
                  <dl className="audit-sheet__fields">
                    {Object.entries(detail.detail).map(([key, value]) => (
                      <div key={key} className="audit-sheet__row">
                        <dt>{key}</dt>
                        <dd>{formatDetailValue(value)}</dd>
                      </div>
                    ))}
                  </dl>
                )}
              </section>

              <div className="audit-sheet__foot">日志编号：{detail.id}</div>
            </>
          )}
        </div>
      </aside>
    </div>
  )
}
