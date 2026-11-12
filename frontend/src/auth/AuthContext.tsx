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

interface AuthContextValue {
  /** 当前登录用户；null = 未登录 */
  user: AuthUser | null
  /** 首次会话恢复（getMe）是否仍在进行 */
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setUser(await getMeV4())
    } catch {
      // 网络/服务异常同样按未登录处理，避免页面卡在加载态
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
    const handleUnauthorized = () => setUser(null)
    window.addEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized)
    return () => window.removeEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized)
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    await loginV4(username, password)
    setUser(await getMeV4())
  }, [])

  const logout = useCallback(async () => {
    try {
      await logoutV4()
    } catch {
      // 登出请求失败（网络/会话已失效）也必须清空本地状态
    } finally {
      setUser(null)
    }
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({ user, loading, login, logout, refresh }),
    [user, loading, login, logout, refresh],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth 必须在 AuthProvider 内使用')
  return ctx
}
