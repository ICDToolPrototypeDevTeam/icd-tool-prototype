import { useEffect, useRef, useState } from 'react'
import { getJobLogsV4 } from '../api'
import type { V4LogLine } from '../types'

/** 日志轮询间隔。比任务状态（10s）更快，便于观察卡在哪一步。 */
const POLL_INTERVAL_MS = 3000
/** 面板内保留的最大行数，防止长任务把 DOM 撑爆。 */
const MAX_LINES = 2000

/**
 * 任务日志增量轮询：按 next_offset 只取新增行。
 *
 * 进程重启后后端 buffer 会重建、seq 从头开始，此时返回的 next_offset 会小于
 * 当前 offset；检测到该情况即把偏移归零重新拉取（后端从 job.log 尾部恢复，
 * 因此重启前的日志仍可见）。
 */
export function useJobLogs(jobId: string | null) {
  const [lines, setLines] = useState<V4LogLine[]>([])
  const [truncated, setTruncated] = useState(false)
  const offsetRef = useRef(0)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    setLines([])
    setTruncated(false)
    offsetRef.current = 0
    if (!jobId) return

    let stopped = false
    let timer: number | null = null

    const tick = async () => {
      try {
        const res = await getJobLogsV4(jobId, offsetRef.current, 500)
        if (stopped || !mountedRef.current) return
        if (res.lines.length > 0) {
          offsetRef.current = res.next_offset
          setLines((prev) => {
            const merged = [...prev, ...res.lines]
            return merged.length > MAX_LINES ? merged.slice(merged.length - MAX_LINES) : merged
          })
        } else if (res.next_offset < offsetRef.current) {
          // 后端进程重启：buffer 重建、seq 从头开始；已累积的行必须一并丢弃，
          // 否则重拉的行会与旧行重复（seq 重复还会触发 React key 重复告警）
          offsetRef.current = 0
          setLines([])
          setTruncated(false)
        }
        if (res.truncated) setTruncated(true)
      } catch {
        // 日志拉取失败不得影响任务主流程；下一轮再试
      }
      if (!stopped && mountedRef.current) {
        timer = window.setTimeout(tick, POLL_INTERVAL_MS)
      }
    }

    tick()

    return () => {
      stopped = true
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [jobId])

  return { lines, truncated }
}
