// Package api 对齐原 FastAPI 请求/响应与权限，业务 SQL 直接在 Go 执行。
package api

import (
	"bytes"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"unicode/utf8"

	"devsupport/backend-go/internal/aiclient"
	"devsupport/backend-go/internal/auth"
	"devsupport/backend-go/internal/config"
	"devsupport/backend-go/internal/model"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type Server struct {
	DB     *gorm.DB
	AI     aiclient.AI
	Config config.Config
}

func (s *Server) Router() *gin.Engine {
	r := gin.New()
	r.Use(gin.Recovery())
	r.Use(func(c *gin.Context) {
		origin := c.GetHeader("Origin")
		if origin == "http://localhost:5173" || origin == "http://127.0.0.1:5173" {
			c.Header("Access-Control-Allow-Origin", origin)
			c.Header("Vary", "Origin")
			c.Header("Access-Control-Allow-Credentials", "true")
			c.Header("Access-Control-Allow-Methods", "*")
			c.Header("Access-Control-Allow-Headers", "*")
		}
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(200)
			return
		}
		c.Next()
	})
	health := func(c *gin.Context) { c.JSON(200, gin.H{"status": "ok", "env": s.Config.Env, "version": "0.1.0"}) }
	r.GET("/health", health)
	r.GET("/api/health", health)
	r.POST("/api/auth/login", s.login)
	a := r.Group("/api", s.authenticate)
	a.GET("/auth/me", s.me)
	a.POST("/chat", s.chat)
	a.GET("/conversations", s.conversations)
	a.GET("/conversations/:id", s.conversation)
	a.POST("/conversations/:id/messages", s.addMessage)
	a.GET("/tickets", s.tickets)
	a.GET("/tickets/:id", s.ticket)
	a.POST("/feedback", s.feedback)
	a.GET("/docs", s.docs)
	a.GET("/docs/:id", s.doc)
	w := a.Group("/workbench", s.internal)
	w.GET("/tickets", s.workbenchTickets)
	w.GET("/tickets/:id", s.workbenchTicket)
	w.POST("/tickets/:id", s.updateTicket)
	w.GET("/conversations/:id/suggest_reply", s.suggest)
	w.POST("/conversations/:id/reply", s.reply)
	w.POST("/conversations/:id/takeover", s.takeover)
	o := a.Group("", s.internal)
	o.GET("/traces", s.traces)
	o.GET("/traces/:id", s.trace)
	o.GET("/metrics", s.metrics)
	o.POST("/eval/run", s.eval)
	r.POST("/internal/tools/execute", s.serviceAuth, s.tool)
	return r
}
func fail(c *gin.Context, code int, detail any) { c.AbortWithStatusJSON(code, gin.H{"detail": detail}) }
func check(c *gin.Context, e error) bool {
	if e == nil {
		return true
	}
	fail(c, 500, "数据库操作失败")
	return false
}
func (s *Server) db(c *gin.Context) *gorm.DB { return s.DB.WithContext(c.Request.Context()) }
func current(c *gin.Context) model.User      { return c.MustGet("user").(model.User) }
func (s *Server) authenticate(c *gin.Context) {
	scheme, credential, found := strings.Cut(c.GetHeader("Authorization"), " ")
	if !found || credential == "" || !strings.EqualFold(scheme, "bearer") {
		fail(c, 401, "未提供凭证")
		return
	}
	id, e := auth.Subject(credential, s.Config.JWTSecret)
	if e != nil {
		fail(c, 401, "凭证无效或已过期")
		return
	}
	var u model.User
	e = s.db(c).Where("id = ?", id).Take(&u).Error
	if errors.Is(e, gorm.ErrRecordNotFound) {
		fail(c, 401, "用户不存在")
		return
	}
	if !check(c, e) {
		return
	}
	c.Set("user", u)
	c.Next()
}
func (s *Server) internal(c *gin.Context) {
	if !current(c).Internal() {
		fail(c, 403, "需要技术支持或管理员权限")
		return
	}
	c.Next()
}
func (s *Server) serviceAuth(c *gin.Context) {
	if len(s.Config.InternalToken) < 32 || subtle.ConstantTimeCompare([]byte(c.GetHeader("X-Internal-Token")), []byte(s.Config.InternalToken)) != 1 {
		fail(c, 401, "内部凭证无效")
		return
	}
	c.Next()
}
func tenantAccess(c *gin.Context, tenant string) bool {
	u := current(c)
	if u.Internal() || u.TenantID == tenant {
		return true
	}
	fail(c, 403, "无权访问其它租户数据")
	return false
}
func (s *Server) findConversation(c *gin.Context, access bool) (model.Conversation, bool) {
	var v model.Conversation
	e := s.db(c).Where("id = ?", c.Param("id")).Take(&v).Error
	if errors.Is(e, gorm.ErrRecordNotFound) {
		fail(c, 404, "会话不存在")
		return v, false
	}
	if !check(c, e) {
		return v, false
	}
	if access && !tenantAccess(c, v.TenantID) {
		return v, false
	}
	return v, true
}
func (s *Server) findTicket(c *gin.Context, access bool) (model.Ticket, bool) {
	var v model.Ticket
	e := s.db(c).Where("ticket_id = ?", c.Param("id")).Take(&v).Error
	if errors.Is(e, gorm.ErrRecordNotFound) {
		fail(c, 404, "工单不存在")
		return v, false
	}
	if !check(c, e) {
		return v, false
	}
	if access && !tenantAccess(c, v.TenantID) {
		return v, false
	}
	return v, true
}

