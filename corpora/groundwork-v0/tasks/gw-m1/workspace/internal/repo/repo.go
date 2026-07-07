// Package repo is accountsvc's persistence layer over database/sql.
package repo

import (
	"context"
	"database/sql"
)

// Account is one row of the accounts table.
type Account struct {
	ID   string
	Name string
}

// Store is the persistence boundary.
type Store interface {
	SelectAccount(ctx context.Context, id string, out *Account) error
	IncrCounter(ctx context.Context, name, id string) error
}

// SQLStore is the live database/sql-backed Store.
type SQLStore struct{ db *sql.DB }

// New returns a SQLStore backed by db.
func New(db *sql.DB) *SQLStore { return &SQLStore{db: db} }

// SelectAccount reads one account (a DB read effect).
func (s *SQLStore) SelectAccount(ctx context.Context, id string, out *Account) error {
	const q = "SELECT id, name FROM accounts WHERE id = $1"
	return s.db.QueryRowContext(ctx, q, id).Scan(&out.ID, &out.Name)
}

// IncrCounter bumps a named per-account counter (a DB mutate effect).
func (s *SQLStore) IncrCounter(ctx context.Context, name, id string) error {
	const q = "UPDATE counters SET n = n + 1 WHERE name = $1 AND account_id = $2"
	_, err := s.db.ExecContext(ctx, q, name, id)
	return err
}
