import { Fragment, useEffect, useRef, useState } from 'react'
import { CheckCircle2, SlidersHorizontal, X } from 'lucide-react'
import {
  ApiError,
  createHlrSchema,
  deleteHlrSchema,
  listHlrSchemas,
  previewHlrSchema,
  scanHlrSchema,
  updateHlrSchema,
} from '../api'
import type {
  HlrSchemaConfig,
  HlrSchemaItem,
  HlrSchemaPayload,
  HlrSchemaPreviewResult,
  HlrSchemaScanTable,
} from '../types'

interface Props {
  hlrFile: File | null
  controllerProfile: string
  onControllerProfileChange: (value: string) => void
  onChange: (payload: HlrSchemaPayload | null) => void
  /** 供上传页拦截「自定义样式但未选系统类型」的提交。 */
  onCustomActiveChange: (active: boolean) => void
}

// 标准字段的中文名，术语与各控制器 profile 的行标签保持一致。
const FIELD_LABELS: Record<string, string> = {
  id: '需求编号',
  content: '需求正文',
  rationale: '基本原理',
  object_type: '对象类型',
  is_derived: '是否衍生',
  is_safety_related: '安全相关',
  verification_method: '验证方法',
  implementation: '实现方法',
  code: '代码',
  source: '来源',
  covered_ids: '覆盖需求ID',
  notes: '注释',
  input_data: '输入数据',
  output_data: '输出数据',
  is_requirement: '是否为需求',
}

// 额外字段下拉（与后端 ALLOWED_SCHEMA_FIELDS 去掉 id/content 后的顺序一致）
const EXTRA_FIELDS = [
  'rationale',
  'object_type',
  'is_derived',
  'is_safety_related',
  'verification_method',
  'implementation',
  'code',
  'source',
  'covered_ids',
  'notes',
  'input_data',
  'output_data',
  'is_requirement',
]

const SHORT_PROFILE: Record<string, string> = {
  ams: 'AMS',
  fgmc: 'FGMC',
  hscu: 'HSCU',
  rpdu: 'RPDU',
  generic: '其他系统',
}

type BusyKind = '' | 'scan' | 'preview' | 'save' | 'update' | 'delete'
type Mode = 'builtin' | 'custom'
type StyleSource =
  | { kind: 'seed'; templateId: string }
  | { kind: 'saved'; id: string }
  | { kind: 'blank' }

function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field
}

/** 完整配置对象本身提交，不包 `{ config: ... }` 外壳（见 HlrSchemaPayload 注释）。 */
function buildConfig(
  fields: Record<string, string[]>,
  idPattern: string,
): HlrSchemaConfig {
  return {
    version: 1,
    mode: 'docx_vertical',
    fields,
    identify: { id_pattern: idPattern },
  }
}

function cloneFields(source: Record<string, string[]>): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  for (const [key, labels] of Object.entries(source)) {
    out[key] = [...labels]
  }
  return out
}