// Pydantic 的字符串字段允许空字符串与额外字段；缺失/null/类型错误统一返回 422 detail。
// required 是必须出现且非 null 的字段；optional 允许缺失与 null，均不转换数字/布尔。
func body(c *gin.Context, required, optional []string) (map[string]*string, bool) {
	data, readErr := io.ReadAll(io.LimitReader(c.Request.Body, 2<<20))
	if readErr != nil {
		fail(c, 400, "请求体读取失败")
		return nil, false
	}
	embedded := len(required) == 1 && required[0] == "content"
	missing := func() {
		loc := []any{"body"}
		if embedded {
			loc = append(loc, "content")
		}
		fail(c, 422, []gin.H{{"type": "missing", "loc": loc, "msg": "Field required", "input": nil}})
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var payload any
	err := dec.Decode(&payload)
	if err == io.EOF {
		missing()
		return nil, false
	}
	if err != nil {
		position := len(data)
		message := "Expecting value"
		if syntax, ok := err.(*json.SyntaxError); ok {
			position = int(syntax.Offset) - 1
			text := syntax.Error()
			if strings.Contains(text, "object key") || strings.Contains(text, "object key string") {
				message = "Expecting property name enclosed in double quotes"
			}
			if strings.Contains(text, "after object key") {
				message = "Expecting ':' delimiter"
			}
			if strings.Contains(text, "after object key:value pair") || strings.Contains(text, "after array element") {
				message = "Expecting ',' delimiter"
			}
		} else if strings.HasSuffix(strings.TrimSpace(string(data)), "{") {
			message = "Expecting property name enclosed in double quotes"
		}
		if position < 0 {
			position = 0
		}
		if position > len(data) {
			position = len(data)
		}
		fail(c, 422, []gin.H{{"type": "json_invalid", "loc": []any{"body", utf8.RuneCount(data[:position])}, "msg": "JSON decode error", "input": gin.H{}, "ctx": gin.H{"error": message}}})
		return nil, false
	}
	offset := int(dec.InputOffset())
	if remaining := bytes.TrimSpace(data[offset:]); len(remaining) > 0 {
		index := offset
		for index < len(data) && (data[index] == ' ' || data[index] == '\n' || data[index] == '\r' || data[index] == '\t') {
			index++
		}
		fail(c, 422, []gin.H{{"type": "json_invalid", "loc": []any{"body", utf8.RuneCount(data[:index])}, "msg": "JSON decode error", "input": gin.H{}, "ctx": gin.H{"error": "Extra data"}}})
		return nil, false
	}
	if payload == nil {
		missing()
		return nil, false
	}
	raw, ok := payload.(map[string]any)
	if !ok {
		if embedded {
			missing()
		} else {
			fail(c, 422, []gin.H{{"type": "model_attributes_type", "loc": []any{"body"}, "msg": "Input should be a valid dictionary or object to extract fields from", "input": payload}})
		}
		return nil, false
	}
	out := map[string]*string{}
	errs := []gin.H{}
	fields := append(append([]string{}, required...), optional...)
	for i, k := range fields {
		v, exists := raw[k]
		if i < len(required) && (!exists || (k == "content" && v == nil)) {
			// Body(embed=True) 的缺字段/null 与 BaseModel 的缺字段 input 不同。
			var input any = raw
			if k == "content" && len(required) == 1 {
				input = nil
			}
			errs = append(errs, gin.H{"type": "missing", "loc": []any{"body", k}, "msg": "Field required", "input": input})
			continue
		}
		if !exists || (v == nil && i >= len(required)) {
			out[k] = nil
			continue
		}
		str, ok := v.(string)
		if !ok {
			errs = append(errs, gin.H{"type": "string_type", "loc": []any{"body", k}, "msg": "Input should be a valid string", "input": v})
			continue
		}
		out[k] = &str
	}
	if len(errs) > 0 {
		fail(c, 422, errs)
		return nil, false
	}
	return out, true
}
func queryInt(c *gin.Context, k string, d int) (int, bool) {
	v, ok := c.GetQuery(k)
	if !ok {
		return d, true
	}
	n, e := strconv.Atoi(v)
	if e != nil {
		fail(c, 422, []gin.H{{"type": "int_parsing", "loc": []any{"query", k}, "msg": "Input should be a valid integer, unable to parse string as an integer", "input": v}})
		return 0, false
	}
	return n, true
}
func (s *Server) userInfo(c *gin.Context, u model.User) (gin.H, bool) {
	var t model.Tenant
	e := s.db(c).Where("id = ?", u.TenantID).Take(&t).Error
	if e != nil && !errors.Is(e, gorm.ErrRecordNotFound) {
		check(c, e)
		return nil, false
	}
	var name any
	if e == nil {
		name = t.Name
	}
	return gin.H{"user_id": u.ID, "username": u.Username, "display_name": u.DisplayName, "role": u.Role, "tenant_id": u.TenantID, "tenant_name": name}, true
}
func (s *Server) login(c *gin.Context) {
	b, ok := body(c, []string{"username", "password"}, nil)
	if !ok {
		return
	}
	var u model.User
	e := s.db(c).Where("username = ?", *b["username"]).Take(&u).Error
	if errors.Is(e, gorm.ErrRecordNotFound) || (e == nil && !auth.VerifyPassword(*b["password"], u.PasswordHash)) {
		fail(c, 401, "用户名或密码错误")
		return
	}
	if !check(c, e) {
		return
	}
	info, ok := s.userInfo(c, u)
	if !ok {
		return
	}
	t, e := auth.Token(u, s.Config.JWTSecret, s.Config.JWTMinutes)
	if e != nil {
		fail(c, 500, "凭证签发失败")
		return
	}
	c.JSON(200, gin.H{"access_token": t, "token_type": "bearer", "user": info})
}
func (s *Server) me(c *gin.Context) {
	v, ok := s.userInfo(c, current(c))
	if ok {
		c.JSON(200, v)
	}
}
func aiFailure(c *gin.Context, e error) {
	if errors.Is(e, c.Request.Context().Err()) && c.Request.Context().Err() != nil {
		return
	}
	fail(c, http.StatusBadGateway, "AI 服务暂不可用，请稍后重试")
}
