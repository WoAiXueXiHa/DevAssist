package service

import (
	"context"
	"devsupport/backend-go/internal/model"
	"encoding/json"
	"errors"
	"fmt"
	"gorm.io/gorm"
	"math"
	"strconv"
	"time"
)

// ToolContext 由 Python 可信调用链传入；不从模型 args 读取身份。
type ToolContext struct {
	TenantID       string  `json:"tenant_id"`
	TraceID        string  `json:"trace_id"`
	IsInternal     bool    `json:"is_internal"`
	UserID         string  `json:"user_id"`
	ConversationID *string `json:"conversation_id"`
}
type Tools struct{ DB *gorm.DB }

func str(a map[string]any, k, d string) string {
	if v, ok := a[k]; ok {
		s, _ := v.(string)
		return s
	}
	return d
}
func optional(a map[string]any, k string) *string {
	v, ok := a[k].(string)
	if !ok {
		return nil
	}
	return &v
}
func notFound(e error) bool { return errors.Is(e, gorm.ErrRecordNotFound) }
func roundRatio(n, total int) float64 {
	if total == 0 {
		return 0
	}
	return math.RoundToEven(float64(n)/float64(total)*1000) / 1000
}
func (t Tools) Execute(ctx context.Context, name string, a map[string]any, identity ToolContext) (map[string]any, error) {
	db := t.DB.WithContext(ctx)
	switch name {
	case "query_call_log":
		id := str(a, "request_id", "")
		if id == "" {
			return map[string]any{"found": false, "reason": "缺少 request_id"}, nil
		}
		var v model.APICallLog
		e := db.Where("request_id = ?", id).Take(&v).Error
		if notFound(e) {
			return map[string]any{"found": false, "reason": "未找到该 request_id 的日志"}, nil
		}
		if e != nil {
			return nil, e
		}
		if !identity.IsInternal && v.TenantID != identity.TenantID {
			return map[string]any{"found": false, "reason": "无权访问其它租户的调用日志"}, nil
		}
		var masked *string
		if v.APIKeyID != nil && *v.APIKeyID != "" {
			var key model.APIKey
			e := db.Where("id = ?", *v.APIKeyID).Take(&key).Error
			if e != nil && !notFound(e) {
				return nil, e
			}
			if e == nil {
				masked = &key.KeyMasked
			}
		}
		return map[string]any{"found": true, "request_id": v.RequestID, "app_id": v.AppID, "endpoint": v.Endpoint, "http_status": v.HTTPStatus, "error_code": v.ErrorCode, "latency_ms": v.LatencyMS, "api_key_masked": masked, "called_at": model.ISO(v.CreatedAt)}, nil
	case "query_recent_call_stats":
		minutes := 240
		if v, ok := a["minutes"]; ok {
			var e error
			switch n := v.(type) {
			case float64:
				minutes = int(n)
			case json.Number:
				var f float64
				f, e = n.Float64()
				minutes = int(f)
			case string:
				minutes, e = strconv.Atoi(n)
			case bool:
				minutes = 0
				if n {
					minutes = 1
				}
			default:
				e = fmt.Errorf("minutes 无效")
			}
			if e != nil {
				return nil, e
			}
		}
		q := db.Model(&model.APICallLog{}).Where("tenant_id = ?", identity.TenantID)
		endpoint := optional(a, "endpoint")
		if endpoint != nil && *endpoint != "" {
			q = q.Where("endpoint = ?", *endpoint)
		}
		var anchor struct{ Anchor *time.Time }
		if e := q.Session(&gorm.Session{}).Select("MAX(created_at) AS anchor").Where("http_status = ?", 429).Scan(&anchor).Error; e != nil {
			return nil, e
		}
		if anchor.Anchor == nil {
			if e := q.Session(&gorm.Session{}).Select("MAX(created_at) AS anchor").Scan(&anchor).Error; e != nil {
				return nil, e
			}
		}
		if anchor.Anchor == nil {
			return map[string]any{"total": 0, "by_status": map[string]int{}}, nil
		}
		// 保持原窗口只有下界：429 锚点之后的日志也计入，不按当前时间统计。
		since := anchor.Anchor.Add(-time.Duration(minutes) * time.Minute)
		var rows []struct {
			HTTPStatus int `gorm:"column:http_status"`
			Count      int
		}
		if e := q.Session(&gorm.Session{}).Select("http_status, COUNT(*) AS count").Where("created_at >= ?", since).Group("http_status").Scan(&rows).Error; e != nil {
			return nil, e
		}
		by := map[int]int{}
		total := 0
		for _, r := range rows {
			by[r.HTTPStatus] = r.Count
			total += r.Count
		}
		return map[string]any{"endpoint": endpoint, "window_minutes": minutes, "total": total, "by_status": by, "rate_limited_count": by[429], "rate_limited_ratio": roundRatio(by[429], total)}, nil
	case "query_apikey_status":
		q := db.Model(&model.APIKey{})
		if id := str(a, "api_key_id", ""); id != "" {
			q = q.Where("id = ?", id)
		} else if id := str(a, "app_id", ""); id != "" {
			q = q.Where("app_id = ?", id)
		} else {
			return map[string]any{"found": false, "reason": "需提供 api_key_id 或 app_id"}, nil
		}
		if !identity.IsInternal {
			q = q.Where("tenant_id = ?", identity.TenantID)
		}
		var rows []model.APIKey
		if e := q.Find(&rows).Error; e != nil {
			return nil, e
		}
		if len(rows) == 0 {
			return map[string]any{"found": false, "reason": "未找到对应 API Key 或无权访问"}, nil
		}
		out := []map[string]any{}
		baseline := time.Date(2026, 6, 15, 0, 0, 0, 0, time.UTC)
		for _, v := range rows {
			out = append(out, map[string]any{"api_key_masked": v.KeyMasked, "status": v.Status, "expire_at": model.OptionalISO(v.ExpireAt), "expired": v.ExpireAt != nil && v.ExpireAt.Before(baseline)})
		}
		return map[string]any{"found": true, "keys": out}, nil
	case "query_plan":
		var tenant model.Tenant
		e := db.Where("id = ?", identity.TenantID).Take(&tenant).Error
		if notFound(e) || (e == nil && tenant.PlanID == nil) {
			return map[string]any{"found": false, "reason": "未找到套餐信息"}, nil
		}
		if e != nil {
			return nil, e
		}
		var plan model.Plan
		if e := db.Where("id = ?", tenant.PlanID).Take(&plan).Error; e != nil {
			return nil, e
		}
		return map[string]any{"found": true, "plan_name": plan.Name, "qps_limit": plan.QPSLimit, "monthly_quota": plan.MonthlyQuota, "price_per_call": plan.PricePerCall, "overage_price_per_call": plan.OveragePricePerCall}, nil
	case "query_usage":
		q := db.Where("tenant_id = ?", identity.TenantID)
		if month := str(a, "month", ""); month != "" {
			q = q.Where("month = ?", month)
		}
		var rows []model.UsageRecord
		if e := q.Order("month").Find(&rows).Error; e != nil {
			return nil, e
		}
		if len(rows) == 0 {
			return map[string]any{"found": false, "reason": "未找到用量记录"}, nil
		}
		out := []map[string]any{}
		for _, v := range rows {
			out = append(out, map[string]any{"month": v.Month, "call_count": v.CallCount, "overage_count": v.OverageCount})
		}
		return map[string]any{"found": true, "usage": out}, nil
	case "query_bill":
		q := db.Where("tenant_id = ?", identity.TenantID)
		if month := str(a, "month", ""); month != "" {
			q = q.Where("month = ?", month)
		}
		var rows []model.Invoice
		if e := q.Order("month").Find(&rows).Error; e != nil {
			return nil, e
		}
		if len(rows) == 0 {
			return map[string]any{"found": false, "reason": "未找到账单"}, nil
		}
		out := []map[string]any{}
		for _, v := range rows {
			out = append(out, map[string]any{"month": v.Month, "amount": v.Amount, "status": v.Status, "items": v.Items})
		}
		return map[string]any{"found": true, "bills": out}, nil
	case "create_ticket":
		v := NewTicket(identity.TenantID, identity.UserID, str(a, "title", "技术支持工单"), str(a, "category", "其它"), str(a, "priority", "P2"))
		v.Summary = str(a, "summary", "")
		if requests, ok := a["related_request_ids"]; ok {
			v.RelatedRequestIDs = model.Encode(requests)
		}
		v.RelatedEndpoint = optional(a, "related_endpoint")
		v.ErrorCode = optional(a, "error_code")
		v.Evidence = str(a, "evidence", "")
		v.AIDiagnosis = str(a, "ai_diagnosis", "")
		v.ConversationID = identity.ConversationID
		// 写入工具不加重试/去重；Python 原 registry 的重试仍可能生成重复工单。
		if e := db.Create(&v).Error; e != nil {
			return nil, e
		}
		return map[string]any{"ticket_id": v.TicketID, "status": "new", "priority": v.Priority}, nil
	case "query_ticket":
		var v model.Ticket
		e := db.Where("ticket_id = ?", a["ticket_id"]).Take(&v).Error
		if notFound(e) {
			return map[string]any{"found": false}, nil
		}
		if e != nil {
			return nil, e
		}
		if !identity.IsInternal && v.TenantID != identity.TenantID {
			return map[string]any{"found": false, "reason": "无权访问"}, nil
		}
		return map[string]any{"found": true, "ticket_id": v.TicketID, "status": v.Status, "priority": v.Priority, "title": v.Title}, nil
	default:
		return nil, fmt.Errorf("未知业务工具: %s", name)
	}
}
