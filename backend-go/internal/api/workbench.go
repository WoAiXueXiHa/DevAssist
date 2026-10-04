package api

import (
	"devsupport/backend-go/internal/model"
	"devsupport/backend-go/internal/service"
	"fmt"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
	"strings"
)

func (s *Server) workbenchTickets(c *gin.Context) {
	q := s.db(c).Order("created_at DESC").Limit(100)
	for _, key := range []string{"tenant_id", "status", "priority", "error_code", "category"} {
		if v := c.Query(key); v != "" {
			q = q.Where(key+" = ?", v)
		}
	}
	var rows []model.Ticket
	if !check(c, q.Find(&rows).Error) {
		return
	}
	out := []gin.H{}
	for _, v := range rows {
		d := ticketListDTO(v)
		d["tenant_id"] = v.TenantID
		d["assignee"] = v.Assignee
		out = append(out, d)
	}
	c.JSON(200, gin.H{"tickets": out})
}
func (s *Server) workbenchTicket(c *gin.Context) {
	v, ok := s.findTicket(c, false)
	if !ok {
		return
	}
	out := []gin.H{}
	if v.ConversationID != nil && *v.ConversationID != "" {
		rows, ok := s.messages(c, *v.ConversationID)
		if !ok {
			return
		}
		for _, m := range rows {
			out = append(out, gin.H{"role": m.Role, "content": m.Content, "meta": m.Meta})
		}
	}
	d := ticketDTO(v)
	delete(d, "related_endpoint")
	delete(d, "created_at")
	d["tenant_id"] = v.TenantID
	c.JSON(200, gin.H{"ticket": d, "conversation_messages": out})
}
func pythonString(v *string) string {
	if v == nil {
		return "None"
	}
	return *v
}
func (s *Server) updateTicket(c *gin.Context) {
	b, ok := body(c, nil, []string{"status", "assignee", "note"})
	if !ok {
		return
	}
	v, ok := s.findTicket(c, false)
	if !ok {
		return
	}
	valid := map[string]bool{"new": true, "processing": true, "waiting_customer": true, "resolved": true, "closed": true, "escalated": true}
	updates := map[string]any{"updated_at": model.Now()}
	if status := b["status"]; status != nil && *status != "" {
		if !valid[*status] {
			fail(c, 400, "非法状态: "+*status)
			return
		}
		updates["status"] = *status
		v.Status = *status
	}
	if b["assignee"] != nil {
		updates["assignee"] = *b["assignee"]
		v.Assignee = b["assignee"]
	}
	u := current(c)
	// map 更新不会吞掉空字符串；更新与审计必须一起成功。
	e := s.db(c).Transaction(func(tx *gorm.DB) error {
		if e := tx.Model(&model.Ticket{}).Where("ticket_id = ?", v.TicketID).Updates(updates).Error; e != nil {
			return e
		}
		return service.Audit(tx, v.TenantID, u.ID, "update_ticket", fmt.Sprintf("ticket=%s status=%s assignee=%s note=%s", v.TicketID, pythonString(b["status"]), pythonString(b["assignee"]), pythonString(b["note"])))
	})
	if check(c, e) {
		c.JSON(200, gin.H{"ok": true, "ticket_id": v.TicketID, "status": v.Status, "assignee": v.Assignee})
	}
}
func (s *Server) suggest(c *gin.Context) {
	v, ok := s.findConversation(c, false)
	if !ok {
		return
	}
	rows, ok := s.messages(c, v.ID)
	if !ok {
		return
	}
	if len(rows) > 8 {
		rows = rows[len(rows)-8:]
	}
	lines := []string{}
	for _, m := range rows {
		role := "助手"
		if m.Role == "user" {
			role = "客户"
		}
		lines = append(lines, role+": "+m.Content)
	}
	out, e := s.AI.Call(c.Request.Context(), "suggest-reply", gin.H{"context": strings.Join(lines, "\n")})
	if e != nil {
		aiFailure(c, e)
		return
	}
	if _, ok := out["suggestion"].(string); !ok {
		fail(c, 502, "AI 推荐回复返回数据无效")
		return
	}
	c.JSON(200, out)
}
func (s *Server) reply(c *gin.Context) {
	b, ok := body(c, []string{"content"}, nil)
	if !ok {
		return
	}
	v, ok := s.findConversation(c, false)
	if !ok {
		return
	}
	clean, e := s.AI.Clean(c.Request.Context(), *b["content"])
	if e != nil {
		aiFailure(c, e)
		return
	}
	u := current(c)
	m := service.NewMessage(v.ID, "assistant", clean, gin.H{"by": "human", "agent_id": u.ID, "agent_name": u.DisplayName})
	e = s.db(c).Transaction(func(tx *gorm.DB) error {
		if e := tx.Create(&m).Error; e != nil {
			return e
		}
		changes := map[string]any{}
		if !v.TransferredToHuman {
			changes["transferred_to_human"] = true
		}
		if e := service.UpdateConversation(tx, v, changes); e != nil {
			return e
		}
		return service.Audit(tx, v.TenantID, u.ID, "human_reply", "conversation="+v.ID)
	})
	if check(c, e) {
		c.JSON(200, gin.H{"ok": true, "message_id": m.ID})
	}
}
func (s *Server) takeover(c *gin.Context) {
	v, ok := s.findConversation(c, false)
	if !ok {
		return
	}
	u := current(c)
	e := s.db(c).Transaction(func(tx *gorm.DB) error {
		changes := map[string]any{}
		if !v.TransferredToHuman {
			changes["transferred_to_human"] = true
		}
		if e := service.UpdateConversation(tx, v, changes); e != nil {
			return e
		}
		return service.Audit(tx, v.TenantID, u.ID, "takeover", "conversation="+v.ID)
	})
	if check(c, e) {
		c.JSON(200, gin.H{"ok": true, "conversation_id": v.ID, "assignee": u.ID})
	}
}
