# ADR-009 会话吊销与 Token 版本号

| 项目 | 内容 |
| --- | --- |
| 状态 | **已接受（已实施）** |
| 生效范围 | 后端认证模块（User 文档 / JWT 策略 / 管理员账号接口）；前端无改动 |
| 提议日期 | 2026-11-13 |
| 关联 ADR | ADR-007（用户登录与账号隔离）：本决策修订其「重置密码不会使旧 JWT 立即失效」的已知限制；不改变 Cookie 参数、TTL、权限矩阵与任务隔离 |

---

## 背景

ADR-007 采用无状态 Cookie JWT 会话。其明确记录的已知限制是：管理员重置密码不会使已签发的旧 Cookie 立即失效，最长 8 小时自然过期；禁用账号在下一次请求即被拒绝，但重新启用后旧 Cookie 会恢复（端到端脚本已断言该行为）。

这意味着：

- 密码泄露后，仅重置密码不能赶走已经登录的攻击者；
- 因怀疑账号被盗而禁用、事后重新启用时，攻击者手中的旧 Cookie 会随账号恢复一起复活；
- 管理员缺少一种“立即让某账号所有会话下线”的手段。

阶段 A 的目标是最小改动上线，该限制被接受。本次补充会话吊销能力，关闭上述窗口。

## 决策

### D1：每用户整数版本号

`User` 文档增加内部字段 `token_version: int = 0`。旧文档没有该字段时读取为 0，无需数据迁移。

### D2：JWT 携带 `ver`，按相等校验

- 登录签发 JWT 时写入 claim `ver = user.token_version`；
- 每次请求在 JWT 策略层比对 `token.ver == user.token_version`，不相等返回未认证（401）；
- 旧 Token 没有 `ver` 时按 0 处理；
- `ver` 与 `sub` / `aud` / `exp` 一起被签名，客户端只能读取、不能篡改；
- `token_version` 不进入 `/auth/me`、用户列表等 API 响应。

### D3：递增规则

| 操作 | 是否 +1 | 说明 |
| --- | --- | --- |
| 管理员重置密码 | 总是 | 即使新密码与旧密码相同也 +1 |
| `is_active` 由 true 变为 false | 是 | 重复提交 false（已禁用）不重复 +1 |
| 启用账号 | 否 | 旧会话保持已吊销，必须重新登录 |
| 设置 / 取消管理员 | 否 | 权限每次请求从 DB 读取，变更即时生效 |
| 普通登出 | 否 | 仅清除当前浏览器 Cookie |

版本号只增不减，不提供重置或回绕逻辑。

### D4：在 JWT 策略层统一校验

新增 `VersionedJWTStrategy(JWTStrategy)`，覆盖 `write_token` / `read_token`：

- `write_token` 在标准 claim 之外写入 `ver`；
- `read_token` 解码后按上游逻辑加载用户，再比对版本；不匹配返回 `None`，由 fastapi-users 统一转为 401；
- 校验位于策略层，所有受保护接口自动生效，不需要逐个路由改造；
- fastapi-users 锁定 13.0.0；升级时必须重新验证该子类与上游 `write_token` / `read_token` 的兼容性。

版本比对不增加数据库查询：`read_token` 本来就需要按 JWT 的 `sub` 加载用户，以判断账号是否存在与是否启用。

### D5：管理员更新使用原子操作

`PATCH /api/v4/admin/users/{user_id}` 在需要变更时使用一次原子更新：

```text
$set: {变更的字段, updated_at}
$inc: {token_version: 1}   # 仅重置密码或 true→false 时
```

不得使用“读取整文档 → 修改 → `save()` 回写”，避免两个管理员并发操作同一账号时互相覆盖。最后活跃管理员的 409 校验必须在更新之前，被拒绝的请求不得产生版本副作用。

### D6：吊销语义

- 一次 +1 吊销该账号在**所有浏览器 / 设备**上的会话；
- 重新启用后旧 Cookie 仍然 401，用户必须重新登录；
- 本阶段不提供单设备下线、在线设备列表或管理员“强制下线”按钮；
- 不改变 8 小时 TTL、不引入刷新 Token；版本号是吊销机制，不替代短 TTL、HttpOnly、SameSite 与后续 TLS。

### D7：兼容、回滚与恢复

