import { useState } from 'react'
import { NavLink, Navigate, Outlet, Route, Routes, useNavigate } from 'react-router-dom'
import { useAuth } from './auth/AuthContext'
import { RequireAdmin, RequireAuth } from './components/RequireAuth'
import { useV4Health } from './hooks/useV4Health'
import { useMockMode } from './hooks/useMockMode'
import LandingPage from './pages/LandingPage'
import CorrectnessPage from './pages/CorrectnessPage'
import CompletenessPage from './pages/CompletenessPage'
import HistoryPage from './pages/HistoryPage'
import SettingsPage from './pages/SettingsPage'
import LoginPage from './pages/LoginPage'

/** Task 9 前的占位页：只保证管理员路由与导航可用，不含用户管理功能。 */
function AdminUsersPlaceholder() {
  return (
    <div className="settings-card">
      <div className="settings-card__header">
        <div className="settings-card__heading">
          <div className="settings-card__title">用户管理</div>
          <div className="settings-card__subtitle">账号管理功能将在后续任务中提供。</div>
        </div>
      </div>
    </div>
  )
}

/** 受保护的页面外壳：顶栏、MOCK 提示、页脚与嵌套路由出口。 */
function AppShell() {
  const { user, logout } = useAuth()
  const { v4Online } = useV4Health()
  const { mockMode, setMockMode } = useMockMode()
  const navigate = useNavigate()
  const [loggingOut, setLoggingOut] = useState(false)

  const handleLogout = async () => {
    if (loggingOut) return
    setLoggingOut(true)
    try {
      await logout()
      navigate('/login', { replace: true })
    } finally {
      setLoggingOut(false)
    }
  }

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className="header__left">
          <img src="/logo1.png" alt="Logo" className="header__logo-img" />
          <div>
            <div className="header__title">ICD工具平台</div>
            <div className="header__subtitle">ICD Tool Platform</div>
          </div>
        </div>

        <nav className="header__nav">
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              `header__nav-link ${isActive ? 'header__nav-link--active' : ''}`
            }
          >
            工具入口
          </NavLink>
          <NavLink
            to="/correctness"
            className={({ isActive }) =>
              `header__nav-link ${isActive ? 'header__nav-link--active' : ''}`
            }
          >
            正确性分析
          </NavLink>
          <NavLink
            to="/completeness"
            className={({ isActive }) =>
              `header__nav-link ${isActive ? 'header__nav-link--active' : ''}`
            }
          >
            完整性分析
          </NavLink>
          <NavLink
            to="/history"
            className={({ isActive }) =>
              `header__nav-link ${isActive ? 'header__nav-link--active' : ''}`
            }
          >
            历史结果
          </NavLink>
          {user?.is_admin && (
            <NavLink
              to="/settings"
              className={({ isActive }) =>
                `header__nav-link ${isActive ? 'header__nav-link--active' : ''}`
              }
            >
              模型设置
            </NavLink>
          )}
          {user?.is_admin && (
            <NavLink
              to="/admin/users"
              className={({ isActive }) =>
                `header__nav-link ${isActive ? 'header__nav-link--active' : ''}`
              }
            >
              用户管理
            </NavLink>
          )}
        </nav>

        <div className="header__user">
          <span className="header__user-name" title={user?.username}>
            {user?.username}
          </span>
          <button
            type="button"
            className="header__logout"
            onClick={() => void handleLogout()}
            disabled={loggingOut}
          >
            {loggingOut ? '退出中…' : '退出'}
          </button>
        </div>

        <div className="header__status">
          <label
            className={`mock-switch ${mockMode ? 'mock-switch--on' : ''}`}
            title="MOCK 模式：模型调用返回模拟数据，用于验证流程本身"
          >
            <input
              type="checkbox"
              checked={mockMode}
              onChange={(e) => setMockMode(e.target.checked)}
            />
            <span>MOCK 模式</span>
          </label>
          <div className={`status-dot ${!v4Online ? 'status-dot--offline' : ''}`} />
          <span>{!v4Online ? 'V4 服务不可用' : '在线服务'}</span>
        </div>
      </header>

      {mockMode && (
        <div className="mock-banner">
          MOCK 模式已开启：所有模型调用返回模拟数据，分析结果不可用于验收。正式分析前请关闭。
        </div>
      )}

      {/* Main */}
      <main className="main">
        <Outlet />
      </main>

      {/* Footer */}
      <footer className="footer">
        <div className="footer__left">
          <img src="/logo2.jpg" alt="AVIC" className="footer__logo-img" />
          <span className="footer__divider">|</span>
          <span>中航民机机载系统有限公司</span>
        </div>
        <div className="footer__right">
          <span>© 2026 中航民机机载系统有限公司</span>
        </div>
      </footer>
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      {/* 登录页不需要登录 */}
      <Route path="/login" element={<LoginPage />} />

      {/* 其余页面统一登录守卫 */}
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route path="/" element={<LandingPage />} />
        <Route path="/correctness" element={<CorrectnessPage />} />
        <Route path="/completeness" element={<CompletenessPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route
          path="/settings"
          element={
            <RequireAdmin>
              <SettingsPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/admin/users"
          element={
            <RequireAdmin>
              <AdminUsersPlaceholder />
            </RequireAdmin>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
