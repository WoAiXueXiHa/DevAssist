package api

import (
	"context"
	"crypto/sha256"
	"devsupport/backend-go/internal/model"
	"devsupport/backend-go/internal/service"
	"encoding/hex"
	"encoding/json"
	"errors"
	"github.com/gin-gonic/gin"
	"github.com/go-sql-driver/mysql"
	"gorm.io/gorm"
	"regexp"
	"time"
)

var clientRequestPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

type chatResponse struct {
	Meta   gin.H  `json:"meta"`
	Answer string `json:"answer"`
	Done   gin.H  `json:"done"`
}

func chatPayloadHash(message string, conversationID *string) string {
	b, _ := json.Marshal(struct {
		Message        string
		ConversationID *string
	}{message, conversationID})
	hash := sha256.Sum256(b)
	return hex.EncodeToString(hash[:])
}
func (s *Server) claimChat(ctx context.Context, u model.User, key, message string, conversationID *string) (model.ChatRequest, model.Conversation, model.Message, bool, error) {
	now := model.Now()
	request := model.ChatRequest{ID: service.ID("chatreq", 24), UserID: u.ID, ClientRequestID: key, TenantID: u.TenantID, PayloadHash: chatPayloadHash(message, conversationID), Status: "pending", CreatedAt: now, UpdatedAt: now}
	var conv model.Conversation
	var msg model.Message
	err := s.DB.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(&request).Error; err != nil {
			return err
		}
		found := false
		if conversationID != nil && *conversationID != "" {
			err := tx.Where("id = ?", *conversationID).Take(&conv).Error
			if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
				return err
			}
			found = err == nil && (u.Internal() || conv.TenantID == u.TenantID)
		}
		if !found {
			conv = service.NewConversation(u)
			if err := tx.Create(&conv).Error; err != nil {
				return err
			}
		}
		msg = service.NewMessage(conv.ID, "user", message, nil)
		if err := tx.Create(&msg).Error; err != nil {
			return err
		}
		request.ConversationID = &conv.ID
		return tx.Model(&model.ChatRequest{}).Where("id = ?", request.ID).Update("conversation_id", conv.ID).Error
	})
	if err == nil {
		return request, conv, msg, true, nil
	}
	var duplicate *mysql.MySQLError
	if !errors.As(err, &duplicate) || duplicate.Number != 1062 {
		return request, conv, msg, false, err
	}
	// Use a fresh destination: GORM otherwise adds the failed candidate ID to WHERE.
	var existing model.ChatRequest
	err = s.DB.WithContext(ctx).Where("user_id = ? AND client_request_id = ?", u.ID, key).Take(&existing).Error
	return existing, conv, msg, false, err
}
func (s *Server) completeChat(db *gorm.DB, request model.ChatRequest, response chatResponse, assistant *model.Message, conv model.Conversation, updates map[string]any) error {
	return db.Transaction(func(tx *gorm.DB) error {
		if assistant != nil {
			if err := tx.Create(assistant).Error; err != nil {
				return err
			}
		}
		if err := service.UpdateConversation(tx, conv, updates); err != nil {
			return err
		}
		result := tx.Model(&model.ChatRequest{}).Where("id = ? AND status = ?", request.ID, "pending").Updates(map[string]any{"status": "completed", "response": model.Encode(response), "updated_at": model.Now()})
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return errors.New("chat request no longer pending")
		}
		return nil
	})
}
func chatRequestError(c *gin.Context, status int, code, message string) {
	fail(c, status, gin.H{"code": code, "message": message})
}
func (s *Server) failChat(c *gin.Context, id string) {
	// An uncertain AI failure may already have committed side effects. Never retry it.
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	s.DB.WithContext(ctx).Model(&model.ChatRequest{}).Where("id = ? AND status = ?", id, "pending").Updates(map[string]any{"status": "failed", "error_code": "chat_request_failed", "updated_at": model.Now()})
	chatRequestError(c, 502, "chat_request_failed", "本次请求处理失败，系统不会自动重复执行，请查看会话或联系技术支持。")
}
func (s *Server) replayChat(c *gin.Context, request model.ChatRequest, hash string) {
	if request.PayloadHash != hash {
		chatRequestError(c, 409, "chat_request_payload_conflict", "同一个请求标识不能用于不同的消息或会话。")
		return
	}
	switch request.Status {
	case "completed":
		var response chatResponse
		if err := json.Unmarshal(request.Response, &response); err != nil || response.Meta == nil || response.Done == nil {
			chatRequestError(c, 500, "chat_request_response_invalid", "已保存的请求结果无法读取。")
			return
		}
		s.stream(c, response.Meta, response.Answer, 18, response.Done)
	case "failed":
		chatRequestError(c, 502, "chat_request_failed", "本次请求处理失败，系统不会自动重复执行，请查看会话或联系技术支持。")
	default:
		chatRequestError(c, 409, "chat_request_pending", "请求正在处理或结果尚未确认，请稍后使用同一请求标识重试。")
	}
}

// Replay is a fresh read of saved content and must honor current tenant/role access.
func (s *Server) authorizeChatReplay(c *gin.Context, request model.ChatRequest, user model.User) bool {
	if request.UserID != user.ID || request.TenantID != user.TenantID {
		fail(c, 403, "无权访问该请求")
		return false
	}
	if request.ConversationID != nil {
		var conv model.Conversation
		err := s.db(c).Where("id = ?", *request.ConversationID).Take(&conv).Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			fail(c, 403, "无权访问该会话")
			return false
		}
		if !check(c, err) {
			return false
		}
		if !user.Internal() && conv.TenantID != user.TenantID {
			fail(c, 403, "无权访问该会话")
			return false
		}
	}
	return true
}
