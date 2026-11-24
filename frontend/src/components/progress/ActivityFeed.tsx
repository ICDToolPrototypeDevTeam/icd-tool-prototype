import { useState } from 'react'
import type { ActivityEvent } from './parseJobProgress'
import { formatLogTime } from './progressFormat'

interface Props {
  activities: ActivityEvent[]
}

const COLLAPSED_COUNT = 7
const EXPANDED_COUNT = 30

/** 实时动态：只保留语义化事件，不呈现原始日志行。 */
export default function ActivityFeed({ activities }: Props) {
  const [expanded, setExpanded] = useState(false)
  const limit = expanded ? EXPANDED_COUNT : COLLAPSED_COUNT
  const visible = activities.slice(0, limit)
  const restCount = Math.max(0, activities.length - COLLAPSED_COUNT)

  return (
    <section className="tp-activity" aria-label="实时动态">
      <div className="tp-activity__head">
        <h3 className="tp-section-title tp-section-title--sm">实时动态</h3>
        <span className="tp-activity__hint">本地时间，按完成顺序更新</span>
      </div>

      {visible.length === 0 ? (
        <p className="tp-activity__empty">正在等待任务输出…</p>
      ) : (
        <ol className="tp-events" aria-live="polite">
          {visible.map((event) => (
            <li key={event.seq} className={`tp-event tp-event--${event.kind}`}>
              <span className="tp-event__time">{formatLogTime(event.ts) || '—'}</span>
              <span className="tp-event__dot" aria-hidden="true" />
              <span className="tp-event__body">
                <span className="tp-event__title">{event.title}</span>
                {event.detail && <span className="tp-event__detail">{event.detail}</span>}
              </span>
            </li>
          ))}
        </ol>
      )}

      {restCount > 0 && (
        <button
          type="button"
          className="tp-activity__more"
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? '收起' : `显示更多（${restCount} 条）`}
        </button>
      )}
    </section>
  )
}
