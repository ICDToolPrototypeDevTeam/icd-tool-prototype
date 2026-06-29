interface Props {
  progress?: string
}

export default function ProcessingView({ progress }: Props) {
  return (
    <div className="processing-state">
      <div className="processing-spinner" />
      <div className="processing-title">
        {progress || '任务正在处理'}
      </div>
    </div>
  )
}
