// Package repo is inboxsvc's persistence layer over database/sql.
package repo

import (
	"context"
	"database/sql"
)

// Message is one row of the messages table.
type Message struct {
	ID      string
	Subject string
	Body    string
}

// Store is the persistence boundary.
type Store interface {
	SelectMessage(ctx context.Context, id string, out *Message) error
	InsertReceipt(ctx context.Context, id string) error
}

// SQLStore is the live database/sql-backed Store.
type SQLStore struct{ db *sql.DB }

// New returns a SQLStore backed by db.
func New(db *sql.DB) *SQLStore { return &SQLStore{db: db} }

// SelectMessage reads one message (a DB read effect).
func (s *SQLStore) SelectMessage(ctx context.Context, id string, out *Message) error {
	const q = "SELECT id, subject, body FROM messages WHERE id = $1"
	return s.db.QueryRowContext(ctx, q, id).Scan(&out.ID, &out.Subject, &out.Body)
}

// InsertReceipt appends a receipt row for a message (a DB write effect).
func (s *SQLStore) InsertReceipt(ctx context.Context, id string) error {
	const q = "INSERT INTO receipts (message_id) VALUES ($1)"
	_, err := s.db.ExecContext(ctx, q, id)
	return err
}
