/** 正/反向管线的阶段元数据。
 *
 * 这里只放「阶段是什么」的静态信息，进度状态由 parseJobProgress 计算。
 * 反向 6 步 / 正向 4 步；另保留正向旧版 8 步映射，供挂到升级前任务时兜底。
 */

export type PipelineKind = 'reverse' | 'forward'

export interface StageMeta {
  key: string
  index: number
  label: string
  short: string
  description: string
}

/** 与旧 ProcessingView / ErrorDiagnostics 保持兼容的阶段名表。 */
export const STAGE_LABELS: Record<string, string> = {
  parse: '解析输入',
  label: 'SWHLR 标注',
  match: '反向匹配',
  multi_judge: '多智能体裁判',
  review: '共识复核',
  report: '报告生成',
  done: '完成',
  // 正向旧版 8 步
  scope: '追溯范围',
  blocks: '构建业务对象块',
  identity_index: '构建HLR身份索引',
  candidate_recall: '候选召回',
  deterministic: '确定性判定',
  ai_review: 'AI三态复核',
}

const REVERSE_STAGES: StageMeta[] = [
  { key: 'parse', index: 1, label: '解析输入', short: '解析', description: '读取 EoICD 与 SWHLR' },
  { key: 'label', index: 2, label: 'SWHLR 标注', short: '标注', description: '逐条标注总线、设备与信号' },
  { key: 'match', index: 3, label: '反向匹配', short: '匹配', description: '按 Label 与信号画像匹配 EoICD Block' },
  { key: 'multi_judge', index: 4, label: '多智能体裁判', short: '裁判', description: '三个模型独立判定每条 SWHLR' },
  { key: 'review', index: 5, label: '共识复核', short: '复核', description: '跨模型达成共识并复查低星条目' },
  { key: 'report', index: 6, label: '生成报告', short: '报告', description: '写出 Word 与 Excel 交付物' },
]

const FORWARD_STAGES: StageMeta[] = [
  { key: 'parse', index: 1, label: '解析输入', short: '解析', description: '解析 HLR 与 EoICD，共享阶段 1 产物' },
  { key: 'multi_judge', index: 2, label: '多模型裁判', short: '裁判', description: '每个 judge 独立完成一次正向检查' },
  { key: 'review', index: 3, label: '共识复核', short: '复核', description: '聚合各 judge 结果并仲裁分歧' },
  { key: 'report', index: 4, label: '生成报告', short: '报告', description: '写出正向完整性分析报告' },
]

/** 正向旧版 8 步（仅用于挂到升级前任务时展示，不参与新任务）。 */
const FORWARD_STAGES_LEGACY: StageMeta[] = [
  { key: 'parse', index: 1, label: '解析输入', short: '解析', description: '解析输入文件' },
  { key: 'scope', index: 2, label: '追溯范围', short: '范围', description: '确定追溯范围' },
  { key: 'blocks', index: 3, label: '构建业务对象块', short: '对象块', description: '构建正向 ICD Block' },
  { key: 'identity_index', index: 4, label: '构建 HLR 身份索引', short: '索引', description: '建立 HLR 身份索引' },
  { key: 'candidate_recall', index: 5, label: '候选召回', short: '召回', description: '召回候选信号' },
  { key: 'deterministic', index: 6, label: '确定性判定', short: '判定', description: '规则层覆盖判定' },
  { key: 'ai_review', index: 7, label: 'AI 三态复核', short: '复核', description: 'AI 三态覆盖复核' },
  { key: 'report', index: 8, label: '生成报告', short: '报告', description: '写出正向分析报告' },
]

export function getStages(pipeline: PipelineKind, stageTotal?: number): StageMeta[] {
  if (pipeline === 'reverse') return REVERSE_STAGES
  if (stageTotal === 8) return FORWARD_STAGES_LEGACY
  return FORWARD_STAGES
}

/** 把日志/状态里的 Step 编号映射到阶段 key。 */
export function stageKeyFromStep(
  pipeline: PipelineKind,
  stepIndex: number,
  stepTotal: number,
): string | null {
  if (pipeline === 'reverse') {
    if (stepTotal === 6) {
      if (stepIndex === 1) return 'parse'
      if (stepIndex === 2) return 'label'
      if (stepIndex === 3) return 'match'
      if (stepIndex === 4) return 'multi_judge'
      if (stepIndex === 5) return 'review'
      if (stepIndex === 6) return 'report'
    }
    return null
  }
  if (stepTotal === 4) {
    if (stepIndex === 1) return 'parse'
    if (stepIndex === 2) return 'multi_judge'
    if (stepIndex === 3) return 'review'
    if (stepIndex === 4) return 'report'
    return null
  }
  if (stepTotal === 8) {
    if (stepIndex === 1) return 'parse'
    if (stepIndex === 2) return 'scope'
    if (stepIndex === 3) return 'blocks'
    if (stepIndex === 4) return 'identity_index'
    if (stepIndex === 5) return 'candidate_recall'
    if (stepIndex === 6) return 'deterministic'
    if (stepIndex === 7) return 'ai_review'
    if (stepIndex === 8) return 'report'
  }
  return null
}

/** 把 `[timing] Step 4/6: ...` 这类标签映射回阶段 key。 */
export function stageKeyFromTimingLabel(
  pipeline: PipelineKind,
  label: string,
): string | null {
  const m = label.match(/Step\s+(\d+(?:\.\d+)?)\s*\/\s*(\d+)/)
  if (!m) return null
  const index = Math.floor(Number(m[1]))
  const total = Number(m[2])
  return stageKeyFromStep(pipeline, index, total)
}

export function taskKindLabel(taskType: 'correctness' | 'completeness'): string {
  return taskType === 'correctness'
    ? 'HLR → EoICD 正确性比对'
    : 'EoICD → HLR 完整性分析'
}
