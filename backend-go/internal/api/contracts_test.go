package api

import (
	"encoding/json"
	"github.com/gin-gonic/gin"
	"net/http/httptest"
	"strings"
	"testing"
	"unicode/utf8"
)

func TestPydanticStringContract(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, tc := range []struct {
		input  string
		status int
	}{{`{}`, 422}, {`{"message":null}`, 422}, {`{"message":2}`, 422}, {`{"message":true}`, 422}, {`{"message":"","conversation_id":null,"extra":1}`, 200}, {`{"message":"中文","conversation_id":4}`, 422}, {`{"message":"ok"} {}`, 422}} {
		w := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(w)
		c.Request = httptest.NewRequest("POST", "/", strings.NewReader(tc.input))
		_, ok := body(c, []string{"message"}, []string{"conversation_id"})
		if ok {
			c.Status(200)
		}
		if w.Code != tc.status {
			t.Fatalf("%s: %d %s", tc.input, w.Code, w.Body.String())
		}
		if !ok {
			var out map[string]any
			if json.Unmarshal(w.Body.Bytes(), &out) != nil || out["detail"] == nil {
				t.Fatal("缺少 422 detail")
			}
		}
	}
}
func TestSSEUnicodeMultiline(t *testing.T) {
	gin.SetMode(gin.TestMode)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = httptest.NewRequest("POST", "/api/chat", nil)
	answer := "中文🙂第一行\n第二行，保留换行及内容。"
	(&Server{}).stream(c, map[string]string{"conversation_id": "conv"}, answer, 18, map[string]string{"answer": answer})
	out := w.Body.String()
	if !utf8.ValidString(out) || !strings.Contains(out, "event: meta\r\n") || !strings.Contains(out, "event: done\r\n") || !strings.Contains(out, "data: 第二行") {
		t.Fatal(out)
	}
	if w.Header().Get("Content-Type") != "text/event-stream; charset=utf-8" {
		t.Fatal(w.Header())
	}
}
func TestInternalAuthRejectsEmptyOrWrongToken(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, token := range []string{"", "wrong"} {
		w := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(w)
		c.Request = httptest.NewRequest("POST", "/", nil)
		c.Request.Header.Set("X-Internal-Token", token)
		s := &Server{}
		s.Config.InternalToken = strings.Repeat("a", 48)
		s.serviceAuth(c)
		if w.Code != 401 {
			t.Fatal(w.Code)
		}
	}
}

// embed 消息接口和 BaseModel 的空体/空值错误不能混为一谈。
func TestEmbeddedBodyAndBearerErrors(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, input := range []string{"", "null", "[]", `{}`, `{"content":null}`} {
		w := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(w)
		c.Request = httptest.NewRequest("POST", "/", strings.NewReader(input))
		if _, ok := body(c, []string{"content"}, nil); ok {
			t.Fatal("无效 content 被接受")
		}
		var response struct {
			Detail []struct {
				Type  string
				Loc   []string
				Input any
			}
		}
		if e := json.Unmarshal(w.Body.Bytes(), &response); e != nil {
			t.Fatal(e)
		}
		if len(response.Detail) != 1 || response.Detail[0].Type != "missing" || response.Detail[0].Input != nil || len(response.Detail[0].Loc) != 2 {
			t.Fatal(w.Body.String())
		}
	}
	for _, header := range []string{"", "Basic a", "Bearer", "Bearer a b"} {
		w := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(w)
		c.Request = httptest.NewRequest("GET", "/", nil)
		c.Request.Header.Set("Authorization", header)
		(&Server{}).authenticate(c)
		expected := "未提供凭证"
		if header == "Bearer a b" {
			expected = "凭证无效或已过期"
		}
		if w.Code != 401 || !strings.Contains(w.Body.String(), expected) {
			t.Fatal(header, w.Code, w.Body.String())
		}
	}
}
