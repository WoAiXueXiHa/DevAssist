# Go/Python 迁移验收

本文件记录迁移时的验收状态。目录随后已整理为单一 Git 根目录，虚拟环境位于 `ai-py/.venv`；源码推广注释和文档已清理。以下 SHA256 清单是当时的快照，不用于校验当前工作树。当前启动方式见 [README](../README.md)。

日期：2026-10-04。实施目录为 `/home/hp/workspace/DevSupport-AI` 外层；内层副本未合并、未删除。源码摘要见 [SHA256 清单](GO_PYTHON_MIGRATION_SOURCE_SHA256.json)。

## 实施结果

Go 已实现原外部 REST、登录/JWT、角色/租户权限、会话消息、反馈工单、工作台、文档、Trace、metrics、聊天 SSE 外壳和八个业务 SQL 工具。Python `app.ai_main` 只注册内部能力，复用原 Supervisor/RAG/模型/Redis/安全与 AI 记录写入；推荐话术提取为共用生成函数。React 继续使用原页面，代理指向 Go :8080，增加 HTTP 错误、CRLF、多行 SSE 与未收到 done 的 EOF 处理。

关键中文注释覆盖表/JSON/时间兼容、JWT 按 sub 重查用户、可信 ToolContext、消息先提交与事务边界、内部凭证、脱敏依赖、SSE 模拟字符块、超时与重试边界。学习链路见 [源码学习差异](GO_PYTHON_LEARNING_DIFF.md)。

Go 不 AutoMigrate；已有 ai-py/.env 只补缺失的 INTERNAL_SERVICE_TOKEN、GO_TOOLS_URL、BUSINESS_TOOLS_BACKEND，没有覆盖原账号/模型密钥/JWT。内部凭证仅本地保存，不写入报告。旧 API 与明确 legacy_sql 回退保留。未执行业务库 init-db、seed、ingest、clean 或付费全量评估。

## 最终测试结果

所有业务模块接线完成后统一执行测试；发现的 Body(embed=True) 422 差异及 Bearer 解析差异已修复并回归。

| 检查 | 实际结果 | 证据与范围 |
| --- | --- | --- |
| Go 格式 / vet / build | 通过 | Go 1.26.0；固定 go.mod/go.sum；构建 `bin/server` |
| Go 单元测试 / race | 7 个测试函数通过，race 通过 | bcrypt 72 字节、Token/算法/过期、内部 HTTP/取消/无重试、JSON/微秒、校验、中文多行 SSE、内部认证 |
| Python 接线测试 | 5 passed、0 skipped | pytest；模拟模型与 HTTP，内部认证、可信输入、8 个适配、无 SQL 回退、registry 原重试/包装 |
| 前端构建 | 通过 | Node 22.16.0、Vite 5.4.21、TypeScript；现有大 bundle 警告，未借迁移扩展性能优化 |
| 前端 SSE 测试 | 4 passed、0 skipped | 中文逐字节网络块、CRLF、多行 data、HTTP 502、缺 done、空体/错误类型 |
| 原接口 / 隔离 MySQL / 浏览器 | 127 项检查通过 | 其中 10 项浏览器检查；[逐项 JSON 证据](GO_PYTHON_MIGRATION_TEST_EVIDENCE.json) |
| 原服务与清理 | 通过 | 原 MySQL/Redis/Milvus 等容器仍 healthy；临时测试库查询数量为 0，测试进程与临时 Redis 已清理 |

Python 使用工作区内层已有虚拟环境解释器，但 cwd/PYTHONPATH 指向外层实现，未改内层源文件。Go 模块依赖已固定；Python 依赖沿用当前虚拟环境，不宣称所有宽松版本范围均已验证。外层没有有效 Git 历史，构建使用 `-buildvcs=false`，不伪造提交证据。

执行环境的宿主回环 TCP 被拒绝，Go HTTP 测试与隔离验收改在项目 Docker 私网的一次性容器内执行；工具链、解释器、浏览器缓存只读挂载。正常机器可直接运行以下命令：

```bash
cd backend-go
go vet ./...
go test ./...
go test -race ./...
go build -buildvcs=false -o bin/server ./cmd/server
cd ../backend
python -m pytest tests -q
python -m scripts.check_migration
cd ../frontend
npm run build
npm run test
```

浏览器选项：从 backend 执行 `MIGRATION_BROWSER_CHECK=1 python -m scripts.check_migration`，要求 Node/Chromium 可用，可用 `MIGRATION_NODE` 指定 Node 路径。`check_migration` 使用隔离库管理员，开发 Compose 默认 root；通过 MIGRATION_TEST_ADMIN_USER / MIGRATION_TEST_ADMIN_PASSWORD 覆盖。脚本只创建/删除随机命名 devsupport_migration_test_* 临时库，没有重建原业务库。

## 对照、链路和故障证据

查询接口对照原 FastAPI/SQLAlchemy 的状态码与 JSON；写操作在两份相同但独立的 fixture 执行，比较业务字段和后续数据状态。随机 ID、更新时间按格式/语义处理，不强求逐字符串相同。两个方向的 JWT 均实际验证：原 Token 被 Go 接受（即使 claim 伪造角色也按当前用户查表），Go Token 可由原 python-jose 解码；原 bcrypt 哈希和 UTF-8 72 字节截断登录通过。

普通与人工聊天逐事件对照原 SSE，包括中文/换行、18/12 字符块和完整答案；同租户同伴会话可复用，客户跨租户/不存在会话新建，内部跨租户保留原会话租户并传当前用户。

