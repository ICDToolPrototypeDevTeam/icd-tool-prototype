import type { PageState } from '../types'

interface Props {
  pageState: PageState
}

export default function WorkflowSteps({ pageState }: Props) {
  function getStepClass(step: number): string {
    if (step === 1 && pageState === 'upload') return 'active'
    if (step === 1 && pageState !== 'upload') return 'completed'
    if (step === 2 && pageState === 'processing') return 'active'
    if (step === 2 && (pageState === 'success' || pageState === 'error')) return 'completed'
    if (step === 3 && pageState === 'success') return 'active'
    if (step === 3 && pageState === 'error') return 'completed'
    return ''
  }

  return (
    <div className="workflow">
      <div className={`step ${getStepClass(1)}`}>
        <div className="step__number">1</div>
        <div className="step__label">上传文件</div>
        <div className="step__arrow">›</div>
      </div>
      <div className={`step ${getStepClass(2)}`}>
        <div className="step__number">2</div>
        <div className="step__label">文件处理</div>
        <div className="step__arrow">›</div>
      </div>
      <div className={`step ${getStepClass(3)}`}>
        <div className="step__number">3</div>
        <div className="step__label">查看结果</div>
      </div>
    </div>
  )
}
