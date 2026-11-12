import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Users } from 'lucide-react'
import {
  ApiError,
  createAdminUserV4,
  listAdminUsersV4,
  updateAdminUserV4,
} from '../api'
import { useAuth } from '../auth/AuthContext'
import type { AdminUser, AdminUserUpdatePayload } from '../types'

/**
 * 后端 created_at 可能是 Mongo 返回的 naive UTC（无时区后缀），
 * 直接 new Date() 会被当成本地时间；无后缀时显式按 UTC 解析。
 */
function formatTime(value: string): string {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)
  const date = new Date(hasZone ? value : `${value}Z`)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

/** FastAPI 的错误体是 JSON（detail 可能是字符串或校验错误数组），转成可读文案。 */
function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    try {
      const body = JSON.parse(error.message) as { detail?: unknown }
      if (typeof body.detail === 'string') {
        // 409 包括「用户名已存在」与「最后一个活跃管理员」两类业务冲突
        return error.status === 409 ? `操作未完成：${body.detail}` : body.detail
      }
      if (Array.isArray(body.detail)) {
        const messages = body.detail
          .map((item) => (item as { msg?: unknown })?.msg)
          .filter((msg): msg is string => typeof msg === 'string')
        if (messages.length) return messages.join('；')
      }
    } catch {
      // 非 JSON 错误体：按原文展示
    }
    return error.message || '请求失败'
  }
  return error instanceof Error ? error.message : '请求失败'
}

