// Package aiclient 仅调用内部 AI 能力，不代理旧业务 API，也不自动重试。
package aiclient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"time"
)

type AI interface {
	Call(context.Context, string, any) (map[string]any, error)
	Clean(context.Context, string) (string, error)
}
type Client struct {
	HTTP       *http.Client
	URL, Token string
}

func New(url, token string, timeout time.Duration) *Client {
	return &Client{URL: strings.TrimRight(url, "/"), Token: token, HTTP: &http.Client{Timeout: timeout, Transport: &http.Transport{Proxy: nil, DialContext: (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext, MaxIdleConns: 20, MaxIdleConnsPerHost: 10, IdleConnTimeout: 90 * time.Second, ResponseHeaderTimeout: timeout}, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
}
func (c *Client) Call(ctx context.Context, path string, input any) (map[string]any, error) {
	b, e := json.Marshal(input)
	if e != nil {
		return nil, e
	}
	r, e := http.NewRequestWithContext(ctx, "POST", c.URL+"/internal/ai/"+path, bytes.NewReader(b))
	if e != nil {
		return nil, e
	}
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("X-Internal-Token", c.Token)
	resp, e := c.HTTP.Do(r)
	if e != nil {
		return nil, fmt.Errorf("AI 服务调用失败: %w", e)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("AI 服务返回状态 %d", resp.StatusCode)
	}
	var out map[string]any
	e = json.NewDecoder(io.LimitReader(resp.Body, 8<<20)).Decode(&out)
	if e != nil || out == nil {
		return nil, fmt.Errorf("AI 返回数据无效")
	}
	return out, nil
}
func (c *Client) Clean(ctx context.Context, s string) (string, error) {
	out, e := c.Call(ctx, "desensitize", map[string]any{"texts": []string{s}})
	if e != nil {
		return "", e
	}
	xs, ok := out["texts"].([]any)
	if !ok || len(xs) != 1 {
		return "", fmt.Errorf("AI 脱敏返回数据无效")
	}
	v, ok := xs[0].(string)
	if !ok {
		return "", fmt.Errorf("AI 脱敏返回数据无效")
	}
	return v, nil
}
