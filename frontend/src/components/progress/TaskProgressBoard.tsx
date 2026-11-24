import { useEffect, useMemo, useRef, useState } from 'react'
import { Clock3, Coins } from 'lucide-react'
import type { V4LogLine } from '../../types'
import ActivityFeed from './ActivityFeed'
import AlertStrip from './AlertStrip'
import ItemProgress from './ItemProgress'
import StageRibbon from './StageRibbon'
import TaskMetrics from './TaskMetrics'
import { getStages, taskKindLabel, type PipelineKind } from './stageMeta'
import { parseJobProgress, type JobProgressModel } from './parseJobProgress'
import { formatDuration, formatElapsed } from './progressFormat'
import './task-progress.css'

interface Props {
  taskType: 'correctness' | 'completeness'
  progress: string
  stage: string
  stageIndex: number
  stageTotal: number
  caseIndex: number
  caseTotal: number
  resumed: boolean
  reuse: { reused: number; rerun: number } | null
  resumedHint?: string
  tokenUsage: number | null
  mock: boolean
  lines: V4LogLine[]
  truncated?: boolean
  createdAt?: string | null
  onCancel?: () => void
  cancelRequested?: boolean
  onForceCancel?: () => void
  forceCancelAvailable?: boolean
}

/** 每秒刷新一次的时间基准；与状态轮询（5s）同量级，避免无意义的逐秒重绘。 */
function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs])
  return now
}

function computeEta(model: JobProgressModel, now: number): number | null {
  const track = model.itemTrack
  if (!track || track.total <= 0 || track.done >= track.total) return null
  const times = track.completionTimes.filter((value) => value > 0).slice(-10)
  if (times.length < 3) return null
  const span = (times[times.length - 1] - times[0]) / 1000
  if (span <= 1) return null
  const rate = (times.length - 1) / span
  if (rate <= 0) return null
  const eta = (track.total - track.done) / rate
  const lastAge = (now - times[times.length - 1]) / 1000
  // 30 分钟没有新条目视为停滞，不再给一个过期的估算。
  if (eta > 6 * 3600 || lastAge > 1800) return null
  return Math.max(1, eta)
}

