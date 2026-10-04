package api

import (
	"devsupport/backend-go/internal/model"
	"devsupport/backend-go/internal/service"
	"errors"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

func ticketListDTO(v model.Ticket) gin.H {
	return gin.H{"ticket_id": v.TicketID, "title": v.Title, "category": v.Category, "priority": v.Priority, "status": v.Status, "error_code": v.ErrorCode, "created_at": model.ISO(v.CreatedAt)}
}
func ticketDTO(v model.Ticket) gin.H {
	d := ticketListDTO(v)
	d["summary"] = v.Summary
	d["related_request_ids"] = v.RelatedRequestIDs
	d["related_endpoint"] = v.RelatedEndpoint
	d["ai_diagnosis"] = v.AIDiagnosis
	d["evidence"] = v.Evidence
	d["assignee"] = v.Assignee
	d["conversation_id"] = v.ConversationID
	return d
}
func (s *Server) tickets(c *gin.Context) {
	q := s.db(c).Order("created_at DESC").Limit(50)
	u := current(c)
	if !u.Internal() {
		q = q.Where("tenant_id = ?", u.TenantID)
	}
	var rows []model.Ticket
	if !check(c, q.Find(&rows).Error) {
		return
	}
	out := []gin.H{}
	for _, v := range rows {
		out = append(out, ticketListDTO(v))
	}
	c.JSON(200, gin.H{"tickets": out})
}
func (s *Server) ticket(c *gin.Context) {
	v, ok := s.findTicket(c, true)
	if ok {
		c.JSON(200, ticketDTO(v))
	}
}
func (s *Server) feedback(c *gin.Context) {
	b, ok := body(c, []string{"conversation_id", "type"}, []string{"message_id"})
	if !ok {
		return
	}
	var v model.Conversation
	e := s.db(c).Where("id = ?", *b["conversation_id"]).Take(&v).Error
	if errors.Is(e, gorm.ErrRecordNotFound) {
		fail(c, 404, "会话不存在")
		return
	}
	if !check(c, e) || !tenantAccess(c, v.TenantID) {
		return
	}
	var tid *string
	u := current(c)
	// 反馈、会话标记和主动转人工工单同一短事务提交，任一步失败全部回滚。
	e = s.db(c).Transaction(func(tx *gorm.DB) error {
		if e := tx.Create(&model.Feedback{ConversationID: v.ID, MessageID: b["message_id"], TenantID: v.TenantID, Type: *b["type"], CreatedAt: model.Now()}).Error; e != nil {
			return e
		}
		changes := map[string]any{}
		kind := *b["type"]
		if kind == "resolved" || kind == "unresolved" {
			if v.Satisfaction == nil || *v.Satisfaction != kind {
				changes["satisfaction"] = kind
			}
			resolved := kind == "resolved"
			if v.ResolvedByAI != resolved {
				changes["resolved_by_ai"] = resolved
			}
		}
		if kind == "need_human" {
			if !v.TransferredToHuman {
				changes["transferred_to_human"] = true
			}
			var last model.Message
			e := tx.Where("conversation_id = ? AND role = ?", v.ID, "user").Order("created_at DESC").Take(&last).Error
			if e != nil && !errors.Is(e, gorm.ErrRecordNotFound) {
				return e
			}
			title := "用户请求人工支持"
			if e == nil {
				title = service.Runes(last.Content, 60)
			}
			t := service.NewTicket(v.TenantID, u.ID, title, "人工支持", "P2")
			t.Summary = title
			t.AIDiagnosis = "用户在会话中主动请求人工支持"
			t.ConversationID = &v.ID
			tid = &t.TicketID
			if e := tx.Create(&t).Error; e != nil {
				return e
			}
		}
		return service.UpdateConversation(tx, v, changes)
	})
	if check(c, e) {
		c.JSON(200, gin.H{"ok": true, "ticket_id": tid})
	}
}
