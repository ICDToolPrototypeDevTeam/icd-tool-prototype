import { useEffect, useRef, useState, type ReactNode } from 'react'

/** 坐标轴刻度优先落在 1 / 2 / 2.5 / 5 的整数倍上，避免出现 21.849999… 这类长小数。 */
function niceStep(raw: number): number {
  if (!(raw > 0)) return 1
  const exponent = Math.floor(Math.log10(raw))
  const magnitude = 10 ** exponent
  const fraction = raw / magnitude
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10
  return nice * magnitude
}

/** 计数类图表只允许整数刻度：0 / 5 / 10 …，避免出现 0.5 条事件。 */
function niceCountStep(raw: number): number {
  if (!(raw > 0)) return 1
  const magnitude = 10 ** Math.max(0, Math.floor(Math.log10(raw)))
  for (const multiplier of [1, 2, 5, 10]) {
    const step = multiplier * magnitude
    if (step >= raw) return Math.max(1, Math.round(step))
  }
  return Math.max(1, Math.round(10 * magnitude))
}

/** 百分比刻度最小 1%，避免低占用率被四舍五入成一串重复的 0%。 */
const PERCENT_STEPS = [1, 2, 5, 10, 20, 25, 50, 100]

function nicePercentStep(raw: number): number {
  return PERCENT_STEPS.find((step) => step >= raw) ?? 100
}

function formatAxisValue(value: number): string {
  if (!Number.isFinite(value)) return ''
  const rounded = Math.round(value * 10) / 10
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)
}

