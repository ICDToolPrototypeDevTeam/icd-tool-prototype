import { Coins } from 'lucide-react'

interface Props {
  /** 累计 token 用量；null/undefined（旧任务未统计）不渲染 */
  totalTokens?: number | null
  /** MOCK 模式下 0 是正确值，用标签说明避免误读 */
  mock?: boolean
}

/** 完成页的总 Token 用量卡片。 */
export default function TokenUsageCard({ totalTokens, mock }: Props) {
  if (totalTokens == null) return null

  return (
    <div className="token-usage-card">
      <div className="token-usage-card__icon">
        <Coins size={20} />
      </div>
      <div className="token-usage-card__body">
        <div className="token-usage-card__label">总 Token 用量</div>
        <div className="token-usage-card__value">{totalTokens.toLocaleString()}</div>
      </div>
      {mock && <span className="token-usage-card__mock">MOCK</span>}
    </div>
  )
}
