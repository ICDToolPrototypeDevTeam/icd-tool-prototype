import type {
  AuditContainerInfo,
  AuditHistoryResponse,
  AuditResourceMetric,
  AuditSystemResponse,
} from '../../types'
import { AreaChart, Meter } from './Charts'
import {
  formatBytes,
  formatDateTime,
  formatPercent,
  formatRuntime,
  formatTimeOnly,
  resourceTone,
} from './format'

interface Props {
  data: AuditSystemResponse | null
  history: AuditHistoryResponse | null
  loading: boolean
  error: string
  onRefresh: () => void
}

const MODE_LABELS: Record<string, string> = {
  celery: 'Celery Worker 队列执行',
  inline: 'API 进程内执行',
}

const SOURCE_LABELS: Record<string, string> = {
  cgroup: '容器限额口径',
  process: '进程内存口径',
  filesystem: '文件系统口径',
  unavailable: '指标不可用',
}

function memoryDetail(metric: AuditResourceMetric): string {
  const used = formatBytes(metric.used_bytes)
  if (metric.limit_bytes === null) return `${used}（未设置限额）`
  return `${used} / ${formatBytes(metric.limit_bytes)}`
}

function workerCaption(worker: AuditContainerInfo): string {
  const parts = [`进程号 ${worker.pid}`]
  if (worker.pool_size) parts.push(`并发槽位 ${worker.pool_size}`)
  if (worker.children.length) parts.push(`子进程 ${worker.children.length}`)
  return parts.join('，')
}

