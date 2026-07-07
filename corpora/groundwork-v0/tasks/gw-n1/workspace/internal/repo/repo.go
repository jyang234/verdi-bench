// Package repo is catalogsvc's persistence layer over database/sql.
package repo

import (
	"context"
	"database/sql"
)

// Product is one row of the products table.
type Product struct {
	ID    string
	Name  string
	Price int64
}

// Store is the persistence boundary.
type Store interface {
	SelectProduct(ctx context.Context, id string, out *Product) error
	InsertProduct(ctx context.Context, p Product) error
	UpdateProduct(ctx context.Context, id, name string) error
	InsertAudit(ctx context.Context, id, action string) error
}

// SQLStore is the live database/sql-backed Store.
type SQLStore struct{ db *sql.DB }

// New returns a SQLStore backed by db.
func New(db *sql.DB) *SQLStore { return &SQLStore{db: db} }

// SelectProduct reads one product (a DB read effect).
func (s *SQLStore) SelectProduct(ctx context.Context, id string, out *Product) error {
	const q = "SELECT id, name, price FROM products WHERE id = $1"
	return s.db.QueryRowContext(ctx, q, id).Scan(&out.ID, &out.Name, &out.Price)
}

// InsertProduct creates a product (a DB mutate effect).
func (s *SQLStore) InsertProduct(ctx context.Context, p Product) error {
	const q = "INSERT INTO products (id, name, price) VALUES ($1, $2, $3)"
	_, err := s.db.ExecContext(ctx, q, p.ID, p.Name, p.Price)
	return err
}

// UpdateProduct renames a product (a DB mutate effect).
func (s *SQLStore) UpdateProduct(ctx context.Context, id, name string) error {
	const q = "UPDATE products SET name = $2 WHERE id = $1"
	_, err := s.db.ExecContext(ctx, q, id, name)
	return err
}

// InsertAudit appends an audit record (a DB mutate effect).
func (s *SQLStore) InsertAudit(ctx context.Context, id, action string) error {
	const q = "INSERT INTO audit_log (product_id, action) VALUES ($1, $2)"
	_, err := s.db.ExecContext(ctx, q, id, action)
	return err
}
