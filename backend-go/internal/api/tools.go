package api

import (
	"devsupport/backend-go/internal/service"
	"encoding/json"
	"github.com/gin-gonic/gin"
	"io"
)

func (s *Server) tool(c *gin.Context) {
	var input struct {
		Name    string              `json:"name"`
		Args    map[string]any      `json:"args"`
		Context service.ToolContext `json:"context"`
	}
	dec := json.NewDecoder(io.LimitReader(c.Request.Body, 2<<20))
	dec.UseNumber()
	if dec.Decode(&input) != nil || input.Context.TenantID == "" {
		fail(c, 422, "业务工具请求无效")
		return
	}
	allowed := map[string]bool{"query_call_log": true, "query_recent_call_stats": true, "query_apikey_status": true, "query_plan": true, "query_usage": true, "query_bill": true, "create_ticket": true, "query_ticket": true}
	if !allowed[input.Name] {
		fail(c, 400, "未知业务工具")
		return
	}
	out, e := (service.Tools{DB: s.DB}).Execute(c.Request.Context(), input.Name, input.Args, input.Context)
	if e != nil {
		fail(c, 500, "业务工具执行失败")
		return
	}
	c.JSON(200, out)
}
