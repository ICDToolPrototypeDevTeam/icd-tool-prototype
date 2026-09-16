import { useEffect, useRef, useState } from 'react'
import { ApiError, getJobStatusV4 } from '../api'
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
  /** 恢复运行标记 / 实时复用计数（按模型调用次数计） */
  resumed: boolean
  reuse: { reused: number; rerun: number } | null
  resultData: T | null
  errorMessage: string
  start: (
    submit: () => Promise<{ job_id: string }>,
    fetchResult: (jobId: string) => Promise<T>,
  ) => void
  /** 挂到已存在的任务上继续观察（中断任务「继续」/ 从工具入口页跳转过来）。 */
  attach: (jobId: string, fetchResult: (jobId: string) => Promise<T>) => void
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
  const [resumed, setResumed] = useState(false)
  const [reuse, setReuse] = useState<{ reused: number; rerun: number } | null>(null)
  const [resultData, setResultData] = useState<T | null>(null)
  const [errorMessage, setErrorMessage] = useState('')

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

  function clearProgress() {
    setProgress('已提交，等待开始')
    setStage('')
    setStageIndex(0)
    setStageTotal(0)
    setCaseIndex(0)
    setCaseTotal(0)
    setResumed(false)
    setReuse(null)
    setErrorMessage('')
    setResultData(null)
  }

  function reset() {
    clearTimer()
    setPageState('upload')
    clearProgress()
  }

  function start(
    submit: () => Promise<{ job_id: string }>,
    fetchResult: (jobId: string) => Promise<T>,
  ) {
    clearTimer()
    setPageState('processing')
    clearProgress()

    submit()
      .then((res) => poll(res.job_id, fetchResult))
      .catch((err) => {
        console.error(err)
        if (mountedRef.current) setPageState('error')
      })
  }

  function attach(jobId: string, fetchResult: (jobId: string) => Promise<T>) {
    clearTimer()
    setPageState('processing')
    clearProgress()
    setProgress('正在连接任务…')
    poll(jobId, fetchResult)
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
        if (status.resumed === true) setResumed(true)
        // 后端可能给 null（非恢复运行），不能用 !== undefined 判空
        setReuse(status.reuse ?? null)

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
        } else if (status.status === 'interrupted' || status.status === 'abandoned') {
          // 进程曾关闭（或用户已放弃）：立即停止轮询，回到上传页由用户选择继续/放弃
          if (mountedRef.current) {
            setErrorMessage(
              status.status === 'interrupted'
                ? '任务已中断（程序曾关闭）。请返回上传页选择「继续」或「放弃」该任务。'
                : '该任务已被放弃。',
            )
            setPageState('error')
          }
        } else if (retries < MAX_RETRIES) {
          retries++
          timerRef.current = window.setTimeout(check, POLL_INTERVAL_MS)
        } else {
          if (mountedRef.current) setPageState('error')
        }
      } catch (e) {
        // 任务不存在（后端重启且该任务未留下可恢复记录）→ 立即报错，不再空转
        if (e instanceof ApiError && e.status === 404) {
          if (mountedRef.current) {
            setErrorMessage('任务已不存在（服务可能已重启）。请重新提交或从任务列表继续。')
            setPageState('error')
          }
          return
        }
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
    resumed,
    reuse,
    resultData,
    errorMessage,
    start,
    attach,
    reset,
  }
}
