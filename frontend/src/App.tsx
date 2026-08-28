import { NavLink, Routes, Route } from 'react-router-dom'
import { useV4Health } from './hooks/useV4Health'
import LandingPage from './pages/LandingPage'
import CorrectnessPage from './pages/CorrectnessPage'
import CompletenessPage from './pages/CompletenessPage'

export default function App() {
  const { isOnline, v4Online } = useV4Health()

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
        </nav>

        <div className="header__status">
          <div className={`status-dot ${(!isOnline || !v4Online) ? 'status-dot--offline' : ''}`} />
          <span>{!isOnline ? '服务离线' : !v4Online ? 'V4 服务不可用' : '在线服务'}</span>
        </div>
      </header>

      {/* Main */}
      <main className="main">
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/correctness" element={<CorrectnessPage />} />
          <Route path="/completeness" element={<CompletenessPage />} />
        </Routes>
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
