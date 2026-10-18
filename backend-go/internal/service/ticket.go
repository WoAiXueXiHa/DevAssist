package service

import (
	"devsupport/backend-go/internal/model"
	"errors"
	"github.com/go-sql-driver/mysql"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// CreateTicketIdempotent relies on the tenant/key unique constraint, so concurrent
// requests and retries after a lost response return the originally accepted ticket.
func CreateTicketIdempotent(db *gorm.DB, ticket *model.Ticket) error {
	if ticket.IdempotencyKey == nil || *ticket.IdempotencyKey == "" {
		return errors.New("ticket idempotency key is required")
	}
	err := db.Create(ticket).Error
	if err == nil {
		return nil
	}
	var duplicate *mysql.MySQLError
	if !errors.As(err, &duplicate) || duplicate.Number != 1062 {
		return err
	}
	var existing model.Ticket
	if lookupErr := db.Clauses(clause.Locking{Strength: "UPDATE"}).Where("tenant_id = ? AND idempotency_key = ?", ticket.TenantID, *ticket.IdempotencyKey).Take(&existing).Error; lookupErr != nil {
		return err
	}
	*ticket = existing
	return nil
}
