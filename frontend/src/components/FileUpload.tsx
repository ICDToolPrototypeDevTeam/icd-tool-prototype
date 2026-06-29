import { useState } from 'react'
import { analyzeFiles } from '../api'

interface Props {
  onJobCreated: (jobId: string) => void
}

export default function FileUpload({ onJobCreated }: Props) {
  const [eoicdWord, setEoicdWord] = useState<File | null>(null)
  const [eoicdExcels, setEoicdExcels] = useState<File[]>([])
  const [swReq, setSwReq] = useState<File | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if ((!eoicdWord && eoicdExcels.length === 0) || !swReq) {
      setError('请至少上传 EoICD Word 文件或 EoICD Excel 附件，以及软件高层需求文件')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await analyzeFiles(eoicdWord, eoicdExcels, swReq)
      onJobCreated(res.job_id)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div>
        <label>EoICD Word 主文件：</label>
        <input
          type="file"
          accept=".docx"
          onChange={(e) => setEoicdWord(e.target.files?.[0] ?? null)}
        />
      </div>

      <div>
        <label>EoICD Excel 附件（可多选）：</label>
        <input
          type="file"
          accept=".xlsx,.xls"
          multiple
          onChange={(e) => setEoicdExcels(Array.from(e.target.files ?? []))}
        />
      </div>

      <div>
        <label>软件高层需求文件 (*)：</label>
        <input
          type="file"
          accept=".docx"
          onChange={(e) => setSwReq(e.target.files?.[0] ?? null)}
        />
      </div>

      {error && <p style={{ color: 'red' }}>{error}</p>}

      <button type="submit" disabled={loading}>
        {loading ? '上传中...' : '提交分析'}
      </button>
    </form>
  )
}