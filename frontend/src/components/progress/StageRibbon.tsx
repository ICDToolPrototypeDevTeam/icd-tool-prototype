import { useEffect, useRef, useState } from 'react'
import { Check } from 'lucide-react'
import type { StageView } from './parseJobProgress'
import { formatDuration } from './progressFormat'

interface Props {
  stages: StageView[]
  activeElapsedSec: number | null
}

/** 阶段脉冲带：整条任务被拆成一条可读的轨道，而不是一个抽象的百分比。 */
export default function StageRibbon({ stages, activeElapsedSec }: Props) {
  const listRef = useRef<HTMLOListElement>(null)
  const [scrolled, setScrolled] = useState(false)
  const activeKey = stages.find((stage) => stage.state === 'active')?.key

  useEffect(() => {
    const rail = listRef.current
    if (!rail) return
    const active = rail.querySelector<HTMLElement>('[aria-current="step"]')
    if (!active) return
    // 移动端阶段带横向滚动：把当前阶段滚到可视区中间，避免用户只能看到前几步。
    const railRect = rail.getBoundingClientRect()
    const activeRect = active.getBoundingClientRect()
    const delta = activeRect.left - railRect.left - rail.clientWidth / 2 + activeRect.width / 2
    const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      ? 'auto'
      : 'smooth'
    rail.scrollTo({ left: Math.max(0, rail.scrollLeft + delta), behavior })
  }, [activeKey])

  return (
    <ol
      className={`tp-stages${scrolled ? ' tp-stages--scrolled' : ''}`}
      aria-label="任务阶段"
      ref={listRef}
      onScroll={(event) => setScrolled(event.currentTarget.scrollLeft > 4)}
    >
      {stages.map((stage) => {
        const isActive = stage.state === 'active'
        const duration = isActive ? activeElapsedSec : stage.durationSec
        return (
          <li
            key={stage.key}
            className={`tp-stage tp-stage--${stage.state}`}
            aria-current={isActive ? 'step' : undefined}
          >
            <span className="tp-stage__rail" aria-hidden="true" />
            <span className="tp-stage__node" aria-hidden="true">
              {stage.state === 'done' ? <Check size={13} strokeWidth={3} /> : stage.index}
            </span>
            <span className="tp-stage__text">
              <span className="tp-stage__label">{stage.label}</span>
              <span className="tp-stage__meta">
                {isActive
                  ? duration !== null
                    ? `进行中，${formatDuration(duration)}`
                    : '进行中'
                  : stage.state === 'done'
                    ? duration !== null
                      ? `用时 ${formatDuration(duration)}`
                      : '已完成'
                    : '待开始'}
              </span>
            </span>
          </li>
        )
      })}
    </ol>
  )
}
