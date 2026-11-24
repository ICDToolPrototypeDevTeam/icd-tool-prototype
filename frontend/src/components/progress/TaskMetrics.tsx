import type { DistributionSegment, Metric, StageView } from './parseJobProgress'
import { formatDuration } from './progressFormat'

interface Props {
  metrics: Metric[]
  distribution: { title: string; segments: DistributionSegment[] } | null
  stages: StageView[]
  activeElapsedSec: number | null
  labelModel: string | null
  judgeModels: string[]
}

/** 右侧指标栏：数字少而硬，不做装饰性图表。 */
export default function TaskMetrics({
  metrics,
  distribution,
  stages,
  activeElapsedSec,
  labelModel,
  judgeModels,
}: Props) {
  const visibleStages = stages.filter((stage) => stage.state !== 'pending')
  const distributionTotal =
    distribution?.segments.reduce((sum, segment) => sum + segment.value, 0) ?? 0

  return (
    <aside className="tp-metrics" aria-label="任务关键数据">
      <div className="tp-metrics__block">
        <h3 className="tp-section-title tp-section-title--sm">关键数据</h3>
        {metrics.length > 0 ? (
          <dl className="tp-metric-list">
            {metrics.map((metric) => (
              <div key={metric.key} className="tp-metric">
                <dt className="tp-metric__label">
                  {metric.label}
                  {metric.hint && <span className="tp-metric__hint">{metric.hint}</span>}
                </dt>
                <dd className="tp-metric__value">{metric.value}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="tp-metrics__empty">解析完成后将显示 EoICD 与 SWHLR 数量</p>
        )}
      </div>

      {distribution && distributionTotal > 0 && (
        <div className="tp-metrics__block">
          <h3 className="tp-section-title tp-section-title--sm">{distribution.title}</h3>
          <div className="tp-dist" aria-hidden="true">
            {distribution.segments.map((segment) => (
              <span
                key={segment.key}
                className={`tp-dist__seg tp-dist__seg--${segment.tone}`}
                style={{ width: `${(segment.value / distributionTotal) * 100}%` }}
              />
            ))}
          </div>
          <ul className="tp-dist__legend">
            {distribution.segments.map((segment) => (
              <li key={segment.key}>
                <i className={`tp-dist__dot tp-dist__dot--${segment.tone}`} />
                <span>{segment.label}</span>
                <strong>{segment.value}</strong>
              </li>
            ))}
          </ul>
        </div>
      )}

      {visibleStages.length > 0 && (
        <div className="tp-metrics__block">
          <h3 className="tp-section-title tp-section-title--sm">阶段耗时</h3>
          <ul className="tp-time-list">
            {visibleStages.map((stage) => {
              const duration =
                stage.state === 'active' ? activeElapsedSec : stage.durationSec
              return (
                <li key={stage.key} className={`tp-time-list__row tp-time-list__row--${stage.state}`}>
                  <span className="tp-time-list__label">{stage.short}</span>
                  <span className="tp-time-list__value">
                    {duration !== null
                      ? formatDuration(duration)
                      : stage.state === 'active'
                        ? '计时中'
                        : '—'}
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {(labelModel || judgeModels.length > 0) && (
        <div className="tp-models">
          {labelModel && (
            <p>
              <span className="tp-models__label">标注模型</span>
              {labelModel}
            </p>
          )}
          {judgeModels.length > 0 && (
            <p>
              <span className="tp-models__label">裁判模型</span>
              {judgeModels.join('、')}
            </p>
          )}
        </div>
      )}
    </aside>
  )
}
