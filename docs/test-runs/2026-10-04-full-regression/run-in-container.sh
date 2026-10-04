#!/bin/bash
# Run from a temporary container with read-only host toolchains and project mounts.
set -uo pipefail
task_root=/home/hp/workspace/DevSupport-AI
task_report="$task_root/docs/test-runs/2026-10-04-full-regression"
task_python="$task_root/ai-py/.venv/bin/python"
export PATH="/home/hp/.nvm/versions/node/v22.16.0/bin:/usr/bin:/bin"
export GOCACHE=/tmp/devsupport-full-regression-go-cache
export GOPATH=/home/hp/go
export GOFLAGS=-buildvcs=false
export GOPROXY=off
export PYTHONDONTWRITEBYTECODE=1
export PLAYWRIGHT_BROWSERS_PATH=/home/hp/.cache/ms-playwright
export MYSQL_HOST=devsupport-mysql MYSQL_PORT=3306
export DASHSCOPE_API_KEY=""
export MIGRATION_BROWSER_CHECK=1
export MIGRATION_NODE=/home/hp/.nvm/versions/node/v22.16.0/bin/node

cd "$task_root/backend-go"
gofmt -l cmd internal > "$task_report/go-format.log"
format_status=$?
if [ -s "$task_report/go-format.log" ]; then format_status=1; fi
go vet ./... > "$task_report/go-vet.log" 2>&1
vet_status=$?
go test -count=1 -timeout=90s -json ./... > "$task_report/go-tests.jsonl" 2>&1
test_status=$?
go test -count=1 -timeout=90s -race -json ./... > "$task_report/go-race.jsonl" 2>&1
race_status=$?
go build -buildvcs=false -o bin/server ./cmd/server > "$task_report/go-build.log" 2>&1
build_status=$?
printf 'Go format=%s vet=%s test=%s race=%s build=%s\n' "$format_status" "$vet_status" "$test_status" "$race_status" "$build_status"

cd "$task_root/ai-py"
timeout 120 "$task_python" -m pytest tests -v -ra --junitxml="$task_report/python-junit.xml" > "$task_report/python.log" 2>&1
python_status=$?
cat "$task_report/python.log"

migration_status=125
if [ "$build_status" -eq 0 ]; then
    timeout --signal=TERM --kill-after=15 240 "$task_python" -m scripts.check_migration > "$task_report/migration.log" 2>&1
    migration_status=$?
    cat "$task_report/migration.log"
    if [ "$migration_status" -eq 0 ]; then
        cp "$task_root/docs/GO_PYTHON_MIGRATION_TEST_EVIDENCE.json" "$task_report/migration-evidence.json"
    fi
fi
printf '{"go_format":%s,"go_vet":%s,"go_test":%s,"go_race":%s,"go_build":%s,"python":%s,"migration_with_browser":%s}\n' "$format_status" "$vet_status" "$test_status" "$race_status" "$build_status" "$python_status" "$migration_status" > "$task_report/exit-codes.json"
cat "$task_report/exit-codes.json"
test "$format_status" -eq 0 && test "$vet_status" -eq 0 && test "$test_status" -eq 0 && test "$race_status" -eq 0 && test "$build_status" -eq 0 && test "$python_status" -eq 0 && test "$migration_status" -eq 0
