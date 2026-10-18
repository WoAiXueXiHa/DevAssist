# 建单与问答故障处理修复（2026-10-18）

本轮修复工具日志失败触发业务重试、人工受理状态不实、RAG 无命中提示丢失、降级回复进入缓存，以及辅助记录失败导致正常答案丢失。

## 部署顺序

已有数据库先运行增量迁移，再更新 Go 和 Python 服务：

```bash
cd ai-py
.venv/bin/python -m scripts.migrate_ticket_idempotency
```

迁移添加可空的 `ticket.idempotency_key` 和 `(tenant_id, idempotency_key)` 唯一约束，可重复执行，旧工单保留 NULL。不要为本次更新执行 `make setup` 或 `make init-db`，它们会重建数据。本轮测试没有迁移当前业务数据库，也没有部署或重启业务服务。

Python 内部建单使用可信 trace 标识生成幂等键，同一次执行中的工具重试和管线兜底返回原工单。主动转人工反馈按会话去重。Go 内部建单接口要求幂等键；旧版本 AI 服务应与新服务协调更新。

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

幂等覆盖一次 AI 执行内的建单重试，以及重复的主动转人工反馈；浏览器重新提交聊天会生成新的 trace，目前没有覆盖整次聊天请求的 client_request_id 去重，也没有跨 Go/Python 原子事务或后台自动补偿。

Redis 记忆成对事务写入，辅助写入失败不阻断本轮答案；下一轮若 Redis 不可用仍可能进入兜底，并未增加从 MySQL 重建记忆。成本、工具日志和 Trace 的失败会记录错误，但没有持久重试队列。没有调用付费模型，也未新增真实模型质量或生产容量结论。
