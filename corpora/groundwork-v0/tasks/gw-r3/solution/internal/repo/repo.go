// Package repo is billingsvc's persistence layer over database/sql. Its methods
// are the service's only DB boundary edges; nothing above the core layer may
// call into repo directly.
package repo

import (
	"context"
	"database/sql"
)

// Invoice is one row of the invoices table.
type Invoice struct {
	ID        string
	Customer  string
	Amount    int
	Finalized bool
}

// Store is the invoice persistence boundary. It is an interface so the domain
// layer can be exercised in tests against an in-memory double.
type Store interface {
	SelectInvoice(ctx context.Context, id string, out *Invoice) error
	InsertInvoice(ctx context.Context, id, customer string, amount int) error
	InsertAuditLog(ctx context.Context, id, action string) error
	UpdateInvoiceFinalized(ctx context.Context, id, actor string) error
	InsertReceipt(ctx context.Context, id string) error
}

// SQLStore is the live database/sql-backed Store.
type SQLStore struct {
	db *sql.DB
}

// New returns a SQLStore backed by db.
func New(db *sql.DB) *SQLStore { return &SQLStore{db: db} }

// SelectInvoice reads one invoice by id (a DB read effect).
func (s *SQLStore) SelectInvoice(ctx context.Context, id string, out *Invoice) error {
	const q = "SELECT id, customer, amount, finalized FROM invoices WHERE id = $1"
	return s.db.QueryRowContext(ctx, q, id).Scan(&out.ID, &out.Customer, &out.Amount, &out.Finalized)
}

// InsertInvoice creates an invoice row (a DB mutate effect on invoices).
func (s *SQLStore) InsertInvoice(ctx context.Context, id, customer string, amount int) error {
	const q = "INSERT INTO invoices (id, customer, amount) VALUES ($1, $2, $3)"
	_, err := s.db.ExecContext(ctx, q, id, customer, amount)
	return err
}

// InsertAuditLog appends an audit record (a DB mutate effect on audit_log).
func (s *SQLStore) InsertAuditLog(ctx context.Context, id, action string) error {
	const q = "INSERT INTO audit_log (invoice_id, action) VALUES ($1, $2)"
	_, err := s.db.ExecContext(ctx, q, id, action)
	return err
}

// UpdateInvoiceFinalized marks an invoice finalized and records who finalized it
// in the invoice's own columns (a DB mutate effect on invoices).
func (s *SQLStore) UpdateInvoiceFinalized(ctx context.Context, id, actor string) error {
	const q = "UPDATE invoices SET finalized = true, finalized_by = $2 WHERE id = $1"
	_, err := s.db.ExecContext(ctx, q, id, actor)
	return err
}

// InsertReceipt issues a receipt row for an invoice (a DB mutate effect on
// receipts).
func (s *SQLStore) InsertReceipt(ctx context.Context, id string) error {
	const q = "INSERT INTO receipts (invoice_id) VALUES ($1)"
	_, err := s.db.ExecContext(ctx, q, id)
	return err
}
