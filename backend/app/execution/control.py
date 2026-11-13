# -*- coding: utf-8 -*-
"""跨进程任务控制面（Redis）。

职责边界：

- **权威状态仍是 ``job.json``**。这里只保存「排队/运行控制意图」与租约：
  取消、强制终止、放弃、运行代次（run_token）、心跳时间。
- API 进程与 Worker 进程都只通过本模块读写控制面；``job.json`` 由 Worker
  进程独占写入（API 只在任务明确未开始时写终态）。
- 所有「检查 + 写入」用 Lua 原子完成，避免 API 与 Worker 之间的检查-写入竞态。

Redis key：

- ``icd:job:{job_id}:ctl``    hash：run_token / claimed_token / heartbeat_at /
  cancel / force / abandon
- ``icd:active``              zset：job_id → 最近心跳时间，供 reaper 找失联任务

本模块不在导入期连接 Redis（Celery prefork 父进程导入了子模块时，连接池不能
被 fork 共享）；首次使用时才创建客户端。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

from app.execution import config as execution_config

KEY_PREFIX = "icd:job:"
ACTIVE_KEY = "icd:active"

# 强制终止的兜底宽限：控制面收到 force 后，先让管线在检查点退出；
# 超过该时间仍未结束则直接退出 Worker 子进程（一个子进程只跑一个任务）。
FORCE_GRACE_S = 10.0

# 控制面连续不可达时，多久打一次告警（24h 任务不能每次心跳都刷屏）
_REDIS_WARN_INTERVAL_S = 300.0


class RedisUnavailable(RuntimeError):
    """Redis 连接/命令失败。调用方据此返回 503 或「本轮不判断」。"""


_client = None
_client_lock = threading.Lock()
_last_warn_at = 0.0


def _warn(message: str) -> None:
    global _last_warn_at
    now = time.monotonic()
    if now - _last_warn_at < _REDIS_WARN_INTERVAL_S:
        return
    _last_warn_at = now
    print(f"[execution] {message}", file=sys.stderr)


def reset_client() -> None:
    """丢弃缓存的客户端（测试 / 连接参数变更后使用）。"""
    global _client
    with _client_lock:
        _client = None


def _get_client():
    global _client
    with _client_lock:
        if _client is not None:
            return _client
        try:
            import redis
        except Exception as exc:  # noqa: BLE001
            raise RedisUnavailable(
                f"redis 未安装或不可导入: {type(exc).__name__}: {exc}"
            ) from exc
        try:
            _client = redis.Redis.from_url(
                execution_config.broker_url(),
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
                retry_on_timeout=True,
                health_check_interval=30,
            )
        except Exception as exc:  # noqa: BLE001
            raise RedisUnavailable(
                f"无法创建 Redis 连接: {type(exc).__name__}: {exc}"
            ) from exc
        return _client


def _run(operation):
    try:
        return operation(_get_client())
    except RedisUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — redis 的连接/协议异常统一归类
        raise RedisUnavailable(f"{type(exc).__name__}: {exc}") from exc


def _key(job_id: str) -> str:
    return f"{KEY_PREFIX}{job_id}:ctl"


def _now_text(now: Optional[float] = None) -> str:
    return "%.6f" % (time.time() if now is None else now)


# ---------------------------------------------------------------- Lua 脚本

_CLAIM_LUA = """
-- KEYS[1]=ctl  KEYS[2]=active
-- ARGV[1]=run_token ARGV[2]=now ARGV[3]=ttl ARGV[4]=job_id
if redis.call('HEXISTS', KEYS[1], 'cancel') == 1
   or redis.call('HEXISTS', KEYS[1], 'force') == 1
   or redis.call('HEXISTS', KEYS[1], 'abandon') == 1 then
  return 0
end
if redis.call('HGET', KEYS[1], 'claimed_token') then
  return 0
end
local current = redis.call('HGET', KEYS[1], 'run_token')
if current and current ~= ARGV[1] then
  return 0
