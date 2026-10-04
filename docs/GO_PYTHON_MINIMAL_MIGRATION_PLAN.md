# Go 业务服务与 Python AI 服务拆分设计

## 目标与范围

DevAssist 采用 Go 处理业务 API、权限与事务，Python 处理 Agent、RAG 和模型调用。拆分保持会话、工单、业务表及前端 API 的兼容性，不新增业务场景。

Go 承担实际业务查询与写入；Python 通过内部 HTTP 获取业务工具结果。两种服务共用 MySQL，但按业务记录与 AI 执行记录划分写入职责。

## 模块职责

| 模块 | 当前职责 | 源码 |
| --- | --- | --- |
| Go HTTP 与鉴权 | 登录、JWT、用户重查、角色和租户权限 | `backend-go/internal/api/server.go`、`backend-go/internal/auth/` |
| Go 业务数据 | 会话、消息、反馈、工单、人工接管、文档与统计 | `backend-go/internal/api/`、`backend-go/internal/service/` |
| Go 业务工具 | 日志、Key、套餐、用量、账单、建单和查单 | `backend-go/internal/api/tools.go`、`backend-go/internal/service/tools.go` |
| Python AI 入口 | 受内部凭证保护的运行、建议回复、脱敏和评估接口 | `ai-py/app/ai_main.py` |
| Python 编排与检索 | LangGraph、专业 Agent、RAG、模型、记忆与缓存 | `ai-py/app/agents/`、`ai-py/app/rag/` |
| Python 可观测记录 | Trace、工具调用日志、Token 用量 | `ai-py/app/observability/`、`ai-py/app/tools/registry.py` |

Python 旧业务路由与 SQL 工具保留用于兼容对照和回退，常规运行不注册到 AI 入口。

## 请求时序

1. Go 校验 JWT，根据用户 ID 重查身份与租户，检查会话访问范围。
2. 提交用户消息，再调用 Python；不持有覆盖整个 AI 等待期的业务事务。
3. Python 读取记忆、路由意图，按需执行检索及业务工具。
4. 工具回调 Go，由 Go 校验可信上下文并执行对应 SQL。
5. Python 汇总回复并返回完整结果。
6. Go 在短事务内保存助手消息及会话状态，再发送 SSE 字符块。

两个内部方向均验证至少 32 字符的共享 `INTERNAL_SERVICE_TOKEN`。模型参数不能覆盖 `ToolContext` 中的用户、租户、角色、会话或 Trace 信息。

## 数据兼容与故障处理

- Go 显式映射业务表，不在服务启动时运行 AutoMigrate；建表由数据准备脚本负责。
- 登录沿用 bcrypt 与 HS256；授权使用当前数据库用户信息，不直接信任 Token 内角色。
- 工单、反馈、人工回复与审计等相关 Go 写入使用对应业务事务。
- AI 请求失败时不伪造完成事件，已提交的用户消息可能保留。
- Python 脱敏不可达时拒绝人工回复和会话补充写入。
- 未实现跨服务事务、工具幂等或自动补偿；客户端重试和取消不保证工具副作用被撤销。

## 验证设计

`make check` 覆盖 Go 检查/测试/race/构建、Python 新入口与工具适配，以及前端构建和 SSE 测试，零模型调用。

`ai-py/scripts/check_migration.py` 创建两份独立的临时 MySQL 数据，比较 Python 对照入口与 Go 的协议及数据行为，并验证真实内部 HTTP、工具回调、事务失败、SSE 与可选浏览器操作。退出时清理测试数据库。运行要求、结果及限制见 [验收记录](GO_PYTHON_MIGRATION_ACCEPTANCE.md)。

真实模型测试与工程回归分开：目前一个真实文档问答样本的集成链路通过，但人工核查发现无依据细节，不能据此认定整体回答质量通过。服务器部署、长期容量和生产 SLA 尚未验收。

## 配置与运行

Go 读取共享 `ai-py/.env`，可通过环境变量或 `backend-go/.env` 覆盖专属配置；Python 从 `ai-py/.env` 读取配置。已有配置和业务数据不由普通启动命令重置。

分别运行 `make run-ai`、`make run`、`make front`。基础设施使用项目根目录的 Compose，完整首次安装步骤见 [README](../README.md)。

回退时先停止新版 Go 和 `app.ai_main`，运行 `make run-legacy`，将 Vite 代理改回 Python 的 :8000。`legacy_sql` 必须显式启用；两套业务入口不能同时接收写请求，回退不会撤销已提交数据。
