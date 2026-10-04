// Package config 复用原 Python 数据库/JWT 配置，不重置既有账号或数据。
package config

import (
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"time"

	mysqlDriver "github.com/go-sql-driver/mysql"
	"github.com/joho/godotenv"
)

type Config struct {
	Listen, DSN, JWTSecret, InternalToken, AIURL, KnowledgeDir, Env string
	JWTMinutes                                                      int
	AITimeout                                                       time.Duration
	MaxOpen, MaxIdle                                                int
}

func Load() (Config, error) {
	// 环境变量优先，然后 Go .env；未配置的共享字段从原 ai-py/.env 补齐。
	for _, p := range []string{".env", "../ai-py/.env", "ai-py/.env"} {
		_ = godotenv.Load(p)
	}
	get := func(k, d string) string {
		if v, ok := os.LookupEnv(k); ok {
			return v
		}
		return d
	}
	number := func(k string, d int) (int, error) {
		n, e := strconv.Atoi(get(k, strconv.Itoa(d)))
		if e != nil || n <= 0 {
			return 0, fmt.Errorf("%s 必须为正整数", k)
		}
		return n, nil
	}
	c := Config{Listen: get("GO_LISTEN_ADDR", "127.0.0.1:8080"), JWTSecret: get("JWT_SECRET", "change-me-in-production-please"), InternalToken: get("INTERNAL_SERVICE_TOKEN", ""), AIURL: get("PYTHON_AI_URL", "http://127.0.0.1:8000"), Env: get("APP_ENV", "dev")}
	if get("JWT_ALGORITHM", "HS256") != "HS256" {
		return c, fmt.Errorf("迁移仅兼容 HS256")
	}
	if len(c.InternalToken) < 32 {
		return c, fmt.Errorf("请配置至少 32 字符的 INTERNAL_SERVICE_TOKEN")
	}
	var e error
	if c.JWTMinutes, e = number("JWT_EXPIRE_MINUTES", 720); e != nil {
		return c, e
	}
	seconds, e := number("AI_TIMEOUT_SECONDS", 180)
	if e != nil {
		return c, e
	}
	c.AITimeout = time.Duration(seconds) * time.Second
	if c.MaxOpen, e = number("MYSQL_MAX_OPEN_CONNS", 10); e != nil {
		return c, e
	}
	if c.MaxIdle, e = number("MYSQL_MAX_IDLE_CONNS", 3); e != nil {
		return c, e
	}
	dir := get("KNOWLEDGE_DIR", "")
	if dir == "" {
		dir = "../data/knowledge"
		if _, err := os.Stat(dir); err != nil {
			dir = "data/knowledge"
		}
	}
	c.KnowledgeDir, e = filepath.Abs(dir)
	if e != nil {
		return c, e
	}
	d := mysqlDriver.NewConfig()
	d.User = get("MYSQL_USER", "devsupport")
	d.Passwd = get("MYSQL_PASSWORD", "devsupport123")
	d.Net = "tcp"
	d.Addr = net.JoinHostPort(get("MYSQL_HOST", "127.0.0.1"), get("MYSQL_PORT", "3307"))
	d.DBName = get("MYSQL_DB", "devsupport")
	d.ParseTime = true
	d.Loc = time.UTC
	d.Params = map[string]string{"charset": "utf8mb4"}
	d.Timeout = 5 * time.Second
	d.ReadTimeout = 15 * time.Second
	d.WriteTimeout = 15 * time.Second
	c.DSN = d.FormatDSN()
	return c, nil
}