export default function TaskProgressBoard({
  taskType,
  progress,
  stage,
  stageIndex,
  stageTotal,
  caseIndex,
  caseTotal,
  resumed,
  reuse,
  resumedHint,
  tokenUsage,
  mock,
  lines,
  truncated,
  createdAt,
  onCancel,
  cancelRequested,
  onForceCancel,
  forceCancelAvailable,
}: Props) {
  const pipeline: PipelineKind = taskType === 'completeness' ? 'forward' : 'reverse'
  const stages = useMemo(() => getStages(pipeline, stageTotal), [pipeline, stageTotal])

  const model = useMemo(
    () =>
      parseJobProgress({
        pipeline,
        lines,
        stages,
        stage,
        stageIndex,
        stageTotal,
        caseIndex,
        caseTotal,
        progressMessage: progress,
        reuse,
        resumed,
      }),
    [
      pipeline,
      lines,
      stages,
      stage,
      stageIndex,
      stageTotal,
      caseIndex,
      caseTotal,
      progress,
      reuse,
      resumed,
    ],
  )

  const now = useNow(5000)
  const mountRef = useRef(Date.now())
  const [localStage, setLocalStage] = useState<{ key: string; at: number } | null>(null)

  useEffect(() => {
    if (!stage) return
    setLocalStage((current) => (current?.key === stage ? current : { key: stage, at: Date.now() }))
  }, [stage])

  const startMs = useMemo(() => {
    const created = createdAt ? Date.parse(createdAt) : Number.NaN
    if (Number.isFinite(created)) return created
    const firstLog = model.firstLogTs ? Date.parse(model.firstLogTs) : Number.NaN
    if (Number.isFinite(firstLog)) return firstLog
    return mountRef.current
  }, [createdAt, model.firstLogTs])

  const elapsedSec = Math.max(0, (now - startMs) / 1000)

  const activeKey = model.stages.find((item) => item.state === 'active')?.key ?? ''
  const stageStartMs = useMemo(() => {
    const raw = model.stageStartedAt[activeKey]
    const parsed = raw ? Date.parse(raw) : Number.NaN
    if (Number.isFinite(parsed)) return parsed
    if (localStage && localStage.key === activeKey) return localStage.at
    return Number.NaN
  }, [model.stageStartedAt, activeKey, localStage])
  const activeElapsedSec = Number.isFinite(stageStartMs)
    ? Math.max(0, (now - stageStartMs) / 1000)
    : null

  const etaSeconds = useMemo(() => computeEta(model, now), [model, now])

  const totalSteps = stageTotal || model.stages.length
  const stepNumber = Math.min(totalSteps, Math.max(stageIndex, model.activeIndex))
  const activeStage = model.stages.find((item) => item.key === activeKey)
  const latest = model.activities[0]

  function handleForceCancel() {
    if (
      onForceCancel &&
      window.confirm(
        '强制终止会立即结束该任务，且不支持续跑。\n\n' +
          '已产出的文件会保留在输出目录，但任务不会有结果页。\n\n确定要强制终止吗？',
      )
    ) {
      onForceCancel()
    }
  }

  return (
    <div className="tp">
      {resumed && (
        <div className="tp-resume">
          <span className="tp-resume__mark" aria-hidden="true" />
          <span>{resumedHint || '中断恢复执行，中断前已完成的结果会被复用'}</span>
          {reuse && reuse.reused > 0 && (
            <span className="tp-resume__count">
              已复用 {reuse.reused} 次结果，接续调用 {reuse.rerun} 次
            </span>
          )}
        </div>
      )}

      <section className="tp-hero" aria-label="任务运行状态">
        <div className="tp-hero__top">
          <div className="tp-hero__id">
            <div className={`tp-runstate${cancelRequested ? ' tp-runstate--stopping' : ''}`}>
              <span className="tp-runstate__dot" aria-hidden="true" />
              {cancelRequested ? '正在终止' : '正在执行'}
            </div>
            <h2 className="tp-hero__title">{model.activeLabel}</h2>
            <p className="tp-hero__subtitle">
              <span>{taskKindLabel(taskType)}</span>
              <span className="tp-hero__step">
                {stepNumber > 0 ? `Step ${stepNumber}/${totalSteps}` : '准备启动'}
              </span>
              {activeStage && <span className="tp-hero__desc">{activeStage.description}</span>}
            </p>
          </div>

          <div className="tp-hero__aside">
            <div className="tp-hero__meta">
              <span className="tp-meta">
                <Clock3 size={14} aria-hidden="true" />
                <span className="tp-meta__label">已用时</span>
                <strong className="tp-meta__value">{formatElapsed(elapsedSec)}</strong>
              </span>
              {tokenUsage !== null && (
                <span className="tp-meta">
                  <Coins size={14} aria-hidden="true" />
                  <span className="tp-meta__label">Token</span>
                  <strong className="tp-meta__value">
                    {tokenUsage.toLocaleString('zh-CN')}
                  </strong>
                </span>
              )}
              {etaSeconds !== null && (
                <span className="tp-meta">
                  <Clock3 size={14} aria-hidden="true" />
                  <span className="tp-meta__label">预计剩余</span>
                  <strong className="tp-meta__value">{formatDuration(etaSeconds)}</strong>
                </span>
              )}
              {mock && <span className="tp-mock">MOCK 模拟数据</span>}
            </div>

            {onCancel && (
              <div className="tp-actions">
                <button
                  type="button"
                  className="btn btn--secondary tp-actions__cancel"
                  onClick={onCancel}
                  disabled={cancelRequested}
                >
                  {cancelRequested ? '正在终止…' : '终止任务'}
                </button>
                {forceCancelAvailable && onForceCancel && (
                  <button
                    type="button"
                    className="btn btn--ghost tp-actions__force"
                    onClick={handleForceCancel}
                  >
                    强制终止
                  </button>
                )}
              </div>
            )}
          </div>
        </div>

        <StageRibbon stages={model.stages} activeElapsedSec={activeElapsedSec} />

        {onCancel && (
          <p className="tp-actions__hint">
            {forceCancelAvailable && onForceCancel
              ? '任务未在检查点停下（可能正卡在某一步里）。强制终止会立即结束该任务且不支持续跑，已产出的文件保留。'
              : cancelRequested
                ? '已请求终止，任务会在当前步骤或 Case 结束时停止；已产出的文件会保留。'
                : '终止后不删除已产出的文件，但任务不会有结果页。'}
          </p>
        )}
      </section>

      <AlertStrip alerts={model.alerts} />

      <div className="tp-grid">
        {model.itemTrack ? (
          <ItemProgress track={model.itemTrack} etaSeconds={etaSeconds} />
        ) : (
          <section className="tp-items tp-items--stage" aria-label="当前阶段">
            <h3 className="tp-section-title">{model.activeLabel}</h3>
            <p className="tp-items__summary">
              {activeStage?.description || '任务正在执行，请稍候'}
            </p>
            <div className="tp-stage-current">
              <span className="tp-stage-current__pulse" aria-hidden="true" />
              <span>
                <strong>{latest?.title || progress || '正在处理'}</strong>
                {latest?.detail && <span>{latest.detail}</span>}
              </span>
            </div>
            <div
              className="tp-bar tp-bar--indeterminate"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="当前阶段进行中"
            >
              <span className="tp-bar__fill" />
            </div>
            <p className="tp-stage-hint">
              本阶段没有逐条条目；进入 SWHLR 标注、裁判或复核阶段后，会显示每条的处理进度。
            </p>
          </section>
        )}

        <TaskMetrics
          metrics={model.metrics}
          distribution={model.distribution}
          stages={model.stages}
          activeElapsedSec={activeElapsedSec}
          labelModel={model.labelModel}
          judgeModels={model.judgeModels}
        />
        <ActivityFeed activities={model.activities} />
      </div>

      {truncated && (
        <p className="tp-foot">
          任务日志过长，早期明细可能未纳入本视图；条目计数以任务状态为准。
        </p>
      )}
    </div>
  )
}
