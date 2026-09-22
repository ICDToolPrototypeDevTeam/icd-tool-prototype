import { useEffect, useRef, useState } from 'react'
import { ApiError, cancelJobV4, getJobStatusV4 } from '../api'
import type { PageState, V4JobError, V4JobStatus } from '../types'

const POLL_INTERVAL_MS = 10000
/**
 * 前端等待上限。
 *
 * 原实现是 MAX_RETRIES=120 × 10s = 20 分钟，超时后进错误页且 errorMessage 为空，
 * 与「后端真的失败」在 UI 上完全同形 —— 这正是问题 5 的第二条根因。改为按时间
 * 上限，并在超时文案里明确写出「任务可能仍在服务器上运行」。
 */
const POLL_DEADLINE_MS = 6 * 60 * 60 * 1000

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
  /** 后端返回的结构化失败信息；前端等待超时时为 null（两者语义不同，不可混同） */
  error: V4JobError | null
  errorMessage: string
  /** 本次运行是否为 MOCK 模式（结果页据此提示「不可用于验收」） */
  mock: boolean
  jobId: string | null
  jobStatus: V4JobStatus | null
  /** 已请求终止，等待管线在检查点停止 */
  cancelRequested: boolean
  start: (
    submit: () => Promise<{ job_id: string }>,
    fetchResult: (jobId: string) => Promise<T>,
  ) => void
  /** 挂到已存在的任务上继续观察（中断任务「继续」/ 从工具入口页跳转过来）。 */
  attach: (jobId: string, fetchResult: (jobId: string) => Promise<T>) => void
  /** 请求终止当前任务（后端在步骤/case 边界停止） */
  cancel: () => void
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
  const [error, setError] = useState<V4JobError | null>(null)
  const [errorMessage, setErrorMessage] = useState('')
  const [mock, setMock] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<V4JobStatus | null>(null)
  const [cancelRequested, setCancelRequested] = useState(false)

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
    setError(null)
    setErrorMessage('')
    setResultData(null)
    setMock(false)
    setJobId(null)
    setJobStatus(null)
    setCancelRequested(false)
  }

  function reset() {
    clearTimer()
    setPageState('upload')
    clearProgress()
  }

  /** 后端失败 / 已取消 / 中断 —— 一律用结构化信息填充错误页。 */
  function failWith(
    status: V4JobStatus,
    structured: V4JobError | null,
    fallbackMessage: string,
  ) {
    setJobStatus(status)
    setError(structured)
    setErrorMessage(structured?.title || structured?.message || fallbackMessage)
    setPageState('error')
  }

  function start(
    submit: () => Promise<{ job_id: string }>,
    fetchResult: (jobId: string) => Promise<T>,
  ) {
    clearTimer()
    setPageState('processing')
    clearProgress()

    submit()
      .then((res) => {
        setJobId(res.job_id)
        poll(res.job_id, fetchResult)
      })
      .catch((err) => {
        console.error(err)
        if (mountedRef.current) {
          setErrorMessage(err instanceof Error ? err.message : '提交任务失败')
          setPageState('error')
        }
      })
  }

  function attach(id: string, fetchResult: (jobId: string) => Promise<T>) {
    clearTimer()
    setPageState('processing')
    clearProgress()
    setJobId(id)
    setProgress('正在连接任务…')
    poll(id, fetchResult)
  }

  function cancel() {
    const id = jobId
    if (!id) return
    setCancelRequested(true)          // 乐观置位：按钮立即变「正在终止…」
    cancelJobV4(id).catch((e) => {
      console.error(e)
      if (mountedRef.current) setCancelRequested(false)
    })
  }

  function poll(id: string, fetchResult: (jobId: string) => Promise<T>) {
    const deadline = Date.now() + POLL_DEADLINE_MS

    const check = async () => {
      if (!mountedRef.current) return
      try {
        const status = await getJobStatusV4(id)
        if (!mountedRef.current) return
        if (status.message) setProgress(status.message)
        if (status.stage) setStage(status.stage)
        // 这几个字段后端是 Optional[...]，会回 null；必须用 != null 同时挡掉
        // null 与 undefined（写成 !== undefined 会把 null 放行进来）
        if (status.stage_index != null) setStageIndex(status.stage_index)
        if (status.stage_total != null) setStageTotal(status.stage_total)
        if (status.case_index != null) setCaseIndex(status.case_index)
        if (status.case_total != null) setCaseTotal(status.case_total)
        if (status.resumed === true) setResumed(true)
        setMock(status.mock === true)
        setJobStatus(status.status)
        setCancelRequested(status.cancel_requested === true)
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
          if (mountedRef.current) failWith('failed', status.error, '任务在后端执行失败')
        } else if (status.status === 'canceled') {
          if (mountedRef.current) failWith('canceled', status.error, '任务已被终止')
        } else if (status.status === 'interrupted' || status.status === 'abandoned') {
          // 进程曾关闭（或用户已放弃）：立即停止轮询，回到上传页由用户选择继续/放弃
          if (mountedRef.current) {
            failWith(
              status.status,
              status.error,
              status.status === 'interrupted'
                ? '任务已中断（程序曾关闭）。请返回上传页选择「继续」或「放弃」该任务。'
                : '该任务已被放弃。',
            )
          }
        } else if (Date.now() < deadline) {
          timerRef.current = window.setTimeout(check, POLL_INTERVAL_MS)
        } else {
          // 前端放弃轮询 ≠ 任务失败：必须与后端失败明确区分
          if (mountedRef.current) {
            setError(null)
            setErrorMessage(
              '前端等待已超时（6 小时）。任务可能仍在服务器上运行，' +
              '请稍后回到任务列表查看；也可在任务详情中查看日志判断进度。',
            )
            setPageState('error')
          }
        }
      } catch (e) {
        // 任务不存在（后端重启且该任务未留下可恢复记录）→ 立即报错，不再空转
        if (e instanceof ApiError && e.status === 404) {
          if (mountedRef.current) {
            setError(null)
            setErrorMessage('任务已不存在（服务可能已重启）。请重新提交或从任务列表继续。')
            setPageState('error')
          }
          return
        }
        if (Date.now() < deadline) {
          timerRef.current = window.setTimeout(check, POLL_INTERVAL_MS)
        } else {
          if (mountedRef.current) {
            setError(null)
            setErrorMessage(
              '与服务器连接持续失败（6 小时内未能获取任务状态）。' +
              '请检查网络或联系管理员查看服务器状态。',
            )
            setPageState('error')
          }
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
    error,
    errorMessage,
    mock,
    jobId,
    jobStatus,
    cancelRequested,
    start,
    attach,
    cancel,
    reset,
  }
}
