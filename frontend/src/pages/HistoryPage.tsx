import HistoryList from '../components/HistoryList'

export default function HistoryPage() {
  return (
    <div className="landing">
      <div className="landing-hero">
        <h1 className="landing-hero__title">历史结果</h1>
        <p className="landing-hero__subtitle">
          服务器上保留的全部分析结果，可随时重新下载；不再需要的可以删除以释放磁盘
        </p>
      </div>

      <HistoryList />
    </div>
  )
}
