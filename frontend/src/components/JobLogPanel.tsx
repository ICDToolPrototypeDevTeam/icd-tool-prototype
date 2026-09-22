import { useEffect, useRef, useState } from 'react'
import type { V4LogLine } from '../types'

interface Props {
  lines: V4LogLine[]
  truncated?: boolean
  /** 默认展开（错误页传 true，处理页传 false） */
  defaultOpen?: boolean
}

/**
 * 任务日志面板（纯展示）。
 *
 * 轮询由父组件通过 useJobLogs 负责，本组件不自己拉取 —— 否则同一页面会出现
 * 两个轮询器，也会让「复制诊断信息」拿到的行数与面板显示不一致。
 */
export default function JobLogPanel({ lines, truncated = false, defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen)
  const [autoScroll, setAutoScroll] = useState(true)
  const boxRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open || !autoScroll) return
    const box = boxRef.current
    if (box) box.scrollTop = box.scrollHeight
  }, [lines, open, autoScroll])

  return (
    <div className="log-panel">
      <div className="log-panel__bar">
        <button className="log-panel__toggle" onClick={() => setOpen((v) => !v)}>
          {open ? '▾' : '▸'} 执行日志{lines.length > 0 ? `（${lines.length} 行）` : ''}
        </button>
        {open && (
          <>
            <label className="log-panel__auto">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
              />
              自动滚动
            </label>
            <span className="log-panel__tz">时间列为 UTC</span>
          </>
        )}
      </div>
      {open && (
        <div className="log-panel__body" ref={boxRef}>
          {truncated && (
            <div className="log-panel__note">
              日志过长，此处仅保留最近 2000 行；完整日志见任务输出目录的 job.log
            </div>
          )}
          {lines.length === 0 && <div className="log-panel__empty">暂无日志…</div>}
          {lines.map((l) => (
            <div key={l.seq} className={`log-line log-line--${l.level}`}>
              <span className="log-line__ts">{l.ts ? l.ts.slice(11, 19) : ''}</span>
              <span className="log-line__text">{l.text}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
