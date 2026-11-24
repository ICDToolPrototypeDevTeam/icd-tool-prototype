/** 把任务日志解析成结构化进度模型。
 *
 * 日志文案不是稳定 API 契约，因此所有正则都集中在本文件：
 * - 只识别已知模式，未知行一律忽略（界面上不再出现原始日志）；
 * - 解析失败时静默降级，页面用状态接口的阶段/Case 进度兜底；
 * - 进程重启后从 job.log 恢复的行没有 ts，ETA 等依赖时间戳的派生信息会自动隐藏。
 */
import type { V4LogLine } from '../../types'
import type { PipelineKind, StageMeta } from './stageMeta'
import { stageKeyFromStep, stageKeyFromTimingLabel } from './stageMeta'

export type ItemOutcome = '一致' | '不一致' | '待确认' | '无匹配' | '异常' | '未知'
export type TrackKind = 'label' | 'case' | 'judge'
export type ActivityKind = 'stage' | 'item' | 'result' | 'warning' | 'error' | 'info'

export interface ProgressItem {
  id: string
  order: number
  judged: boolean
  reviewed: boolean
  cached: boolean
  running: boolean
  outcome?: ItemOutcome
  stars?: number
  providers?: Array<{ provider: string; status: string }>
}

export interface ItemTrack {
  kind: TrackKind
  title: string
  done: number
  total: number
  items: ProgressItem[]
  recent: ProgressItem[]
  /** 当前阶段用哪个字段计完成数：标注 = 全部条目，裁判 = judged，复核 = reviewed */
  countedBy: 'all' | 'judged' | 'reviewed'
  /** 日志里没有逐条明细，只剩状态接口给出的完成数 */
  fallbackOnly: boolean
  /** 当前阶段的完成时间戳（ms），用于估算剩余时间 */
  completionTimes: number[]
}

export interface ActivityEvent {
  seq: number
  ts: string
  kind: ActivityKind
  title: string
  detail?: string
}

export interface ProgressAlert {
  seq: number
  level: 'warning' | 'error'
  text: string
}

export interface Metric {
  key: string
  label: string
  value: string
  hint?: string
}

export interface DistributionSegment {
  key: string
  label: string
  value: number
  tone: 'blue' | 'green' | 'amber' | 'red' | 'muted'
}

export interface StageView extends StageMeta {
  state: 'done' | 'active' | 'pending'
  durationSec: number | null
}

export interface JobProgressModel {
  stages: StageView[]
  activeIndex: number
  activeLabel: string
  itemTrack: ItemTrack | null
  metrics: Metric[]
  distribution: { title: string; segments: DistributionSegment[] } | null
  activities: ActivityEvent[]
  alerts: ProgressAlert[]
  labelModel: string | null
  judgeModels: string[]
  stageDurations: Record<string, number>
  stageStartedAt: Record<string, string>
  firstLogTs: string | null
}

export interface ParseInput {
  pipeline: PipelineKind
  lines: V4LogLine[]
  stages: StageMeta[]
  stage: string
  stageIndex: number
  stageTotal: number
  caseIndex: number
  caseTotal: number
  progressMessage: string
  reuse: { reused: number; rerun: number } | null
  /** 续跑任务的日志里可能残留上一次运行的阶段横幅：当前阶段计时取最后一次。 */
  resumed?: boolean
}

const MAX_ACTIVITIES = 80
const MAX_ALERTS = 20
const MAX_RECENT = 24

function tsToMs(ts: string): number | null {
  if (!ts) return null
  const value = Date.parse(ts)
  return Number.isFinite(value) ? value : null
}

function formatNumber(value: number): string {
  return value.toLocaleString('zh-CN')
}

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total} 秒`
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (hours > 0) return `${hours} 小时 ${minutes} 分`
  const rest = total % 60
  return rest > 0 ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`
}

