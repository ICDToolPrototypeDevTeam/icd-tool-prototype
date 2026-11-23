import { Activity, ArrowRight, LogIn, ScrollText, ShieldAlert } from 'lucide-react'
import type { AuditOverview as AuditOverviewData } from '../../types'
import { BarList, CompositionList, EventTrendChart } from './Charts'
import { auditSummary, formatMonthDay, formatTimeOnly } from './format'

interface Props {
  data: AuditOverviewData | null
  loading: boolean
  error: string
  onSelect: (logId: string) => void
  onDrilldown: (category: 'login' | 'operation', result?: string) => void
  onRetry?: () => void
}

function rangeLabel(hours: number): string {
  if (hours >= 720) return '30 天'
  if (hours >= 168) return '7 天'
  return '24 小时'
}

function percentOf(part: number, total: number): string {
  if (!total) return '0.0'
  return ((part / total) * 100).toFixed(1)
}

/**
 * 审计概览：四个关键指标先行，随后是事件趋势与构成、最近记录和分布，
 * 失败指标可直接下钻到已过滤的日志列表。
 */
export function AuditOverview({
  data,
  loading,
  error,
  onSelect,
  onDrilldown,
  onRetry,
}: Props) {
  if (error) {
    return (
      <section className="audit-panel">
        <div className="audit-empty audit-empty--error">
          <span>{error}</span>
          {onRetry && (
            <button type="button" className="audit-text-btn" onClick={onRetry}>
              重新加载
            </button>
          )}
        </div>
      </section>
    )
  }

  if (!data) {
    return (
      <section className="audit-panel">
        <div className="audit-empty">正在加载审计概览…</div>
      </section>
    )
  }

  const { totals } = data
  const range = rangeLabel(data.range_hours)
  const failureTotal = totals.login_failure + totals.operation_failure
  const failureRate = percentOf(failureTotal, totals.total)

  const composition = [
    { key: 'login-success', label: '登录成功', count: totals.login_success, tone: 'success' as const },
    { key: 'login-failure', label: '登录失败', count: totals.login_failure, tone: 'failure' as const },
    {
      key: 'operation-success',
      label: '操作成功',
      count: totals.operation_success,
      tone: 'success' as const,
    },
    {
      key: 'operation-failure',
      label: '操作失败',
      count: totals.operation_failure,
      tone: 'failure' as const,
    },
  ]

  return (
    <div className="audit-overview" aria-busy={loading}>
      <div className="audit-kpis">
        <div className="audit-kpi">
          <span className="audit-kpi__icon">
            <ScrollText size={20} aria-hidden="true" />
          </span>
          <span className="audit-kpi__label">审计记录</span>
          <b className="audit-kpi__value">{totals.total.toLocaleString('zh-CN')}</b>
          <span className="audit-kpi__hint">最近 {range}的所有登录与操作事件</span>
        </div>

        <button
          type="button"
          className={`audit-kpi audit-kpi--danger audit-kpi--link${
            totals.login_failure === 0 ? ' is-clear' : ''
          }`}
          onClick={() => onDrilldown('login', 'failure')}
        >
          <span className="audit-kpi__icon">
            <LogIn size={20} aria-hidden="true" />
          </span>
          <span className="audit-kpi__label">登录失败</span>
          <b className="audit-kpi__value">{totals.login_failure.toLocaleString('zh-CN')}</b>
          <span className="audit-kpi__hint">
            占全部 {percentOf(totals.login_failure, totals.total)}%
            <span className="audit-kpi__go">
              查看记录
              <ArrowRight size={13} aria-hidden="true" />
            </span>
          </span>
        </button>

        <button
          type="button"
          className={`audit-kpi audit-kpi--danger audit-kpi--link${
            totals.operation_failure === 0 ? ' is-clear' : ''
          }`}
          onClick={() => onDrilldown('operation', 'failure')}
        >
          <span className="audit-kpi__icon">
            <ShieldAlert size={20} aria-hidden="true" />
          </span>
          <span className="audit-kpi__label">操作失败</span>
          <b className="audit-kpi__value">{totals.operation_failure.toLocaleString('zh-CN')}</b>
          <span className="audit-kpi__hint">
            占全部 {percentOf(totals.operation_failure, totals.total)}%
            <span className="audit-kpi__go">
              查看记录
              <ArrowRight size={13} aria-hidden="true" />
            </span>
          </span>
        </button>

        <div className="audit-kpi">
          <span className="audit-kpi__icon">
            <Activity size={20} aria-hidden="true" />
          </span>
          <span className="audit-kpi__label">活跃账号</span>
          <b className="audit-kpi__value">
            {totals.active_users_30m.toLocaleString('zh-CN')}
          </b>
          <span className="audit-kpi__hint">
            30 分钟内活跃，统计期涉及 {totals.unique_actors} 个账号
          </span>
        </div>
      </div>

      <div className="audit-grid audit-grid--overview">
        <section className="audit-panel">
          <header className="audit-panel__head">
            <div>
              <h2 className="audit-panel__title">事件趋势</h2>
              <p className="audit-panel__desc">
                成功与失败事件的分布，失败以红色叠加显示；失败占比 {failureRate}%
              </p>
            </div>
            <span className="audit-panel__meta">{loading ? '更新中…' : `最近 ${range}`}</span>
          </header>
          <EventTrendChart points={data.by_hour} />
        </section>

        <section className="audit-panel">
          <header className="audit-panel__head">
            <div>
              <h2 className="audit-panel__title">事件构成</h2>
              <p className="audit-panel__desc">登录与操作记录的成功 / 失败数量</p>
            </div>
          </header>
          <CompositionList rows={composition} total={totals.total} />
        </section>
      </div>

      <section className="audit-panel">
        <header className="audit-panel__head">
          <div>
            <h2 className="audit-panel__title">最近记录</h2>
            <p className="audit-panel__desc">统计范围内最新的审计事件，点击行查看完整信息</p>
          </div>
          <button
            type="button"
            className="audit-text-btn"
            onClick={() => onDrilldown('operation')}
          >
            查看操作记录
            <ArrowRight size={14} aria-hidden="true" />
          </button>
        </header>

        {data.recent.length === 0 ? (
          <div className="audit-empty">统计范围内还没有审计事件。</div>
        ) : (
          <div className="audit-table-wrap">
            <table className="audit-table audit-table--recent">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>操作人</th>
                  <th className="audit-table__cat">类别</th>
                  <th>事件</th>
                  <th>结果</th>
                </tr>
              </thead>
              <tbody>
                {data.recent.map((item) => (
                  <tr
                    key={item.id}
                    className={`is-clickable${item.result === 'failure' ? ' is-failure' : ''}`}
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
                    <td className="audit-cell--time">
                      {formatMonthDay(item.ts)} {formatTimeOnly(item.ts)}
                    </td>
                    <td>{item.actor_username || '未登录 / 系统'}</td>
                    <td className="audit-cell--cat">
                      <span className="audit-tag">{item.category_label}</span>
                    </td>
                    <td className="audit-cell--event" title={auditSummary(item)}>
                      {auditSummary(item)}
                    </td>
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
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="audit-grid audit-grid--two">
        <section className="audit-panel">
          <header className="audit-panel__head">
            <div>
              <h2 className="audit-panel__title">高频模块</h2>
              <p className="audit-panel__desc">按审计事件数量排序</p>
            </div>
            <span className="audit-panel__meta">最近 {range}</span>
          </header>
          <BarList
            items={data.by_module.slice(0, 6).map((item) => ({
              value: item.value,
              label: item.label,
              count: item.count,
            }))}
            total={totals.total}
          />
        </section>

        <section className="audit-panel">
          <header className="audit-panel__head">
            <div>
              <h2 className="audit-panel__title">操作角色</h2>
              <p className="audit-panel__desc">按角色统计的事件数量</p>
            </div>
            <span className="audit-panel__meta">最近 {range}</span>
          </header>
          <BarList
            items={data.by_role.map((item) => ({
              value: item.value,
              label: item.label,
              count: item.count,
            }))}
            total={totals.total}
          />
        </section>
      </div>
    </div>
  )
}
