import { AlertTriangle, ChevronDown } from 'lucide-react'
import type { ProgressAlert } from './parseJobProgress'

interface Props {
  alerts: ProgressAlert[]
}

/** 提示区：只放会影响结果解读的事件，不重复实时动态里的普通进度。 */
export default function AlertStrip({ alerts }: Props) {
  if (alerts.length === 0) return null

  const errorCount = alerts.filter((alert) => alert.level === 'error').length
  const hasError = errorCount > 0

  return (
    <details className={`tp-alerts${hasError ? ' tp-alerts--error' : ''}`}>
      <summary className="tp-alerts__summary">
        <AlertTriangle size={15} aria-hidden="true" />
        <span className="tp-alerts__count">
          {hasError ? `${errorCount} 条异常` : `${alerts.length} 条提示`}
        </span>
        <span className="tp-alerts__preview">{alerts[0].text}</span>
        <ChevronDown size={15} className="tp-alerts__chevron" aria-hidden="true" />
      </summary>
      <ul className="tp-alerts__list">
        {alerts.map((alert) => (
          <li key={alert.seq} className={`tp-alert tp-alert--${alert.level}`}>
            <span className="tp-alert__mark" aria-hidden="true" />
            <span>{alert.text}</span>
          </li>
        ))}
      </ul>
    </details>
  )
}