function hasLabels(fields: Record<string, string[]>): boolean {
  return Object.values(fields).some((labels) => labels.length > 0)
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

export default function HlrSchemaEditor({
  hlrFile,
  controllerProfile,
  onControllerProfileChange,
  onChange,
  onCustomActiveChange,
}: Props) {
  const [templates, setTemplates] = useState<HlrSchemaItem[]>([])
  // 挂载时若系统类型已是「其他系统」（例如清空后重挂载），直接进入自定义模式
  const [mode, setMode] = useState<Mode>(
    controllerProfile === 'generic' ? 'custom' : 'builtin',
  )
  const [source, setSource] = useState<StyleSource>({ kind: 'blank' })
  const [dirty, setDirty] = useState(false)
  const [fields, setFields] = useState<Record<string, string[]>>({})
  const [idPattern, setIdPattern] = useState('')
  const [pinnedFields, setPinnedFields] = useState<string[]>([])
  const [activeField, setActiveField] = useState('id')
  const [scanTables, setScanTables] = useState<HlrSchemaScanTable[]>([])
  const [selectedTableIndex, setSelectedTableIndex] = useState<number | null>(null)
  const [scanned, setScanned] = useState(false)
  const [preview, setPreview] = useState<HlrSchemaPreviewResult | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<BusyKind>('')
  // 最近一次提交给父组件的 payload 的本地镜像，供「试解析」与禁用态使用
  const [payload, setPayload] = useState<HlrSchemaPayload | null>(null)
  // 自定义样式编辑器放在弹窗里：页面只保留系统类型 / 解析方式 / 样式摘要
  const [editorOpen, setEditorOpen] = useState(false)

  const prevFileRef = useRef<File | null>(null)
  const dialogRef = useRef<HTMLDialogElement | null>(null)

  const customTemplates = templates.filter((t) => t.source === 'custom')
  const savedTemplate =
    source.kind === 'saved'
      ? (templates.find((t) => t.id === source.id) ?? null)
      : null
  const selectedTable =
    selectedTableIndex === null
      ? null
      : (scanTables.find((t) => t.table_index === selectedTableIndex) ?? null)

  const isBusy = busy !== ''
  const systemSupportsBuiltin = controllerProfile !== 'generic'
  const systemSupportsCustom =
    controllerProfile !== '' && controllerProfile !== 'rpdu'
  const controllerMissing = mode === 'custom' && !controllerProfile
  const canPreview =
    hlrFile !== null &&
    payload !== null &&
    !controllerMissing &&
    !isBusy
  const canSaveNew = mode === 'custom' && source.kind !== 'saved'

  const visibleFields = [
    'id',
    'content',
    ...EXTRA_FIELDS.filter(
      (field) => (fields[field]?.length ?? 0) > 0 || pinnedFields.includes(field),
    ),
  ]

  const labelOwner = new Map<string, string>()
  for (const [field, labels] of Object.entries(fields)) {
    for (const label of labels) labelOwner.set(label.toLowerCase(), field)
  }

  // 挂载时加载内置模板 + 已保存自定义样式（同一接口混合返回）
  useEffect(() => {
    let cancelled = false
    listHlrSchemas()
      .then((items) => {
        if (!cancelled) setTemplates(items)
      })
      .catch((e) => {
        if (!cancelled) setError(describeError(e))
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    onCustomActiveChange(mode === 'custom')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])

  // 原生 <dialog>：showModal 提供焦点陷阱、Esc 关闭与顶层遮罩
  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (editorOpen && !dialog.open) dialog.showModal()
    if (!editorOpen && dialog.open) dialog.close()
  }, [editorOpen])

  // 弹窗打开时锁住页面滚动
  useEffect(() => {
    if (!editorOpen) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [editorOpen])

  // hlrFile 为 null（未上传，或点了「清空全部」）时无条件复位编辑器并
  // onChange(null)，保证「界面可见的样式」与「父组件将要提交的样式」一致；
  // 非空文件变化时只清扫描/试解析结果（用 ref 保证只在真的换了文件时触发）。
  useEffect(() => {
    if (hlrFile === null) {
      prevFileRef.current = null
      resetAll()
      return
    }
    if (prevFileRef.current === hlrFile) return
    prevFileRef.current = hlrFile
    setScanTables([])
    setSelectedTableIndex(null)
    setScanned(false)
    setPreview(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hlrFile])

  function resetAll() {
    setScanTables([])
    setSelectedTableIndex(null)
    setScanned(false)
    setPreview(null)
    setEditorOpen(false)
    // 其他系统没有内置规则，复位时保持自定义模式，避免落在不可用的「内置规则」上
    setMode(controllerProfile === 'generic' ? 'custom' : 'builtin')
    setSource({ kind: 'blank' })
    setDirty(false)
    setFields({})
    setIdPattern('')
    setPinnedFields([])
    setActiveField('id')
    setName('')
    setError('')
    setPayload(null)
    onChange(null)
  }

  function resetDraft() {
    setPreview(null)
    setSource({ kind: 'blank' })
    setDirty(false)
    setFields({})
    setIdPattern('')
    setPinnedFields([])
    setActiveField('id')
    setName('')
  }

  function emit(next: HlrSchemaPayload | null) {
    setPayload(next)
    onChange(next)
  }

  /** 以「当前系统的内置模板」预填一份草稿；未做修改时仍走原生解析。 */
  function applySeed(template: HlrSchemaItem) {
    setSource({ kind: 'seed', templateId: template.id })
    setDirty(false)
    setFields(cloneFields(template.config.fields))
    setIdPattern(template.config.identify?.id_pattern ?? '')
    setPinnedFields([])
    setActiveField('id')
    setName('')
  }

  function builtinFor(profileId: string): HlrSchemaItem | null {
    return templates.find((t) => t.id === `builtin_${profileId}`) ?? null
  }

  /** 按当前草稿状态计算实际要提交的样式引用。 */
  function computePayload(
    nextFields: Record<string, string[]>,
    nextPattern: string,
    nextSource: StyleSource,
    nextDirty: boolean,
  ): HlrSchemaPayload | null {
    if (nextSource.kind === 'seed' && !nextDirty) {
      return { template_id: nextSource.templateId }
    }
    if (nextSource.kind === 'saved' && !nextDirty) {
      return { schema_id: nextSource.id }
    }
    if (!nextFields.id?.length || !nextFields.content?.length) return null
    return buildConfig(nextFields, nextPattern)
  }

  function commit(
    nextFields: Record<string, string[]>,
    nextPattern: string,
    nextDirty = true,
  ) {
    setFields(nextFields)
    setIdPattern(nextPattern)
    setDirty(nextDirty)
    setPreview(null)
    emit(computePayload(nextFields, nextPattern, source, nextDirty))
  }

  function handleSystemChange(nextProfile: string) {
    setError('')
    setPreview(null)
    onControllerProfileChange(nextProfile)

    if (nextProfile === 'rpdu') {
      resetDraft()
      setMode('builtin')
      setEditorOpen(false)
      emit(null)
      return
    }

    if (nextProfile === 'generic') {
      setMode('custom')
      if (source.kind === 'saved') {
        emit(computePayload(fields, idPattern, source, dirty))
        return
      }
      resetDraft()
      emit(null)
      // 其他系统没有内置规则，选完系统直接进入配置
      setEditorOpen(true)
      return
    }

    if (!nextProfile) {
      // 自动识别：内置路径不需要样式；自定义草稿保留，但提交会被上传页拦截
      if (mode === 'builtin') {
        emit(null)
      } else {
        emit(computePayload(fields, idPattern, source, dirty))
      }
      return
    }

    // ams / fgmc / hscu
    if (mode === 'builtin') {
      emit(null)
      return
    }
    if (source.kind === 'saved') {
      emit(computePayload(fields, idPattern, source, dirty))
      return
    }
    if (source.kind === 'seed' && !dirty) {
      const template = builtinFor(nextProfile)
      if (template) {
        applySeed(template)
        emit({ template_id: template.id })
      } else {
        resetDraft()
        emit(null)
      }
      return
    }
    if (!hasLabels(fields)) {
      const template = builtinFor(nextProfile)
      if (template) {
        applySeed(template)
        emit({ template_id: template.id })
      } else {
        resetDraft()
        emit(null)
      }
      return
    }
    emit(computePayload(fields, idPattern, { kind: 'blank' }, true))
  }

  function switchMode(next: Mode) {
    if (next === mode) return
    setError('')
    setPreview(null)
    if (next === 'builtin') {
      setMode('builtin')
      setEditorOpen(false)
      emit(null)
      return
    }
    if (!systemSupportsCustom) return
    setMode('custom')
    // 选择自定义样式即进入编辑弹窗，避免只切换了分段却没有下一步
    setEditorOpen(true)
    if (source.kind === 'saved') {
      emit(computePayload(fields, idPattern, source, dirty))
      return
    }
    if (source.kind === 'seed' && !dirty) {
      emit({ template_id: source.templateId })
      return
    }
    if (hasLabels(fields)) {
      emit(computePayload(fields, idPattern, { kind: 'blank' }, true))
      return
    }
    if (controllerProfile === 'generic') {
      resetDraft()
      emit(null)
      return
    }
    const template = builtinFor(controllerProfile)
    if (template) {
      applySeed(template)
      emit({ template_id: template.id })
    } else {
      resetDraft()
      emit(null)
    }
  }

  function handleSavedSelect(value: string) {
    setError('')
    setPreview(null)
    if (!value) {
      // 回到「基于当前系统内置规则」的新草稿
      if (controllerProfile !== 'generic') {
        const template = builtinFor(controllerProfile)
        if (template) {
          applySeed(template)
          emit({template_id: template.id})
          return
        }
      }
      resetDraft()
      emit(null)
      return
    }
    const item = templates.find((t) => t.id === value)
    if (!item) return
    setSource({ kind: 'saved', id: item.id })
    setDirty(false)
    setFields(cloneFields(item.config.fields))
    setIdPattern(item.config.identify?.id_pattern ?? '')
    setPinnedFields([])
    setActiveField('id')
    setName(item.name)
    emit({ schema_id: item.id })
  }

  /** 任何标签/idPattern 编辑：内置模板或已保存样式进入「已修改」状态。 */
  function applyCustomEdit(
    nextFields: Record<string, string[]>,
    nextPattern: string,
  ) {
    commit(nextFields, nextPattern)
  }

  function addLabel(label: string) {
    const trimmed = label.trim()
    if (!trimmed) return
    const key = trimmed.toLowerCase()
    const current = fields[activeField] ?? []
    if (current.some((l) => l.toLowerCase() === key)) return
    const owner = labelOwner.get(key)
    if (owner && owner !== activeField) {
      setError(
        `标签「${trimmed}」已用于「${fieldLabel(owner)}」，同一标签不能映射到多个字段`,
      )
      return
    }
    const next = { ...fields, [activeField]: [...current, trimmed] }
    setError('')
    applyCustomEdit(next, idPattern)
  }

  function removeLabel(field: string, label: string) {
    const next: Record<string, string[]> = {}
    for (const [key, labels] of Object.entries(fields)) {
      if (key !== field) {
        next[key] = [...labels]
      } else {
        const remaining = labels.filter((l) => l !== label)
        if (remaining.length > 0) next[key] = remaining
      }
    }
    setError('')
    applyCustomEdit(next, idPattern)
  }

  function handleIdPatternChange(value: string) {
    setError('')
    applyCustomEdit(fields, value)
  }

  async function handleScan() {
    if (!hlrFile || isBusy) return
    setBusy('scan')
    setError('')
    try {
      const tables = await scanHlrSchema(hlrFile)
      setScanTables(tables)
      const first =
        tables.find((t) => t.looks_like_requirement) ?? tables[0] ?? null
      setSelectedTableIndex(first ? first.table_index : null)
      setScanned(true)
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy('')
    }
  }

  async function handlePreview() {
    if (!hlrFile || !payload || isBusy) return
    if (controllerMissing) {
      setError('请先选择系统类型')
      return
    }
    setBusy('preview')
    setError('')
    try {
      const result = await previewHlrSchema(hlrFile, payload, controllerProfile)
      setPreview(result)
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy('')
    }
  }

  function validateForSave(): string | null {
    if (!name.trim()) return '请填写样式名称'
    if (!fields.id?.length || !fields.content?.length) {
      return '样式必须包含「需求编号」与「需求正文」的标签'
    }
    return null
  }

  async function handleSaveNew() {
    if (isBusy) return
    const problem = validateForSave()
    if (problem) {
      setError(problem)
      return
    }
    setBusy('save')
    setError('')
    try {
      const created = await createHlrSchema({
        name: name.trim(),
        config: buildConfig(fields, idPattern),
      })
      setSource({ kind: 'saved', id: created.id })
      setDirty(false)
      setName(created.name)
      setPreview(null)
      emit({ schema_id: created.id })
      try {
        setTemplates(await listHlrSchemas())
      } catch (e) {
        // 刷新列表失败时至少把新样式并入本地列表，保证选中项可见
        setTemplates((prev) => [...prev, created])
        setError(describeError(e))
      }
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy('')
    }
  }

  async function handleUpdate() {
    if (isBusy || source.kind !== 'saved' || !savedTemplate) return
    const problem = validateForSave()
    if (problem) {
      setError(problem)
      return
    }
    setBusy('update')
    setError('')
    try {
      const updated = await updateHlrSchema(source.id, {
        name: name.trim(),
        config: buildConfig(fields, idPattern),
      })
      setName(updated.name)
      setDirty(false)
      setPreview(null)
      emit({ schema_id: updated.id })
      try {
        setTemplates(await listHlrSchemas())
      } catch (e) {
        setTemplates((prev) =>
          prev.map((t) => (t.id === updated.id ? updated : t)),
        )
        setError(describeError(e))
      }
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy('')
    }
  }

  async function handleDelete() {
    if (isBusy || source.kind !== 'saved' || !savedTemplate) return
    if (!window.confirm(`确定删除自定义样式「${savedTemplate.name}」吗？`)) return
    setBusy('delete')
    setError('')
    try {
      await deleteHlrSchema(savedTemplate.id)
    } catch (e) {
      setError(describeError(e))
      setBusy('')
      return
    }
    // 删除成功后停止提交已删除的 schema_id；其他系统没有内置规则，保持自定义空草稿
    resetDraft()
    setMode(controllerProfile === 'generic' ? 'custom' : 'builtin')
    if (controllerProfile !== 'generic') setEditorOpen(false)
    emit(null)
    setTemplates((prev) => prev.filter((t) => t.id !== savedTemplate.id))
    try {
      setTemplates(await listHlrSchemas())
    } catch (e) {
      setError(describeError(e))
    }
    setBusy('')
  }

  function selectLabelRow(row: { label: string; value_preview: string }) {
    const owner = labelOwner.get(row.label.toLowerCase())
    if (owner) {
      setActiveField(owner)
      return
    }
    addLabel(row.label)
  }

  const profileShort = controllerProfile
    ? (SHORT_PROFILE[controllerProfile] ?? controllerProfile)
    : ''

  const mappedFieldCount = Object.values(fields).filter(
    (labels) => labels.length > 0,
  ).length
  const configured =
    (fields.id?.length ?? 0) > 0 && (fields.content?.length ?? 0) > 0
  const showDirty = dirty && (source.kind === 'saved' || source.kind === 'seed')

  let summaryTitle = '尚未配置'
  if (source.kind === 'saved') {
    summaryTitle = name.trim() || savedTemplate?.name || '已保存样式'
  } else if (source.kind === 'seed') {
    summaryTitle = profileShort ? `基于 ${profileShort} 内置规则` : '自定义样式'
  } else if (hasLabels(fields)) {
    summaryTitle = '手工配置'
  }

  let badgeText = '自动识别'
  let badgeAccent = false
  if (mode === 'custom') {
    badgeText = '自定义样式'
    badgeAccent = true
  } else if (controllerProfile) {
    badgeText = `${profileShort} 内置规则`
  }

  const dialogStatus = controllerMissing
    ? '请先在页面上选择系统类型'
    : configured
      ? `已映射 ${mappedFieldCount} 个字段，修改会立即用于本次分析`
      : controllerProfile === 'generic'
        ? '映射需求编号与需求正文后才能开始处理'
        : '尚未完成配置，本次将按系统内置规则解析'

  const seedOptionLabel =
    controllerProfile === 'generic'
      ? '新建空白样式'
      : profileShort
        ? `基于 ${profileShort} 内置规则`
        : '新建样式'

  return (
    <section className="hlr-rules">
      <div className="hlr-rules__header">
        <div className="hlr-rules__heading">
          <SlidersHorizontal size={18} className="hlr-rules__icon" />
          <h3 className="hlr-rules__title">解析规则</h3>
          <span
            className={`hlr-rules__badge${badgeAccent ? ' hlr-rules__badge--accent' : ''}`}
          >
            {badgeText}
          </span>
        </div>
      </div>

      <div className="hlr-rules__bar">
        <label className="hlr-field">
          <span className="hlr-field__label">系统类型</span>
          <select
            className="hlr-select"
            value={controllerProfile}
            onChange={(e) => handleSystemChange(e.target.value)}
            disabled={isBusy}
          >
            <option value="">自动识别</option>
            <option value="ams">环控系统（AMS）</option>
            <option value="fgmc">燃油系统（FGMC）</option>
            <option value="hscu">液压系统（HSCU）</option>
            <option value="rpdu">远程功率分配单元（RPDU）</option>
            <option value="generic">其他系统</option>
          </select>
        </label>

        <div className="hlr-field">
          <span className="hlr-field__label">解析方式</span>
          <div className="hlr-segment" role="group" aria-label="解析方式">
            <button
              type="button"
              className={`hlr-segment__btn${mode === 'builtin' ? ' hlr-segment__btn--active' : ''}`}
              aria-pressed={mode === 'builtin'}
              onClick={() => switchMode('builtin')}
              disabled={!systemSupportsBuiltin || isBusy}
              title={
                systemSupportsBuiltin
                  ? '按系统内置规则解析'
                  : '其他系统没有内置规则，需要自定义样式'
              }
            >
              内置规则
            </button>
            <button
              type="button"
              className={`hlr-segment__btn${mode === 'custom' ? ' hlr-segment__btn--active' : ''}`}
              aria-pressed={mode === 'custom'}
              onClick={() => switchMode('custom')}
              disabled={!systemSupportsCustom || isBusy}
              title={
                systemSupportsCustom
                  ? '自定义表格样式'
                  : controllerProfile === 'rpdu'
                    ? 'RPDU 使用内置 Excel 解析'
                    : '请先选择系统类型'
              }
            >
              自定义样式
            </button>
          </div>
        </div>

        {mode === 'custom' && (
          <div className="hlr-field hlr-field--grow">
            <span className="hlr-field__label">表格样式</span>
            <div
              className={`hlr-summary${configured ? '' : ' hlr-summary--empty'}`}
            >
              <span className="hlr-summary__text" title={summaryTitle}>
                {summaryTitle}
                {mappedFieldCount > 0 && (
                  <span className="hlr-summary__meta">
                    {' '}
                    · {mappedFieldCount} 个字段
                  </span>
                )}
                {showDirty && <span className="hlr-summary__dirty">已修改</span>}
              </span>
              <button
                type="button"
                className={`hlr-summary__edit${configured ? '' : ' hlr-summary__edit--attention'}`}
                onClick={() => {
                  setError('')
                  setEditorOpen(true)
                }}
                disabled={isBusy}
              >
                {configured ? '编辑' : '配置样式'}
              </button>
            </div>
          </div>
        )}
      </div>

      {mode === 'builtin' && controllerProfile === 'rpdu' && (
        <div className="hlr-alert hlr-alert--info">
          RPDU 使用内置 Excel 解析，无需配置表格样式。
        </div>
      )}

      {controllerMissing && (
        <div className="hlr-alert hlr-alert--warn">
          自定义样式需要明确系统类型，请先在上方选择。
        </div>
      )}

      <dialog
        ref={dialogRef}
        className="hlr-dialog"
        aria-labelledby="hlr-dialog-title"
        onClose={() => setEditorOpen(false)}
        onClick={(e) => {
          if (e.target === e.currentTarget) setEditorOpen(false)
        }}
      >
        <div className="hlr-dialog__surface">
          <header className="hlr-dialog__head">
            <div>
              <h3 className="hlr-dialog__title" id="hlr-dialog-title">
                编辑表格样式
              </h3>
              <p className="hlr-dialog__subtitle">
                {profileShort || '未选择系统'} · 自定义样式
              </p>
            </div>
            <button
              type="button"
              className="hlr-dialog__close"
              aria-label="关闭"
              onClick={() => setEditorOpen(false)}
            >
              <X size={18} />
            </button>
          </header>

          <div className="hlr-dialog__body">
            {controllerMissing && (
              <div className="hlr-alert hlr-alert--warn hlr-alert--dialog">
                自定义样式需要明确系统类型，请关闭弹窗后在上方选择。
              </div>
            )}

            <div className="hlr-dialog__source">
              <span className="hlr-field__label">样式来源</span>
              {customTemplates.length === 0 ? (
                <span className="hlr-static">{seedOptionLabel}</span>
              ) : (
                <select
                  className="hlr-select"
                  value={source.kind === 'saved' ? source.id : ''}
                  onChange={(e) => handleSavedSelect(e.target.value)}
                  disabled={isBusy}
                  aria-label="样式来源"
                >
                  <option value="">{seedOptionLabel}</option>
                  <optgroup label="已保存样式">
                    {customTemplates.map((template) => (
                      <option key={template.id} value={template.id}>
                        {template.name}
                      </option>
                    ))}
                  </optgroup>
                </select>
              )}
            </div>

            <div className="hlr-editor__tools">
              <button
                type="button"
                className="btn btn--secondary hlr-btn"
                onClick={handleScan}
                disabled={!hlrFile || isBusy}
                title={hlrFile ? undefined : '请先上传 SWHLR Word 文件'}
              >
                {busy === 'scan' ? '读取中…' : '读取文档表格'}
              </button>
              {scanTables.length > 0 && (
                <select
                  className="hlr-select hlr-select--compact"
                  value={
                    selectedTableIndex === null ? '' : String(selectedTableIndex)
                  }
                  onChange={(e) =>
                    setSelectedTableIndex(
                      e.target.value === '' ? null : Number(e.target.value),
                    )
                  }
                  disabled={isBusy}
                  aria-label="选择需求表"
                >
                  {scanTables.map((table) => (
                    <option key={table.table_index} value={table.table_index}>
                      表 {table.table_index + 1} · {table.row_count} 行 ×{' '}
                      {table.column_count} 列
                      {table.looks_like_requirement ? ' · 疑似需求表' : ''}
                    </option>
                  ))}
                </select>
              )}
              {!hlrFile ? (
                <span className="hlr-editor__tip">
                  请先上传 SWHLR Word 文件，再读取文档表格。
                </span>
              ) : !scanned ? (
                <span className="hlr-editor__tip">
                  {controllerProfile === 'generic'
                    ? '该文档没有内置规则：读取表格后手动映射编号与正文。'
                    : '需要更换行标签时，先读取文档表格。'}
                </span>
              ) : null}
              {scanned && scanTables.length === 0 && !error && (
                <span className="hlr-editor__tip">未在文档中识别到表格</span>
              )}
              <button
                type="button"
                className="btn btn--secondary hlr-btn hlr-tools__preview"
                onClick={handlePreview}
                disabled={!canPreview}
                title={
                  canPreview ? undefined : '请先完成需求编号与需求正文的映射'
                }
              >
                {busy === 'preview' ? '试解析中…' : '试解析'}
              </button>
            </div>

            <div className="hlr-editor__grid">
              <section className="hlr-panel">
                <div className="hlr-panel__head">
                  <span>字段映射</span>
                  <span className="hlr-panel__sub">先选字段，再点右侧行标签</span>
                </div>
                <div className="hlr-map">
                  {visibleFields.map((field) => {
                    const labels = fields[field] ?? []
                    const required = field === 'id' || field === 'content'
                    const active = activeField === field
                    return (
                      <Fragment key={field}>
                        <div
                          className={`hlr-map__row${active ? ' hlr-map__row--active' : ''}${field === 'id' ? ' hlr-map__row--has-sub' : ''}`}
                          role="button"
                          tabIndex={0}
                          onClick={() => setActiveField(field)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault()
                              setActiveField(field)
                            }
                          }}
                        >
                          <span className="hlr-map__field">
                            {fieldLabel(field)}
                            {required && (
                              <span className="hlr-map__required">*</span>
                            )}
                          </span>
                          <span className="hlr-map__labels">
                            {labels.length === 0 ? (
                              <span className="hlr-map__empty">未映射</span>
                            ) : (
                              labels.map((label) => (
                                <span key={label} className="hlr-chip">
                                  {label}
                                  <button
                                    type="button"
                                    title="移除"
                                    className="hlr-chip__remove"
                                    onClick={(e) => {
                                      e.stopPropagation()
                                      removeLabel(field, label)
                                    }}
                                  >
                                    <X size={11} />
                                  </button>
                                </span>
                              ))
                            )}
                          </span>
                        </div>
                        {field === 'id' && (
                          <div className="hlr-map__subrow">
                            <details className="hlr-idcheck">
                              <summary>
                                编号正则
                                {idPattern.trim() && (
                                  <span
                                    className="hlr-idcheck__value"
                                    title={idPattern.trim()}
                                  >
                                    {idPattern.trim()}
                                  </span>
                                )}
                              </summary>
                              <div className="hlr-idcheck__body">
                                <input
                                  className="hlr-input"
                                  value={idPattern}
                                  onChange={(e) =>
                                    handleIdPatternChange(e.target.value)
                                  }
                                  placeholder="可选，例如 ^[A-Z]+-\d+"
                                  aria-label="编号正则"
                                />
                                <p>
                                  用于校验提取出的需求编号；从开头匹配，整串匹配需加
                                  $；留空则不校验。
                                </p>
                              </div>
                            </details>
                          </div>
                        )}
                      </Fragment>
                    )
                  })}
                  <div className="hlr-map__tools">
                    <select
                      className="hlr-select hlr-select--compact"
                      value=""
                      onChange={(e) => {
                        const field = e.target.value
                        if (!field) return
                        setPinnedFields((prev) =>
                          prev.includes(field) ? prev : [...prev, field],
                        )
                        setActiveField(field)
                      }}
                      disabled={isBusy}
                      aria-label="添加字段"
                    >
                      <option value="">+ 添加字段</option>
                      {EXTRA_FIELDS.filter(
                        (field) => !visibleFields.includes(field),
                      ).map((field) => (
                        <option key={field} value={field}>
                          {fieldLabel(field)}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                {fields.is_requirement && fields.is_requirement.length > 0 && (
                  <p className="hlr-panel__note">
                    已启用非需求过滤：该行值为「否」的表格会被跳过。
                  </p>
                )}
              </section>

              <section className="hlr-panel">
                <div className="hlr-panel__head">
                  <span>文档行标签</span>
                  {selectedTable && (
                    <span className="hlr-panel__sub">
                      表 {selectedTable.table_index + 1} ·{' '}
                      {selectedTable.row_count} 行
                    </span>
                  )}
                </div>
                <div className="hlr-labels">
                  {!selectedTable ? (
                    <div className="hlr-empty">
                      {scanned
                        ? '未识别到可用的表格'
                        : '读取文档表格后，这里会列出每行标签。'}
                    </div>
                  ) : (
                    selectedTable.rows.map((row, idx) => {
                      const owner = labelOwner.get(row.label.toLowerCase())
                      return (
                        <button
                          key={`${row.label}-${idx}`}
                          type="button"
                          className="hlr-label-row"
                          onClick={() => selectLabelRow(row)}
                          disabled={isBusy}
                        >
                          <span className="hlr-label-row__name">{row.label}</span>
                          <span className="hlr-label-row__preview">
                            {row.value_preview}
                          </span>
                          {owner ? (
                            <span className="hlr-label-row__tag">
                              {fieldLabel(owner)}
                            </span>
                          ) : (
                            <span />
                          )}
                        </button>
                      )
                    })
                  )}
                </div>
              </section>
            </div>

            {preview && (
              <div className="hlr-preview">
                <div className="hlr-preview__title">
                  <CheckCircle2 size={15} />
                  试解析通过 · 共 {preview.total_count} 条需求
                  {preview.hook_applied ? '（已应用控制器预处理）' : ''}
                </div>
                {preview.requirements.length === 0 && (
                  <p className="hlr-preview__empty">
                    未解析出需求，请检查「需求编号」「需求正文」的映射。
                  </p>
                )}
                {preview.requirements.slice(0, 3).map((req, idx) => (
                  <p
                    key={`${req.requirement_id}-${idx}`}
                    className="hlr-preview__item"
                  >
                    <span className="hlr-preview__id">
                      {req.requirement_id || '（无编号）'}
                    </span>
                    {req.content_preview}
                  </p>
                ))}
                {preview.requirements.length > 3 && (
                  <p className="hlr-preview__empty">仅展示前 3 条。</p>
                )}
                {preview.warnings.length > 0 && (
                  <details className="hlr-preview__warnings">
                    <summary>{preview.warnings.length} 条提示</summary>
                    <ul>
                      {preview.warnings.map((w, idx) => (
                        <li key={idx}>{w.message}</li>
                      ))}
                    </ul>
                  </details>
                )}
                {preview.parse_warnings.length > 0 && (
                  <p className="hlr-preview__empty">
                    另有 {preview.parse_warnings.length} 条解析警告。
                  </p>
                )}
              </div>
            )}

            {error && <p className="hlr-error">{error}</p>}

            <div className="hlr-save">
              <input
                className="hlr-input hlr-input--name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="样式名称"
                disabled={isBusy}
                aria-label="样式名称"
              />
              {source.kind === 'saved' ? (
                <>
                  <button
                    type="button"
                    className="btn btn--secondary hlr-btn"
                    onClick={handleUpdate}
                    disabled={isBusy || !configured}
                    title={
                      configured
                        ? undefined
                        : '请先映射需求编号与需求正文'
                    }
                  >
                    {busy === 'update' ? '保存中…' : '保存修改'}
                  </button>
                  <button
                    type="button"
                    className="btn btn--danger hlr-btn"
                    onClick={handleDelete}
                    disabled={isBusy}
                  >
                    {busy === 'delete' ? '删除中…' : '删除'}
                  </button>
                </>
              ) : canSaveNew ? (
                <button
                  type="button"
                  className="btn btn--secondary hlr-btn"
                  onClick={handleSaveNew}
                  disabled={isBusy || !configured}
                  title={
                    configured ? undefined : '请先映射需求编号与需求正文'
                  }
                >
                  {busy === 'save' ? '保存中…' : '保存为新样式'}
                </button>
              ) : null}
            </div>
          </div>

          <footer className="hlr-dialog__foot">
            <span className="hlr-dialog__status">{dialogStatus}</span>
            <button
              type="button"
              className="btn btn--primary hlr-btn"
              onClick={() => setEditorOpen(false)}
            >
              完成
            </button>
          </footer>
        </div>
      </dialog>
    </section>
  )
}
