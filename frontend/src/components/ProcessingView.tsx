interface Props {
  progress?: string
  stage?: string
  stageIndex?: number
  stageTotal?: number
  caseIndex?: number
  caseTotal?: number
}

const STAGE_LABELS: Record<string, string> = {
  parse: '解析文件',
  label: 'HLR标注',
  match: '反向匹配',
  multi_judge: '多模型裁判',
  review: '共识复核',
  report: '报告生成',
  done: '完成',
}

export default function ProcessingView({
  progress,
  stage,
  stageIndex,
  stageTotal,
  caseIndex,
  caseTotal,
}: Props) {
  const stageLabel = stage ? STAGE_LABELS[stage] || stage : null
  const hasV4Progress = stageLabel && stageTotal !== undefined && stageIndex !== undefined

  return (
    <div className="processing-state">
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
    </div>
  )
}
