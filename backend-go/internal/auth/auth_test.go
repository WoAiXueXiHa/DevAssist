package auth

import (
	"devsupport/backend-go/internal/model"
	"github.com/golang-jwt/jwt/v5"
	"golang.org/x/crypto/bcrypt"
	"strings"
	"testing"
	"time"
)

func TestPasswordAndTokenCompatibility(t *testing.T) {
	password := strings.Repeat("中", 25)
	hash, e := bcrypt.GenerateFromPassword([]byte(password)[:72], bcrypt.MinCost)
	if e != nil {
		t.Fatal(e)
	}
	if !VerifyPassword(password, string(hash)) || !VerifyPassword(strings.Repeat("中", 24)+"其他后缀", string(hash)) {
		t.Fatal("未保持 UTF-8 72 字节截断")
	}
	if VerifyPassword("wrong", string(hash)) || VerifyPassword(password, "invalid") {
		t.Fatal("错误密码被接受")
	}
	secret := "contract-test-secret"
	u := model.User{ID: "u", TenantID: "tenant", Role: "support"}
	token, e := Token(u, secret, 720)
	if e != nil {
		t.Fatal(e)
	}
	sub, e := Subject(token, secret)
	if e != nil || sub != "u" {
		t.Fatal(sub, e)
	}
	for _, tc := range []struct {
		method jwt.SigningMethod
		claims jwt.MapClaims
	}{{jwt.SigningMethodHS384, jwt.MapClaims{"sub": "u", "exp": time.Now().Add(time.Hour).Unix()}}, {jwt.SigningMethodHS256, jwt.MapClaims{"sub": "u", "exp": time.Now().Add(-time.Hour).Unix()}}, {jwt.SigningMethodHS256, jwt.MapClaims{"sub": 123}}} {
		token, e := jwt.NewWithClaims(tc.method, tc.claims).SignedString([]byte(secret))
		if e != nil {
			t.Fatal(e)
		}
		if _, e := Subject(token, secret); e == nil {
			t.Fatal("错误算法/过期/错误 sub 被接受")
		}
	}
	// 模拟原 python-jose 的四个 claim，不依赖新增的 issuer/audience/iat。
	old, e := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.MapClaims{"sub": "old-user", "tenant_id": "tenant", "role": "customer_dev", "exp": time.Now().Add(time.Hour).Unix()}).SignedString([]byte(secret))
	if e != nil {
		t.Fatal(e)
	}
	if sub, e := Subject(old, secret); e != nil || sub != "old-user" {
		t.Fatal(sub, e)
	}
}
