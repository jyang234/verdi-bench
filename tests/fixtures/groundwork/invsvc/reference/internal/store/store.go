// Package store is invsvc's persistence layer over database/sql (a built-in DB
// classification hint). Its methods are the service's only DB boundary edges.
// Nothing above the catalog layer may call into store directly.
//
// THIS IS THE REFERENCE (correct-solution) VARIANT: it adds CountItems, a new
// read-only query the "show item with sibling count" feature needs. The read
// route gains structure but stays read-only, so groundwork reports
// STRUCTURALLY-CLEAR — the contrast to the violating variant's DB write.
package store

import (
	"context"
	"database/sql"
)

// Item is one row of the items table.
type Item struct {
	ID   string
	Name string
}

// Store persists items. A nil *sql.DB is fine for static analysis; the methods
// are never executed by the graph pipeline.
type Store struct {
	db *sql.DB
}

// New returns a Store backed by db.
func New(db *sql.DB) *Store { return &Store{db: db} }

// FetchItem reads one item by id (a DB read effect).
func (s *Store) FetchItem(ctx context.Context, id string, out *Item) error {
	const q = "SELECT id, name FROM items WHERE id = $1"
	row := s.db.QueryRowContext(ctx, q, id)
	return row.Scan(&out.ID, &out.Name)
}

// CountItems reads how many items exist (a DB read effect) — the reference
// feature's new query, added to the read route without any write.
func (s *Store) CountItems(ctx context.Context) (int, error) {
	const q = "SELECT count(*) FROM items"
	var n int
	err := s.db.QueryRowContext(ctx, q).Scan(&n)
	return n, err
}

// SaveName renames an item (a DB mutate effect).
func (s *Store) SaveName(ctx context.Context, id, name string) error {
	const q = "UPDATE items SET name = $2 WHERE id = $1"
	_, err := s.db.ExecContext(ctx, q, id, name)
	return err
}

// AppendAudit appends an audit record (a DB mutate effect).
func (s *Store) AppendAudit(ctx context.Context, id string) error {
	const q = "INSERT INTO audit_log (item_id) VALUES ($1)"
	_, err := s.db.ExecContext(ctx, q, id)
	return err
}
