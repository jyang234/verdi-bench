// Package repo is userdirsvc's persistence layer over database/sql.
package repo

import (
	"context"
	"database/sql"
)

// User is one row of the users table.
type User struct {
	ID    string
	Name  string
	Email string
}

// Store is the persistence boundary.
type Store interface {
	SelectUser(ctx context.Context, id string, out *User) error
	CountGroups(ctx context.Context, userID string) (int, error)
	UpdateUser(ctx context.Context, id, name string) error
	InsertAudit(ctx context.Context, id, action string) error
}

// SQLStore is the live database/sql-backed Store.
type SQLStore struct{ db *sql.DB }

// New returns a SQLStore backed by db.
func New(db *sql.DB) *SQLStore { return &SQLStore{db: db} }

// SelectUser reads one user (a DB read effect).
func (s *SQLStore) SelectUser(ctx context.Context, id string, out *User) error {
	const q = "SELECT id, name, email FROM users WHERE id = $1"
	return s.db.QueryRowContext(ctx, q, id).Scan(&out.ID, &out.Name, &out.Email)
}

// CountGroups reads how many groups a user belongs to (a DB read effect).
func (s *SQLStore) CountGroups(ctx context.Context, userID string) (int, error) {
	const q = "SELECT count(*) FROM memberships WHERE user_id = $1"
	var n int
	if err := s.db.QueryRowContext(ctx, q, userID).Scan(&n); err != nil {
		return 0, err
	}
	return n, nil
}

// UpdateUser renames a user (a DB mutate effect).
func (s *SQLStore) UpdateUser(ctx context.Context, id, name string) error {
	const q = "UPDATE users SET name = $2 WHERE id = $1"
	_, err := s.db.ExecContext(ctx, q, id, name)
	return err
}

// InsertAudit appends an audit record (a DB mutate effect).
func (s *SQLStore) InsertAudit(ctx context.Context, id, action string) error {
	const q = "INSERT INTO audit_log (user_id, action) VALUES ($1, $2)"
	_, err := s.db.ExecContext(ctx, q, id, action)
	return err
}
