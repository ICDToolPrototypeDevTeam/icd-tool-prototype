import { useEffect, useRef, useState } from 'react'
import type { V4JobError, V4LogLine } from '../types'
import { STAGE_LABELS } from './ProcessingView'

export interface ErrorDiagnosticsProps {
  error: V4JobError | null
  jobId: string | null
  taskType: 'correctness' | 'completeness'
  mockMode: boolean
  jobStatus: string | null
  lines: V4LogLine[]
}

export function buildDiagnosticsText(input: ErrorDiagnosticsProps): string {
  const e = input.error
  const tail = input.lines
    .slice(-200)
    .map((l) => (l.ts ? `${l.ts} [${l.level}] ${l.text}` : l.text))
    .join('\n')
  return [
    '===== ICD 工具诊断信息 =====',
    `任务 ID: ${input.jobId ?? '(未知)'}`,
    `任务类型: ${input.taskType}`,
    `任务状态: ${input.jobId === null ? '(未创建任务)' : (input.jobStatus ?? '(未知)')}`,
    `MOCK 模式: ${
      input.jobId === null ? '(任务未创建，未生效)' : input.mockMode ? '开启' : '关闭'
    }`,
    `失败分类: ${
      e
        ? e.title
          ? `${e.title} (${e.category})`
          : e.category
        : input.jobId === null
          ? '(任务未创建：提交失败，非后端失败)'
          : '(前端等待超时，未取得后端终态；非后端失败)'
    }`,
    `失败步骤: ${
      e?.stage ? (STAGE_LABELS[e.stage] ? `${STAGE_LABELS[e.stage]}（${e.stage}）` : e.stage) : '(未知)'
    }${e?.stage_index ? ` (Step ${e.stage_index})` : ''}`,
    `异常类型: ${e?.error_type || '(无)'}`,
    `异常信息: ${e?.message || '(无)'}`,
    `建议: ${e?.hint || '(无)'}`,
    '',
    '----- 堆栈尾部 -----',
    e?.traceback_tail || '(无)',
    '',
    `----- 日志尾部（最多 200 行；本次共收集 ${input.lines.length} 行）-----`,
    tail || '(无)',
  ].join('\n')
}

/**
 * 复制文本到剪贴板。
 *
 * 服务器是内网明文 HTTP，`navigator.clipboard` 在非安全上下文下**不可用**
 * （只有 https 或 localhost 才暴露），因此必须保留 execCommand 兜底，
 * 否则「一键复制诊断信息」在真实部署环境里会静默失效。
 */
async function copyText(text: string): Promise<boolean> {
  try {
    if (window.isSecureContext && navigator.clipboard) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // 落到 execCommand 兜底
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

/**
 * 「复制诊断信息」按钮。
 *
 * 它是错误视图的一个**动作**，由页面放在底部动作行里与「继续执行 / 放弃 / 重新尝试」
 * 同排，而不是埋在诊断卡片内部 —— 三颗按钮分处两地、两种对齐，收尾动作看起来是散的。
 */
export function DiagnosticsCopyButton(props: ErrorDiagnosticsProps) {
  const [copied, setCopied] = useState<'idle' | 'ok' | 'fail'>('idle')

  const resetTimerRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (resetTimerRef.current !== null) window.clearTimeout(resetTimerRef.current)
    }
  }, [])

  async function handleCopy() {
    const ok = await copyText(buildDiagnosticsText(props))
    setCopied(ok ? 'ok' : 'fail')
    if (resetTimerRef.current !== null) window.clearTimeout(resetTimerRef.current)
    resetTimerRef.current = window.setTimeout(() => {
      resetTimerRef.current = null
      setCopied('idle')
    }, 3000)
  }

  return (
    <>
      <button className="btn btn--ghost" onClick={handleCopy}>
        {copied === 'ok' ? '已复制' : copied === 'fail' ? '复制失败：请手动全选下方文本' : '复制诊断信息'}
      </button>
      {copied === 'fail' && (
        <textarea
          className="error-diagnostics__fallback"
          readOnly
          rows={10}
          value={buildDiagnosticsText(props)}
          onFocus={(e) => e.currentTarget.select()}
        />
      )}
    </>
  )
}

/** 失败分类 / 失败步骤 / 原始信息 / 建议 —— 只呈现事实，动作在页面的动作行里。 */
export default function ErrorDiagnostics(props: ErrorDiagnosticsProps) {
  const { error } = props

  return (
    <div className="error-diagnostics">
      {error && (
        <>
          <div className="error-diagnostics__row">
            <span className="error-diagnostics__key">失败分类</span>
            <span className="error-diagnostics__value">
              <span className={`error-badge error-badge--${error.category.toLowerCase()}`}>
                {error.title || error.category}
              </span>
            </span>
          </div>
          {error.stage && (
            <div className="error-diagnostics__row">
              <span className="error-diagnostics__key">失败步骤</span>
              <span className="error-diagnostics__value">
                {STAGE_LABELS[error.stage] || error.stage}
              </span>
            </div>
          )}
          <div className="error-diagnostics__row">
            <span className="error-diagnostics__key">原始信息</span>
            <span className="error-diagnostics__value error-diagnostics__value--mono">
              {error.error_type}: {error.message}
            </span>
          </div>
          {error.hint && <p className="error-diagnostics__hint">建议：{error.hint}</p>}
        </>
      )}
      {!error && (
        <p className="error-diagnostics__hint">
          {props.jobId === null
            ? '任务未创建：提交失败，后端没有开始执行。'
            : '未取得后端终态：前端未能等到任务结束，具体原因见上方提示。'}
        </p>
      )}
    </div>
  )
}
