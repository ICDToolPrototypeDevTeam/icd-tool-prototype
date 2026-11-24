import { useMemo, useState } from 'react'
import { Clock3, X } from 'lucide-react'
import type { ItemTrack, ProgressItem } from './parseJobProgress'
import { formatProviderStatus } from './parseJobProgress'
import { formatDuration, percentDone } from './progressFormat'

interface Props {
  track: ItemTrack
  etaSeconds: number | null
}

/** 条目数超过该值时，每格聚合多条，避免点阵变成长满屏的噪点。 */
const MAX_ONE_DOT = 240
const BUCKET_CELLS = 160

type Tone =
  | 'match'
  | 'pending'
  | 'diff'
  | 'nomatch'
  | 'error'
  | 'unknown'
  | 'running'
  | 'waiting'
  | 'done'
  | 'idle'

interface MatrixCell {
  key: string
  tone: Tone
  label: string
  /** 聚合格用段内比例渐变，逐条格用单色类名 */
  background?: string
}

const TONE_LABELS: Record<Tone, string> = {
  match: '一致',
  pending: '待确认',
  diff: '不一致',
  nomatch: '无匹配',
  error: '异常',
  unknown: '未知',
  running: '进行中',
  waiting: '待复核',
  done: '已完成',
  idle: '待处理',
}

const TONE_COLORS: Record<Tone, string> = {
  match: '#0f8a5f',
  pending: '#e0a33a',
  diff: '#d64545',
  nomatch: '#64748b',
  error: '#b42318',
  unknown: '#9aa5b4',
  running: '#e0a33a',
  waiting: '#bfdbfe',
  done: '#0066cc',
  idle: '#e2e8f0',
}

const TONE_ORDER: Tone[] = [
  'match',
  'pending',
  'diff',
  'nomatch',
  'error',
  'running',
  'waiting',
  'done',
  'unknown',
  'idle',
]

/** 聚合格画成段内比例渐变：红黄不会被「多数绿」淹没，也不会让整格虚报成红色。 */
function bucketBackground(counts: Map<Tone, number>, total: number): string {
  const stops: string[] = []
  let cursor = 0
  for (const tone of TONE_ORDER) {
    const count = counts.get(tone) ?? 0
    if (count <= 0) continue
    const next = cursor + (count / total) * 100
    stops.push(`${TONE_COLORS[tone]} ${cursor.toFixed(2)}% ${next.toFixed(2)}%`)
    cursor = next
  }
  return `linear-gradient(90deg, ${stops.join(', ')})`
}

function itemTone(item: ProgressItem, countedBy: ItemTrack['countedBy']): Tone {
  if (item.outcome === '一致') return 'match'
  if (item.outcome === '待确认') return 'pending'
  if (item.outcome === '不一致') return 'diff'
  if (item.outcome === '无匹配') return 'nomatch'
  if (item.outcome === '异常') return 'error'
  if (item.outcome === '未知') return 'unknown'
  if (item.running) return 'running'
  // 复核阶段：已经裁判但还没复核的条目用浅蓝「待复核」，
  // 与已复核结果区分开，避免看成已经完成。
  if (countedBy === 'reviewed' && item.judged && !item.reviewed) return 'waiting'
  if (item.judged || item.reviewed) return 'done'
  return 'idle'
}

function itemLabel(item: ProgressItem, countedBy: ItemTrack['countedBy']): string {
  if (item.outcome) {
    const stars = item.stars !== undefined ? `${item.stars} 星，` : ''
    return `${item.id}：${stars}${item.outcome}`
  }
  if (item.running) return `${item.id}：判定中`
  if (countedBy === 'reviewed' && item.judged && !item.reviewed) {
    return `${item.id}：待复核`
  }
  if (item.judged) return `${item.id}：已裁判`
  return `${item.id}：待处理`
}

function chipText(item: ProgressItem, kind: ItemTrack['kind']): string {
  if (item.outcome) return item.outcome
  if (kind === 'label') return item.cached ? '缓存' : ''
  if (kind === 'judge') return item.running ? '判定中' : '已完成'
  return '已裁判'
}

