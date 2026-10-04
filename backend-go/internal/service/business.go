// Package service 提供原业务共享操作；Go 负责业务表，Python 负责 AI 执行记录。
package service

import (
	"crypto/rand"
	"devsupport/backend-go/internal/model"
	"encoding/hex"
	"gorm.io/gorm"
	"time"
)

func ID(prefix string, n int) string {
	b := make([]byte, (n+1)/2)
	if _, e := rand.Read(b); e != nil {
		panic(e)
	}
	return prefix + "_" + hex.EncodeToString(b)[:n]
}
func Ptr(s string) *string { return &s }
func NewConversation(u model.User) model.Conversation {
	now := model.Now()
	return model.Conversation{ID: ID("conv", 12), TenantID: u.TenantID, UserID: u.ID, Channel: "web", Status: "active", CollectedEntities: model.JSON("{}"), CreatedAt: now, UpdatedAt: now}
}
func NewMessage(conv, role, content string, meta any) model.Message {
	if meta == nil {
		meta = map[string]any{}
	}
	return model.Message{ID: ID("msg", 12), ConversationID: conv, Role: role, Content: content, Meta: model.Encode(meta), CreatedAt: model.Now()}
}
func NewTicket(tenant, user, title, category, priority string) model.Ticket {
	now := model.Now()
	return model.Ticket{TicketID: ID("tk_20260615", 6), TenantID: tenant, UserID: user, Category: category, Priority: priority, Status: "new", Title: title, RelatedRequestIDs: model.JSON("[]"), CreatedAt: now, UpdatedAt: now}
}
func Audit(db *gorm.DB, tenant, user, action, detail string) error {
	return db.Create(&model.AuditLog{TenantID: Ptr(tenant), UserID: Ptr(user), Action: action, Detail: detail, CreatedAt: model.Now()}).Error
}
func Runes(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

// 原 SQLAlchemy onupdate 发生在实际字段变化时；不凭一次无变化请求修改更新时间。
func UpdateConversation(db *gorm.DB, c model.Conversation, changes map[string]any) error {
	if len(changes) == 0 {
		return nil
	}
	changes["updated_at"] = time.Now().UTC()
	return db.Model(&model.Conversation{}).Where("id = ?", c.ID).Updates(changes).Error
}
