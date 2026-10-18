package api

import (
	"context"
	"devsupport/backend-go/internal/service"
	"encoding/json"
	"fmt"
	"github.com/gin-gonic/gin"
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

// 人工受理以已确认的工单为准，不能仅凭模型的处理意图切换会话。
func handoffAccepted(result map[string]any) bool {
	ticketID, _ := result["ticket_id"].(string)
	return result["handoff_status"] == "accepted" && strings.TrimSpace(ticketID) != ""
}
func (s *Server) chat(c *gin.Context) {
	b, ok := body(c, []string{"message", "client_request_id"}, []string{"conversation_id"})
	if !ok {
		return
	}
	if !clientRequestPattern.MatchString(*b["client_request_id"]) {
		fail(c, 422, "client_request_id 必须是 1-128 位字母、数字、下划线或连字符")
		return
	}
	u := current(c)
	request, conv, userMessage, fresh, e := s.claimChat(c.Request.Context(), u, *b["client_request_id"], *b["message"], b["conversation_id"])
	if !check(c, e) {
		return
	}
	if !fresh {
		if !s.authorizeChatReplay(c, request, u) {
			return
		}
		s.replayChat(c, request, chatPayloadHash(*b["message"], b["conversation_id"]))
		return
	}
	// Once claimed, finish this bounded execution even if the browser disconnects.
	timeout := s.Config.AITimeout
	if timeout <= 0 {
		timeout = 180 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.WithoutCancel(c.Request.Context()), timeout+15*time.Second)
	defer cancel()
	db := s.DB.WithContext(ctx)
	if conv.TransferredToHuman && !u.Internal() {
		answer := "您的消息已转达人工技术支持，我们会尽快回复，可在「我的会话」查看进展。"
		response := chatResponse{Meta: gin.H{"conversation_id": conv.ID, "message_id": userMessage.ID, "intent": "human", "trace_id": nil}, Answer: answer, Done: gin.H{"human_mode": true, "answer": answer}}
		if e := s.completeChat(db, request, response, nil, conv, nil); e != nil {
			s.failChat(c, request.ID)
			return
		}
		s.stream(c, response.Meta, response.Answer, 12, response.Done)
		return
	}
	result, e := s.AI.Call(ctx, "run", gin.H{"query": *b["message"], "tenant_id": conv.TenantID, "user_id": u.ID, "conversation_id": conv.ID, "is_internal": u.Internal(), "request_id": request.ID})
	if e != nil {
		s.failChat(c, request.ID)
		return
	}
	answer, valid := result["answer"].(string)
	if !valid {
		s.failChat(c, request.ID)
		return
	}
	meta := gin.H{"intent": result["intent"], "citations": value(result, "citations", []any{}), "card": result["card"], "trace_id": result["trace_id"], "ticket_id": result["ticket_id"], "need_human": result["need_human"], "handoff_status": result["handoff_status"], "trace_persisted": result["trace_persisted"], "from_cache": value(result, "from_cache", false)}
	assistant := service.NewMessage(conv.ID, "assistant", answer, meta)
	updates := map[string]any{}
	intent, _ := result["intent"].(string)
	var next *string
	if result["intent"] != nil {
		next = &intent
	}
	if (next == nil) != (conv.LatestIntent == nil) || (next != nil && conv.LatestIntent != nil && *next != *conv.LatestIntent) {
		updates["latest_intent"] = next
	}
	if handoffAccepted(result) && !conv.TransferredToHuman {
		updates["transferred_to_human"] = true
	}
	done := gin.H{"answer": answer, "card": result["card"], "citations": value(result, "citations", []any{}), "ticket_id": result["ticket_id"], "need_human": value(result, "need_human", false), "handoff_status": value(result, "handoff_status", "not_required"), "trace_persisted": result["trace_persisted"], "need_clarify": value(result, "need_clarify", false), "from_cache": value(result, "from_cache", false), "trace_id": result["trace_id"]}
	response := chatResponse{Meta: gin.H{"conversation_id": conv.ID, "message_id": assistant.ID, "intent": result["intent"], "trace_id": result["trace_id"]}, Answer: answer, Done: done}
	if e := s.completeChat(db, request, response, &assistant, conv, updates); e != nil {
		s.failChat(c, request.ID)
		return
	}
	s.stream(c, response.Meta, response.Answer, 18, response.Done)
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
