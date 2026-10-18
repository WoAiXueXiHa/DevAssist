# 建单与问答故障处理修复（2026-10-18）

本轮修复工具日志失败触发业务重试、人工受理状态不实、RAG 无命中提示丢失、降级回复进入缓存，以及辅助记录失败导致正常答案丢失。

## 部署顺序

已有数据库先运行增量迁移，再更新 Go 和 Python 服务：

```bash
cd ai-py
.venv/bin/python -m scripts.migrate_ticket_idempotency
```

迁移添加可空的 `ticket.idempotency_key` 和 `(tenant_id, idempotency_key)` 唯一约束，可重复执行，旧工单保留 NULL。不要为本次更新执行 `make setup` 或 `make init-db`，它们会重建数据。本轮测试没有迁移当前业务数据库，也没有部署或重启业务服务。

Python 内部建单优先使用 Go 生成的可信聊天请求标识生成幂等键；直接内部调用未提供该标识时回退 trace。同一次执行中的工具重试和管线兜底返回原工单。主动转人工反馈按会话去重。Go 内部建单接口要求幂等键；旧版本 AI 服务应与新服务协调更新。

`need_human` 仅表示需要人工，`handoff_status=accepted` 且存在工单 ID 才表示已受理；`failed` 不会将原 AI 会话切入人工模式。`trace_persisted=false` 表示关联 ID 对应的 Trace 未成功保存。

语义缓存改为 `semcache:v2`，旧缓存不再读取；只有文档检索命中且未降级的首轮回答能写入。旧缓存没有自动删除，可在确认不再回滚后单独清理。知识库版本自动失效尚未实现，修改知识库后仍需清理对应缓存。

## 验证

- `make check-go`：vet、单元测试、race、构建通过。普通运行中 MySQL 集成测试因未设置 TEST_MYSQL_DSN 跳过，另进行了下述真实数据库验证。
- `make check-python`：16 项离线测试通过，禁止真实模型调用；包括全失败/部分失败、建单失败、无命中不缓存、辅助写入失败保留答案。
- `make check-front`：构建及 4 项 SSE 测试通过。现有大资源包提示仍在。
- 真实隔离 MySQL：迁移执行两次后保留旧工单；8 路并发建单只保存一张；响应丢失后重试返回原工单；跨租户同键互不影响。完成后删除临时数据库。

重跑数据库验收时，设置专门用于本地测试的 MySQL 管理凭证后，从 ai-py 执行：

```bash
.venv/bin/python -m scripts.test_ticket_idempotency
```

脚本要求 `MIGRATION_TEST_ADMIN_USER` 和 `MIGRATION_TEST_ADMIN_PASSWORD`，仅使用配置中的 MySQL 地址，新建随机命名的测试库并在结束时删除，不修改业务库。需要创建/删除测试库权限、Go 和 Python 依赖。

## 仍有边界

聊天请求去重现已补齐，见下节。仍没有跨 Go/Python 原子事务或后台自动补偿。

Redis 记忆成对事务写入，辅助写入失败不阻断本轮答案；下一轮若 Redis 不可用仍可能进入兜底，并未增加从 MySQL 重建记忆。成本、工具日志和 Trace 的失败会记录错误，但没有持久重试队列。没有调用付费模型，也未新增真实模型质量或生产容量结论。


## 聊天请求去重（第二轮）

前端为每次发送生成 `client_request_id`，发送前将原始消息、原始会话参数和标识保存到按租户及用户隔离的 sessionStorage。网络失败、缺少 SSE done 或刷新页面后，用户点击“重试上次请求”会沿用原参数。即使已收到新会话的 meta，也不能修改待重试请求中的原始空会话参数。

Go 在短事务内认领 `(user_id, client_request_id)`，同时创建或复用会话并保存一条用户消息；数据库唯一约束保证多个进程和并发请求只认领一次。正文和原始会话参数的哈希绑定到请求：同键不同参数返回 409；当前身份或会话权限变化后禁止重放原结果。

请求状态为 pending、completed、failed。completed 在同一事务中保存助手消息、会话状态和完整 SSE 响应，重复提交仅重放响应，不再次调用 AI。pending 返回 409；failed 返回 502，均带结构化错误码。失败或进程崩溃后，不自动重新执行可能已经产生工单副作用的请求。pending 无自动过期接管，避免旧执行仍在运行时启动第二次执行。

认领成功后，浏览器断连不会直接取消后台操作；执行受 AI 超时加保存缓冲的上限约束。Go 生成的内部请求 ID 随调用传给 Python，建单使用它作为可信幂等依据，模型不能覆盖它。

前端收到 done 后才自动移除待确认记录；终态失败可显式“结束本次重试”，确认后开始新会话。这不会撤销已有后台执行或工单，也不会自动重发原问题。关闭标签页、清理浏览器数据、换设备后，sessionStorage 不能恢复原标识；用户手动发起新请求或主动放弃后重新发送，相同文本仍视为新问题。请求记录目前没有自动清理策略。

### 更新已有环境

先停止接收新请求，在现有库运行两次增量迁移（均不删表），再协调更新 Go、Python 和前端：

```bash
cd ai-py
.venv/bin/python -m scripts.migrate_ticket_idempotency
.venv/bin/python -m scripts.migrate_chat_requests
```

`POST /api/chat` 现在要求 `client_request_id`，旧客户端需要同步更新。真实模型验收脚本已补充该参数，但本轮未运行付费验收。本轮只执行隔离数据库迁移，没有更新当前业务库或重启业务服务。

### 第二轮验收

`make check-go` 的 vet、测试、race、构建通过；Python 全部 19 项离线测试通过。前端构建与传输、待重试状态测试通过。真实隔离 MySQL 通过迁移重复执行、处理中重复请求、正文冲突、断连后继续保存、完成后多次重放、用户隔离、失败后不重复执行及权限变化后拒绝重放。

重跑真实数据库验收：设置 `MIGRATION_TEST_ADMIN_USER` 和 `MIGRATION_TEST_ADMIN_PASSWORD` 后，从 ai-py 执行 `.venv/bin/python -m scripts.test_chat_requests`。脚本创建随机命名测试库，执行 Go API 和建单集成测试及 race，最后删除测试库。AI 使用 Fake，不产生模型调用。

真实 Chromium 浏览器重试专项已通过，页面无脚本错误；使用模拟 API，不验证真实模型。重跑脚本：启动前端后，在 frontend 运行 `node tests/browser-chat-retry.mjs http://127.0.0.1:5173`。覆盖中断响应、刷新恢复、相同参数重试、收到 done 清理、独立发送新 ID、显式结束重试不重发。
