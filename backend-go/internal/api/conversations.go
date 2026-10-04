package api

import (
	"devsupport/backend-go/internal/model"
	"devsupport/backend-go/internal/service"
	"github.com/gin-gonic/gin"
)

func conversationDTO(v model.Conversation) gin.H {
	return gin.H{"id": v.ID, "tenant_id": v.TenantID, "status": v.Status, "latest_intent": v.LatestIntent, "transferred_to_human": v.TransferredToHuman, "satisfaction": v.Satisfaction}
}
func (s *Server) conversations(c *gin.Context) {
	q := s.db(c).Order("updated_at DESC").Limit(50)
	u := current(c)
	if !u.Internal() {
		q = q.Where("tenant_id = ? AND user_id = ?", u.TenantID, u.ID)
	}
	var rows []model.Conversation
	if !check(c, q.Find(&rows).Error) {
		return
	}
	out := []gin.H{}
	for _, v := range rows {
		d := conversationDTO(v)
		d["updated_at"] = model.ISO(v.UpdatedAt)
		out = append(out, d)
	}
	c.JSON(200, gin.H{"conversations": out})
}
func (s *Server) messages(c *gin.Context, id string) ([]model.Message, bool) {
	var rows []model.Message
	e := s.db(c).Where("conversation_id = ?", id).Order("created_at").Find(&rows).Error
	return rows, check(c, e)
}
func messageDTO(v model.Message) gin.H {
	return gin.H{"id": v.ID, "role": v.Role, "content": v.Content, "meta": v.Meta, "created_at": model.ISO(v.CreatedAt)}
}
func (s *Server) conversation(c *gin.Context) {
	v, ok := s.findConversation(c, true)
	if !ok {
		return
	}
	rows, ok := s.messages(c, v.ID)
	if !ok {
		return
	}
	out := []gin.H{}
	for _, m := range rows {
		out = append(out, messageDTO(m))
	}
	c.JSON(200, gin.H{"conversation": conversationDTO(v), "messages": out})
}
func (s *Server) addMessage(c *gin.Context) {
	b, ok := body(c, []string{"content"}, nil)
	if !ok {
		return
	}
	v, ok := s.findConversation(c, true)
	if !ok {
		return
	}
	clean, e := s.AI.Clean(c.Request.Context(), *b["content"])
	if e != nil {
		aiFailure(c, e)
		return
	}
	m := service.NewMessage(v.ID, "user", clean, nil)
	if check(c, s.db(c).Create(&m).Error) {
		c.JSON(200, gin.H{"ok": true, "message_id": m.ID})
	}
}
