import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import {
  ApiError,
  getAuditMeta,
  getAuditOverview,
  getAuditSystem,
  getAuditSystemHistory,
} from '../api'
import type {
  AuditHistoryResponse,
  AuditMeta,
  AuditOverview as AuditOverviewData,
  AuditSystemResponse,
} from '../types'
import { AuditDetailDrawer } from '../components/audit/AuditDetailDrawer'
import { AuditLogExplorer, type AuditLogSeed } from '../components/audit/AuditLogExplorer'
import { AuditOverview } from '../components/audit/AuditOverview'
import { SystemMonitor } from '../components/audit/SystemMonitor'
import { formatRailDate, formatWeekday } from '../components/audit/format'
import '../components/audit/audit.css'

type TabKey = 'overview' | 'login' | 'operation' | 'system'

interface LogSeed extends AuditLogSeed {
  category: 'login' | 'operation'
}

const TABS: { key: TabKey; label: string }[] = [
  { key: 'overview', label: '概览' },
  { key: 'login', label: '登录记录' },
  { key: 'operation', label: '操作记录' },
  { key: 'system', label: '系统运行' },
]

const RANGE_OPTIONS = [
  { value: 24, label: '24 小时' },
  { value: 168, label: '7 天' },
  { value: 720, label: '30 天' },
]

function describeError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return '当前账号没有审计中心访问权限'
    try {
      const body = JSON.parse(error.message) as { detail?: unknown }
      if (typeof body.detail === 'string') return body.detail
    } catch {
      // 非 JSON 错误体：按原文展示
    }
  }
  return fallback
}

function presetOf(hours: number): string {
  if (hours >= 720) return '30d'
  if (hours >= 168) return '7d'
  return '1d'
}