function buildCells(
  items: ProgressItem[],
  total: number,
  done: number,
  countedBy: ItemTrack['countedBy'],
): { cells: MatrixCell[]; perCell: number } {
  if (total <= 0) return { cells: [], perCell: 1 }

  if (total <= MAX_ONE_DOT) {
    const cells = Array.from({ length: total }, (_, index): MatrixCell => {
      const item = items[index]
      if (item) {
        return {
          key: item.id,
          tone: itemTone(item, countedBy),
          label: itemLabel(item, countedBy),
        }
      }
      if (index < done) {
        return { key: `done-${index}`, tone: 'done', label: '已完成（明细未同步）' }
      }
      return { key: `idle-${index}`, tone: 'idle', label: '待处理' }
    })
    return { cells, perCell: 1 }
  }

  // 上百 / 上千条：每格聚合一段连续条目。点阵规模固定在 160 格左右，
  // 仍然能看出完成推进与异常聚集；具体条目由下方「最近完成」承担。
  const perCell = Math.ceil(total / BUCKET_CELLS)
  const cells: MatrixCell[] = []
  for (let start = 0; start < total; start += perCell) {
    const end = Math.min(total, start + perCell)
    const counts = new Map<Tone, number>()
    for (let index = start; index < end; index += 1) {
      const item = items[index]
      const tone: Tone = item
        ? itemTone(item, countedBy)
        : index < done
          ? 'done'
          : 'idle'
      counts.set(tone, (counts.get(tone) ?? 0) + 1)
    }
    let tone: Tone = 'idle'
    let best = -1
    for (const [key, count] of counts) {
      if (count > best) {
        best = count
        tone = key
      }
    }
    const summary = Array.from(counts.entries())
      .map(([key, count]) => `${count} ${TONE_LABELS[key]}`)
      .join('，')
    cells.push({
      key: `bucket-${start}`,
      tone,
      label: `第 ${start + 1}–${end} 条：${summary}`,
      background: bucketBackground(counts, end - start),
    })
  }
  return { cells, perCell }
}