function normalizeOutcome(raw: string | undefined): ItemOutcome {
  if (!raw) return '未知'
  // 后端裁判的 coverage_status 是英文（covered / inconsistent / needs_review），
  // 共识日志直接透传该值；报告层才翻成中文。这里必须两种都认。
  const value = raw.trim().toLowerCase()
  if (value.includes('无匹配') || /unmatched|no[_-]?match/.test(value)) return '无匹配'
  if (value.includes('不一致') || value.includes('inconsistent')) return '不一致'
  if (
    value.includes('待确认') ||
    value.includes('needs_review') ||
    value.includes('needs review')
  ) {
    return '待确认'
  }
  if (value.includes('一致') || value.includes('covered')) return '一致'
  if (value.includes('error') || value.includes('fail')) return '异常'
  return '未知'
}

function parseProviders(text: string): Array<{ provider: string; status: string }> {
  const pairs: Array<{ provider: string; status: string }> = []
  const re = /'([^']+)'\s*:\s*'([^']*)'/g
  let match: RegExpExecArray | null
  while ((match = re.exec(text)) !== null) {
    pairs.push({ provider: match[1], status: match[2] })
  }
  return pairs
}

export function formatProviderStatus(status: string): string {
  if (!status || status === '?') return '未知'
  if (/error|fail/i.test(status)) return '调用失败'
  const normalized = normalizeOutcome(status)
  return normalized === '未知' ? status : normalized
}

function summarizeProviders(pairs: Array<{ provider: string; status: string }>): string {
  if (pairs.length === 0) return '裁判结果已返回'
  const counts = new Map<string, number>()
  for (const pair of pairs) {
    const key = formatProviderStatus(pair.status)
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  return Array.from(counts.entries())
    .map(([status, count]) => `${count} 个${status}`)
    .join('，')
}

function pushRecent(queue: string[], id: string): void {
  const existing = queue.indexOf(id)
  if (existing >= 0) queue.splice(existing, 1)
  queue.push(id)
  if (queue.length > MAX_RECENT) queue.shift()
}

export function parseJobProgress(input: ParseInput): JobProgressModel {
  const {
    pipeline,
    lines,
    stages,
    stage: statusStage,
    caseIndex,
    caseTotal: statusCaseTotal,
    progressMessage,
    reuse,
    resumed = false,
  } = input

  const labelItems = new Map<string, ProgressItem>()
  const caseItems = new Map<string, ProgressItem>()
  const judgeItems = new Map<string, ProgressItem>()

  let labelTotal = 0
  let labelCacheHits = 0
  let caseTotalParsed = 0
  let judgeTotalParsed = 0
  let judgedCount = 0
  let reviewedCount = 0
  let judgeDone = 0

  let eoicdFinal: number | null = null
  let eoicdRaw: number | null = null
  let duplicatesRemoved: number | null = null
  let hlrTotal: number | null = null
  let glossaryCount: number | null = null
  let termCount: number | null = null
  let glossaryHits: { hit: number; total: number } | null = null

  const labelRecent: string[] = []
  const multiRecent: string[] = []
  const reviewRecent: string[] = []
  const judgeRecent: string[] = []

  const labelTimes: number[] = []
  const multiTimes: number[] = []
  const reviewTimes: number[] = []
  const judgeTimes: number[] = []

  const activities: ActivityEvent[] = []
  const alerts: ProgressAlert[] = []
  const timingByLabel = new Map<string, number>()
  const stageFirstStartedAt: Record<string, string> = {}
  const stageLastStartedAt: Record<string, string> = {}
  const judgeModels = new Set<string>()
  let labelModel: string | null = null

  let bannerStageKey = ''
  let lastAnnouncedStage = ''
  let firstLogTs: string | null = null
  let eoicdActivityDone = false
  let hlrActivityDone = false
  let matchActivityDone = false

  function addActivity(seq: number, ts: string, kind: ActivityKind, title: string, detail?: string) {
    activities.push({ seq, ts, kind, title, detail })
    if (activities.length > MAX_ACTIVITIES) activities.shift()
  }

  function addAlert(seq: number, level: 'warning' | 'error', text: string) {
    if (alerts.some((item) => item.text === text)) return
    alerts.push({ seq, level, text })
    if (alerts.length > MAX_ALERTS) alerts.shift()
  }

  function ensureCaseItem(id: string): ProgressItem {
    let item = caseItems.get(id)
    if (!item) {
      item = {
        id,
        order: caseItems.size,
        judged: false,
        reviewed: false,
        cached: false,
        running: false,
      }
      caseItems.set(id, item)
    }
    return item
  }

  for (const rawLine of lines) {
    let text = (rawLine.text ?? '').replace(/\r$/, '')
    let effectiveTs = rawLine.ts

    // Celery / 进程重启后从 job.log 同步来的行，text 里带着 "ISO [level] " 前缀；
    // 剥掉前缀，并把前缀时间当作该行时间戳（内存 buffer 恢复时 ts 为空）。
    const persisted = text.match(/^(\d{4}-\d{2}-\d{2}T\S+)\s+\[[^\]]+\]\s+(.*)$/)
    if (persisted) {
      if (!effectiveTs) effectiveTs = persisted[1]
      text = persisted[2]
    }

    if (!text.trim()) continue
    const line = effectiveTs === rawLine.ts ? rawLine : { ...rawLine, ts: effectiveTs }
    if (!firstLogTs && line.ts) firstLogTs = line.ts

    const lineTsMs = tsToMs(line.ts)

    // ---------- 阶段横幅 ----------
    // 必须从行首匹配：`  [timing] Step 1/6: ...` 是「上一步耗时」，
    // 若被当成横幅会把当前阶段回退到上一步（Step 2 之后被 Step 1 timing 覆盖）。
    const stepMatch = text.match(/^\s*Step\s+(\d+(?:\.\d+)?)\s*\/\s*(\d+)/)
    if (stepMatch) {
      const stepIndexRaw = Number(stepMatch[1])
      const stepIndex = Math.floor(stepIndexRaw)
      const stepTotal = Number(stepMatch[2])
      const key = stageKeyFromStep(pipeline, stepIndex, stepTotal)
      if (key) {
        bannerStageKey = key
        if (line.ts) {
          if (!stageFirstStartedAt[key]) stageFirstStartedAt[key] = line.ts
          stageLastStartedAt[key] = line.ts
        }
        if (lastAnnouncedStage !== key) {
          lastAnnouncedStage = key
          const meta = stages.find((item) => item.key === key)
          addActivity(line.seq, line.ts, 'stage', `进入${meta?.label ?? key}`, `Step ${stepIndexRaw}/${stepTotal}`)
        }
      }
    }

    // ---------- 解析摘要 ----------
    const eoicdRawMatch = text.match(/Generated \(before any dedup\):\s*(\d+)/)
    if (eoicdRawMatch) eoicdRaw = Number(eoicdRawMatch[1])

    const eoicdFinalMatch = text.match(/After global dedup:\s*(\d+)/)
    if (eoicdFinalMatch) {
      eoicdFinal = Number(eoicdFinalMatch[1])
      if (!eoicdActivityDone) {
        eoicdActivityDone = true
        addActivity(
          line.seq,
          line.ts,
          'info',
          'EoICD 解析完成',
          `去重后 ${formatNumber(eoicdFinal)} 条`,
        )
      }
    }

    const dupMatch = text.match(/Duplicates removed:\s*(\d+)/)
    if (dupMatch) duplicatesRemoved = Number(dupMatch[1])

    const hlrMatch = text.match(/Requirements:\s*(\d+)/)
    if (hlrMatch) {
      hlrTotal = Number(hlrMatch[1])
      if (!hlrActivityDone) {
        hlrActivityDone = true
        addActivity(line.seq, line.ts, 'info', 'SWHLR 解析完成', `${formatNumber(hlrTotal)} 条需求`)
      }
    }

    const glossaryMatch = text.match(/Glossary entries:\s*(\d+)/)
    if (glossaryMatch) glossaryCount = Number(glossaryMatch[1])

    const termMatch = text.match(/Terminology entries:\s*(\d+)/)
    if (termMatch) termCount = Number(termMatch[1])

    const glossaryHitMatch = text.match(/Glossary hits:\s*(\d+)\/(\d+)/)
    if (glossaryHitMatch) {
      glossaryHits = { hit: Number(glossaryHitMatch[1]), total: Number(glossaryHitMatch[2]) }
      addActivity(
        line.seq,
        line.ts,
        'info',
        '文档术语索引完成',
        `${glossaryHits.hit}/${glossaryHits.total} 个 Case 命中术语或缩略语`,
      )
    }

    // ---------- SWHLR 标注 ----------
    const labelStart = text.match(/\[label\]\s+Labeling\s+(\d+)\s+SWHLRs(?:\s+via\s+(.+?))?\.\.\./)
    if (labelStart) {
      labelTotal = Number(labelStart[1])
      if (labelStart[2]) {
        labelModel = labelStart[2].trim()
        addActivity(
          line.seq,
          line.ts,
          'info',
          '开始逐条标注 SWHLR',
          `共 ${labelTotal} 条，模型 ${labelModel}`,
        )
      }
    }

    const labelItem = text.match(/^\s*\[label\]\s+(\d+)\/(\d+)\s+(\S+)\s*(.*)$/)
    if (labelItem && !text.includes('Labeling')) {
      const total = Number(labelItem[2])
      const id = labelItem[3]
      const rest = labelItem[4] ?? ''
      if (total > 0) labelTotal = Math.max(labelTotal, total)
      const cached = /\bHIT\b/.test(rest)
      let item = labelItems.get(id)
      if (!item) {
        item = {
          id,
          order: labelItems.size,
          judged: true,
          reviewed: false,
          cached,
          running: false,
        }
        labelItems.set(id, item)
        if (lineTsMs !== null) labelTimes.push(lineTsMs)
        pushRecent(labelRecent, id)
        // 逐条标注不进入实时动态：条目面板的矩阵和最近完成卡片已经承载了
        // 这部分信息，几十上百条「已标注」会把真正有用的事件挤出视野。
      } else if (cached) {
        item.cached = true
      }
    }

    const labelCache = text.match(/\[label\]\s+Cache:\s*(\d+)\s+hit/)
    if (labelCache) {
      labelCacheHits = Number(labelCache[1])
      addActivity(line.seq, line.ts, 'info', '标注缓存命中', `${labelCacheHits} 条结果直接复用`)
    }

    const labelInvalid = text.match(/\[label\]\s+Cache invalid/)
    if (labelInvalid) {
      addActivity(line.seq, line.ts, 'warning', '标注缓存已失效', '正在重新标注 SWHLR')
    }

    if (/\[label\].*(JSON parse error|API error)/.test(text)) {
      const id = text.match(/\[label\]\s+(\S+?):/)?.[1]
      addAlert(line.seq, 'warning', `${id ?? 'SWHLR'} 标注失败，已使用兜底标签`)
    }
    if (/\[label\].*using fallback/.test(text)) {
      addAlert(line.seq, 'warning', '部分 SWHLR 标注失败，已使用兜底结果')
    }

    // ---------- 匹配统计 ----------
    const statsMatch = text.match(/Stats:\s*(\{.*\})\s*$/)
    if (statsMatch) {
      const stats: Record<string, number> = {}
      const pairRe = /'([^']+)'\s*:\s*(\d+)/g
      let pair: RegExpExecArray | null
      while ((pair = pairRe.exec(statsMatch[1])) !== null) {
        stats[pair[1]] = Number(pair[2])
      }
      if (Object.keys(stats).length > 0) {
        if (!matchActivityDone) {
          matchActivityDone = true
          const matched = stats['hlr_已匹配'] ?? 0
          const pending = stats['hlr_待确定'] ?? 0
          const unmatched = stats['hlr_无匹配'] ?? 0
          addActivity(
            line.seq,
            line.ts,
            'info',
            '反向匹配完成',
            `${matched} 条已匹配，${pending} 条待确定，${unmatched} 条无匹配`,
          )
        }
      }
    }

    // ---------- 多智能体裁判 ----------
    const caseBanner = text.match(/\((\d+)\s+(?:cases|judges?),\s*providers=(\[.*?\])\)/)
    if (caseBanner) {
      const bannerCount = Number(caseBanner[1])
      if (pipeline === 'forward') {
        judgeTotalParsed = Math.max(judgeTotalParsed, bannerCount)
      } else {
        caseTotalParsed = Math.max(caseTotalParsed, bannerCount)
      }
      for (const model of caseBanner[2].matchAll(/['"]([^'"]+)['"]/g)) {
        judgeModels.add(model[1])
      }
    }

    const multiMatch = text.match(/^\s*\[multi\]\s+(\S+)\s+\((\d+)\/(\d+)\)\s+(.+)$/)
    if (multiMatch) {
      const id = multiMatch[1]
      const index = Number(multiMatch[2])
      const total = Number(multiMatch[3])
      const providers = parseProviders(multiMatch[4])
      if (total > 0) caseTotalParsed = Math.max(caseTotalParsed, total)
      const item = ensureCaseItem(id)
      if (!item.judged) {
        item.judged = true
        judgedCount += 1
        if (lineTsMs !== null) multiTimes.push(lineTsMs)
        pushRecent(multiRecent, id)
        addActivity(
          line.seq,
          line.ts,
          'result',
          `Case ${id} 裁判完成`,
          `${index}/${total}，${summarizeProviders(providers)}`,
        )
      }
      if (providers.length > 0) item.providers = providers
    }

    const cacheHit = text.match(/\[cache\]\s+(\S+)\s+hit=(\d+)\/(\d+)/)
    if (cacheHit) {
      const item = caseItems.get(cacheHit[1])
      if (item) item.cached = true
    }

    // ---------- 共识复核 ----------
    const reviewMatch = text.match(/^\s*\[review\]\s+(\S+)\s+\((\d+)\/(\d+)\)\s+(.*)$/)
    if (reviewMatch) {
      const id = reviewMatch[1]
      const index = Number(reviewMatch[2])
      const total = Number(reviewMatch[3])
      const rest = reviewMatch[4]
      if (total > 0) caseTotalParsed = Math.max(caseTotalParsed, total)
      const starsMatch = rest.match(/stars=(\d+)/)
      const starValue = starsMatch ? Number(starsMatch[1]) : undefined
      const outcome = normalizeOutcome(rest.match(/→\s*(\S+)/)?.[1])
      const cached = /\bHIT\b/.test(rest)
      const item = ensureCaseItem(id)
      if (!item.reviewed) {
        item.reviewed = true
        reviewedCount += 1
        if (lineTsMs !== null) reviewTimes.push(lineTsMs)
        pushRecent(reviewRecent, id)
        const parts = [`${index}/${total}`]
        if (starValue !== undefined) parts.push(`${starValue} 星`)
        parts.push(outcome)
        addActivity(line.seq, line.ts, 'result', `Case ${id} 共识完成`, parts.join('，'))
      }
      item.stars = starValue
      item.outcome = outcome
      if (cached) item.cached = true
    }

    const reReviewMatch = text.match(/^\s*\[re-review\]\s+(\S+?):/)
    if (reReviewMatch) {
      const item = caseItems.get(reReviewMatch[1])
      if (item) {
        addActivity(line.seq, line.ts, 'info', `Case ${item.id} 完成低星复查`, '已重新征求三个模型的意见')
      }
    }

    // ---------- 正向 judge ----------
    const judgeStart = text.match(/^>?\s*judge\[(\d+)\]\s+(\S+)(?:\s+\(([^)]+)\))?/)
    if (judgeStart) {
      const index = Number(judgeStart[1])
      const id = judgeStart[2]
      if (judgeStart[3]) judgeModels.add(judgeStart[3].trim())
      judgeTotalParsed = Math.max(judgeTotalParsed, index)
      let item = judgeItems.get(id)
      if (!item) {
        item = {
          id,
          order: judgeItems.size,
          judged: false,
          reviewed: false,
          cached: false,
          running: true,
        }
        judgeItems.set(id, item)
        addActivity(line.seq, line.ts, 'stage', `Judge ${id} 开始判定`, `第 ${index} 个 judge`)
      } else {
        item.running = true
      }
    }

    const judgeDoneMatch = text.match(/judge\s+(\S+)\s+完成（/)
    if (judgeDoneMatch) {
      const id = judgeDoneMatch[1]
      let item = judgeItems.get(id)
      if (!item) {
        item = {
          id,
          order: judgeItems.size,
          judged: false,
          reviewed: false,
          cached: false,
          running: false,
        }
        judgeItems.set(id, item)
      }
      if (!item.judged) {
        item.judged = true
        item.running = false
        judgeDone += 1
        if (lineTsMs !== null) judgeTimes.push(lineTsMs)
        pushRecent(judgeRecent, id)
        addActivity(
          line.seq,
          line.ts,
          'result',
          `Judge ${id} 判定完成`,
          `${judgeDone}/${judgeTotalParsed || '?'}`,
        )
      }
    }

    const aggMatch = text.match(/多 judge 聚合完成：(\d+)\/(\d+)/)
    if (aggMatch) {
      addActivity(
        line.seq,
        line.ts,
        'info',
        '多 judge 聚合完成',
        `${aggMatch[1]}/${aggMatch[2]} 个 judge 正常产出`,
      )
    }

    // ---------- 步骤耗时 ----------
    const timingMatch = text.match(/\[timing\]\s+(.+?):\s+([\d.]+)s/)
    if (timingMatch) {
      const timingLabel = timingMatch[1]
      const key = stageKeyFromTimingLabel(pipeline, timingLabel)
      if (key) {
        const seconds = Number(timingMatch[2])
        timingByLabel.set(timingLabel, seconds)
        const meta = stages.find((item) => item.key === key)
        // 只为主线步骤发声：4.5 / 5.5 / 5.6 的耗时并入所属阶段，避免同一阶段
        // 在动态里连报三次「完成」。
        if (meta && !/Step\s+\d+\.\d+/.test(timingLabel)) {
          addActivity(
            line.seq,
            line.ts,
            'info',
            `${meta.label}完成`,
            `本段用时 ${formatDuration(seconds)}`,
          )
        }
      }
    }

    // ---------- 提示与异常 ----------
    if (/\[fix\]/.test(text)) {
      addActivity(line.seq, line.ts, 'info', '已自动修正上传文件结构', '文件包条目名不规范，已就地规范化')
    }

    if (/\[tokens\].*不一致/.test(text)) {
      addActivity(line.seq, line.ts, 'info', 'Token 统计已校正', '实时增量与汇总增量不一致，取较大值')
    }

    if (/\[degradation\]/.test(text)) {
      const caseId = text.match(/\b(case[-_][\w-]+|hlr[-_][\w-]+)/i)?.[1]
      addAlert(
        line.seq,
        'warning',
        caseId ? `${caseId} 触发模型降级，已按可用模型收紧结论` : '模型调用触发降级，部分结论已收紧',
      )
    }

    if (/\[HARD-LIMIT\]/.test(text)) {
      addAlert(line.seq, 'error', 'judge 子进程超过硬上限，已被终止')
    }

    if (/\[Popen 启动失败\]/.test(text)) {
      addAlert(line.seq, 'error', 'judge 子进程启动失败，该 judge 已跳过')
    }

    if (/跳过：无聚合结果（0 个信号）/.test(text)) {
      addAlert(line.seq, 'error', '正向聚合结果为空，报告已跳过')
    }

    if (/Traceback \(most recent call last\)/.test(text)) {
      addAlert(line.seq, 'error', '执行过程中出现异常堆栈，任务可能已降级或失败')
    }
  }

  // ---------- 阶段状态 ----------
  // 同一阶段可能有多个计时片段（例如复核 = Step 5 + 5.5 + 5.6）：
  // 按计时标签取最后一次的值再求和，既累计子步骤，又不会被续跑旧日志重复计算。
  const stageDurations: Record<string, number> = {}
  for (const [label, seconds] of timingByLabel) {
    const key = stageKeyFromTimingLabel(pipeline, label)
    if (key) stageDurations[key] = (stageDurations[key] ?? 0) + seconds
  }

  // 状态接口与日志横幅取「更靠后」的一个：
  // - Step 5 / 5.5 / 5.6 / 6 没有调用 set_progress，status 的 stage 会停在
  //   multi_judge（后端另有修复），日志横幅能把它推进到 review / report；
  // - 日志被截断或延迟时，status 更可靠。
  // 续跑任务的日志可能残留上一次运行的横幅，此时以 status 为准。
  const statusMeta = stages.find((item) => item.key === statusStage)
  const bannerMeta = bannerStageKey
    ? stages.find((item) => item.key === bannerStageKey)
    : undefined
  let activeMeta = statusMeta ?? bannerMeta
  if (!resumed && bannerMeta && statusMeta && bannerMeta.index > statusMeta.index) {
    activeMeta = bannerMeta
  }
  const activeKey = activeMeta?.key ?? ''
  const activeIndex = activeMeta ? Math.min(stages.length, activeMeta.index) : 0

  const stageViews: StageView[] = stages.map((meta) => {
    let state: StageView['state'] = 'pending'
    if (activeKey && meta.index < activeIndex) state = 'done'
    else if (activeKey && meta.index === activeIndex) state = 'active'
    return {
      ...meta,
      state,
      durationSec: stageDurations[meta.key] ?? null,
    }
  })

  // ---------- 当前阶段的条目轨迹 ----------
  let itemTrack: ItemTrack | null = null

  if (activeKey === 'label') {
    const total = labelTotal || labelItems.size
    const items = Array.from(labelItems.values())
    itemTrack = {
      kind: 'label',
      title: 'SWHLR 标注',
      done: labelItems.size,
      total,
      items,
      recent: labelRecent
        .map((id) => labelItems.get(id))
        .filter((item): item is ProgressItem => Boolean(item)),
      countedBy: 'all',
      fallbackOnly: false,
      completionTimes: labelTimes,
    }
  } else if (activeKey === 'multi_judge' || activeKey === 'review') {
    if (pipeline === 'forward') {
      if (activeKey === 'multi_judge') {
        const total = Math.max(statusCaseTotal || 0, judgeTotalParsed || 0, judgeItems.size)
        const statusDone = statusCaseTotal > 0 ? Math.min(caseIndex || 0, total) : 0
        const done = Math.max(judgeDone, statusDone)
        itemTrack = {
          kind: 'judge',
          title: 'Judge 判定',
          done,
          total,
          items: Array.from(judgeItems.values()),
          recent: judgeRecent
            .map((id) => judgeItems.get(id))
            .filter((item): item is ProgressItem => Boolean(item)),
          countedBy: 'judged',
          fallbackOnly: judgeItems.size === 0 && done > 0,
          completionTimes: judgeTimes,
        }
      }
    } else {
      const total = Math.max(statusCaseTotal || 0, caseTotalParsed || 0, caseItems.size)
      const items = Array.from(caseItems.values())
      if (activeKey === 'multi_judge') {
        const statusDone = statusCaseTotal > 0 ? Math.min(caseIndex || 0, total) : 0
        const done = Math.max(judgedCount, statusDone)
        itemTrack = {
          kind: 'case',
          title: '裁判 Case',
          done,
          total,
          items,
          recent: multiRecent
            .map((id) => caseItems.get(id))
            .filter((item): item is ProgressItem => Boolean(item)),
          countedBy: 'judged',
          fallbackOnly: caseItems.size === 0 && done > 0,
          completionTimes: multiTimes,
        }
      } else {
        itemTrack = {
          kind: 'case',
          title: '复核 Case',
          done: reviewedCount,
          total,
          items,
          recent: reviewRecent
            .map((id) => caseItems.get(id))
            .filter((item): item is ProgressItem => Boolean(item)),
          countedBy: 'reviewed',
          fallbackOnly: caseItems.size === 0 && reviewedCount > 0,
          completionTimes: reviewTimes,
        }
      }
    }
  }

  // ---------- 指标 ----------
  const metrics: Metric[] = []
  if (eoicdFinal !== null) {
    metrics.push({
      key: 'eoicd',
      label: 'EoICD 条目',
      value: formatNumber(eoicdFinal),
      hint:
        duplicatesRemoved !== null && duplicatesRemoved > 0
          ? `已去除 ${formatNumber(duplicatesRemoved)} 条重复`
          : eoicdRaw !== null && eoicdRaw !== eoicdFinal
            ? `原始生成 ${formatNumber(eoicdRaw)} 条`
            : undefined,
    })
  }
  if (hlrTotal !== null) {
    metrics.push({
      key: 'hlr',
      label: 'SWHLR 需求',
      value: formatNumber(hlrTotal),
      hint:
        glossaryCount !== null || termCount !== null
          ? `术语 ${formatNumber(glossaryCount ?? 0)} 条，缩略语 ${formatNumber(termCount ?? 0)} 条`
          : undefined,
    })
  }
  const effectiveCaseTotal = Math.max(statusCaseTotal || 0, caseTotalParsed || 0, judgeTotalParsed || 0)
  if (effectiveCaseTotal > 0) {
    metrics.push({
      key: 'cases',
      label: pipeline === 'forward' ? 'Judge 数量' : 'Case 数量',
      value: formatNumber(effectiveCaseTotal),
    })
  }
  if (pipeline === 'forward' && activeKey === 'multi_judge') {
    const runningJudge = Array.from(judgeItems.values()).find((item) => item.running)
    if (runningJudge) {
      metrics.push({ key: 'running-judge', label: '当前 Judge', value: runningJudge.id })
    }
  }
  const reuseCount = reuse?.reused ?? 0
  const cacheCount = reuseCount > 0 ? reuseCount : labelCacheHits
  if (cacheCount > 0) {
    metrics.push({
      key: 'cache',
      label: reuseCount > 0 ? '缓存复用' : '标注缓存命中',
      value: formatNumber(cacheCount),
      hint: reuse && reuse.rerun > 0 ? `本次模型调用 ${formatNumber(reuse.rerun)} 次` : undefined,
    })
  }
  if (glossaryHits) {
    metrics.push({
      key: 'glossary',
      label: '术语命中',
      value: `${formatNumber(glossaryHits.hit)}/${formatNumber(glossaryHits.total)}`,
    })
  }
  const reviewedWithStars = Array.from(caseItems.values()).filter(
    (item) => item.reviewed && item.stars !== undefined,
  )
  if (reviewedWithStars.length >= 3) {
    const average =
      reviewedWithStars.reduce((sum, item) => sum + (item.stars ?? 0), 0) / reviewedWithStars.length
    metrics.push({
      key: 'stars',
      label: '平均星级',
      value: `${average.toFixed(1)} ★`,
      hint: `基于 ${reviewedWithStars.length} 个已复核 Case`,
    })
  }

  // ---------- 分布 ----------
  let distribution: JobProgressModel['distribution'] = null
  const outcomeCounts = { 一致: 0, 待确认: 0, 不一致: 0, 无匹配: 0, 异常: 0, 未知: 0 }
  for (const item of caseItems.values()) {
    if (!item.reviewed || !item.outcome) continue
    outcomeCounts[item.outcome] += 1
  }
  const reviewedTotal = Array.from(caseItems.values()).filter((item) => item.reviewed).length
  if (reviewedTotal > 0) {
    const allSegments: DistributionSegment[] = [
      { key: 'match', label: '一致', value: outcomeCounts.一致, tone: 'green' },
      { key: 'pending', label: '待确认', value: outcomeCounts.待确认, tone: 'amber' },
      { key: 'diff', label: '不一致', value: outcomeCounts.不一致, tone: 'red' },
      { key: 'nomatch', label: '无匹配', value: outcomeCounts.无匹配, tone: 'muted' },
      { key: 'error', label: '异常', value: outcomeCounts.异常 + outcomeCounts.未知, tone: 'red' },
    ]
    const segments = allSegments.filter((segment) => segment.value > 0)
    distribution = { title: '复核结果分布', segments }
  }

  // ---------- 动态 ----------
  // 最新事件在前；同一 seq 只保留最后一次（阶段横幅等偶尔会重复打印）
  const seenSeq = new Set<number>()
  const feed: ActivityEvent[] = []
  for (let i = activities.length - 1; i >= 0; i -= 1) {
    const event = activities[i]
    if (seenSeq.has(event.seq)) continue
    seenSeq.add(event.seq)
    feed.push(event)
  }

  const stageStartedAt = resumed ? stageLastStartedAt : stageFirstStartedAt

  return {
    stages: stageViews,
    activeIndex,
    activeLabel:
      activeMeta?.label ??
      (progressMessage ? progressMessage.replace(/^Step\s+[\d.]+\/\d+:\s*/, '') : '任务正在处理'),
    itemTrack,
    metrics,
    distribution,
    activities: feed,
    alerts,
    labelModel,
    judgeModels: Array.from(judgeModels),
    stageDurations,
    stageStartedAt,
    firstLogTs,
  }
}
