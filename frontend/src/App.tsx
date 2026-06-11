import { useState } from 'react'
import FileUpload from './components/FileUpload'
import JobStatus from './components/JobStatus'

function App() {
  const [jobId, setJobId] = useState<string | null>(null)

  return (
    <div style={{ padding: '2rem', maxWidth: '800px', margin: '0 auto' }}>
      <h1>ICD工具原型</h1>
      {!jobId ? (
        <FileUpload onJobCreated={setJobId} />
      ) : (
        <JobStatus jobId={jobId} />
      )}
    </div>
  )
}

export default App