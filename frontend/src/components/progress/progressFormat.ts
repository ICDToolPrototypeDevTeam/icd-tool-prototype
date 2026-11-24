/** 进度看板的时间/数字格式化。 */

export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total} 秒`
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (hours > 0) return `${hours} 小时 ${minutes} 分`
  const rest = total % 60
  return rest > 0 ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`
}

/** 顶部已用时：mm:ss，超过一小时显示 h:mm:ss。 */
export function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const rest = total % 60
  const pad = (value: number) => String(value).padStart(2, '0')
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(rest)}` : `${pad(minutes)}:${pad(rest)}`
}

/** 日志时间戳转本地时间；恢复行没有 ts 时返回空串。 */
export function formatLogTime(ts: string): string {
  if (!ts) return ''
  const date = new Date(ts)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

export function percentDone(done: number, total: number): number {
  if (total <= 0) return 0
  return Math.max(0, Math.min(100, (done / total) * 100))
}
