package service

import (
	"context"
	"devsupport/backend-go/internal/model"
	"os"
	"sync"
	"testing"

	"gorm.io/driver/mysql"
	"gorm.io/gorm"
)

func TestCreateTicketRequiresKey(t *testing.T) {
	db, err := gorm.Open(mysql.New(mysql.Config{DSN: "unused:unused@tcp(127.0.0.1:1)/unused", SkipInitializeWithVersion: true}), &gorm.Config{DisableAutomaticPing: true})
	if err != nil {
		t.Fatal(err)
	}
	for _, args := range []map[string]any{nil, {"idempotency_key": " "}} {
		_, err := (Tools{DB: db}).Execute(context.Background(), "create_ticket", args, ToolContext{})
		if err == nil {
			t.Fatal("missing key accepted")
		}
	}
}

// TEST_MYSQL_DSN must reference an isolated test database. No production tables
// are dropped; schema must be initialized first. Each run uses unique tenants
// and deletes only its own records.
func TestTicketIdempotencyMySQL(t *testing.T) {
	dsn := os.Getenv("TEST_MYSQL_DSN")
	if dsn == "" {
		t.Skip("TEST_MYSQL_DSN not set")
	}
	db, err := gorm.Open(mysql.Open(dsn), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	tenant := ID("test", 16)
	defer db.Where("tenant_id IN ?", []string{tenant, tenant + "_other"}).Delete(&model.Ticket{})
	key := "trace:create_ticket"
	var wg sync.WaitGroup
	ids := make(chan string, 8)
	errs := make(chan error, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			v := NewTicket(tenant, "user", "title", "other", "P2")
			v.IdempotencyKey = &key
			if e := CreateTicketIdempotent(db, &v); e != nil {
				errs <- e
				return
			}
			ids <- v.TicketID
		}()
	}
	wg.Wait()
	close(ids)
	close(errs)
	for e := range errs {
		t.Fatal(e)
	}
	var original string
	for id := range ids {
		if original == "" {
			original = id
		}
		if id != original {
			t.Fatalf("duplicate ticket: %s != %s", id, original)
		}
	}
	// Simulate response loss: the caller repeats the operation after commit.
	retry := NewTicket(tenant, "user", "changed title", "other", "P1")
	retry.IdempotencyKey = &key
	if e := CreateTicketIdempotent(db, &retry); e != nil {
		t.Fatal(e)
	}
	if retry.TicketID != original || retry.Title != "title" {
		t.Fatal("retry replaced original ticket")
	}
	other := NewTicket(tenant+"_other", "user", "other tenant", "other", "P2")
	other.IdempotencyKey = &key
	if e := CreateTicketIdempotent(db, &other); e != nil {
		t.Fatal(e)
	}
	if other.TicketID == original {
		t.Fatal("cross tenant deduplication")
	}
	var count int64
	if e := db.Model(&model.Ticket{}).Where("tenant_id = ?", tenant).Count(&count).Error; e != nil || count != 1 {
		t.Fatalf("count %d: %v", count, e)
	}
}