export default function AuditPage() {
  const [tab, setTab] = useState<TabKey>('overview')
  const [meta, setMeta] = useState<AuditMeta | null>(null)
  const [metaError, setMetaError] = useState('')

  const [hours, setHours] = useState(24)
  const [overview, setOverview] = useState<AuditOverviewData | null>(null)
  const [overviewLoading, setOverviewLoading] = useState(false)
  const [overviewError, setOverviewError] = useState('')

  const [system, setSystem] = useState<AuditSystemResponse | null>(null)
  const [history, setHistory] = useState<AuditHistoryResponse | null>(null)
  const [systemLoading, setSystemLoading] = useState(false)
  const [systemError, setSystemError] = useState('')

  const [reloadToken, setReloadToken] = useState(0)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [logSeed, setLogSeed] = useState<LogSeed | null>(null)

  useEffect(() => {
    let cancelled = false
    getAuditMeta()
      .then((data) => {
        if (!cancelled) setMeta(data)
      })
      .catch((error: unknown) => {
        if (!cancelled) setMetaError(describeError(error, '筛选词表加载失败'))
      })
    return () => {
      cancelled = true
    }
  }, [])

  const loadOverview = useCallback(async () => {
    setOverviewLoading(true)
    setOverviewError('')
    try {
      setOverview(await getAuditOverview(hours))
    } catch (error) {
      setOverviewError(describeError(error, '审计概览加载失败，请稍后重试'))
    } finally {
      setOverviewLoading(false)
    }
  }, [hours])

  useEffect(() => {
    if (tab === 'overview') void loadOverview()
  }, [tab, loadOverview])

  const loadSystem = useCallback(async () => {
    setSystemLoading(true)
    setSystemError('')
    const [systemResult, historyResult] = await Promise.allSettled([
      getAuditSystem(),
      getAuditSystemHistory(30),
    ])
    if (systemResult.status === 'fulfilled') {
      setSystem(systemResult.value)
    } else {
      setSystemError(
        describeError(systemResult.reason, '系统运行状态加载失败，请稍后重试'),
      )
    }
    if (historyResult.status === 'fulfilled') {
      setHistory(historyResult.value)
    }
    setSystemLoading(false)
  }, [])

  useEffect(() => {
    if (tab !== 'system') return
    void loadSystem()
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadSystem()
    }, 5000)
    const onVisible = () => {
      if (document.visibilityState === 'visible') void loadSystem()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [tab, loadSystem])

  const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'auto' })

  const changeTab = (next: TabKey) => {
    if (next === tab) return
    setLogSeed(null)
    setTab(next)
    scrollToTop()
  }

  const handleRefresh = () => {
    if (tab === 'overview') {
      void loadOverview()
    } else if (tab === 'system') {
      void loadSystem()
    } else {
      setReloadToken((value) => value + 1)
    }
  }

  /** 概览指标下钻：带着结果条件跳到对应日志页。 */
  const openLogs = (category: 'login' | 'operation', result?: string) => {
    setLogSeed({
      category,
      result,
      preset: presetOf(hours),
      token: Date.now(),
    })
    setTab(category)
    scrollToTop()
  }

  const attention = overview
    ? overview.totals.login_failure + overview.totals.operation_failure
    : 0
  const refreshing =
    tab === 'overview' ? overviewLoading : tab === 'system' ? systemLoading : false
  const today = Date.now()
  const statusState = meta ? 'is-ok' : metaError ? 'is-warn' : 'is-idle'

  return (
    <div className="audit-page">
      <header className="audit-page__head">
        <div className="audit-page__intro">
          <h1 className="audit-page__title">审计中心</h1>
          <p className="audit-page__subtitle">
            登录、操作与系统运行的完整留痕，时间按北京时间展示。
          </p>
        </div>
        <div className="audit-page__aside">
          <span className="audit-page__date">
            {formatRailDate(today)} {formatWeekday(today)}
          </span>
          <span className={`audit-page__status ${statusState}`}>
            <i aria-hidden="true" />
            {meta ? '留痕采集正常' : metaError ? '词表暂不可用' : '正在连接'}
          </span>
        </div>
      </header>

      <div className="audit-tabbar">
        <nav className="audit-tabs" aria-label="审计栏目">
          {TABS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`audit-tab${tab === item.key ? ' is-active' : ''}`}
              onClick={() => changeTab(item.key)}
              aria-current={tab === item.key ? 'page' : undefined}
            >
              {item.label}
              {item.key === 'overview' && attention > 0 && (
                <span className="audit-tab__badge" aria-label={`${attention} 条失败待复核`}>
                  {attention}
                </span>
              )}
            </button>
          ))}
        </nav>

        <div className="audit-tabbar__tools">
          {tab === 'overview' && (
            <div className="audit-range" role="group" aria-label="统计范围">
              {RANGE_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={`audit-range__btn${hours === option.value ? ' is-active' : ''}`}
                  onClick={() => setHours(option.value)}
                  aria-pressed={hours === option.value}
                >
                  {option.label}
                </button>
              ))}
            </div>
          )}
          {refreshing && <span className="audit-tabbar__updating">更新中</span>}
          <button
            type="button"
            className="audit-btn audit-btn--secondary"
            onClick={handleRefresh}
            disabled={refreshing}
          >
            <RefreshCw
              size={14}
              className={refreshing ? 'is-spinning' : undefined}
              aria-hidden="true"
            />
            {refreshing ? '刷新中' : '刷新'}
          </button>
        </div>
      </div>

      {metaError && (
        <div className="audit-inline-warning">
          <span>{metaError}，部分筛选项暂不可用。</span>
        </div>
      )}

      {tab === 'overview' && (
        <AuditOverview
          data={overview}
          loading={overviewLoading}
          error={overviewError}
          onSelect={setDetailId}
          onDrilldown={openLogs}
          onRetry={() => void loadOverview()}
        />
      )}

      {tab === 'login' && (
        <AuditLogExplorer
          category="login"
          meta={meta}
          reloadToken={reloadToken}
          seed={logSeed?.category === 'login' ? logSeed : null}
          onSelect={setDetailId}
        />
      )}

      {tab === 'operation' && (
        <AuditLogExplorer
          category="operation"
          meta={meta}
          reloadToken={reloadToken}
          seed={logSeed?.category === 'operation' ? logSeed : null}
          onSelect={setDetailId}
        />
      )}

      {tab === 'system' && (
        <SystemMonitor
          data={system}
          history={history}
          loading={systemLoading}
          error={systemError}
          onRefresh={() => void loadSystem()}
        />
      )}

      <AuditDetailDrawer logId={detailId} onClose={() => setDetailId(null)} />
    </div>
  )
}
