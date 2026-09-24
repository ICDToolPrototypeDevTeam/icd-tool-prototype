import type { ReactNode } from 'react'

interface Props {
  progress?: string
  stage?: string
  stageIndex?: number
  stageTotal?: number
  caseIndex?: number
  caseTotal?: number
  /** 本次运行是否为中断后的恢复运行 */
  resumed?: boolean
  /** 恢复运行的实时复用计数（按模型调用次数计：缓存复用 / 接续调用） */
  reuse?: { reused: number; rerun: number } | null
  /** 恢复横幅文案；不传用默认中性文案（正向管线无判定缓存） */
  resumedHint?: string
  /** 请求终止任务；不传则不显示终止按钮 */
  onCancel?: () => void
  /** 已请求终止，等待管线在检查点停止 */
  cancelRequested?: boolean
  /** 强制终止：不等检查点，后端立即置终态并中断该任务的执行线程 */
  onForceCancel?: () => void
  /** 已请求终止但迟迟没停住 —— 此时才允许「强制终止」 */
  forceCancelAvailable?: boolean
  /** 日志面板等附加内容 */
  children?: ReactNode
}

export const STAGE_LABELS: Record<string, string> = {
  parse: '解析文件',
  label: 'HLR标注',
  match: '反向匹配',
  multi_judge: '多模型裁判',
  review: '共识复核',
  report: '报告生成',
  done: '完成',
  // 正向（完整性分析）8 步
  scope: '追溯范围',
  blocks: '构建业务对象块',
  identity_index: '构建HLR身份索引',
  candidate_recall: '候选召回',
  deterministic: '确定性判定',
  ai_review: 'AI三态复核',
}

export default function ProcessingView({
  progress,
  stage,
  stageIndex,
  stageTotal,
  caseIndex,
  caseTotal,
  resumed,
  reuse,
  resumedHint,
  onCancel,
  cancelRequested,
  onForceCancel,
  forceCancelAvailable,
  children,
}: Props) {
  const stageLabel = stage ? STAGE_LABELS[stage] || stage : null
  const hasV4Progress = stageLabel && stageTotal !== undefined && stageIndex !== undefined

  /** 强制终止不可续跑，且已产出的文件不再通过结果页交付——按破坏性动作确认一次。 */
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
    <div className="processing-state">
      {resumed && (
        <div className="processing-resumed">
          {resumedHint || '中断恢复执行 · 中断前已完成的分析结果将被复用'}
        </div>
      )}
      <div className="processing-spinner" />
      <div className="processing-title">
        {progress || '任务正在处理'}
      </div>
      {hasV4Progress && (
        <div className="processing-subtitle">
          <div className="processing-stage">
            {stageLabel}
            {stageTotal > 0 && (
              <span className="processing-step"> · Step {stageIndex}/{stageTotal}</span>
            )}
          </div>
          {caseTotal !== undefined && caseTotal > 0 && (
            <div className="processing-case">
              Case {caseIndex}/{caseTotal}
            </div>
          )}
        </div>
      )}
      {resumed && reuse && reuse.reused > 0 && (
        <div className="processing-reuse">
          已复用中断前结果 {reuse.reused} 次 · 接续调用模型 {reuse.rerun} 次
        </div>
      )}
      {onCancel && (
        <div className="processing-actions">
          <button
            className="btn btn--secondary"
            onClick={onCancel}
            disabled={cancelRequested}
          >
            {cancelRequested ? '正在终止…' : '终止任务'}
          </button>
          {forceCancelAvailable && onForceCancel && (
            <button className="btn btn--ghost" onClick={handleForceCancel}>
              强制终止
            </button>
          )}
          <span className="processing-actions__hint">
            {forceCancelAvailable && onForceCancel
              ? '任务未在检查点停下（可能正卡在某一步里）。强制终止会立即结束该任务且不支持续跑，已产出的文件保留。'
              : cancelRequested
                ? '已请求终止，任务会在当前步骤/Case 结束时停止；已产出的文件会保留。'
                : '终止后不删除已产出的文件，但任务不会有结果页。'}
          </span>
        </div>
      )}
      {children}
    </div>
  )
}
