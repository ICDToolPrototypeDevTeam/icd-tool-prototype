import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, Info, SlidersHorizontal } from 'lucide-react'
import { ApiError, getModelSettingsV4, updateModelSettingsV4 } from '../api'
import type { V4ModelSettings, V4ModelSettingsUpdate } from '../types'

type ModelKey = keyof V4ModelSettingsUpdate

interface ProviderMeta {
  key: ModelKey
  name: string
  short: string
  role: string
  desc: string
  tone: 'blue' | 'violet' | 'amber'
  placeholder: string
}

const PROVIDERS: ProviderMeta[] = [
  {
    key: 'deepseek_model',
    name: 'DeepSeek',
    short: 'DS',
    role: '标注 · 共识 · 裁判',
    desc: '负责 HLR 智能标注与 Review 共识复核，同时作为三个裁判模型之一',
    tone: 'blue',
    placeholder: 'deepseek-v4-flash',
  },
  {
    key: 'minimax_model',
    name: 'MiniMax',
    short: 'MM',
    role: '裁判',
    desc: '参与正确性分析的多模型独立裁判与低星复查',
    tone: 'violet',
    placeholder: 'MiniMax-M2.5',
  },
  {
    key: 'qwen_model',
    name: 'Qwen',
    short: 'QW',
    role: '裁判',
    desc: '参与正确性分析的多模型独立裁判与低星复查',
    tone: 'amber',
    placeholder: 'qwen3.6-35b-a3b',
  },
]

function pickForm(settings: V4ModelSettings): V4ModelSettingsUpdate {
  return {
    deepseek_model: settings.deepseek_model,
    minimax_model: settings.minimax_model,
    qwen_model: settings.qwen_model,
  }
}

/** FastAPI 的错误体是 JSON（detail 可能是字符串或校验错误数组），转成可读文案。 */
function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    try {
      const body = JSON.parse(error.message) as { detail?: unknown }
      if (typeof body.detail === 'string') return body.detail
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

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

export default function SettingsPage() {
  const [saved, setSaved] = useState<V4ModelSettings | null>(null)
  const [form, setForm] = useState<V4ModelSettingsUpdate | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const loadSettings = useCallback(async () => {
    setLoading(true)
    setError('')
    setNotice('')
    try {
      const data = await getModelSettingsV4()
      setSaved(data)
      setForm(pickForm(data))
    } catch (e) {
      setForm(null)
      setError(describeError(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadSettings()
  }, [loadSettings])

  const dirty =
    saved !== null &&
    form !== null &&
    PROVIDERS.some((provider) => form[provider.key].trim() !== saved[provider.key])

  function updateField(key: ModelKey, value: string) {
    setForm((current) => (current ? { ...current, [key]: value } : current))
    setNotice('')
    setError('')
  }

  function handleReset() {
    if (!saved) return
    setForm(pickForm(saved))
    setNotice('')
    setError('')
  }

  async function handleSave() {
    if (!form || saving) return
    const payload: V4ModelSettingsUpdate = {
      deepseek_model: form.deepseek_model.trim(),
      minimax_model: form.minimax_model.trim(),
      qwen_model: form.qwen_model.trim(),
    }
    if (!payload.deepseek_model || !payload.minimax_model || !payload.qwen_model) {
      setNotice('')
      setError('三个模型名称都不能为空')
      return
    }

    setSaving(true)
    setError('')
    setNotice('')
    try {
      const updated = await updateModelSettingsV4(payload)
      setSaved(updated)
      setForm(pickForm(updated))
      setNotice('已保存，后续模型调用将使用新的模型名称')
    } catch (e) {
      setError(describeError(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="landing">
      <div className="landing-hero">
        <h1 className="landing-hero__title">模型设置</h1>
        <p className="landing-hero__subtitle">
          配置三类分析模型的服务端模型名称，保存后对新任务生效，无需重启服务
        </p>
      </div>

      <section className="settings-card">
        <header className="settings-card__header">
          <div className="settings-card__icon">
            <SlidersHorizontal size={22} />
          </div>
          <div className="settings-card__heading">
            <div className="settings-card__title-row">
              <h2 className="settings-card__title">分析模型</h2>
              {saved && (
                <span
                  className={`settings-card__badge ${
                    saved.customized ? 'settings-card__badge--custom' : ''
                  }`}
                >
                  {saved.customized ? '已自定义' : '部署默认'}
                </span>
              )}
            </div>
            <p className="settings-card__subtitle">
              三个模型分别用于正确性分析的多模型裁判与完整性分析的多 judge 判定
            </p>
          </div>
          {saved?.customized && saved.updated_at && (
            <div className="settings-card__updated">
              上次保存
              <br />
              {formatTime(saved.updated_at)}
            </div>
          )}
        </header>

        <div className="settings-card__body">
          {loading && <div className="settings-state">正在加载当前配置…</div>}

          {!loading && !form && (
            <div className="settings-state settings-state--error">
              <span>{error || '加载失败'}</span>
              <button className="btn btn--secondary" onClick={() => void loadSettings()}>
                重新加载
              </button>
            </div>
          )}

          {form && (
            <>
              <div className="model-fields">
                {PROVIDERS.map((provider) => (
                  <label className="model-field" key={provider.key}>
                    <span
                      className={`model-field__badge model-field__badge--${provider.tone}`}
                    >
                      {provider.short}
                    </span>
                    <span className="model-field__info">
                      <span className="model-field__name">
                        {provider.name}
                        <span className="model-field__role">{provider.role}</span>
                      </span>
                      <span className="model-field__desc">{provider.desc}</span>
                    </span>
                    <input
                      className="model-field__input"
                      value={form[provider.key]}
                      placeholder={provider.placeholder}
                      maxLength={200}
                      spellCheck={false}
                      autoComplete="off"
                      onChange={(event) => updateField(provider.key, event.target.value)}
                    />
                  </label>
                ))}
              </div>

              <div className="settings-card__hint">
                <Info size={15} />
                <span>
                  设置保存在服务器输出目录，容器重启后仍然有效。修改只影响之后的模型调用；
                  如当前有任务正在运行，建议等其结束后再保存。
                </span>
              </div>

              {notice && (
                <div className="settings-card__notice settings-card__notice--success">
                  <CheckCircle2 size={15} />
                  {notice}
                </div>
              )}
              {error && (
                <div className="settings-card__notice settings-card__notice--error">
                  <AlertCircle size={15} />
                  {error}
                </div>
              )}

              <div className="settings-card__footer">
                <button
                  className="btn btn--secondary"
                  onClick={handleReset}
                  disabled={!dirty || saving}
                >
                  撤销修改
                </button>
                <button
                  className="btn btn--primary"
                  onClick={() => void handleSave()}
                  disabled={!dirty || saving}
                >
                  {saving ? '保存中…' : '保存设置'}
                </button>
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  )
}
