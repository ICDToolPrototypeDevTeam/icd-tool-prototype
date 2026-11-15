import { Fragment, useCallback, useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { ApiError, getAuditLogs } from '../../api'
import type { AuditLogItem, AuditLogListResponse, AuditMeta } from '../../types'
import {
  EMPTY_FILTERS,
  resolveTimeRange,
  type AuditFilterValues,
} from './AuditFilters'
import { AuditFilters } from './AuditFilters'
import { auditSummary, formatDayKey, formatDayLabel, formatDuration, formatTimeOnly } from './format'

const PAGE_SIZE = 20

export interface AuditLogSeed {
  result?: string
  preset?: string
  token: number
}

interface Props {
  category: 'login' | 'operation'
  meta: AuditMeta | null
  /** 顶栏「刷新」按钮递增；变化时重新查询当前页 */
  reloadToken: number
  /** 概览下钻带入的初始筛选条件 */
  seed?: AuditLogSeed | null
  onSelect: (logId: string) => void
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return '没有审计日志查看权限'
    try {
      const body = JSON.parse(error.message) as { detail?: unknown }
      if (typeof body.detail === 'string') return body.detail
    } catch {
      // 非 JSON 错误体：按原文展示
    }
  }
  return '日志加载失败，请稍后重试'
}

function initialFilters(seed?: AuditLogSeed | null): AuditFilterValues {
  return {
    ...EMPTY_FILTERS,
    preset: seed?.preset || EMPTY_FILTERS.preset,
    result: seed?.result || '',
  }
}