end
redis.call('HSET', KEYS[1], 'run_token', ARGV[1], 'claimed_token', ARGV[1],
           'claimed_at', ARGV[2], 'heartbeat_at', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('ZADD', KEYS[2], ARGV[2], ARGV[4])
return 1
"""

_HEARTBEAT_LUA = """
-- KEYS[1]=ctl  KEYS[2]=active
-- ARGV[1]=run_token ARGV[2]=now ARGV[3]=ttl ARGV[4]=job_id
local claimed = redis.call('HGET', KEYS[1], 'claimed_token')
if claimed and claimed ~= ARGV[1] then
  return -1
end
local current = redis.call('HGET', KEYS[1], 'run_token')
if current and current ~= ARGV[1] then
  return -1
end
if not claimed then
  -- 控制键丢失（Redis 重启 / TTL 过期）：没有终止意图时自愈重新占位，
  -- 不能把仍健康运行的长任务误判成「被取代」。
  if redis.call('HEXISTS', KEYS[1], 'cancel') == 0
     and redis.call('HEXISTS', KEYS[1], 'force') == 0
     and redis.call('HEXISTS', KEYS[1], 'abandon') == 0 then
    redis.call('HSET', KEYS[1], 'run_token', ARGV[1], 'claimed_token', ARGV[1],
               'claimed_at', ARGV[2])
  end
end
redis.call('HSET', KEYS[1], 'heartbeat_at', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('ZADD', KEYS[2], ARGV[2], ARGV[4])
local flags = 0
if redis.call('HEXISTS', KEYS[1], 'cancel') == 1 then flags = flags + 1 end
if redis.call('HEXISTS', KEYS[1], 'force') == 1 then flags = flags + 2 end
if redis.call('HEXISTS', KEYS[1], 'abandon') == 1 then flags = flags + 4 end
return flags
"""

_CANCEL_LUA = """
redis.call('HSET', KEYS[1], 'cancel', '1')
redis.call('EXPIRE', KEYS[1], ARGV[1])
if redis.call('HEXISTS', KEYS[1], 'claimed_token') == 1 then return 1 end
return 0
"""

_FORCE_LUA = """
redis.call('HSET', KEYS[1], 'force', '1', 'cancel', '1')
redis.call('EXPIRE', KEYS[1], ARGV[1])
if redis.call('HEXISTS', KEYS[1], 'claimed_token') == 1 then return 1 end
return 0
"""

_ABANDON_LUA = """
if redis.call('HEXISTS', KEYS[1], 'claimed_token') == 1 then return 0 end
redis.call('HSET', KEYS[1], 'abandon', '1')
redis.call('EXPIRE', KEYS[1], ARGV[1])
return 1
"""

_RELEASE_LUA = """
if redis.call('HGET', KEYS[1], 'claimed_token') == ARGV[1] then
  redis.call('DEL', KEYS[1])
  redis.call('ZREM', KEYS[2], ARGV[2])
  return 1
end
return 0
"""


# ---------------------------------------------------------------- 控制面操作


def ping() -> bool:
    """Redis 是否可用。启动扫描据此决定「能不能判断 Worker 是否还活着」。"""
    try:
        return bool(_run(lambda client: client.ping()))
    except RedisUnavailable:
        return False


def prepare(job_id: str, run_token: str, ttl: Optional[int] = None) -> None:
    """为一次新运行准备控制键：写入代次票据，清掉上一轮的终止意图。

    同时把该任务从 active 集合移除：上一轮若已失联且 reaper 尚未处理，
    旧心跳分数会让新任务刚入队就被误判为 stale。
    """
    ttl = execution_config.lease_ttl_s() if ttl is None else int(ttl)

    def op(client):
        pipe = client.pipeline(transaction=True)
        pipe.hset(_key(job_id), mapping={"run_token": run_token})
        pipe.hdel(
            _key(job_id),
            "cancel", "force", "abandon",
            "claimed_token", "claimed_at", "heartbeat_at", "worker",
        )
        pipe.expire(_key(job_id), ttl)
        pipe.zrem(ACTIVE_KEY, job_id)
        return pipe.execute()

    _run(op)


def claim(job_id: str, run_token: str) -> bool:
    """原子占用任务；被终止/放弃/已被占用/代次不符时返回 False。"""
    return int(
        _run(
            lambda client: client.eval(
                _CLAIM_LUA, 2,
                _key(job_id), ACTIVE_KEY,
                run_token, _now_text(), execution_config.lease_ttl_s(), job_id,
            )
        )
    ) == 1


def heartbeat(job_id: str, run_token: str) -> int:
    """心跳并读取控制意图。

    返回：``-1`` 表示本代次已被取代/丢失；否则按位返回意图：
    ``1`` cancel、``2`` force、``4`` abandon。
    """
    return int(
        _run(
            lambda client: client.eval(
                _HEARTBEAT_LUA, 2,
                _key(job_id), ACTIVE_KEY,
                run_token, _now_text(), execution_config.lease_ttl_s(), job_id,
            )
        )
    )


def request_cancel(job_id: str) -> bool:
    """置取消意图；返回此刻是否已有 Worker 占用（占用时由 Worker 消费）。"""
    return int(
        _run(
            lambda client: client.eval(
                _CANCEL_LUA, 1, _key(job_id), execution_config.lease_ttl_s()
            )
        )
    ) == 1


def request_force(job_id: str) -> bool:
    """置强制终止意图；返回此刻是否已有 Worker 占用（占用时才需要 revoke）。"""
    return int(
        _run(
            lambda client: client.eval(
                _FORCE_LUA, 1, _key(job_id), execution_config.lease_ttl_s()
            )
        )
    ) == 1


def request_abandon(job_id: str) -> bool:
    """置放弃意图；返回是否记录成功。

    只有「当前没有 Worker 占用」才允许放弃（Lua 内原子判断）。返回 False
    说明任务正在运行，调用方应提示用户先终止。
    """
    return int(
        _run(
            lambda client: client.eval(
                _ABANDON_LUA, 1, _key(job_id), execution_config.lease_ttl_s()
            )
        )
    ) == 1


def release(job_id: str, run_token: str) -> None:
    """运行结束释放租约；只释放自己这一代次（被新运行接管时不动）。"""
    _run(
        lambda client: client.eval(
            _RELEASE_LUA, 2, _key(job_id), ACTIVE_KEY, run_token, job_id
        )
    )


def clear(job_id: str) -> None:
    """无条件删除控制键与 active 记录（reaper 收尾 / 入队失败回滚）。"""
    def op(client):
        pipe = client.pipeline(transaction=True)
        pipe.delete(_key(job_id))
        pipe.zrem(ACTIVE_KEY, job_id)
        return pipe.execute()

    _run(op)


def refresh_active(job_id: str, heartbeat_at: float) -> None:
    """刷新 active 集合分数（心跳新鲜但 zset 分数落后的自愈）。"""
    _run(lambda client: client.zadd(ACTIVE_KEY, {job_id: heartbeat_at}))


def snapshot_or_raise(job_id: str) -> dict:
    """读取控制面快照；Redis 不可用 → :class:`RedisUnavailable`。"""
    return dict(_run(lambda client: client.hgetall(_key(job_id))) or {})


def snapshot(job_id: str) -> Optional[dict]:
    """读取控制面快照；Redis 不可用 → ``None``（调用方不据此改变状态）。"""
    try:
        return snapshot_or_raise(job_id)
    except RedisUnavailable:
        return None


def stale_active(cutoff: float) -> list[str]:
    """active 集合中心跳早于 ``cutoff`` 的任务 id。"""
    raw = _run(
        lambda client: client.zrangebyscore(ACTIVE_KEY, "-inf", cutoff)
    )
    return [str(item) for item in raw or []]


def is_fresh(ctl: dict, now: Optional[float] = None) -> bool:
    """控制面心跳是否仍新鲜（用于 API 启动扫描 / 续跑前判断）。"""
    try:
        heartbeat_at = float(ctl.get("heartbeat_at") or 0)
    except (TypeError, ValueError):
        return False
    current = time.time() if now is None else now
    return heartbeat_at >= current - execution_config.stale_seconds()


# ---------------------------------------------------------------- 心跳线程


class Heartbeat:
    """Worker 侧心跳线程：续租 + 消费 cancel/force 意图。

    - Redis 短暂不可达：不终止任务，下个周期继续（24h 任务不能因一次抖动失败）。
    - ``force``：先置取消标志让检查点退出；超过 :data:`FORCE_GRACE_S` 仍未结束
      则直接退出当前 Worker 子进程（一个子进程只跑一个任务，不会误伤其他任务）。
    """

    def __init__(
        self,
        job_id: str,
        run_token: str,
        *,
        on_cancel,
        on_force=None,
        interval: Optional[float] = None,
    ) -> None:
        self._job_id = job_id
        self._run_token = run_token
        self._on_cancel = on_cancel
        self._on_force = on_force
        self._interval = (
            execution_config.heartbeat_interval_s() if interval is None else interval
        )
        self._stop = threading.Event()
        self._force_timer: Optional[threading.Timer] = None
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name=f"job-heartbeat-{job_id[:8]}",
        )

    def start(self) -> "Heartbeat":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                flags = heartbeat(self._job_id, self._run_token)
            except RedisUnavailable as exc:
                _warn(f"heartbeat redis unavailable for {self._job_id}: {exc}")
                continue
            if flags == -1:
                _warn(
                    f"job {self._job_id} run_token superseded; requesting cancel"
                )
                self._notify(self._on_cancel)
                continue
            if flags & 4:  # abandon
                self._notify(self._on_cancel)
            if flags & 1:  # cancel
                self._notify(self._on_cancel)
            if flags & 2:  # force
                self._notify(self._on_cancel)
                if self._on_force is not None:
                    self._notify(self._on_force)
                self._schedule_hard_exit()

    @staticmethod
    def _notify(callback) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:  # noqa: BLE001 — 回调失败不能让心跳线程退出
            _warn(f"heartbeat callback failed: {type(exc).__name__}: {exc}")

    def _schedule_hard_exit(self) -> None:
        if self._force_timer is not None:
            return
        timer = threading.Timer(FORCE_GRACE_S, self._hard_exit)
        timer.daemon = True
        timer.start()
        self._force_timer = timer

    def _hard_exit(self) -> None:
        if self._stop.is_set():
            # 任务已在宽限期内正常收尾
            return
        print(
            f"[worker] job {self._job_id} force-cancel grace expired; exiting worker child",
            file=sys.stderr,
        )
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:  # noqa: BLE001
                pass
        # 只退出当前 Worker 子进程；Celery 父进程会按 max_tasks_per_child 补一个。
        os._exit(1)


class JobControl:
    """把 ``job_id`` 绑到控制面操作上的薄封装（调用点更短）。"""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def prepare(self, run_token: str) -> None:
        prepare(self.job_id, run_token)

    def claim(self, run_token: str) -> bool:
        return claim(self.job_id, run_token)

    def heartbeat(self, run_token: str) -> int:
        return heartbeat(self.job_id, run_token)

    def snapshot(self) -> Optional[dict]:
        return snapshot(self.job_id)

    def snapshot_or_raise(self) -> dict:
        return snapshot_or_raise(self.job_id)

    def request_cancel(self) -> bool:
        return request_cancel(self.job_id)

    def request_force(self) -> bool:
        return request_force(self.job_id)

    def request_abandon(self) -> bool:
        return request_abandon(self.job_id)

    def release(self, run_token: str) -> None:
        release(self.job_id, run_token)

    def clear(self) -> None:
        clear(self.job_id)

    def start_heartbeat(self, run_token: str, *, on_cancel, on_force=None) -> Heartbeat:
        return Heartbeat(
            self.job_id, run_token, on_cancel=on_cancel, on_force=on_force
        ).start()
