import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

function AuthLoading() {
  return <div className="auth-loading">正在检查登录状态…</div>
}

/** 登录守卫：未登录访问任何受保护页面时跳转登录页，并记住来源路径。 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) return <AuthLoading />
  if (!user) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location.pathname + location.search }}
      />
    )
  }
  return <>{children}</>
}

/** 管理员守卫：未登录跳登录页；已登录但非管理员回到首页。 */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) return <AuthLoading />
  if (!user) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location.pathname + location.search }}
      />
    )
  }
  if (!user.is_admin) return <Navigate to="/" replace />
  return <>{children}</>
}

/** 审计管理员守卫：未登录跳登录页；非审计管理员（含系统管理员）回到首页。 */
export function RequireAuditor({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) return <AuthLoading />
  if (!user) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location.pathname + location.search }}
      />
    )
  }
  if (!user.is_auditor) return <Navigate to="/" replace />
  return <>{children}</>
}

/** 业务页面守卫：审计管理员只能使用审计中心，访问业务页面时送回 /audit。 */
export function RequireBusiness({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) return <AuthLoading />
  if (!user) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location.pathname + location.search }}
      />
    )
  }
  if (user.is_auditor) return <Navigate to="/audit" replace />
  return <>{children}</>
}
