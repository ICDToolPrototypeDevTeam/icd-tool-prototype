import { useCallback, useEffect, useState } from 'react'
import { MOCK_STORAGE_KEY } from '../api'

/** 同页内多组件同步用的自定义事件（storage 事件只在其他标签页触发）。 */
const CHANGE_EVENT = 'icd:mock-change'

function readStored(): boolean {
  try {
    return window.localStorage.getItem(MOCK_STORAGE_KEY) === '1'
  } catch {
    // 隐私模式 / 禁用存储：退化为「本次会话关闭」
    return false
  }
}

/**
 * 全局 MOCK 开关。
 *
 * 用 localStorage 持久化，使开关在刷新后仍生效；仍**显式**把
 * use_mock_llm=true|false 提交给后端 —— 容器 .env 里可能已开 mock，
 * 显式传 false 才能让开关双向可用。
 */
export function useMockMode() {
  const [mockMode, setMockModeState] = useState<boolean>(readStored)

  useEffect(() => {
    const sync = () => setMockModeState(readStored())
    window.addEventListener(CHANGE_EVENT, sync)
    window.addEventListener('storage', sync)
    return () => {
      window.removeEventListener(CHANGE_EVENT, sync)
      window.removeEventListener('storage', sync)
    }
  }, [])

  const setMockMode = useCallback((next: boolean) => {
    let persisted = true
    try {
      window.localStorage.setItem(MOCK_STORAGE_KEY, next ? '1' : '0')
    } catch {
      // 存储不可用（隐私模式 / 禁用存储 / 配额满）：开关只在本页本次会话内生效，不持久化。
      // 此时**不能**广播 CHANGE_EVENT —— 监听者会同步重读存储，把这次点击覆盖成旧值，
      // 结果是「能开不能关」（存量 '1' + 写入被拒 = 永远停在 MOCK）。
      persisted = false
    }
    setMockModeState(next)
    if (persisted) window.dispatchEvent(new Event(CHANGE_EVENT))
  }, [])

  return { mockMode, setMockMode }
}