/** 系统运行：健康结论、线性资源尺、队列读数、趋势与进程名录。 */
export function SystemMonitor({ data, history, loading, error, onRefresh }: Props) {
  if (error && !data) {
    return (
      <div className="audit-empty audit-empty--error">
        <span>{error}</span>
        <button type="button" className="audit-text-btn" onClick={onRefresh}>
          重新加载
        </button>
      </div>
    )
  }

  if (!data) {
    return <div className="audit-empty">正在采集系统运行状态…</div>
  }

  const workerMemory = data.worker_summary.memory
  const workerCpu = data.worker_summary.cpu_percent
  const apiCpu = data.api.cpu_percent

  const points = history?.points || []
  const hasMemoryPercent = points.some(
    (point) => point.api_memory_percent !== null || point.worker_memory_percent !== null,
  )
  const historyLabels = points.map((point) => formatTimeOnly(point.ts * 1000))

  const memorySeries = hasMemoryPercent
    ? [
        {
          key: 'api-memory',
          label: 'API 容器内存',
          color: '#0066cc',
          values: points.map((point) => point.api_memory_percent),
          fill: true,
        },
        {
          key: 'worker-memory',
          label: 'Worker 容器内存',
          color: '#d97706',
          values: points.map((point) => point.worker_memory_percent),
        },
      ]
    : [
        {
          key: 'api-rss',
          label: 'API 进程内存',
          color: '#0066cc',
          values: points.map((point) =>
            point.api_rss_bytes === null ? null : point.api_rss_bytes / 1024 / 1024,
          ),
          fill: true,
        },
        {
          key: 'worker-rss',
          label: 'Worker 进程内存',
          color: '#d97706',
          values: points.map((point) =>
            point.worker_rss_bytes === null ? null : point.worker_rss_bytes / 1024 / 1024,
          ),
        },
      ]

  const concurrency = data.queue.concurrency
  const queueText =
    concurrency === null
      ? `${data.queue.running} 个运行中`
      : `${data.queue.running} / ${concurrency}`

  const maxUserMemory = Math.max(1, ...data.users.map((user) => user.rss_bytes))
  const maxProcessMemory = Math.max(
    1,
    ...data.processes.map((process) => process.rss_bytes || 0),
  )

  return (
    <div className="audit-system">
      <section className={`audit-health audit-health--${data.health.level}`}>
        <span className="audit-health__rule" aria-hidden="true" />
        <div className="audit-health__body">
          <h2 className="audit-health__label">{data.health.label}</h2>
          <p className="audit-health__reasons">
            {data.health.reasons.length > 0
              ? data.health.reasons.join('；')
              : '所有监控指标均在正常范围内。'}
          </p>
        </div>
        <div className="audit-health__meta">
          <span>数据时间 {formatDateTime(data.generated_at)}</span>
          <span className="audit-health__live">
            <i className={loading ? 'is-loading' : ''} />
            {loading ? '刷新中' : '每 5 秒刷新'}
          </span>
        </div>
      </section>

      {history && !history.available && history.warning && (
        <div className="audit-inline-warning">{history.warning}</div>
      )}

      <div className="audit-meters">
        <Meter
          label="API 容器内存"
          percent={data.api.memory.percent}
          tone={resourceTone(data.api.memory.percent, 75, 90)}
          detail={memoryDetail(data.api.memory)}
          caption={`${SOURCE_LABELS[data.api.memory.source] || data.api.memory.source}，进程号 ${data.api.pid}`}
        />
        <Meter
          label="Worker 容器内存"
          percent={workerMemory.percent}
          tone={resourceTone(workerMemory.percent, 75, 90)}
          detail={memoryDetail(workerMemory)}
          caption={
            data.worker_summary.online
              ? `${data.worker_summary.count} 个 Worker，${data.worker_summary.pool_size} 个并发槽位`
              : 'Worker 指标未上报或已失联'
          }
        />
        <Meter
          label="Worker CPU"
          percent={workerCpu}
          tone={resourceTone(workerCpu, 85, 95)}
          detail={
            workerCpu === null
              ? '未采集到 CPU 指标'
              : `${formatPercent(workerCpu)}，可用 ${data.workers[0]?.cpu_available_cores ?? '—'} 核`
          }
          caption="按容器 CPU 配额折算；无配额时按物理核数折算"
        />
        <Meter
          label="结果目录磁盘"
          percent={data.disk.percent}
          tone={resourceTone(data.disk.percent, 80, 90)}
          detail={memoryDetail(data.disk)}
          caption="输出目录所在文件系统使用率"
        />
      </div>

      <div className="audit-readings">
        <div className="audit-reading">
          <span className="audit-reading__label">执行队列</span>
          <b className="audit-reading__value">{queueText}</b>
          <span className="audit-reading__hint">
            排队等待 {data.queue.length === null ? '—' : `${data.queue.length} 个`}，
            {data.worker_summary.online ? ' Worker 在线' : ' Worker 未上报'}
          </span>
        </div>
        <div className="audit-reading">
          <span className="audit-reading__label">任务进程</span>
          <b className="audit-reading__value">{data.processes.length}</b>
          <span className="audit-reading__hint">来自 Redis 控制面的活跃任务</span>
        </div>
        <div className="audit-reading">
          <span className="audit-reading__label">API 进程</span>
          <b className="audit-reading__value">
            {apiCpu === null ? '—' : formatPercent(apiCpu)}
          </b>
          <span className="audit-reading__hint">
            进程号 {data.api.pid}，线程 {data.api.threads ?? '—'}，已运行{' '}
            {formatRuntime(data.api.uptime_seconds)}
          </span>
        </div>
        <div className="audit-reading">
          <span className="audit-reading__label">Worker 最近上报</span>
          <b className="audit-reading__value">
            {data.worker_summary.last_seen
              ? formatTimeOnly(data.worker_summary.last_seen)
              : '—'}
          </b>
          <span className="audit-reading__hint">
            执行模式：{MODE_LABELS[data.mode] || data.mode}
          </span>
        </div>
      </div>

      <div className="audit-chart-grid">
        <section className="audit-chart-block">
          <header className="audit-chart-block__head">
            <h3 className="audit-chart-block__title">容器内存趋势</h3>
            <p className="audit-chart-block__sub">
              {hasMemoryPercent
                ? '按容器限额计算的使用率，最近 30 分钟'
                : '未设置容器限额，展示进程内存占用（MB）'}
            </p>
          </header>
          <AreaChart
            labels={historyLabels}
            series={memorySeries}
            height={180}
            percent={hasMemoryPercent}
            valueFormatter={(value) =>
              hasMemoryPercent ? `${Math.round(value)}%` : String(Math.round(value))
            }
            emptyText="历史采样点尚未积累，请稍后查看"
          />
        </section>

        <section className="audit-chart-block">
          <header className="audit-chart-block__head">
            <h3 className="audit-chart-block__title">CPU 使用趋势</h3>
            <p className="audit-chart-block__sub">API 与 Worker 容器的 CPU 使用率</p>
          </header>
          <AreaChart
            labels={historyLabels}
            series={[
              {
                key: 'api-cpu',
                label: 'API 容器 CPU',
                color: '#0066cc',
                values: points.map((point) => point.api_cpu_percent),
                fill: true,
              },
              {
                key: 'worker-cpu',
                label: 'Worker 容器 CPU',
                color: '#d97706',
                values: points.map((point) => point.worker_cpu_percent),
              },
            ]}
            height={180}
            percent
            valueFormatter={(value) => `${Math.round(value)}%`}
            emptyText="历史采样点尚未积累，请稍后查看"
          />
        </section>

        <section className="audit-chart-block audit-chart-block--wide">
          <header className="audit-chart-block__head">
            <h3 className="audit-chart-block__title">任务并发趋势</h3>
            <p className="audit-chart-block__sub">正在运行的任务数量，最近 30 分钟</p>
          </header>
          <AreaChart
            labels={historyLabels}
            series={[
              {
                key: 'running-jobs',
                label: '运行中任务',
                color: '#0066cc',
                values: points.map((point) => point.running_jobs),
                fill: true,
              },
            ]}
            height={150}
            integer
            valueFormatter={(value) => `${Math.round(value)} 个`}
            emptyText="历史采样点尚未积累，请稍后查看"
          />
        </section>
      </div>

      <section className="audit-sec audit-table-sec">
        <header className="audit-block-head">
          <div>
            <h2 className="audit-sec__title">任务进程</h2>
            <p className="audit-sec__desc">
              各人员正在使用的任务进程与资源占用，进程号与 Worker 子进程对应。
            </p>
          </div>
          <span className="audit-sec__note">{data.processes.length} 个</span>
        </header>
        {data.processes.length === 0 ? (
          <div className="audit-empty">当前没有正在运行或排队中的任务。</div>
        ) : (
          <div className="audit-table-wrap">
            <table className="audit-table audit-table--process">
              <thead>
                <tr>
                  <th>任务编号</th>
                  <th>归属账号</th>
                  <th>任务类型</th>
                  <th>状态</th>
                  <th>进程号</th>
                  <th>内存</th>
                  <th>CPU</th>
                  <th>已运行</th>
                  <th>阶段 / 说明</th>
                </tr>
              </thead>
              <tbody>
                {data.processes.map((process) => (
                  <tr key={process.job_id}>
                    <td className="audit-table__id" title={process.job_id}>
                      {process.job_id}
                    </td>
                    <td>{process.owner_username || '未归属账号'}</td>
                    <td>{process.task_type_label}</td>
                    <td>
                      <span
                        className={`audit-status ${
                          process.status === 'running' ? 'is-running' : 'is-waiting'
                        }`}
                      >
                        {process.status_label}
                      </span>
                    </td>
                    <td className="audit-mono">{process.pid ?? '—'}</td>
                    <td>
                      <div className="audit-meter-cell">
                        <span>{formatBytes(process.rss_bytes)}</span>
                        {(process.rss_bytes || 0) > 0 && (
                          <span className="audit-mini-bar">
                            <i
                              style={{
                                width: `${Math.max(
                                  2,
                                  ((process.rss_bytes || 0) / maxProcessMemory) * 100,
                                )}%`,
                              }}
                            />
                          </span>
                        )}
                      </div>
                    </td>
                    <td>{formatPercent(process.cpu_percent)}</td>
                    <td>{formatRuntime(process.runtime_seconds)}</td>
                    <td
                      className="audit-table__stage"
                      title={process.stage || process.message || ''}
                    >
                      {process.stage || process.message || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="audit-split">
        <section className="audit-sec audit-table-sec">
          <header className="audit-block-head">
            <div>
              <h2 className="audit-sec__title">账号资源占用</h2>
              <p className="audit-sec__desc">按运行中任务汇总各账号的进程内存与 CPU。</p>
            </div>
          </header>
          {data.users.length === 0 ? (
            <div className="audit-empty">当前没有正在使用资源的账号。</div>
          ) : (
            <div className="audit-table-wrap">
              <table className="audit-table audit-table--users">
                <thead>
                  <tr>
                    <th>账号</th>
                    <th>运行任务</th>
                    <th>内存占用</th>
                    <th>CPU</th>
                  </tr>
                </thead>
                <tbody>
                  {data.users.map((user) => (
                    <tr key={user.username}>
                      <td className="audit-table__user">{user.username}</td>
                      <td>{user.running_jobs}</td>
                      <td>
                        <div className="audit-meter-cell">
                          <span>{formatBytes(user.rss_bytes)}</span>
                          {user.rss_bytes > 0 && (
                            <span className="audit-mini-bar">
                              <i
                                style={{
                                  width: `${Math.max(
                                    2,
                                    (user.rss_bytes / maxUserMemory) * 100,
                                  )}%`,
                                }}
                              />
                            </span>
                          )}
                        </div>
                      </td>
                      <td>{formatPercent(user.cpu_percent)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="audit-sec">
          <header className="audit-block-head">
            <div>
              <h2 className="audit-sec__title">依赖与 Worker</h2>
              <p className="audit-sec__desc">Redis、MongoDB 探活与 Worker 容器列表。</p>
            </div>
          </header>

          <div className="audit-deps">
            {Object.entries(data.dependencies).map(([key, dep]) => (
              <div key={key} className="audit-dep">
                <span className={`audit-dep__mark${dep.ok ? ' is-ok' : ' is-down'}`} />
                <div className="audit-dep__body">
                  <span className="audit-dep__label">{dep.label}</span>
                  <span className="audit-dep__hint">
                    延迟 {dep.latency_ms === null ? '—' : `${dep.latency_ms} 毫秒`}
                  </span>
                </div>
                <span className={`audit-dep__state${dep.ok ? ' is-ok' : ' is-down'}`}>
                  {dep.ok ? '正常' : '异常'}
                </span>
              </div>
            ))}
          </div>

          {data.workers.length === 0 ? (
            <div className="audit-empty">尚未收到 Worker 容器上报的指标。</div>
          ) : (
            <div className="audit-workers">
              {data.workers.map((worker) => (
                <div key={`${worker.hostname}-${worker.pid}`} className="audit-worker">
                  <div className="audit-worker__head">
                    <span className="audit-worker__name">
                      {worker.hostname || `Worker ${worker.pid}`}
                    </span>
                    <span
                      className={`audit-worker__state${
                        data.worker_summary.online ? ' is-online' : ' is-offline'
                      }`}
                    >
                      {data.worker_summary.online ? '在线' : '未上报'}
                    </span>
                  </div>
                  <div className="audit-worker__grid">
                    <span>
                      <i>内存</i>
                      {memoryDetail(worker.memory)}
                    </span>
                    <span>
                      <i>使用率</i>
                      {formatPercent(worker.memory.percent)}
                    </span>
                    <span>
                      <i>CPU</i>
                      {formatPercent(worker.cpu_percent)}
                    </span>
                    <span>
                      <i>启动</i>
                      {formatDateTime(worker.started_at)}
                    </span>
                    <span>
                      <i>已运行</i>
                      {formatRuntime(worker.uptime_seconds)}
                    </span>
                  </div>
                  <div className="audit-worker__foot">{workerCaption(worker)}</div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      <p className="audit-note">
        说明：容器资源按各自 cgroup 限额独立统计，不做相加；磁盘为输出目录所在文件系统的
        使用率；没有设置限额时不显示使用率百分比，避免虚构数据。
      </p>
    </div>
  )
}
