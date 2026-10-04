package api

import (
	"devsupport/backend-go/internal/model"
	"devsupport/backend-go/internal/service"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
	"strings"
	"time"
)

func value(result map[string]any, key string, defaultValue any) any {
	v, ok := result[key]
	if !ok {
		return defaultValue
	}
	return v
}
func boolValue(v any) bool { b, _ := v.(bool); return b }
func (s *Server) chat(c *gin.Context) {
	b, ok := body(c, []string{"message"}, []string{"conversation_id"})
	if !ok {
		return
	}
	u := current(c)
	var conv model.Conversation
	found := false
	if id := b["conversation_id"]; id != nil && *id != "" {
		e := s.db(c).Where("id = ?", *id).Take(&conv).Error
		if e != nil && !errors.Is(e, gorm.ErrRecordNotFound) {
			check(c, e)
			return
		}
		found = e == nil && (u.Internal() || conv.TenantID == u.TenantID)
	}
	if !found {
		conv = service.NewConversation(u)
		if !check(c, s.db(c).Create(&conv).Error) {
			return
		}
	}
	m := service.NewMessage(conv.ID, "user", *b["message"], nil)
	// 原聊天路径存原问题，非 AI 追加/人工回复走脱敏接口；保持原路径差异。
	// AI 前用户消息已提交并释放连接，Python 才能回调 Go 工具，避免长事务互等。
	if !check(c, s.db(c).Create(&m).Error) {
		return
	}
	if conv.TransferredToHuman && !u.Internal() {
		s.stream(c, gin.H{"conversation_id": conv.ID, "message_id": m.ID, "intent": "human", "trace_id": nil}, "您的消息已转达人工技术支持，我们会尽快回复，可在「我的会话」查看进展。", 12, gin.H{"human_mode": true})
		return
	}
	result, e := s.AI.Call(c.Request.Context(), "run", gin.H{"query": *b["message"], "tenant_id": conv.TenantID, "user_id": u.ID, "conversation_id": conv.ID, "is_internal": u.Internal()})
	if e != nil {
		aiFailure(c, e)
		return
	}
	answer, ok := result["answer"].(string)
	if !ok {
		fail(c, 502, "AI 返回答案无效")
		return
	}
	meta := gin.H{"intent": result["intent"], "citations": value(result, "citations", []any{}), "card": result["card"], "trace_id": result["trace_id"], "ticket_id": result["ticket_id"], "need_human": result["need_human"], "from_cache": value(result, "from_cache", false)}
	assistant := service.NewMessage(conv.ID, "assistant", answer, meta)
	e = s.db(c).Transaction(func(tx *gorm.DB) error {
		if e := tx.Create(&assistant).Error; e != nil {
			return e
		}
		updates := map[string]any{}
		intent, _ := result["intent"].(string)
		var next *string
		if result["intent"] != nil {
			next = &intent
		}
		if (next == nil) != (conv.LatestIntent == nil) || (next != nil && conv.LatestIntent != nil && *next != *conv.LatestIntent) {
			updates["latest_intent"] = next
		}
		if boolValue(result["need_human"]) && !conv.TransferredToHuman {
			updates["transferred_to_human"] = true
		}
		return service.UpdateConversation(tx, conv, updates)
	})
	if !check(c, e) {
		return
	}
	done := gin.H{"answer": answer, "card": result["card"], "citations": value(result, "citations", []any{}), "ticket_id": result["ticket_id"], "need_human": value(result, "need_human", false), "need_clarify": value(result, "need_clarify", false), "from_cache": value(result, "from_cache", false), "trace_id": result["trace_id"]}
	s.stream(c, gin.H{"conversation_id": conv.ID, "message_id": assistant.ID, "intent": result["intent"], "trace_id": result["trace_id"]}, answer, 18, done)
}
func event(c *gin.Context, name, data string) bool {
	if c.Request.Context().Err() != nil {
		return false
	}
	if _, e := fmt.Fprintf(c.Writer, "event: %s\r\n", name); e != nil {
		return false
	}
	data = strings.ReplaceAll(strings.ReplaceAll(data, "\r\n", "\n"), "\r", "\n")
	for _, line := range strings.Split(data, "\n") {
		if _, e := fmt.Fprintf(c.Writer, "data: %s\r\n", line); e != nil {
			return false
		}
	}
	if _, e := fmt.Fprint(c.Writer, "\r\n"); e != nil {
		return false
	}
	c.Writer.Flush()
	return true
}
func jsonData(v any) string { b, _ := json.Marshal(v); return string(b) }

// 完整答案落库后按 Unicode 字符模拟打字，不是模型 token 实时生成。
// 响应开始后遇到断连直接结束，不追加 JSON 错误，也不伪造 done。
func (s *Server) stream(c *gin.Context, meta any, answer string, size int, done any) {
	c.Header("Content-Type", "text/event-stream; charset=utf-8")
	c.Header("Cache-Control", "no-cache")
	c.Header("X-Accel-Buffering", "no")
	c.Status(200)
	if !event(c, "meta", jsonData(meta)) {
		return
	}
	r := []rune(answer)
	for i := 0; i < len(r); i += size {
		end := i + size
		if end > len(r) {
			end = len(r)
		}
		if !event(c, "token", string(r[i:end])) {
			return
		}
		timer := time.NewTimer(20 * time.Millisecond)
		select {
		case <-c.Request.Context().Done():
			timer.Stop()
			return
		case <-timer.C:
		}
	}
	event(c, "done", jsonData(done))
}
