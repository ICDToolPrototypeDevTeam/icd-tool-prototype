import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import {
  AUTH_UNAUTHORIZED_EVENT,
  getMeV4,
  loginV4,
  logoutV4,
} from '../api'
import type { AuthUser } from '../types'

/**
 * 本标签页是否建立过有效会话。用于区分「首次访问」与「刷新后会话已过期」：
 * sessionStorage 按标签页隔离，关掉页面即清除，不会让第二天的首次访问误报过期。
 */
const SESSION_MARKER_KEY = 'icd_had_session'

function hasSessionMarker(): boolean {
  try {
    return window.sessionStorage.getItem(SESSION_MARKER_KEY) === '1'
  } catch {
    return false
  }
}

function setSessionMarker(): void {
  try {
    window.sessionStorage.setItem(SESSION_MARKER_KEY, '1')
  } catch {
    // 隐私模式等禁用 storage 时不会显示过期提示（两条触发路径都依赖标记），但不影响登录
  }
}

function clearSessionMarker(): void {
  try {
    window.sessionStorage.removeItem(SESSION_MARKER_KEY)
  } catch {
    // 同上
  }
}

interface AuthContextValue {
  /** 当前登录用户；null = 未登录 */
  user: AuthUser | null
  /** 首次会话恢复（getMe）是否仍在进行 */
  loading: boolean
  /** 本标签页曾登录、当前会话已失效（用于登录页提示） */
  sessionExpired: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [sessionExpired, setSessionExpired] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const current = await getMeV4()
      setUser(current)
      if (current) {
        setSessionMarker()
        setSessionExpired(false)
      } else if (hasSessionMarker()) {
        // Cookie 已失效但本标签页此前登录过：刷新页面后发现会话过期
        setSessionExpired(true)
      }
    } catch {
      // 网络/服务异常同样按未登录处理，避免页面卡在加载态；
      // 不标记为过期，避免把服务不可用误报成登录失效
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  // 挂载时恢复登录态（浏览器刷新后 Cookie 仍在）
  useEffect(() => {
    void refresh()
  }, [refresh])

  // 任意受保护接口返回 401 时清空用户，路由守卫会跳回登录页
  useEffect(() => {
    const handleUnauthorized = () => {
      setUser(null)
      // 主动退出会先清除标记，因此这里只对「曾经登录过的本标签页」提示过期
      if (hasSessionMarker()) setSessionExpired(true)
    }
    window.addEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized)
    return () => window.removeEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized)
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    await loginV4(username, password)
    const current = await getMeV4()
    setUser(current)
    if (current) {
      setSessionMarker()
      setSessionExpired(false)
    }
  }, [])

  const logout = useCallback(async () => {
    try {
      await logoutV4()
    } catch {
      // 登出请求失败（网络/会话已失效）也必须清空本地状态
    } finally {
      setUser(null)
      setSessionExpired(false)
      clearSessionMarker()
    }
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({ user, loading, sessionExpired, login, logout, refresh }),
    [user, loading, sessionExpired, login, logout, refresh],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth 必须在 AuthProvider 内使用')
  return ctx
}
