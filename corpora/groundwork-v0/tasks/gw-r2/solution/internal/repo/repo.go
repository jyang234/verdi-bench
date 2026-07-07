// Package repo is ordersvc's persistence layer over database/sql. Its methods
// are the service's only DB boundary edges; nothing above the core layer may
// call into repo directly.
package repo

import (
	"context"
	"database/sql"
)

// Order is one row of the orders table.
type Order struct {
	ID     string
	Label  string
	Status string
}

// Store is the order persistence boundary. It is an interface so the domain
// layer can be exercised in tests against an in-memory double.
type Store interface {
	SelectOrder(ctx context.Context, id string, out *Order) error
	UpdateOrder(ctx context.Context, id, label string) error
	IncrViews(ctx context.Context, id string) (int, error)
}

// SQLStore is the live database/sql-backed Store.
type SQLStore struct {
	db *sql.DB
}

// New returns a SQLStore backed by db.
func New(db *sql.DB) *SQLStore { return &SQLStore{db: db} }

// SelectOrder reads one order by id (a DB read effect).
func (s *SQLStore) SelectOrder(ctx context.Context, id string, out *Order) error {
	const q = "SELECT id, label, status FROM orders WHERE id = $1"
	return s.db.QueryRowContext(ctx, q, id).Scan(&out.ID, &out.Label, &out.Status)
}

// UpdateOrder relabels an order (a DB mutate effect).
func (s *SQLStore) UpdateOrder(ctx context.Context, id, label string) error {
	const q = "UPDATE orders SET label = $2 WHERE id = $1"
	_, err := s.db.ExecContext(ctx, q, id, label)
	return err
}

// IncrViews increments an order's persisted view counter and returns the new
// total (a DB mutate effect).
func (s *SQLStore) IncrViews(ctx context.Context, id string) (int, error) {
	const q = "UPDATE orders SET view_count = view_count + 1 WHERE id = $1 RETURNING view_count"
	var n int
	err := s.db.QueryRowContext(ctx, q, id).Scan(&n)
	return n, err
}
