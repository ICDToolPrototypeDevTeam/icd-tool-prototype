/** 审计中心展示用的格式化工具。时间统一按中国标准时间展示（后端存 UTC）。 */

import type { AuditLogItem } from '../../types'

const TIME_ZONE = 'Asia/Shanghai'

/** 登录日志的账号已在「操作人」列展示，事件正文去掉重复的账号前缀。 */
export function auditSummary(item: AuditLogItem): string {
  if (item.category !== 'login') return item.summary
  const username = item.actor_username
  if (!username) return item.summary
  const escaped = username.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const text = item.summary
    .replace(new RegExp(`^${escaped}（[^）]*）`), '')
    .replace(new RegExp(`^账号「${escaped}」`), '')
  return text || item.summary
}

function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === '') return null
  const date = value instanceof Date ? value : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** 完整时间：2026-11-13 14:05:32（北京时间）。 */
export function formatDateTime(value: string | number | Date | null | undefined): string {
  const date = toDate(value)
  if (!date) return '—'
  const text = date.toLocaleString('zh-CN', {
    timeZone: TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
  return text.replace(/\//g, '-')
}

/** 只显示时分秒。 */
export function formatTimeOnly(value: string | number | Date | null | undefined): string {
  const date = toDate(value)
  if (!date) return '—'
  return date.toLocaleTimeString('zh-CN', {
    timeZone: TIME_ZONE,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

/** 北京时间下的日期键：YYYY-MM-DD，用于日志按天分组。 */
export function formatDayKey(value: string | number | Date | null | undefined): string {
  const date = toDate(value)
  if (!date) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
    .format(date)
    .replace(/\//g, '-')
}

/** 月日：11-13。 */
export function formatMonthDay(value: string | number | Date | null | undefined): string {
  const key = formatDayKey(value)
  return key ? key.slice(5) : '—'
}

/** 星期：星期五。 */
export function formatWeekday(value: string | number | Date | null | undefined = Date.now()): string {
  const date = toDate(value)
  if (!date) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: TIME_ZONE,
    weekday: 'long',
  }).format(date)
}

/** 镜像栏日期：2026.11.13。 */
export function formatRailDate(value: string | number | Date | null | undefined = Date.now()): string {
  const key = formatDayKey(value)
  return key ? key.replace(/-/g, '.') : '—'
}

/** 审计台时钟：15:24:08（北京时间，24 小时制）。 */
export function formatClock(value: string | number | Date | null | undefined = Date.now()): string {
  const date = toDate(value)
  if (!date) return '—'
  return date.toLocaleTimeString('zh-CN', {
    timeZone: TIME_ZONE,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  })
}

/** 日志分册页眉：11.13 + 今天 / 昨天 / 星期五。 */
export function formatDayFolio(value: string | number | Date | null | undefined): {
  primary: string
  secondary: string
} {
  const key = formatDayKey(value)
  if (!key) return { primary: '—', secondary: '日期未知' }
  const primary = key.slice(5).replace('-', '.')
  const todayKey = formatDayKey(Date.now())
  const yesterdayKey = formatDayKey(Date.now() - 24 * 60 * 60 * 1000)
  const secondary =
    key === todayKey ? '今天' : key === yesterdayKey ? '昨天' : formatWeekday(value)
  return { primary, secondary }
}

/** 日志分组标题：今天（2026-11-13），其余为 2026-11-12 周四。 */
export function formatDayLabel(value: string | number | Date | null | undefined): string {
  const key = formatDayKey(value)
  if (!key) return '未知日期'
  const todayKey = formatDayKey(Date.now())
  if (key === todayKey) return `今天（${key}）`
  const date = toDate(value)
  if (!date) return key
  const weekday = new Intl.DateTimeFormat('zh-CN', {
    timeZone: TIME_ZONE,
    weekday: 'short',
  }).format(date)
  return `${key} ${weekday}`
}

/** 图表横轴短标签：MM-DD HH:00。 */
export function formatHourLabel(hour: string): string {
  // 后端 by_hour 已是北京时间字符串 "YYYY-MM-DD HH:00"
  const match = /^\d{4}-(\d{2}-\d{2}) (\d{2}):00$/.exec(hour)
  return match ? `${match[1]} ${match[2]}:00` : hour
}

/** 字节数：1.2 GB。 */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return '—'
  if (bytes < 1024) return `${Math.round(bytes)} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  const digits = value >= 100 ? 0 : value >= 10 ? 1 : 2
  return `${value.toFixed(digits)} ${units[index]}`
}

/** 百分数：12.3%。 */
export function formatPercent(percent: number | null | undefined): string {
  if (percent === null || percent === undefined || !Number.isFinite(percent)) return '—'
  return `${percent.toFixed(1)}%`
}

/** 接口耗时：320 毫秒 / 1.4 秒。 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)} 毫秒`
  return `${(ms / 1000).toFixed(1)} 秒`
}

/** 任务已运行时长：1 小时 05 分 / 3 分 20 秒。 */
export function formatRuntime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—'
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total} 秒`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return `${minutes} 分 ${total % 60} 秒`
  const hours = Math.floor(minutes / 60)
  return `${hours} 小时 ${String(minutes % 60).padStart(2, '0')} 分`
}

/** 资源使用率对应的严重程度。 */
export function resourceTone(
  percent: number | null | undefined,
  warn: number,
  critical: number,
): 'ok' | 'warning' | 'critical' | 'unknown' {
  if (percent === null || percent === undefined || !Number.isFinite(percent)) return 'unknown'
  if (percent >= critical) return 'critical'
  if (percent >= warn) return 'warning'
  return 'ok'
}

/** detail 里的值转成中文可读文本。 */
export function formatDetailValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') return value
  if (Array.isArray(value)) {
    return value.map((item) => formatDetailValue(item)).join('、') || '—'
  }
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key}：${formatDetailValue(item)}`)
      .join('；')
  }
  return String(value)
}

/** HTTP 方法翻成中文，括号内保留原方法便于排查。 */
export function formatMethod(method: string | null | undefined): string {
  if (!method) return '—'
  const labels: Record<string, string> = {
    GET: '查询',
    POST: '提交',
    PUT: '更新',
    PATCH: '修改',
    DELETE: '删除',
    HEAD: '探测',
    OPTIONS: '预检',
  }
  const upper = method.toUpperCase()
  return labels[upper] ? `${labels[upper]}（${upper}）` : method
}
