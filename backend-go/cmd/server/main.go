package main

import (
	"context"
	"devsupport/backend-go/internal/aiclient"
	"devsupport/backend-go/internal/api"
	"devsupport/backend-go/internal/config"
	"log"
	"net/http"
	"os/signal"
	"syscall"
	"time"

	"gorm.io/driver/mysql"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func main() {
	cfg, e := config.Load()
	if e != nil {
		log.Fatal(e)
	}
	// 不建表、不改表、不输出 DSN。配置/连接错误不将密码或 SQL 参数写入日志。
	db, e := gorm.Open(mysql.Open(cfg.DSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if e != nil {
		log.Fatal("数据库连接失败，请检查 MySQL 配置与服务")
	}
	sqlDB, e := db.DB()
	if e != nil {
		log.Fatal("数据库连接池初始化失败")
	}
	defer sqlDB.Close()
	sqlDB.SetMaxOpenConns(cfg.MaxOpen)
	sqlDB.SetMaxIdleConns(cfg.MaxIdle)
	sqlDB.SetConnMaxLifetime(30 * time.Minute)
	app := &api.Server{DB: db, AI: aiclient.New(cfg.AIURL, cfg.InternalToken, cfg.AITimeout), Config: cfg}
	srv := &http.Server{Addr: cfg.Listen, Handler: app.Router(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 30 * time.Second, IdleTimeout: 60 * time.Second}
	// 不设置短 WriteTimeout，以免 AI 等待与 SSE 被统一写超时提前截断。
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	errCh := make(chan error, 1)
	go func() { log.Printf("Go 业务后端启动于 %s", cfg.Listen); errCh <- srv.ListenAndServe() }()
	select {
	case e := <-errCh:
		if e != nil && e != http.ErrServerClosed {
			log.Fatal(e)
		}
	case <-ctx.Done():
		end, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if e := srv.Shutdown(end); e != nil {
			_ = srv.Close()
		}
	}
}
