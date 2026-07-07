// Package repo is deskflow's persistence layer over database/sql. Its methods
// are the service's only DB boundary edges; nothing above the core layer may
// call into repo directly.
package repo

import (
	"context"
	"database/sql"
)

// Ticket is one row of the tickets table.
type Ticket struct {
	ID      string
	Subject string
	Status  string
}

// Store is the ticket persistence boundary. It is an interface so the domain
// layer can be exercised in tests against an in-memory double.
type Store interface {
	SelectTicket(ctx context.Context, id string, out *Ticket) error
	UpdateTicket(ctx context.Context, id, subject string) error
	InsertAudit(ctx context.Context, id, action string) error
	CountAudit(ctx context.Context, id string) (int, error)
}

// SQLStore is the live database/sql-backed Store.
type SQLStore struct {
	db *sql.DB
}

// New returns a SQLStore backed by db.
func New(db *sql.DB) *SQLStore { return &SQLStore{db: db} }

// SelectTicket reads one ticket by id (a DB read effect).
func (s *SQLStore) SelectTicket(ctx context.Context, id string, out *Ticket) error {
	const q = "SELECT id, subject, status FROM tickets WHERE id = $1"
	return s.db.QueryRowContext(ctx, q, id).Scan(&out.ID, &out.Subject, &out.Status)
}

// UpdateTicket renames a ticket (a DB mutate effect).
func (s *SQLStore) UpdateTicket(ctx context.Context, id, subject string) error {
	const q = "UPDATE tickets SET subject = $2 WHERE id = $1"
	_, err := s.db.ExecContext(ctx, q, id, subject)
	return err
}

// InsertAudit appends an audit record (a DB mutate effect).
func (s *SQLStore) InsertAudit(ctx context.Context, id, action string) error {
	const q = "INSERT INTO audit_log (ticket_id, action) VALUES ($1, $2)"
	_, err := s.db.ExecContext(ctx, q, id, action)
	return err
}

// CountAudit counts a ticket's audit records (a DB read effect).
func (s *SQLStore) CountAudit(ctx context.Context, id string) (int, error) {
	const q = "SELECT count(*) FROM audit_log WHERE ticket_id = $1"
	var n int
	err := s.db.QueryRowContext(ctx, q, id).Scan(&n)
	return n, err
}
