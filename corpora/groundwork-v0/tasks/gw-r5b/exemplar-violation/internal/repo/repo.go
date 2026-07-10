// Package repo is mailsvc's persistence layer over database/sql. Its methods are
// the service's only DB boundary edges; nothing above the core layer may call
// into repo directly.
package repo

import (
	"context"
	"database/sql"
)

// Message is one row of the messages table.
type Message struct {
	ID        string
	Recipient string
	Body      string
}

// Store is the message persistence boundary. It is an interface so the domain
// layer can be exercised in tests against an in-memory double.
type Store interface {
	InsertMessage(ctx context.Context, id, recipient, body string) error
	InsertAudit(ctx context.Context, id, action string) error
}

// SQLStore is the live database/sql-backed Store.
type SQLStore struct {
	db *sql.DB
}

// New returns a SQLStore backed by db.
func New(db *sql.DB) *SQLStore { return &SQLStore{db: db} }

// InsertMessage stores a sent message (a DB mutate effect on messages).
func (s *SQLStore) InsertMessage(ctx context.Context, id, recipient, body string) error {
	const q = "INSERT INTO messages (id, recipient, body) VALUES ($1, $2, $3)"
	_, err := s.db.ExecContext(ctx, q, id, recipient, body)
	return err
}

// InsertAudit appends a send-audit record (a DB mutate effect on message_audit).
func (s *SQLStore) InsertAudit(ctx context.Context, id, action string) error {
	const q = "INSERT INTO message_audit (message_id, action) VALUES ($1, $2)"
	_, err := s.db.ExecContext(ctx, q, id, action)
	return err
}
