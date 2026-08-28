import { Link } from 'react-router-dom'

export default function LandingPage() {
  return (
    <div className="landing">
      <div className="landing-hero">
        <h1 className="landing-hero__title">ICD工具平台</h1>
        <p className="landing-hero__subtitle">
          面向 EoICD 源文件与软件高层需求的智能化需求生成与差异分析工具
        </p>
      </div>

      <div className="landing-cards">
        <Link to="/correctness" className="entry-card">
          <div className="entry-card__icon">🔍</div>
          <div className="entry-card__title">正确性分析</div>
          <div className="entry-card__desc">
            由软件高层需求（HLR）反向比对 EoICD 条目，识别差异与不一致
          </div>
          <div className="entry-card__cta">进入 ›</div>
        </Link>

        <Link to="/completeness" className="entry-card">
          <div className="entry-card__icon">🧩</div>
          <div className="entry-card__title">完整性分析</div>
          <div className="entry-card__desc">
            由 EoICD 条目正向检查高层需求覆盖情况，识别漏写与缺失
          </div>
          <div className="entry-card__cta">进入 ›</div>
        </Link>
      </div>
    </div>
  )
}