export default function AdminUsersPage() {
  const { user, refresh } = useAuth()

  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  // 行操作与新建互不并发：busyId 表示正在执行写操作的账号
  const [busyId, setBusyId] = useState('')
  const [creating, setCreating] = useState(false)
  const [newUsername, setNewUsername] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newIsAdmin, setNewIsAdmin] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      setUsers(await listAdminUsersV4())
    } catch (e) {
      setLoadError(describeError(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (creating) return

    const username = newUsername.trim()
    if (!username) {
      setNotice('')
      setError('请填写用户名')
      return
    }
    if (/\s/.test(username)) {
      setNotice('')
      setError('用户名不能包含空白字符')
      return
    }
    if (newPassword.length < 8) {
      setNotice('')
      setError('密码至少 8 位')
      return
    }

    setCreating(true)
    setError('')
    setNotice('')
    try {
      await createAdminUserV4({
        username,
        password: newPassword,
        is_admin: newIsAdmin,
      })
      setNewUsername('')
      setNewPassword('')
      setNewIsAdmin(false)
      setNotice(`已创建账号「${username}」`)
      await load()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setCreating(false)
    }
  }

  async function applyUpdate(
    target: AdminUser,
    payload: AdminUserUpdatePayload,
    successText: string,
  ) {
    if (busyId) return
    setBusyId(target.id)
    setError('')
    setNotice('')
    try {
      const updated = await updateAdminUserV4(target.id, payload)

      // 自身被禁用或降级：先刷新登录态，路由守卫随即把页面移走；
      // 此时再拉取管理员列表只会得到 403，因此直接返回。
      const affectsSelf =
        user !== null &&
        updated.id === user.id &&
        (payload.is_active !== undefined || payload.is_superuser !== undefined)
      if (affectsSelf) {
        await refresh()
        return
      }

      setNotice(successText)
      await load()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusyId('')
    }
  }

  function handleResetPassword(target: AdminUser) {
    if (busyId) return
    const next = window.prompt(
      `为账号「${target.username}」设置新密码（至少 8 位）`,
    )
    if (next === null) return
    if (next.length < 8) {
      setNotice('')
      setError('新密码至少 8 位')
      return
    }
    void applyUpdate(
      target,
      { password: next },
      `已重置「${target.username}」的密码`,
    )
  }

  function handleToggleActive(target: AdminUser) {
    if (busyId) return
    void applyUpdate(
      target,
      { is_active: !target.is_active },
      target.is_active
        ? `已禁用「${target.username}」，该账号后续请求会被拒绝`
        : `已启用「${target.username}」`,
    )
  }

  function handleToggleAdmin(target: AdminUser) {
    if (busyId) return
    void applyUpdate(
      target,
      { is_superuser: !target.is_superuser },
      target.is_superuser
        ? `已取消「${target.username}」的管理员权限`
        : `已将「${target.username}」设为管理员`,
    )
  }

  const writeBusy = busyId !== '' || creating

  return (
    <div className="landing">
      <div className="landing-hero">
        <h1 className="landing-hero__title">用户管理</h1>
        <p className="landing-hero__subtitle">
          创建账号、重置密码、启用或禁用账号，并调整管理员角色；系统始终保留至少一个活跃管理员
        </p>
      </div>

      <section className="settings-card">
        <header className="settings-card__header">
          <div className="settings-card__icon">
            <Users size={22} />
          </div>
          <div className="settings-card__heading">
            <div className="settings-card__title-row">
              <h2 className="settings-card__title">账号列表</h2>
              {!loading && !loadError && (
                <span className="settings-card__badge">{users.length} 个账号</span>
              )}
            </div>
            <p className="settings-card__subtitle">
              禁用立即生效；删除账号不在本阶段范围内，需要停用的账号请禁用
            </p>
          </div>
        </header>

        <div className="settings-card__body">
          <form className="admin-users__create" onSubmit={handleCreate}>
            <label className="admin-users__create-field">
              <span className="admin-users__create-label">用户名</span>
              <input
                className="admin-users__input"
                type="text"
                value={newUsername}
                onChange={(e) => setNewUsername(e.target.value)}
                placeholder="例如 zhangsan"
                maxLength={64}
                autoComplete="off"
                spellCheck={false}
                disabled={creating}
              />
            </label>
            <label className="admin-users__create-field">
              <span className="admin-users__create-label">初始密码</span>
              <input
                className="admin-users__input"
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="至少 8 位"
                maxLength={128}
                autoComplete="new-password"
                disabled={creating}
              />
            </label>
            <label className="admin-users__check">
              <input
                type="checkbox"
                checked={newIsAdmin}
                onChange={(e) => setNewIsAdmin(e.target.checked)}
                disabled={creating}
              />
              管理员
            </label>
            <button
              type="submit"
              className="btn btn--primary"
              disabled={creating}
            >
              {creating ? '创建中…' : '创建账号'}
            </button>
          </form>

          {notice && (
            <div className="settings-card__notice settings-card__notice--success">
              {notice}
            </div>
          )}
          {error && (
            <div className="settings-card__notice settings-card__notice--error">
              {error}
            </div>
          )}

          {loading && <div className="settings-state">正在加载账号列表…</div>}

          {!loading && loadError && (
            <div className="settings-state settings-state--error">
              <span>{loadError}</span>
              <button className="btn btn--secondary" onClick={() => void load()}>
                重新加载
              </button>
            </div>
          )}

          {!loading && !loadError && users.length === 0 && (
            <div className="settings-state">还没有任何账号</div>
          )}

          {!loading && !loadError && users.length > 0 && (
            <div className="admin-users__table-wrap">
              <table className="admin-users__table">
                <thead>
                  <tr>
                    <th>用户名</th>
                    <th>角色</th>
                    <th>状态</th>
                    <th>创建时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((item) => (
                    <tr key={item.id}>
                      <td className="admin-users__username">{item.username}</td>
                      <td>
                        <span
                          className={`admin-users__badge ${
                            item.is_superuser
                              ? 'admin-users__badge--admin'
                              : 'admin-users__badge--user'
                          }`}
                        >
                          {item.is_superuser ? '管理员' : '普通用户'}
                        </span>
                      </td>
                      <td>
                        <span
                          className={`admin-users__badge ${
                            item.is_active
                              ? 'admin-users__badge--active'
                              : 'admin-users__badge--inactive'
                          }`}
                        >
                          {item.is_active ? '启用' : '禁用'}
                        </span>
                      </td>
                      <td className="admin-users__time">
                        {formatTime(item.created_at)}
                      </td>
                      <td>
                        <div className="admin-users__actions">
                          <button
                            type="button"
                            className="btn btn--secondary"
                            disabled={writeBusy}
                            onClick={() => handleResetPassword(item)}
                          >
                            重置密码
                          </button>
                          <button
                            type="button"
                            className="btn btn--secondary"
                            disabled={writeBusy}
                            onClick={() => handleToggleActive(item)}
                          >
                            {item.is_active ? '禁用' : '启用'}
                          </button>
                          <button
                            type="button"
                            className="btn btn--secondary"
                            disabled={writeBusy}
                            onClick={() => handleToggleAdmin(item)}
                          >
                            {item.is_superuser ? '取消管理员' : '设为管理员'}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
