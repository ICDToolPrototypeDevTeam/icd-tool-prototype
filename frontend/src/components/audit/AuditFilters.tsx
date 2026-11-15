import { useState, type FormEvent } from 'react'
import { ChevronDown, RotateCcw, Search, SlidersHorizontal } from 'lucide-react'
import type { AuditMeta } from '../../types'

export interface AuditFilterValues {
  preset: string
  start: string
  end: string
  result: string
  role: string
  module: string
  action: string
  actor: string
  ip: string
  keyword: string
}

/** 词表接口失败时的兜底选项，保证查询功能仍可用。 */
const FALLBACK_TIME_PRESETS = [
  { value: '1d', label: '最近 24 小时' },
  { value: '7d', label: '最近 7 天' },
  { value: '30d', label: '最近 30 天' },
]
const FALLBACK_RESULTS = [
  { value: 'success', label: '成功' },
  { value: 'failure', label: '失败' },
]
const FALLBACK_ROLES = [
  { value: 'user', label: '业务用户' },
  { value: 'admin', label: '系统管理员' },
  { value: 'auditor', label: '审计管理员' },
  { value: 'anonymous', label: '未登录' },
  { value: 'system', label: '系统' },
]

export const EMPTY_FILTERS: AuditFilterValues = {
  preset: '7d',
  start: '',
  end: '',
  result: '',
  role: '',
  module: '',
  action: '',
  actor: '',
  ip: '',
  keyword: '',
}

/** 把筛选条件换算成后端需要的 ISO 时间；预设时间段以当前时刻为终点。 */
export function resolveTimeRange(values: AuditFilterValues): {
  start?: string
  end?: string
} {
  if (values.preset === 'custom') {
    return {
      start: values.start ? new Date(values.start).toISOString() : undefined,
      end: values.end ? new Date(values.end).toISOString() : undefined,
    }
  }
  const days = values.preset === '30d' ? 30 : values.preset === '1d' ? 1 : 7
  return {
    start: new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString(),
    end: new Date().toISOString(),
  }
}

interface Props {
  values: AuditFilterValues
  meta: AuditMeta | null
  category: 'login' | 'operation'
  loading: boolean
  onChange: (patch: Partial<AuditFilterValues>) => void
  onApply: () => void
  onReset: () => void
}

/**
 * 登录 / 操作日志共用的查询栏：关键词与常用条件平铺，模块、操作人、
 * IP、自定义时间等排查项按需展开。
 */
export function AuditFilters({
  values,
  meta,
  category,
  loading,
  onChange,
  onApply,
  onReset,
}: Props) {
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    onApply()
  }

  const handleReset = () => {
    setAdvancedOpen(false)
    onReset()
  }

  const actions = (meta?.actions || []).filter(
    (item) => !values.module || item.module === values.module,
  )

  const advancedActive = [values.actor, values.ip, values.module, values.action].filter(
    (value) => value.trim() !== '',
  ).length
  const expanded = advancedOpen || values.preset === 'custom'

  return (
    <form className="audit-query" onSubmit={handleSubmit} role="search">
      <div className="audit-query__main">
        <label className="audit-query__search">
          <Search size={16} aria-hidden="true" />
          <input
            type="text"
            value={values.keyword}
            placeholder="搜索操作内容、对象或失败原因"
            aria-label="关键词"
            onChange={(e) => onChange({ keyword: e.target.value })}
          />
        </label>
        <button type="submit" className="audit-btn audit-btn--primary" disabled={loading}>
          {loading ? '查询中…' : '查询'}
        </button>
      </div>

      <div className="audit-query__row">
        <label className="audit-query__field">
          <span className="audit-query__label">时间范围</span>
          <span className="audit-query__control">
            <select
              className="audit-query__select"
              value={values.preset}
              onChange={(e) => onChange({ preset: e.target.value })}
            >
              {(meta?.time_presets?.length ? meta.time_presets : FALLBACK_TIME_PRESETS).map(
                (item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ),
              )}
              <option value="custom">自定义时间段</option>
            </select>
            <ChevronDown size={14} aria-hidden="true" />
          </span>
        </label>

        <label className="audit-query__field">
          <span className="audit-query__label">结果</span>
          <span className="audit-query__control">
            <select
              className="audit-query__select"
              value={values.result}
              onChange={(e) => onChange({ result: e.target.value })}
            >
              <option value="">全部结果</option>
              {(meta?.results?.length ? meta.results : FALLBACK_RESULTS).map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
            <ChevronDown size={14} aria-hidden="true" />
          </span>
        </label>

        <label className="audit-query__field">
          <span className="audit-query__label">角色</span>
          <span className="audit-query__control">
            <select
              className="audit-query__select"
              value={values.role}
              onChange={(e) => onChange({ role: e.target.value })}
            >
              <option value="">全部角色</option>
              {(meta?.roles?.length ? meta.roles : FALLBACK_ROLES).map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
            <ChevronDown size={14} aria-hidden="true" />
          </span>
        </label>

        <button
          type="button"
          className={`audit-btn audit-btn--secondary audit-query__more${
            expanded ? ' is-open' : ''
          }`}
          onClick={() => setAdvancedOpen((open) => !open)}
          aria-expanded={expanded}
        >
          <SlidersHorizontal size={14} aria-hidden="true" />
          更多筛选
          {advancedActive > 0 && <b className="audit-query__count">{advancedActive}</b>}
          <ChevronDown size={14} aria-hidden="true" />
        </button>

        <button type="button" className="audit-query__reset" onClick={handleReset}>
          <RotateCcw size={13} aria-hidden="true" />
          重置
        </button>
      </div>

      {expanded && (
        <div className="audit-query__advanced">
          {category === 'operation' && (
            <>
              <label className="audit-query__adv">
                <span>模块</span>
                <select
                  className="audit-query__input"
                  value={values.module}
                  onChange={(e) => onChange({ module: e.target.value, action: '' })}
                >
                  <option value="">全部模块</option>
                  {(meta?.modules || []).map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="audit-query__adv">
                <span>操作类型</span>
                <select
                  className="audit-query__input"
                  value={values.action}
                  onChange={(e) => onChange({ action: e.target.value })}
                >
                  <option value="">全部操作</option>
                  {actions.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}

          <label className="audit-query__adv">
            <span>操作人</span>
            <input
              className="audit-query__input"
              type="text"
              value={values.actor}
              placeholder="账号，支持模糊匹配"
              onChange={(e) => onChange({ actor: e.target.value })}
            />
          </label>

          <label className="audit-query__adv">
            <span>IP 地址</span>
            <input
              className="audit-query__input"
              type="text"
              value={values.ip}
              placeholder="支持模糊匹配"
              onChange={(e) => onChange({ ip: e.target.value })}
            />
          </label>

          {values.preset === 'custom' && (
            <>
              <label className="audit-query__adv">
                <span>开始时间</span>
                <input
                  className="audit-query__input"
                  type="datetime-local"
                  value={values.start}
                  onChange={(e) => onChange({ start: e.target.value })}
                />
              </label>
              <label className="audit-query__adv">
                <span>结束时间</span>
                <input
                  className="audit-query__input"
                  type="datetime-local"
                  value={values.end}
                  onChange={(e) => onChange({ end: e.target.value })}
                />
              </label>
            </>
          )}
        </div>
      )}
    </form>
  )
}