八个工具对照包含客户/内部角色、跨租户拒绝、未找到、240 分钟默认与 429 锚点、Key 演示日期、账单 JSON、用量排序、工单默认值和可信身份。日志统计保留只有下界的原行为。尚未穷举所有非法工具参数或所有 JSON 语法错误文本，不宣称完全复制 Pydantic 的每一种边界。

零付费回归使用真实 Go 进程与真实 Python AI HTTP 入口，但 Supervisor/模型以确定性 stub 替换。Python registry、工具 HTTP、Go SQL、Trace/工具日志写入和 Go SSE 真实执行，证明 Go→Python→Go→MySQL→SSE 接线。用户消息先提交，无数据库事务占据整个 AI 等待阶段；工具创建的工单关联当前用户/会话，模型同名参数不能覆盖。

故障包含 AI 503、AI 超时、AI 生成后助手落库失败、审计写入失败导致工单更新、人工回复与接管回滚，工单插入失败导致反馈和会话标记回滚、人工模式不运行 AI、AI 不可达时不能绕过脱敏存明文。助手落库失败保留已提交用户消息；不伪造 done。未宣称跨服务原子性、取消模型执行、工具幂等或自动恢复。

浏览器检查客户登录、中文聊天完成、反馈建单/转人工、会话补充/脱敏、文档正文、工单列表、工作台详情/建议/人工回复、运营指标、390px 聊天入口加载、502 中文提示及发送状态恢复。390px 只验证入口可加载，没有对所有页面做完整多尺寸布局优化或截图评审。

## 真实模型：一例集成通过，内容质量有待补齐

用户明确批准问题及相关知识片段向北京 DashScope 外传后，执行一次签名问答：“签名算法怎么生成？请说明参与签名的参数和服务端验签步骤。”复用现有 32 个知识切片，只读 Milvus；MySQL 和 Redis 隔离，未重新入库、未扩大样本或运行全量评估。此前自动审批拒绝时未执行请求；收到具体授权后才运行。

真实 Go→原 Python Supervisor/RAG→DashScope→Go SSE 返回 200。用户/助手两条消息落库，答案与 MySQL 相同，4 条 Trace、5 条引用，未转人工、未创建工单。本例没有调用业务 SQL 工具，不以本例代替八工具对照与回调测试。详见 [样本与人工核查](GO_PYTHON_REAL_AI_EVIDENCE.json) 和 [费用明细](GO_PYTHON_REAL_AI_COST.json)。

5 次 SDK 调用：qwen-turbo 1 次、text-embedding-v3 2 次、gte-rerank-v2 1 次、qwen-plus 1 次。所有调用返回用量，按公开价格估算 **0.0034387 元**；保守预留上界 **0.0372599 元**，低于批准的 1 元上限。未核对账号最终账单。测试包装器限制单次输出 1024 tokens、最多 8 个网络尝试名额，OpenAI SDK 禁止内部重试，DashScope rerank 为可能的一次连接重试预留双份费用与名额；tenacity 每次重试均重新预留。生产配置与 AI 算法未修改。

北京非思考、输入小于 128K 的核对计价，人民币/百万 Token：qwen-turbo 输入 0.3/输出 0.6，qwen-plus 0.8/2，text-embedding-v3 输入 0.5，gte-rerank-v2 输入 0.8。以 UTF-8 字节数与协议余量保守预留。[阿里云官方计价](https://help.aliyun.com/zh/model-studio/model-pricing)

人工核查：FAQ 支持参数排序、追加 Secret 与 HMAC-SHA256 概要；接入指南支持请求头和秒级时间戳。答案进一步声称 `key=value&...`、不参与 URL 编码、Secret 无分隔符、十六进制小写，当前引用没有提供这些规则；服务端同算法重算是通用推断，完整平台规范仍缺。Webhook 引用与请求签名相关性有限，步骤出现 `1. 1.` 重复编号。**集成检查通过，不能据此认定答案全部有据或整体 AI 质量通过。** 后续应补齐权威鉴权签名资料并单独验收；本次保持原 AI 算法的迁移边界。

临时数据库、Redis 与测试进程已清理；原配置和业务数据未重置。

## 仍未验收的项目

| 项目 | 状态 |
| --- | --- |
| 真实内容质量 | 已执行 1 例，集成/费用记录通过；存在无依据细节与展示问题，需补资料后复验 |
| 服务器部署、资源与外置依赖 | 未执行，缺服务器访问与实际拓扑信息 |
| 本人讲解与演示 | 学习差异已写，需本人复述与实操验收 |

模拟模型回归只能证明对应协议、数据和接线，不能证明真实回答质量、长期容量或生产就绪。T01—T09 实施、T10 对应回归与 T11 单样本集成/费用记录已完成，不把这些结果写成 T01—T13 全部完成。

## 启动与回退

激活含项目依赖的 Python 虚拟环境后，三个终端分别运行 `make run-ai`（Python :8000）、`make run`（Go :8080）、`make front`（React :5173）。Go 自动读取原 ai-py/.env 的共享配置，监听/连接可由 backend-go/.env 或环境变量覆盖。生产内部服务仅走私网，SSE 反代不缓冲，容器间使用服务名。

回退：先停止新版 Go 与 app.ai_main，运行 `make run-legacy`，将 Vite 代理改回 :8000。legacy_sql 必须显式配置；两套业务入口不能同时接收写请求。回退不撤销已经提交的业务数据，不需要恢复表结构。
