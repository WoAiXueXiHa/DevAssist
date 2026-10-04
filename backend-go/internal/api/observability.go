package api

import (
	"devsupport/backend-go/internal/model"
	"errors"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
	"math"
)

func (s *Server) trace(c *gin.Context) {
	var rows []model.AgentTrace
	if !check(c, s.db(c).Where("trace_id = ?", c.Param("id")).Order("step_order").Find(&rows).Error) {
		return
	}
	if len(rows) == 0 {
		fail(c, 404, "未找到该 trace")
		return
	}
	var tools []model.ToolCallLog
	if !check(c, s.db(c).Where("trace_id = ?", c.Param("id")).Order("id").Find(&tools).Error) {
		return
	}
	steps := []gin.H{}
	calls := []gin.H{}
	duration, tokens := 0, 0
	for _, v := range rows {
		duration += v.DurationMS
		tokens += v.TokenUsage
		steps = append(steps, gin.H{"step_order": v.StepOrder, "agent_name": v.AgentName, "status": v.Status, "duration_ms": v.DurationMS, "token_usage": v.TokenUsage, "input_summary": v.InputSummary, "output_summary": v.OutputSummary, "hit_docs": v.HitDocs, "error_message": v.ErrorMessage})
	}
	for _, v := range tools {
		calls = append(calls, gin.H{"tool_name": v.ToolName, "status": v.Status, "duration_ms": v.DurationMS, "args_summary": v.ArgsSummary, "result_summary": v.ResultSummary, "error_message": v.ErrorMessage})
	}
	c.JSON(200, gin.H{"trace_id": c.Param("id"), "conversation_id": rows[0].ConversationID, "tenant_id": rows[0].TenantID, "total_duration_ms": duration, "total_tokens": tokens, "steps": steps, "tool_calls": calls})
}
func (s *Server) traces(c *gin.Context) {
	limit, ok := queryInt(c, "limit", 20)
	if !ok {
		return
	}
	q := s.db(c).Order("id DESC")
	for _, key := range []string{"conversation_id", "tenant_id"} {
		if v := c.Query(key); v != "" {
			q = q.Where(key+" = ?", v)
		}
	}
	if id := c.Query("request_id"); id != "" {
		q = q.Where("trace_id IN (?)", s.db(c).Model(&model.ToolCallLog{}).Select("trace_id").Where("args_summary LIKE ?", "%"+id+"%"))
	}
	if id := c.Query("ticket_id"); id != "" {
		var ticket model.Ticket
		e := s.db(c).Where("ticket_id = ?", id).Take(&ticket).Error
		if e != nil && !errors.Is(e, gorm.ErrRecordNotFound) {
			check(c, e)
			return
		}
		if errors.Is(e, gorm.ErrRecordNotFound) {
			q = q.Where("conversation_id = ?", "__none__")
		} else if ticket.ConversationID == nil {
			q = q.Where("conversation_id IS NULL")
		} else {
			q = q.Where("conversation_id = ?", *ticket.ConversationID)
		}
	}
	var rows []model.AgentTrace
	if !check(c, q.Limit(limit*10).Find(&rows).Error) {
		return
	}
	seen := map[string]bool{}
	out := []gin.H{}
	for _, v := range rows {
		if seen[v.TraceID] {
			continue
		}
		seen[v.TraceID] = true
		out = append(out, gin.H{"trace_id": v.TraceID, "conversation_id": v.ConversationID, "tenant_id": v.TenantID, "created_at": model.ISO(v.CreatedAt)})
		if len(out) >= limit {
			break
		}
	}
	c.JSON(200, gin.H{"traces": out})
}
func (s *Server) metrics(c *gin.Context) {
	db := s.db(c)
	var total, resolved, transferred int64
	for _, item := range []struct {
		where string
		dest  *int64
	}{{"", &total}, {"resolved_by_ai = TRUE", &resolved}, {"transferred_to_human = TRUE", &transferred}} {
		q := db.Model(&model.Conversation{})
		if item.where != "" {
			q = q.Where(item.where)
		}
		if !check(c, q.Count(item.dest).Error) {
			return
		}
	}
	aggregate := func(table, col string) (map[string]int64, error) {
		var rows []struct {
			Key   *string
			Count int64
		}
		e := db.Table(table).Select(col + " AS `key`, COUNT(*) AS count").Group(col).Scan(&rows).Error
		out := map[string]int64{}
		for _, v := range rows {
			k := "unknown"
			if v.Key != nil && *v.Key != "" {
				k = *v.Key
			}
			out[k] = v.Count
		}
		return out, e
	}
	intents, e := aggregate("conversation", "latest_intent")
	if !check(c, e) {
		return
	}
	statuses, e := aggregate("ticket", "status")
	if !check(c, e) {
		return
	}
	priorities, e := aggregate("ticket", "priority")
	if !check(c, e) {
		return
	}
	var tokenRows []struct {
		TenantID           string
		Turns, TotalTokens int64
	}
	if !check(c, db.Table("token_usage").Select("tenant_id, COUNT(*) AS turns, COALESCE(SUM(total_tokens),0) AS total_tokens").Group("tenant_id").Scan(&tokenRows).Error) {
		return
	}
	cost := []gin.H{}
	for _, v := range tokenRows {
		cost = append(cost, gin.H{"tenant_id": v.TenantID, "turns": v.Turns, "total_tokens": v.TotalTokens})
	}
	rate := 0.0
	if total > 0 {
		rate = math.RoundToEven(float64(resolved)/float64(total)*1000) / 1000
	}
	c.JSON(200, gin.H{"conversations": gin.H{"total": total, "resolved_by_ai": resolved, "transferred_to_human": transferred, "ai_resolution_rate": rate}, "intent_distribution": intents, "tickets": gin.H{"by_status": statuses, "by_priority": priorities}, "token_cost_by_tenant": cost})
}
func (s *Server) eval(c *gin.Context) {
	out, e := s.AI.Call(c.Request.Context(), "eval", gin.H{})
	if e != nil {
		aiFailure(c, e)
		return
	}
	c.JSON(200, out)
}