export default function ItemProgress({ track, etaSeconds }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = track.items.find((item) => item.id === selectedId) ?? null

  const displayTotal = Math.max(track.total, track.done, track.items.length)
  const displayDone = displayTotal > 0 ? Math.min(track.done, displayTotal) : track.done
  const percent = percentDone(displayDone, displayTotal)
  const remaining = Math.max(0, displayTotal - displayDone)

  const { cells, perCell } = useMemo(
    () => buildCells(track.items, displayTotal, displayDone, track.countedBy),
    [track.items, displayTotal, displayDone, track.countedBy],
  )

  const hasOutcome = track.items.some((item) => Boolean(item.outcome))
  const hasMatch = track.items.some((item) => item.outcome === '一致')
  const hasPending = track.items.some((item) => item.outcome === '待确认')
  const hasDiff = track.items.some((item) => item.outcome === '不一致')
  const hasNoMatch = track.items.some((item) => item.outcome === '无匹配')
  const hasError = track.items.some((item) => item.outcome === '异常')
  const hasUnknown = track.items.some((item) => item.outcome === '未知')
  const hasWaiting =
    track.countedBy === 'reviewed' &&
    track.items.some((item) => item.judged && !item.reviewed)
  const runningItem = track.items.find((item) => item.running) ?? null
  const hasRunning = runningItem !== null
  const hasIdle = displayTotal > Math.max(track.items.length, displayDone)

  return (
    <section className="tp-items" aria-label={`${track.title}进度`}>
      <div className="tp-items__head">
        <div>
          <h3 className="tp-section-title">
            {track.title}
            <span className="tp-items__fraction">
              <strong>{displayDone}</strong>
              <span>/</span>
              {displayTotal > 0 ? displayTotal : '—'}
            </span>
          </h3>
          <p className="tp-items__summary">
            {displayTotal > 0
              ? `已完成 ${percent.toFixed(0)}%，剩余 ${remaining} 条`
              : '正在统计本阶段条目总数…'}
          </p>
        </div>
        {displayTotal > 0 && (
          <span className="tp-items__percent" aria-hidden="true">
            {percent.toFixed(0)}%
          </span>
        )}
      </div>

      <div
        className={`tp-bar${displayTotal === 0 ? ' tp-bar--indeterminate' : ''}`}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={displayTotal}
        aria-valuenow={displayDone}
        aria-label={`${track.title}进度`}
      >
        <span
          className={`tp-bar__fill${percent >= 100 ? ' tp-bar__fill--complete' : ''}`}
          style={displayTotal > 0 ? { width: `${percent}%` } : undefined}
        />
      </div>

      {etaSeconds !== null ? (
        <p
          className="tp-eta tp-eta--prominent"
          title="按本阶段最近 10 个完成条目的速度估算，仅供参照"
        >
          <Clock3 size={13} aria-hidden="true" />
          本阶段预计剩余约 {formatDuration(etaSeconds)}
        </p>
      ) : (
        displayTotal > 0 &&
        displayDone < 3 && (
          <p className="tp-eta tp-eta--hint">完成 3 条后显示本阶段剩余时间估算</p>
        )
      )}

      <div className="tp-items__matrix-head">
        <span>条目进度矩阵</span>
        {displayTotal > 0 && (
          <span
            className="tp-items__matrix-count"
            title={
              perCell > 1
                ? '每格聚合多条，格内按结果比例着色；悬停可查看该段计数'
                : undefined
            }
          >
            {perCell > 1 ? `每格代表 ${perCell} 条` : '每格代表 1 条'}
          </span>
        )}
      </div>

      <div className="tp-matrix" aria-hidden="true">
        {displayTotal === 0 && <span className="tp-matrix__empty">等待条目明细…</span>}
        {cells.map((cell) => (
          <span
            key={cell.key}
            className={`tp-dot${cell.background ? '' : ` tp-dot--${cell.tone}`}`}
            style={cell.background ? { background: cell.background } : undefined}
            title={cell.label}
          />
        ))}
      </div>

      <div className="tp-legend">
        {hasOutcome && (
          <>
            {hasMatch && (
              <span className="tp-legend__item"><i className="tp-dot tp-dot--match" />一致</span>
            )}
            {hasPending && (
              <span className="tp-legend__item"><i className="tp-dot tp-dot--pending" />待确认</span>
            )}
            {hasDiff && (
              <span className="tp-legend__item"><i className="tp-dot tp-dot--diff" />不一致</span>
            )}
            {hasNoMatch && (
              <span className="tp-legend__item"><i className="tp-dot tp-dot--nomatch" />无匹配</span>
            )}
            {hasError && (
              <span className="tp-legend__item"><i className="tp-dot tp-dot--error" />异常</span>
            )}
            {hasUnknown && (
              <span className="tp-legend__item"><i className="tp-dot tp-dot--unknown" />未知</span>
            )}
          </>
        )}
        {track.countedBy === 'reviewed' ? (
          hasWaiting && (
            <span className="tp-legend__item"><i className="tp-dot tp-dot--waiting" />待复核</span>
          )
        ) : (
          <span className="tp-legend__item"><i className="tp-dot tp-dot--done" />已完成</span>
        )}
        {hasRunning && (
          <span className="tp-legend__item"><i className="tp-dot tp-dot--running" />进行中</span>
        )}
        {hasIdle && (
          <span className="tp-legend__item"><i className="tp-dot tp-dot--idle" />待处理</span>
        )}
      </div>

      {runningItem && (
        <p className="tp-running">
          正在处理 <strong>{runningItem.id}</strong>
        </p>
      )}

      <div className="tp-recent">
        <div className="tp-recent__head">
          <span>最近完成</span>
        </div>
        {track.recent.length === 0 ? (
          <p className="tp-recent__empty">
            {track.fallbackOnly || (displayDone > 0 && track.items.length === 0)
              ? '已完成条目的明细不在近期日志中'
              : '正在等待第一条处理结果…'}
          </p>
        ) : (
          <div className="tp-chips">
            {track.recent
              .slice(-8)
              .reverse()
              .map((item) => {
                const state = chipText(item, track.kind)
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={`tp-chip tp-chip--${itemTone(item, track.countedBy)}${selectedId === item.id ? ' tp-chip--selected' : ''}`}
                    onClick={() =>
                      setSelectedId((current) => (current === item.id ? null : item.id))
                    }
                    title={itemLabel(item, track.countedBy)}
                  >
                    <span className="tp-chip__id">{item.id}</span>
                    {state && <span className="tp-chip__state">{state}</span>}
                  </button>
                )
              })}
          </div>
        )}

        {selected && (
          <div className="tp-detail">
            <div className="tp-detail__head">
              <span className="tp-detail__id">{selected.id}</span>
              <button
                type="button"
                className="tp-detail__close"
                onClick={() => setSelectedId(null)}
                aria-label="关闭条目详情"
              >
                <X size={14} />
              </button>
            </div>
            <dl className="tp-detail__rows">
              {selected.providers && selected.providers.length > 0 && (
                <div className="tp-detail__row">
                  <dt>裁判结果</dt>
                  <dd>
                    {selected.providers
                      .map((pair) => `${pair.provider} ${formatProviderStatus(pair.status)}`)
                      .join('，')}
                  </dd>
                </div>
              )}
              {selected.stars !== undefined && (
                <div className="tp-detail__row">
                  <dt>共识星级</dt>
                  <dd>{selected.stars} 星</dd>
                </div>
              )}
              {selected.outcome && (
                <div className="tp-detail__row">
                  <dt>最终结论</dt>
                  <dd>{selected.outcome}</dd>
                </div>
              )}
              {track.kind === 'label' && (
                <div className="tp-detail__row">
                  <dt>标注来源</dt>
                  <dd>{selected.cached ? '命中缓存' : '本次模型调用'}</dd>
                </div>
              )}
              {track.kind === 'judge' && (
                <div className="tp-detail__row">
                  <dt>执行状态</dt>
                  <dd>{selected.judged ? '已完成判定' : '判定中'}</dd>
                </div>
              )}
            </dl>
          </div>
        )}
      </div>
    </section>
  )
}
