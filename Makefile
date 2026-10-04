.PHONY: infra-up infra-down infra-status init-db seed ingest setup run run-ai run-legacy front health clean check check-go check-python check-front

# 直接使用项目虚拟环境，避免误用已删除的内层路径或系统 Python。
PYTHON ?= $(CURDIR)/ai-py/.venv/bin/python

# ---------- 基础设施（MySQL / Redis / Milvus）----------
infra-up:        ## 启动基础设施
	docker compose up -d

infra-down:      ## 停止基础设施
	docker compose down

infra-status:    ## 查看基础设施状态
	docker compose ps

# ---------- 数据准备（在 ai-py 下执行）----------
init-db:         ## 建 MySQL 表 + Milvus collection（--recreate 删表重建）
	cd ai-py && $(PYTHON) -m scripts.init_db --recreate

seed:            ## 灌种子数据（租户/账号/日志/账单）
	cd ai-py && $(PYTHON) -m scripts.seed_data

ingest:          ## 知识库切片向量化入库
	cd ai-py && $(PYTHON) -m scripts.ingest_knowledge

setup: init-db seed ingest   ## 一键准备数据（建表 + 种子 + 知识库）

# ---------- 运行 ----------
run:             ## 启动 Go 业务后端 :8080（另一终端先起 run-ai）
	cd backend-go && go run -buildvcs=false ./cmd/server

run-ai:          ## 启动 Python 内部 AI :8000，单 worker，不注册外部业务路由
	cd ai-py && $(PYTHON) -m uvicorn app.ai_main:app --host 127.0.0.1 --port 8000 --workers 1

run-legacy:      ## 明确回退：先停新版两个进程，再将 Vite 代理改回 :8000
	cd ai-py && BUSINESS_TOOLS_BACKEND=legacy_sql $(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8000

front:           ## 启动前端 :5173
	cd frontend && npm run dev

health:          ## 健康检查
	curl -s http://127.0.0.1:8080/api/health

# ---------- 评估 / 压测 ----------
eval:            ## 跑标准评估集（意图/引用/脱敏等指标）
	cd ai-py && $(PYTHON) -m eval.run_eval

bench:           ## 并发压测 + 阶段耗时分解 + 缓存优化对比
	cd ai-py && $(PYTHON) -m benchmark.loadtest

# ---------- 清理 ----------
clean:           ## 停止并清理容器与数据卷
	docker compose down -v

# 全部实现完成后统一运行；普通测试零模型调用，隔离 MySQL 验收另见 docs。
check: check-go check-python check-front

check-go:
	cd backend-go && go vet ./... && go test ./... && go test -race ./... && go build -buildvcs=false -o bin/server ./cmd/server

check-python:
	cd ai-py && $(PYTHON) -m pytest tests -q

check-front:
	cd frontend && npm run build && npm run test