- 旧用户文档无 `token_version` → 0；旧 Token 无 `ver` → 0；
- 升级部署不会踢掉现有会话；此后任意一次重置 / 禁用都会让旧格式 Token 一并失效；
- 回滚到旧代码：旧代码忽略 `ver`，新签发的 Token 仍可登录，只是暂时失去“重置即吊销”能力，不产生登录故障；
- 从备份恢复 `users` 集合可能把 `token_version` 恢复到较小值，使仍在 8 小时内的旧 Token 复活；恢复后如需彻底失效，轮换 `AUTH_JWT_SECRET`。

## 原因

1. 会话吊销是密码重置的必要组成部分；只换密码不踢会话，无法应对 Cookie 已被窃取的场景；
2. 无状态版本号不需要会话表、黑名单或刷新 Token，改动集中、回滚安全；
3. 版本比对复用每次请求已有的用户查询，没有额外数据库开销；
4. 校验放在策略层，前端与全部业务路由零改动，401 由现有全局处理跳转登录页；
5. 禁用也递增版本，使“禁用—重新启用”不再复活攻击者的旧 Cookie。

## 影响

- **后端**：`backend/app/auth/models.py` 增加 `token_version`；`backend/app/auth/backend.py` 增加 `VersionedJWTStrategy` 并在 `get_jwt_strategy()` 使用；`backend/app/auth/router.py` 的管理员更新改为原子 `$set` + `$inc`。
- **行为变化**：重置密码或禁用账号后，该账号所有已登录页面在下一次请求时回到登录页；重新启用后必须重新登录。属于预期变化，需在部署 / 更新说明中告知。
- **测试**：本地认证脚本新增版本声明、版本不匹配、旧 Token 兼容、重置 / 禁用 / 重新启用、最后管理员 409 无副作用、角色变更不递增等用例；端到端脚本中“重新启用后旧 Cookie 可用”的断言改为 401 + 重新登录成功。
- **文档**：本 ADR、`docs/architecture/api.md`、`docs/architecture/current-architecture.md`、`docs/decisions/ADR-007`（已知限制指向本 ADR）、`CHANGELOG.md`、`docs/development/development-log.md`、`docs/testing/服务器部署验收清单.md`、`更新说明.md`、`部署说明.md`（§7.6 唯一管理员密码恢复需同时 `$inc` `token_version`）。
- **兼容性**：无数据迁移；旧 Token / 旧文档按 0 处理；回滚不导致登录故障。
- **已知限制**：仅整账号吊销，不支持单设备；未重置 / 未禁用期间被窃 Cookie 仍在 TTL 内有效；版本号依赖 `AUTH_JWT_SECRET` 的保密性。

## 替代方案

| 方案 | 不选择理由 |
| --- | --- |
| 会话表 / jti 黑名单 | 支持单设备下线与在线列表，但需要新增集合、每次登录写入、过期清理与查询，对 40–50 账号规模属过度设计 |
| 把密码哈希摘要写入 JWT | 禁用不改变哈希；且把密码哈希派生值放进客户端可见 payload 无必要 |
| `tokens_valid_after` 时间戳 | 需要处理时钟回拨、多实例时间一致性与签发时间精度；整数版本号无这些边界 |
| 仅缩短 TTL | 不解决“重置后旧会话仍有效”，只缩短窗口 |
| 暂不处理 | 密码重置无法赶走已登录攻击者；禁用后重新启用会让旧 Cookie 复活，风险不可接受 |

## 状态

**已接受（已实施）**。实施计划见 `docs/superpowers/plans/2026-11-13-token-version-session-revocation.md`。

验证结果（2026-11-13，本地 Python 3.12 + 本地 MongoDB）：

1. `backend/tests/auth/test_token_version.py`：通过。覆盖 JWT 携带 `ver=0`、`/auth/me` 不暴露 `token_version`、版本不匹配 401 且恢复版本后 200、旧 Token 无 `ver` 的兼容性、重置密码 / 禁用 / 重新启用 / 重复禁用 / 角色变更 / 最后一个管理员 409 无版本副作用 / 管理员重置自己密码。
2. 认证回归脚本 8/8 通过（`test_token_version` 加 `test_auth_foundation` / `test_auth_routes` / `test_admin_users` / `test_job_owner_fields` / `test_job_isolation` / `test_history_permissions` / `test_static_security`）。
3. 端到端脚本 `backend/tests/auth/test_e2e_isolation.py` 已更新断言（重新启用后旧 Cookie 仍 401、重新登录后正常），但**尚未执行**（需本地服务与 MOCK 环境）。