/** 登录 / 操作日志：查询栏、按天分隔的明细表、服务端分页与详情入口。 */
export function AuditLogExplorer({
  category,
  meta,
  reloadToken,
  seed,
  onSelect,
}: Props) {
  const [draft, setDraft] = useState<AuditFilterValues>(() => initialFilters(seed))
  const [applied, setApplied] = useState<AuditFilterValues>(() => initialFilters(seed))
  const [page, setPage] = useState(1)
  const [data, setData] = useState<AuditLogListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    const range = resolveTimeRange(applied)
    try {
      const result = await getAuditLogs({
        category,
        start: range.start,
        end: range.end,
        result: applied.result || undefined,
        role: applied.role || undefined,
        module: category === 'operation' ? applied.module || undefined : undefined,
        action: category === 'operation' ? applied.action || undefined : undefined,
        actor: applied.actor.trim() || undefined,
        ip: applied.ip.trim() || undefined,
        keyword: applied.keyword.trim() || undefined,
        page,
        page_size: PAGE_SIZE,
      })
      setData(result)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setLoading(false)
    }
  }, [applied, category, page, reloadToken])

  useEffect(() => {
    void load()
  }, [load])

  const handleChange = (patch: Partial<AuditFilterValues>) => {
    setDraft((prev) => ({ ...prev, ...patch }))
  }

  const handleApply = () => {
    setPage(1)
    setApplied({ ...draft })
    // 查询条件未变化时 effect 不会重跑，手动刷新一次
    if (page === 1 && JSON.stringify(draft) === JSON.stringify(applied)) {
      void load()
    }
  }

  const handleReset = () => {
    setDraft({ ...EMPTY_FILTERS })
    setApplied({ ...EMPTY_FILTERS })
    setPage(1)
  }

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const items: AuditLogItem[] = data?.items || []
  const isLogin = category === 'login'
  const columnCount = isLogin ? 7 : 8

  const groups: { key: string; label: string; items: AuditLogItem[] }[] = []
  for (const item of items) {
    const key = formatDayKey(item.ts)
    const last = groups[groups.length - 1]
    if (last && last.key === key) {
      last.items.push(item)
    } else {
      groups.push({ key, label: formatDayLabel(item.ts), items: [item] })
    }
  }

  return (
    <section className="audit-logs" aria-busy={loading}>
      <AuditFilters
        values={draft}
        meta={meta}
        category={category}
        loading={loading}
        onChange={handleChange}
        onApply={handleApply}
        onReset={handleReset}
      />

      <section className="audit-panel">
        <header className="audit-panel__head">
          <div>
            <h2 className="audit-panel__title">{isLogin ? '登录记录' : '操作记录'}</h2>
            <p className="audit-panel__desc">
              {isLogin
                ? '账号、角色、客户端与来源 IP 的完整登录流水，按时间倒序排列'
                : '任务、下载、账号与设置变更的完整操作流水，按时间倒序排列'}
            </p>
          </div>
          <span className={`audit-panel__meta${loading ? ' is-loading' : ''}`}>
            {loading ? '查询中…' : `共 ${total.toLocaleString('zh-CN')} 条`}
          </span>
        </header>

        {error && (
          <div className="audit-empty audit-empty--error">
            <span>{error}</span>
            <button type="button" className="audit-text-btn" onClick={() => void load()}>
              重新查询
            </button>
          </div>
        )}

        {!error && items.length === 0 && loading && (
          <div className="audit-empty">正在查询日志…</div>
        )}

        {!error && items.length === 0 && !loading && (
          <div className="audit-empty">
            <span>当前条件下没有日志。可以扩大时间范围，或清空筛选条件。</span>
            <button type="button" className="audit-text-btn" onClick={handleReset}>
              清空筛选
            </button>
          </div>
        )}

        {!error && items.length > 0 && (
          <>
            <div className="audit-table-wrap">
              <table className="audit-table audit-table--logs">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>操作人</th>
                    <th>角色</th>
                    {!isLogin && <th>模块</th>}
                    <th className="audit-table__wide">事件</th>
                    {isLogin ? <th>客户端</th> : <th>耗时</th>}
                    <th>IP 地址</th>
                    <th>结果</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.map((group) => (
                    <Fragment key={group.key}>
                      <tr className="audit-table__day">
                        <td colSpan={columnCount}>{group.label}</td>
                      </tr>
                      {group.items.map((item) => (
                        <tr
                          key={item.id}
                          className={`is-clickable${
                            item.result === 'failure' ? ' is-failure' : ''
                          }`}
                          tabIndex={0}
                          onClick={() => onSelect(item.id)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault()
                              onSelect(item.id)
                            }
                          }}
                          aria-label={`查看日志详情：${auditSummary(item)}`}
                        >
                          <td className="audit-cell--time">{formatTimeOnly(item.ts)}</td>
                          <td>{item.actor_username || '未登录 / 系统'}</td>
                          <td>{item.actor_role_label}</td>
                          {!isLogin && <td>{item.module_label || '—'}</td>}
                          <td className="audit-cell--event" title={auditSummary(item)}>
                            {auditSummary(item)}
                          </td>
                          {isLogin ? (
                            <td>{item.client || '—'}</td>
                          ) : (
                            <td>{formatDuration(item.duration_ms)}</td>
                          )}
                          <td className="audit-mono">{item.ip || '—'}</td>
                          <td>
                            <span
                              className={`audit-badge ${
                                item.result === 'failure'
                                  ? 'audit-badge--failure'
                                  : 'audit-badge--success'
                              }`}
                            >
                              {item.result_label}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>

            <footer className="audit-panel__foot">
              <span className="audit-pager__info">
                第 {page} 页，共 {totalPages} 页
                <i />
                共 {total.toLocaleString('zh-CN')} 条
              </span>
              <div className="audit-pager__actions">
                <button
                  type="button"
                  className="audit-pager__btn"
                  disabled={page <= 1 || loading}
                  onClick={() => setPage((prev) => Math.max(1, prev - 1))}
                >
                  <ChevronLeft size={14} aria-hidden="true" />
                  上一页
                </button>
                <button
                  type="button"
                  className="audit-pager__btn"
                  disabled={page >= totalPages || loading}
                  onClick={() => setPage((prev) => Math.min(totalPages, prev + 1))}
                >
                  下一页
                  <ChevronRight size={14} aria-hidden="true" />
                </button>
              </div>
            </footer>
          </>
        )}
      </section>
    </section>
  )
}
