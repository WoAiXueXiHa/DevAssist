package api

import (
	"context"
	"devsupport/backend-go/internal/model"
	"devsupport/backend-go/internal/service"
	"encoding/json"
	"errors"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/mysql"
	"gorm.io/gorm"
)

type chatTestAI struct {
	calls   atomic.Int32
	entered chan struct{}
	release chan struct{}
	fail    bool
}

func (a *chatTestAI) Call(ctx context.Context, _ string, input any) (map[string]any, error) {
	a.calls.Add(1)
	if a.entered != nil {
		a.entered <- struct{}{}
	}
	if a.release != nil {
		select {
		case <-a.release:
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	if a.fail {
		return nil, errors.New("uncertain upstream failure")
	}
	return map[string]any{"answer": "saved answer", "intent": "doc"}, nil
}
func (*chatTestAI) Clean(context.Context, string) (string, error) { return "", nil }
func chatTestContext(user model.User, key, message string) (*gin.Context, *httptest.ResponseRecorder) {
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	payload, _ := json.Marshal(gin.H{"message": message, "client_request_id": key})
	c.Request = httptest.NewRequest("POST", "/api/chat", strings.NewReader(string(payload)))
	c.Request.Header.Set("Content-Type", "application/json")
	c.Set("user", user)
	return c, w
}
func TestChatRequestPayloadBinding(t *testing.T) {
	if chatPayloadHash("hello", nil) == chatPayloadHash("changed", nil) {
		t.Fatal("message not bound")
	}
	conv := "conv"
	if chatPayloadHash("hello", nil) == chatPayloadHash("hello", &conv) {
		t.Fatal("conversation not bound")
	}
	for _, v := range []string{"", strings.Repeat("a", 129), "bad key", "中文"} {
		if clientRequestPattern.MatchString(v) {
			t.Fatal(v)
		}
	}
}
func TestChatIdempotencyMySQL(t *testing.T) {
	dsn := os.Getenv("TEST_MYSQL_DSN")
	if dsn == "" {
		t.Skip("TEST_MYSQL_DSN not set")
	}
	db, e := gorm.Open(mysql.Open(dsn), &gorm.Config{})
	if e != nil {
		t.Fatal(e)
	}
	gin.SetMode(gin.TestMode)
	user := model.User{ID: service.ID("test_user", 16), TenantID: "test_tenant", Role: "customer_dev"}
	other := user
	other.ID += "_other"
	defer func() {
		var requests []model.ChatRequest
		db.Where("user_id IN ?", []string{user.ID, other.ID}).Find(&requests)
		for _, r := range requests {
			if r.ConversationID != nil {
				db.Where("conversation_id = ?", *r.ConversationID).Delete(&model.Message{})
				db.Where("id = ?", *r.ConversationID).Delete(&model.Conversation{})
			}
		}
		db.Where("user_id IN ?", []string{user.ID, other.ID}).Delete(&model.ChatRequest{})
	}()
	fake := &chatTestAI{entered: make(chan struct{}, 8), release: make(chan struct{})}
	s := &Server{DB: db, AI: fake}
	c, first := chatTestContext(user, "logical-send", "hello")
	ctx, cancel := context.WithCancel(c.Request.Context())
	c.Request = c.Request.WithContext(ctx)
	finished := make(chan struct{})
	go func() { s.chat(c); close(finished) }()
	select {
	case <-fake.entered:
	case <-time.After(5 * time.Second):
		t.Fatal("AI not reached")
	}
	// Concurrent duplicate has no second AI call and a machine-readable pending response.
	duplicate, pending := chatTestContext(user, "logical-send", "hello")
	s.chat(duplicate)
	if pending.Code != 409 || !strings.Contains(pending.Body.String(), "chat_request_pending") {
		t.Fatal(pending.Code, pending.Body.String())
	}
	conflict, changed := chatTestContext(user, "logical-send", "different")
	s.chat(conflict)
	if changed.Code != 409 || !strings.Contains(changed.Body.String(), "chat_request_payload_conflict") {
		t.Fatal(changed.Code, changed.Body.String())
	}
	// Disconnect after the claim. The detached execution must still save its result.
	cancel()
	close(fake.release)
	select {
	case <-finished:
	case <-time.After(5 * time.Second):
		t.Fatal("detached execution did not complete")
	}
	if first.Body.Len() != 0 {
		t.Fatal("disconnected stream unexpectedly sent data")
	}
	replay, out := chatTestContext(user, "logical-send", "hello")
	s.chat(replay)
	if out.Code != 200 || !strings.Contains(out.Body.String(), "event: done") || !strings.Contains(out.Body.String(), "saved answer") {
		t.Fatal(out.Code, out.Body.String())
	}
	replay2, out2 := chatTestContext(user, "logical-send", "hello")
	s.chat(replay2)
	if out2.Body.String() != out.Body.String() {
		t.Fatal("replay changed response")
	}
	if fake.calls.Load() != 1 {
		t.Fatal("duplicate AI invocation", fake.calls.Load())
	}
	crossUser, crossOut := chatTestContext(other, "logical-send", "hello")
	s.chat(crossUser)
	if crossOut.Code != 200 || fake.calls.Load() != 2 {
		t.Fatal("user-scoped request key failed")
	}
	failedAI := &chatTestAI{fail: true}
	s.AI = failedAI
	bad, badOut := chatTestContext(user, "failed-send", "failure")
	s.chat(bad)
	again, againOut := chatTestContext(user, "failed-send", "failure")
	s.chat(again)
	if badOut.Code != 502 || againOut.Code != 502 || badOut.Body.String() != againOut.Body.String() || failedAI.calls.Load() != 1 {
		t.Fatal("failed request reexecuted or changed", badOut.Body.String(), againOut.Body.String())
	}
	// A later tenant change must not expose the previously saved answer.
	changedUser := user
	changedUser.TenantID = "different_tenant"
	denied, deniedOut := chatTestContext(changedUser, "logical-send", "hello")
	s.chat(denied)
	if deniedOut.Code != 403 {
		t.Fatal("tenant move allowed replay", deniedOut.Code)
	}
	var row model.ChatRequest
	if e := db.Where("user_id = ? AND client_request_id = ?", user.ID, "logical-send").Take(&row).Error; e != nil {
		t.Fatal(e)
	}
	var messages int64
	db.Model(&model.Message{}).Where("conversation_id = ?", *row.ConversationID).Count(&messages)
	// Simulate support originally accessing another tenant, then losing support role.
	if err := db.Model(&model.Conversation{}).Where("id = ?", *row.ConversationID).Update("tenant_id", "another_tenant").Error; err != nil {
		t.Fatal(err)
	}
	downgraded, forbidden := chatTestContext(user, "logical-send", "hello")
	s.chat(downgraded)
	if forbidden.Code != 403 {
		t.Fatal("role downgrade allowed cross-tenant replay", forbidden.Code)
	}
	if messages != 2 {
		t.Fatal("duplicate/missing messages", messages)
	}
}
