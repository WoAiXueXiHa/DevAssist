// Package model 显式映射业务表；运行时不执行 AutoMigrate。
package model

import (
	"database/sql/driver"
	"encoding/json"
	"fmt"
	"time"
)

// JSON 保留对象、数组与 null；SQL 写入一次编码，避免将 JSON 再编码成字符串。
type JSON json.RawMessage

func (j *JSON) Scan(v any) error {
	switch x := v.(type) {
	case nil:
		*j = JSON("null")
	case []byte:
		*j = append((*j)[:0], x...)
	case string:
		*j = JSON(x)
	default:
		return fmt.Errorf("不支持的 JSON 数据类型 %T", v)
	}
	return nil
}
func (j JSON) Value() (driver.Value, error) {
	if len(j) == 0 {
		return nil, nil
	}
	if !json.Valid(j) {
		return nil, fmt.Errorf("非法 JSON")
	}
	return string(j), nil
}
func (j JSON) MarshalJSON() ([]byte, error) {
	if len(j) == 0 {
		return []byte("null"), nil
	}
	return j, nil
}
func (j *JSON) UnmarshalJSON(b []byte) error {
	if !json.Valid(b) {
		return fmt.Errorf("非法 JSON")
	}
	*j = append((*j)[:0], b...)
	return nil
}
func Encode(v any) JSON {
	b, e := json.Marshal(v)
	if e != nil {
		panic(e)
	}
	return b
}
func Now() time.Time { return time.Now().UTC() }

// Python datetime.isoformat：无时区、秒精度；存在微秒时固定输出六位。
func ISO(t time.Time) string {
	t = t.UTC()
	if t.Nanosecond()/1000 == 0 {
		return t.Format("2006-01-02T15:04:05")
	}
	return t.Format("2006-01-02T15:04:05.000000")
}
func OptionalISO(t *time.Time) any {
	if t == nil {
		return nil
	}
	return ISO(*t)
}

type User struct {
	ID                                                  string `gorm:"primaryKey"`
	TenantID, Username, PasswordHash, Role, DisplayName string
	CreatedAt                                           time.Time
}

func (User) TableName() string { return "user" }
func (u User) Internal() bool  { return u.Role == "support" || u.Role == "admin" }

type Tenant struct {
	ID        string `gorm:"primaryKey"`
	Name      string
	PlanID    *string
	CreatedAt time.Time
}

func (Tenant) TableName() string { return "tenant" }

type Conversation struct {
	ID                                string `gorm:"primaryKey"`
	TenantID, UserID, Channel, Status string
	LatestIntent                      *string
	CollectedEntities                 JSON
	ResolvedByAI                      bool `gorm:"column:resolved_by_ai"`
	TransferredToHuman                bool
	Satisfaction                      *string
	CreatedAt, UpdatedAt              time.Time
}

func (Conversation) TableName() string { return "conversation" }

type Message struct {
	ID                            string `gorm:"primaryKey"`
	ConversationID, Role, Content string
	Meta                          JSON
	CreatedAt                     time.Time
}

func (Message) TableName() string { return "message" }

type Ticket struct {
	TicketID                                                     string `gorm:"primaryKey"`
	TenantID, UserID, Category, Priority, Status, Title, Summary string
	RelatedRequestIDs                                            JSON `gorm:"column:related_request_ids"`
	RelatedEndpoint, ErrorCode                                   *string
	Evidence                                                     string
	AIDiagnosis                                                  string `gorm:"column:ai_diagnosis"`
	Assignee, ConversationID                                     *string
	CreatedAt, UpdatedAt                                         time.Time
}

func (Ticket) TableName() string { return "ticket" }

type Feedback struct {
	ID             int64 `gorm:"primaryKey"`
	ConversationID string
	MessageID      *string
	TenantID, Type string
	CreatedAt      time.Time
}

func (Feedback) TableName() string { return "feedback" }

type AuditLog struct {
	ID               int64 `gorm:"primaryKey"`
	TenantID, UserID *string
	Action, Detail   string
	CreatedAt        time.Time
}

func (AuditLog) TableName() string { return "audit_log" }

type APIKey struct {
	ID                                 string `gorm:"primaryKey"`
	AppID, TenantID, KeyMasked, Status string
	ExpireAt                           *time.Time
	CreatedAt                          time.Time
}

func (APIKey) TableName() string { return "api_key" }

type APICallLog struct {
	RequestID       string `gorm:"primaryKey"`
	TenantID, AppID string
	APIKeyID        *string `gorm:"column:api_key_id"`
	Endpoint        string
	HTTPStatus      int `gorm:"column:http_status"`
	ErrorCode       *string
	LatencyMS       int `gorm:"column:latency_ms"`
	ClientIP        *string
	CreatedAt       time.Time
}

func (APICallLog) TableName() string { return "api_call_log" }

type Plan struct {
	ID                                string `gorm:"primaryKey"`
	Name                              string
	QPSLimit                          int `gorm:"column:qps_limit"`
	MonthlyQuota                      int
	PricePerCall, OveragePricePerCall float64
}

func (Plan) TableName() string { return "plan" }

type UsageRecord struct {
	ID                      int64 `gorm:"primaryKey"`
	TenantID, Month         string
	CallCount, OverageCount int
}

func (UsageRecord) TableName() string { return "usage_record" }

type Invoice struct {
	ID              string `gorm:"primaryKey"`
	TenantID, Month string
	Items           JSON
	Amount          float64
	Status          string
}

func (Invoice) TableName() string { return "invoice" }

type AgentTrace struct {
	ID                                  int64 `gorm:"primaryKey"`
	TraceID                             string
	ConversationID, MessageID           *string
	TenantID, AgentName                 string
	StepOrder                           int
	InputSummary, OutputSummary, Status string
	DurationMS                          int `gorm:"column:duration_ms"`
	TokenUsage                          int
	HitDocs                             JSON
	ErrorMessage                        *string
	CreatedAt                           time.Time
}

func (AgentTrace) TableName() string { return "agent_trace" }

type ToolCallLog struct {
	ID                                                              int64 `gorm:"primaryKey"`
	TraceID, TenantID, ToolName, ArgsSummary, ResultSummary, Status string
	DurationMS                                                      int `gorm:"column:duration_ms"`
	ErrorMessage                                                    *string
	CreatedAt                                                       time.Time
}

func (ToolCallLog) TableName() string { return "tool_call_log" }
