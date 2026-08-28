import { useEffect, useRef, useState } from 'react'
import { getJobStatusV4 } from '../api'
import type { PageState } from '../types'

const MAX_RETRIES = 120
const POLL_INTERVAL_MS = 10000

export interface AnalysisJobState<T> {
  pageState: PageState
  progress: string
  stage: string
  stageIndex: number
  stageTotal: number
  caseIndex: number
  caseTotal: number
  resultData: T | null
  start: (
    submit: () => Promise<{ job_id: string }>,
    fetchResult: (jobId: string) => Promise<T>,
  ) => void
  reset: () => void
}

export function useAnalysisJob<T>(): AnalysisJobState<T> {
  const [pageState, setPageState] = useState<PageState>('upload')
  const [progress, setProgress] = useState('已提交，等待开始')
  const [stage, setStage] = useState('')
  const [stageIndex, setStageIndex] = useState(0)
  const [stageTotal, setStageTotal] = useState(0)
  const [caseIndex, setCaseIndex] = useState(0)
  const [caseTotal, setCaseTotal] = useState(0)
  const [resultData, setResultData] = useState<T | null>(null)

  const timerRef = useRef<number | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }
  }, [])

  function clearTimer() {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }

  function reset() {
    clearTimer()
    setPageState('upload')
    setProgress('已提交，等待开始')
    setStage('')
    setStageIndex(0)
    setStageTotal(0)
    setCaseIndex(0)
    setCaseTotal(0)
    setResultData(null)
  }

  function start(
    submit: () => Promise<{ job_id: string }>,
    fetchResult: (jobId: string) => Promise<T>,
  ) {
    clearTimer()
    setPageState('processing')
    setProgress('已提交，等待开始')
    setStage('')
    setStageIndex(0)
    setStageTotal(0)
    setCaseIndex(0)
    setCaseTotal(0)

    submit()
      .then((res) => poll(res.job_id, fetchResult))
      .catch((err) => {
        console.error(err)
        if (mountedRef.current) setPageState('error')
      })
  }

  function poll(id: string, fetchResult: (jobId: string) => Promise<T>) {
    let retries = 0

    const check = async () => {
      if (!mountedRef.current) return
      try {
        const status = await getJobStatusV4(id)
        if (!mountedRef.current) return
        if (status.message) setProgress(status.message)
        if (status.stage) setStage(status.stage)
        if (status.stage_index !== undefined) setStageIndex(status.stage_index)
        if (status.stage_total !== undefined) setStageTotal(status.stage_total)
        if (status.case_index !== undefined) setCaseIndex(status.case_index)
        if (status.case_total !== undefined) setCaseTotal(status.case_total)

        if (status.status === 'completed') {
          try {
            const result = await fetchResult(id)
            if (!mountedRef.current) return
            setResultData(result)
          } catch (e) {
            console.error('Failed to fetch results:', e)
          }
          if (mountedRef.current) setPageState('success')
        } else if (status.status === 'failed') {
          if (mountedRef.current) setPageState('error')
        } else if (retries < MAX_RETRIES) {
          retries++
          timerRef.current = window.setTimeout(check, POLL_INTERVAL_MS)
        } else {
          if (mountedRef.current) setPageState('error')
        }
      } catch {
        if (retries < MAX_RETRIES) {
          retries++
          timerRef.current = window.setTimeout(check, POLL_INTERVAL_MS)
        } else {
          if (mountedRef.current) setPageState('error')
        }
      }
    }
    check()
  }

  return {
    pageState,
    progress,
    stage,
    stageIndex,
    stageTotal,
    caseIndex,
    caseTotal,
    resultData,
    start,
    reset,
  }
}
