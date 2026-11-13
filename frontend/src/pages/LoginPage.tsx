import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { ApiError } from '../api'
import { useAuth } from '../auth/AuthContext'

interface LoginLocationState {
  from?: string
}

/** FastAPI 的错误体是 JSON；把登录相关的已知错误码换成可读文案。 */
function describeLoginError(error: unknown): string {
  if (error instanceof ApiError) {
    try {
      const body = JSON.parse(error.message) as { detail?: unknown }
      if (typeof body.detail === 'string') {
        if (body.detail === 'LOGIN_BAD_CREDENTIALS') return '用户名或密码错误'
        if (body.detail === 'LOGIN_USER_NOT_ACTIVE') return '账号已被禁用，请联系管理员'
        if (body.detail === 'LOGIN_USER_NOT_VERIFIED') return '账号未验证，请联系管理员'
        return body.detail
      }
    } catch {
      // 非 JSON 错误体：按原文展示
    }
    return error.message || '登录失败'
  }
  return error instanceof Error ? error.message : '登录失败'
}

export default function LoginPage() {
  const { user, loading, sessionExpired, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as LoginLocationState | null)?.from || '/'

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  if (loading) {
    return (
      <div className="login-page">
        <div className="login-card login-card--loading">正在检查登录状态…</div>
      </div>
    )
  }

  // 已登录（例如手动访问 /login）直接回到目标页
  if (user) return <Navigate to={from} replace />

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting) return
    setSubmitting(true)
    setError('')
    try {
      await login(username.trim(), password)
      navigate(from, { replace: true })
    } catch (err) {
      setError(describeLoginError(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-card__brand">
          <img src="/logo1.png" alt="Logo" className="login-card__logo" />
          <div>
            <div className="login-card__title">ICD工具平台</div>
            <div className="login-card__subtitle">请使用分配给你的账号登录</div>
          </div>
        </div>

        {sessionExpired && (
          <div className="login-notice" role="status">
            登录状态已失效，请重新登录
          </div>
        )}

        <form className="login-form" onSubmit={handleSubmit}>
          <label className="login-field">
            <span className="login-field__label">用户名</span>
            <input
              className="login-input"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              required
            />
          </label>

          <label className="login-field">
            <span className="login-field__label">密码</span>
            <input
              className="login-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>

          {error && (
            <div className="login-error" role="alert">
              {error}
            </div>
          )}

          <button type="submit" className="btn btn--primary login-submit" disabled={submitting}>
            {submitting ? '登录中…' : '登录'}
          </button>
        </form>
      </div>
    </div>
  )
}
