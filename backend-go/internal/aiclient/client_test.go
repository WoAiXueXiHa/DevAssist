package aiclient

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestInternalHTTPContract(t *testing.T) {
	var calls atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Header.Get("X-Internal-Token") != "private-token" || r.Method != "POST" {
			t.Error("内部认证或方法错误")
		}
		switch r.URL.Path {
		case "/internal/ai/desensitize":
			fmt.Fprint(w, `{"texts":["脱敏内容"]}`)
		case "/internal/ai/fail":
			w.WriteHeader(503)
		case "/internal/ai/invalid":
			fmt.Fprint(w, `[]`)
		case "/internal/ai/slow":
			time.Sleep(80 * time.Millisecond)
			fmt.Fprint(w, `{}`)
		}
	}))
	defer s.Close()
	c := New(s.URL, "private-token", time.Second)
	v, e := c.Clean(context.Background(), "原文")
	if e != nil || v != "脱敏内容" {
		t.Fatal(v, e)
	}
	before := calls.Load()
	if _, e := c.Call(context.Background(), "fail", map[string]string{}); e == nil {
		t.Fatal("非 200 被接受")
	}
	if calls.Load() != before+1 {
		t.Fatal("发生未经授权的重试")
	}
	if _, e := c.Call(context.Background(), "invalid", nil); e == nil {
		t.Fatal("错误协议被接受")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	if _, e := c.Call(ctx, "slow", nil); e == nil {
		t.Fatal("未传递请求 deadline")
	}
}
