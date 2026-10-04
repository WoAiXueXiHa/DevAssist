# 完整自动回归记录

日期：2026-10-04（Asia/Shanghai）。实施根目录：`/home/hp/workspace/DevSupport-AI` 外层代码。

结论：项目现有零付费自动回归全部通过，测试函数无失败、无跳过。没有修改应用源码、已有 `.env` 或重置原业务数据。构建产物与测试证据已更新。

| 检查 | 本轮结果 | 证据 |
| --- | --- | --- |
| Go 格式 | 通过，gofmt 无输出 | [go-format.log](go-format.log) |
| Go vet / build | 通过 | [go-vet.log](go-vet.log)、[go-build.log](go-build.log) |
| Go 单元测试 | 7 passed，0 failed，0 skipped | [go-tests.jsonl](go-tests.jsonl) |
| Go race | 同样 7 项通过，未报告数据竞争 | [go-race.jsonl](go-race.jsonl) |
| Python | 5 passed，0 failed，0 skipped | [python.log](python.log)、[JUnit](python-junit.xml) |
| 前端 TypeScript / Vite 构建 | 通过 | [frontend-build.log](frontend-build.log) |
| 前端 SSE 测试 | 4 passed，0 failed，0 skipped | [frontend-tests.log](frontend-tests.log) |
| 隔离 MySQL、接口对照、故障注入、浏览器 | 127 项检查通过，其中浏览器 10 项 | [migration.log](migration.log)、[逐项证据](migration-evidence.json) |
| 清理与原服务健康 | 临时测试数据库 0 个，测试容器已自动删除，原 5 个项目基础设施容器仍 healthy | [数据库清理](temporary-database-count-after.log)、[容器状态](container-status-after.log) |

Go 无测试文件的 `cmd/server`、`internal/config`、`internal/service` 在 Go JSON 输出中是包级 `skip`；它们没有被计作跳过的测试函数。业务 Service 的对应行为由隔离数据库验收覆盖，不代表这三个包已有独立单元测试。

接口验收覆盖登录与 JWT 兼容、角色/租户权限、会话/消息/反馈/工单、工作台、文档、Trace/指标、8 个业务工具，以及真实 Go → Python HTTP → Go SQL 回调 → SSE。故障检查覆盖 AI 503/超时、助手消息落库失败、审计/工单写入失败的事务回滚、AI 不可达时禁止未脱敏写入。浏览器覆盖客户登录、中文聊天、反馈转人工、会话脱敏补充、文档、工单、工作台建议与回复、指标、390px 聊天入口及 502 提示恢复。

执行使用 Go 1.26.0、Python 3.11.15、pytest 9.1.1、Node 22.16.0、Vite 5.4.21。Python 解释器复用内层 `DevSupport-AI/backend/.venv`，测试 cwd 和导入代码为外层 `backend`；没有合并或改动内层源码。Go 普通测试与 race 均使用 `-count=1` 强制重新执行。

宿主第一次尝试受到环境限制：Go 的本地 HTTP 连接被拒绝，Python TestClient 卡住后已终止。随后在项目 Docker 私网的一次性容器内完整重跑，全部通过。宿主原始输出保存在 `host-go-tests.jsonl`、`host-go-race.jsonl` 和 `host-python-interrupted.log`，不计作应用回归失败。工具链、浏览器缓存、Go 模块缓存均只读挂载；测试脚本为 [run-in-container.sh](run-in-container.sh)。[退出码](exit-codes.json) 和 [本轮源码 SHA256](source-sha256.json) 已保存。

两项非阻断警告：前端主 JS bundle 约 1.42 MB（gzip 约 454 KB），超过 Vite 默认警告阈值；DashScope SDK 导入时产生 Assistants API 弃用警告。本轮没有为测试修改依赖或优化代码。

本轮模型调用为 **0**，没有执行付费真实模型评估、负载压测或知识向量入库。127 项是现有脚本记录的检查条目数，不代表所有业务边界已穷举。模拟模型回归证明对应流程与接线，不能据此认定真实回答质量、长期容量、服务器部署或生产就绪。390px 检查仅验证聊天入口可加载。

隔离验收脚本更新了根目录 `docs/GO_PYTHON_MIGRATION_TEST_EVIDENCE.json`；上一份证据备份为 [previous-migration-evidence.json](previous-migration-evidence.json)。历史真实模型证据未改动。
