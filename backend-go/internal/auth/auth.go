// Package auth 兼容原 bcrypt 截断规则与 HS256 Token。
package auth

import (
	"devsupport/backend-go/internal/model"
	"fmt"
	"github.com/golang-jwt/jwt/v5"
	"golang.org/x/crypto/bcrypt"
	"time"
)

func VerifyPassword(password, hash string) bool {
	b := []byte(password)
	if len(b) > 72 {
		b = b[:72]
	}
	return bcrypt.CompareHashAndPassword([]byte(hash), b) == nil
}
func Token(u model.User, secret string, minutes int) (string, error) {
	return jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.MapClaims{"sub": u.ID, "tenant_id": u.TenantID, "role": u.Role, "exp": time.Now().Add(time.Duration(minutes) * time.Minute).Unix()}).SignedString([]byte(secret))
}
func Subject(token, secret string) (string, error) {
	t, e := jwt.Parse(token, func(t *jwt.Token) (any, error) { return []byte(secret), nil }, jwt.WithValidMethods([]string{"HS256"}))
	if e != nil || !t.Valid {
		return "", fmt.Errorf("凭证无效或已过期")
	}
	sub, e := t.Claims.GetSubject()
	if e != nil || sub == "" {
		return "", fmt.Errorf("凭证无效或已过期")
	}
	return sub, nil
}