/** 图表按容器实际宽度绘制，保证 1 个 SVG 单位等于 1 个 CSS 像素，刻度文字不被缩放。 */
function useChartWidth(fallback = 720) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(fallback)

  useEffect(() => {
    const node = ref.current
    if (!node || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width
      if (next) setWidth(Math.max(280, Math.round(next)))
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  return { ref, width }
}

export interface ChartSeries {
  key: string
  label: string
  color: string
  /** null 表示该时间点无数据（曲线断开，不补零） */
  values: (number | null)[]
  fill?: boolean
}

interface AreaChartProps {
  labels: string[]
  series: ChartSeries[]
  height?: number
  valueFormatter?: (value: number) => string
  /** 计数类折线使用整数刻度 */
  integer?: boolean
  /** 百分比类折线：刻度最小 1%，并保证取整后不重复 */
  percent?: boolean
  emptyText?: string
}

/**
 * 纯 SVG 折线 / 面积图。无第三方图表库：网格、坐标轴、刻度与悬停提示都由
 * 原生 SVG 绘制，保持与页面同一套配色。
 */
export function AreaChart({
  labels,
  series,
  height = 220,
  valueFormatter = formatAxisValue,
  integer = false,
  percent = false,
  emptyText = '暂无数据',
}: AreaChartProps) {
  const { ref, width } = useChartWidth()

  const padLeft = 48
  const padRight = 14
  const padTop = 12
  const padBottom = 28
  const innerWidth = width - padLeft - padRight
  const innerHeight = height - padTop - padBottom

  const values = series.flatMap((item) => item.values.filter((v): v is number => v !== null))
  const hasData = labels.length > 0 && values.length > 0
  const rawMax = Math.max(...values, 0)
  // 五等分：20% / 45% 这类读数会落到 25% / 50% 的坐标上，曲线不至于挤在底部。
  const intervals = 5
  // 全零数据也给出 0–5 的完整刻度，避免五个刻度都被取整成同一个读数。
  const gridStep = percent
    ? nicePercentStep(rawMax / intervals)
    : integer
      ? niceCountStep(rawMax / intervals)
      : rawMax > 0
        ? niceStep(rawMax / intervals)
        : 1
  const maxValue = gridStep * intervals

  const x = (index: number) =>
    padLeft + (labels.length <= 1 ? innerWidth / 2 : (index / (labels.length - 1)) * innerWidth)
  const y = (value: number) => padTop + innerHeight - (value / maxValue) * innerHeight

  const gridLines = Array.from({ length: intervals + 1 }, (_, index) => index / intervals)
  const tickCount = Math.min(6, labels.length)
  const tickIndexes = Array.from({ length: tickCount }, (_, i) =>
    Math.round((i / Math.max(1, tickCount - 1)) * (labels.length - 1)),
  )

  function segments(item: ChartSeries): { line: string; area: string }[] {
    const result: { line: string; area: string }[] = []
    let current: { px: number; py: number }[] = []
    const flush = () => {
      if (current.length === 0) return
      const line = current.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.px},${p.py}`).join(' ')
      const baseline = y(0)
      const area = `${line} L${current[current.length - 1].px},${baseline} L${current[0].px},${baseline} Z`
      result.push({ line, area })
      current = []
    }
    item.values.forEach((value, index) => {
      if (value === null || !Number.isFinite(value)) {
        flush()
        return
      }
      current.push({ px: x(index), py: y(value) })
    })
    flush()
    return result
  }

  return (
    <div className="audit-chart" ref={ref}>
      <div className="audit-chart__legend">
        {series.map((item) => (
          <span key={item.key} className="audit-chart__legend-item">
            <i style={{ background: item.color }} />
            {item.label}
          </span>
        ))}
      </div>
      {!hasData ? (
        <div className="audit-empty audit-empty--chart" style={{ height }}>
          {emptyText}
        </div>
      ) : (
        <svg
          className="audit-chart__svg"
          viewBox={`0 0 ${width} ${height}`}
          width="100%"
          height={height}
          role="img"
          aria-label="趋势图"
        >
          {gridLines.map((ratio) => {
            const value = maxValue * ratio
            return (
              <g key={ratio}>
                <line
                  x1={padLeft}
                  x2={width - padRight}
                  y1={y(value)}
                  y2={y(value)}
                  className="audit-chart__grid"
                />
                <line
                  x1={padLeft - 4}
                  x2={padLeft}
                  y1={y(value)}
                  y2={y(value)}
                  className="audit-chart__tick"
                />
                <text
                  x={padLeft - 8}
                  y={y(value) + 3.5}
                  textAnchor="end"
                  className="audit-chart__axis"
                >
                  {valueFormatter(value)}
                </text>
              </g>
            )
          })}

          <line
            x1={padLeft}
            x2={padLeft}
            y1={padTop}
            y2={y(0)}
            className="audit-chart__axis-line"
          />
          <line
            x1={padLeft}
            x2={width - padRight}
            y1={y(0)}
            y2={y(0)}
            className="audit-chart__axis-line"
          />

          {tickIndexes.map((index) => (
            <g key={index}>
              <line
                x1={x(index)}
                x2={x(index)}
                y1={y(0)}
                y2={y(0) + 4}
                className="audit-chart__tick"
              />
              <text
                x={x(index)}
                y={height - 8}
                textAnchor="middle"
                className="audit-chart__axis"
              >
                {labels[index]}
              </text>
            </g>
          ))}

          {series.map((item) =>
            segments(item).map((segment, index) => (
              <g key={`${item.key}-${index}`}>
                {item.fill && (
                  <path
                    d={segment.area}
                    fill={item.color}
                    fillOpacity={0.06}
                    stroke="none"
                  />
                )}
                <path
                  d={segment.line}
                  fill="none"
                  stroke={item.color}
                  strokeWidth={1.8}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </g>
            )),
          )}
          {labels.length <= 40 &&
            series.map((item) =>
              item.values.map((value, index) =>
                value === null || !Number.isFinite(value) ? null : (
                  <circle
                    key={`${item.key}-dot-${index}`}
                    cx={x(index)}
                    cy={y(value)}
                    r={2.2}
                    fill="#ffffff"
                    stroke={item.color}
                    strokeWidth={1.4}
                  >
                    <title>{`${labels[index]}  ${item.label}：${valueFormatter(value)}`}</title>
                  </circle>
                ),
              ),
            )}
        </svg>
      )}
    </div>
  )
}

export interface TallyPoint {
  hour: string
  total: number
  failure: number
}

type TrendMode = 'hour' | 'six' | 'day'

interface TrendBucket {
  key: string
  total: number
  failure: number
  label: string
}

/**
 * 把后端逐小时数据聚合成适合绘图的桶：24 小时按小时，7 天按 6 小时，
 * 30 天按天。桶数控制在 30 个左右，保证柱子宽度与刻度可读。
 */
function buildTrend(points: TallyPoint[]): { mode: TrendMode; buckets: TrendBucket[] } {
  const mode: TrendMode = points.length <= 48 ? 'hour' : points.length <= 200 ? 'six' : 'day'
  const buckets: TrendBucket[] = []

  for (const point of points) {
    const key = point.hour.replace('T', ' ')
    const date = key.slice(0, 10)
    const hour = Number(key.slice(11, 13)) || 0
    let bucketKey: string
    let label: string

    if (mode === 'hour') {
      bucketKey = key
      label = `${key.slice(5, 10)} ${key.slice(11, 16)}`
    } else if (mode === 'six') {
      const start = Math.floor(hour / 6) * 6
      bucketKey = `${date} ${String(start).padStart(2, '0')}`
      label = `${key.slice(5, 10)} ${String(start).padStart(2, '0')}:00–${String(
        start + 6,
      ).padStart(2, '0')}:00`
    } else {
      bucketKey = date
      label = key.slice(5, 10)
    }

    const last = buckets[buckets.length - 1]
    if (last && last.key === bucketKey) {
      last.total += point.total
      last.failure += point.failure
    } else {
      buckets.push({ key: bucketKey, label, total: point.total, failure: point.failure })
    }
  }

  return { mode, buckets }
}

function trendAxisLabel(mode: TrendMode, bucket: TrendBucket, first: boolean): string {
  if (mode === 'hour') {
    const time = bucket.key.slice(11, 16)
    return first || time === '00:00' ? `${bucket.key.slice(5, 10)} ${time}` : time
  }
  return bucket.key.slice(5, 10)
}

/**
 * 横轴刻度必须等距：只按固定步长取点，不在末尾补点，避免出现「14:00 到 17:00
 * 只有 3 小时」这种不等距轴。刻度数量随图表宽度收缩，窄屏不挤字。
 */
const TICK_STEPS = [1, 2, 4, 6, 8, 12, 24, 48, 96, 168]

function pickTickIndexes(count: number, width: number): number[] {
  if (count <= 1) return [0]
  const target = Math.max(3, Math.min(8, Math.floor(width / 110)))
  const step =
    TICK_STEPS.find((candidate) => Math.floor((count - 1) / candidate) + 1 <= target) ??
    Math.max(1, Math.ceil((count - 1) / Math.max(1, target - 1)))
  const indexes: number[] = []
  for (let index = 0; index < count; index += step) indexes.push(index)
  return indexes
}

/**
 * 概览的事件趋势：成功与失败堆叠的柱状图。柱子按小时 / 6 小时 / 天聚合，
 * 左侧为整数刻度，失败段从柱顶叠加并以红色区分。
 */
export function EventTrendChart({ points }: { points: TallyPoint[] }) {
  const { ref, width } = useChartWidth(820)
  const height = 246

  if (points.length === 0) {
    return <div className="audit-empty audit-empty--chart" style={{ height }}>统计范围内暂无事件</div>
  }

  const { mode, buckets } = buildTrend(points)
  const rawMax = Math.max(1, ...buckets.map((bucket) => bucket.total))
  const step = niceCountStep(rawMax / 4)
  const maxValue = step * 4
  const successTotal = buckets.reduce((sum, bucket) => sum + (bucket.total - bucket.failure), 0)
  const failureTotal = buckets.reduce((sum, bucket) => sum + bucket.failure, 0)
  const peak = buckets.reduce((best, bucket) => (bucket.total > best.total ? bucket : best))
  const seenLabels = new Set<string>()
  const axisTicks = pickTickIndexes(buckets.length, width).filter((index) => {
    const label = trendAxisLabel(mode, buckets[index], index === 0)
    if (seenLabels.has(label)) return false
    seenLabels.add(label)
    return true
  })

  const padLeft = 44
  const padRight = 12
  const padTop = 12
  const padBottom = 30
  const innerWidth = width - padLeft - padRight
  const innerHeight = height - padTop - padBottom
  const groupWidth = innerWidth / buckets.length
  const barWidth = Math.max(4, Math.min(26, groupWidth * 0.62))
  const baseline = padTop + innerHeight
  const bandUnit = mode === 'hour' ? '每小时' : mode === 'six' ? '每 6 小时' : '每天'

  return (
    <div className="audit-chart audit-chart--trend" ref={ref}>
      <div className="audit-chart__legend">
        <span className="audit-chart__legend-item">
          <i style={{ background: '#0066cc' }} />
          成功 {successTotal.toLocaleString('zh-CN')} 条
        </span>
        <span className="audit-chart__legend-item">
          <i style={{ background: '#d92d20' }} />
          失败 {failureTotal.toLocaleString('zh-CN')} 条
        </span>
      </div>

      <svg
        className="audit-chart__svg"
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height={height}
        role="img"
        aria-label={`事件趋势：共 ${successTotal + failureTotal} 条事件，其中失败 ${failureTotal} 条`}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
          const value = maxValue * ratio
          const y = baseline - ratio * innerHeight
          return (
            <g key={ratio}>
              <line
                x1={padLeft}
                x2={width - padRight}
                y1={y}
                y2={y}
                className="audit-chart__grid"
              />
              <line
                x1={padLeft - 4}
                x2={padLeft}
                y1={y}
                y2={y}
                className="audit-chart__tick"
              />
              <text
                x={padLeft - 8}
                y={y + 3.5}
                textAnchor="end"
                className="audit-chart__axis"
              >
                {Math.round(value)}
              </text>
            </g>
          )
        })}

        <line
          x1={padLeft}
          x2={padLeft}
          y1={padTop}
          y2={baseline}
          className="audit-chart__axis-line"
        />
        <line
          x1={padLeft}
          x2={width - padRight}
          y1={baseline}
          y2={baseline}
          className="audit-chart__axis-line"
        />

        {buckets.map((bucket, index) => {
          if (bucket.total <= 0) return null
          const x = padLeft + index * groupWidth + (groupWidth - barWidth) / 2
          const totalHeight = (bucket.total / maxValue) * innerHeight
          const failureHeight =
            bucket.failure > 0
              ? Math.max(2, (bucket.failure / maxValue) * innerHeight)
              : 0
          const successHeight = Math.max(0, totalHeight - failureHeight)
          return (
            <g key={bucket.key}>
              <title>{`${bucket.label}：成功 ${bucket.total - bucket.failure} 条，失败 ${bucket.failure} 条`}</title>
              {bucket.failure > 0 && (
                <rect
                  x={x}
                  y={baseline - totalHeight}
                  width={barWidth}
                  height={failureHeight}
                  fill="#d92d20"
                  rx={1}
                />
              )}
              <rect
                x={x}
                y={baseline - successHeight}
                width={barWidth}
                height={successHeight}
                fill="#0066cc"
                rx={1}
              />
            </g>
          )
        })}

        {axisTicks.map((index) => {
          const x = padLeft + index * groupWidth + groupWidth / 2
          return (
            <g key={index}>
              <line
                x1={x}
                x2={x}
                y1={baseline}
                y2={baseline + 4}
                className="audit-chart__tick"
              />
              <text
                x={x}
                y={height - 10}
                textAnchor="middle"
                className="audit-chart__axis"
              >
                {trendAxisLabel(mode, buckets[index], index === 0)}
              </text>
            </g>
          )
        })}
      </svg>

      <div className="audit-chart__foot">
        <span>按{bandUnit}统计，共 {buckets.length} 个统计格</span>
        <span>
          {peak.total > 0 ? `峰值 ${peak.total} 条（${peak.label}）` : '统计范围内没有事件'}
        </span>
      </div>
    </div>
  )
}

export interface RankItem {
  value: string
  label: string
  count: number
}

/** 排名名录：细线表达占比，数字与百分比同列对齐。 */
export function BarList({ items, total }: { items: RankItem[]; total?: number }) {
  const max = Math.max(1, ...items.map((item) => item.count))
  if (items.length === 0) {
    return <div className="audit-empty audit-empty--inline">暂无数据</div>
  }

  const sum = total && total > 0 ? total : 0

  return (
    <div className="audit-rank">
      {items.map((item) => (
        <div key={item.value} className="audit-rank__row">
          <div className="audit-rank__line">
            <span className="audit-rank__label" title={item.label}>
              {item.label}
            </span>
            <b className="audit-rank__count">{item.count.toLocaleString('zh-CN')}</b>
            {sum > 0 && (
              <span className="audit-rank__percent">
                {((item.count / sum) * 100).toFixed(1)}%
              </span>
            )}
          </div>
          <span className="audit-rank__track">
            <i
              className="audit-rank__fill"
              style={{ width: `${Math.max(2, (item.count / max) * 100)}%` }}
            />
          </span>
        </div>
      ))}
    </div>
  )
}

export interface CompositionRow {
  key: string
  label: string
  count: number
  tone: 'success' | 'failure'
}

/** 事件构成：成功 / 失败分组的横向条形，数字与占比右对齐。 */
export function CompositionList({
  rows,
  total,
}: {
  rows: CompositionRow[]
  total: number
}) {
  const max = Math.max(1, ...rows.map((row) => row.count))
  return (
    <div className="audit-composition">
      {rows.map((row) => (
        <div key={row.key} className="audit-composition__row">
          <div className="audit-composition__line">
            <span className={`audit-composition__label is-${row.tone}`}>{row.label}</span>
            <b>{row.count.toLocaleString('zh-CN')}</b>
            <span className="audit-composition__percent">
              {total > 0 ? ((row.count / total) * 100).toFixed(1) : '0.0'}%
            </span>
          </div>
          <span className="audit-composition__track">
            <i
              className={`audit-composition__fill is-${row.tone}`}
              style={{ width: `${Math.max(2, (row.count / max) * 100)}%` }}
            />
          </span>
        </div>
      ))}
    </div>
  )
}

const METER_COLORS: Record<string, string> = {
  ok: '#0066cc',
  warning: '#d97706',
  critical: '#d92d20',
  unknown: '#94a3b8',
}

interface MeterProps {
  label: string
  percent: number | null
  detail: ReactNode
  tone: 'ok' | 'warning' | 'critical' | 'unknown'
  caption?: string
}

/** 资源刻度：读数、比例与 75 / 90 阈值摆在同一把尺上，未设置限额时如实显示「—」。 */
export function Meter({ label, percent, detail, tone, caption }: MeterProps) {
  const unset = percent === null || !Number.isFinite(percent)
  const value = unset ? 0 : Math.min(100, Math.max(0, percent))
  const color = METER_COLORS[tone] || METER_COLORS.unknown

  return (
    <div className={`audit-meter audit-meter--${tone}`}>
      <div className="audit-meter__head">
        <span>{label}</span>
        <b>{unset ? '—' : `${Math.round(value)}%`}</b>
      </div>
      <div
        className={`audit-meter__track${unset ? ' is-unset' : ''}`}
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={unset ? undefined : Math.round(value)}
      >
        {!unset && (
          <>
            <span
              className="audit-meter__fill"
              style={{ width: `${value}%`, background: color }}
            />
            <span className="audit-meter__tick" style={{ left: '75%' }} />
            <span className="audit-meter__tick" style={{ left: '90%' }} />
          </>
        )}
      </div>
      <div className="audit-meter__foot">
        <span>{detail}</span>
        {caption && <span>{caption}</span>}
      </div>
    </div>
  )
}
