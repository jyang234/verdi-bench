// Package repo is feedsvc's persistence layer over database/sql.
package repo

import (
	"context"
	"database/sql"
)

// Item is one row of the feed_items table.
type Item struct {
	ID    string
	Actor string
	Verb  string
}

// Store is the persistence boundary.
type Store interface {
	SelectItem(ctx context.Context, id string, out *Item) error
}

// SQLStore is the live database/sql-backed Store.
type SQLStore struct{ db *sql.DB }

// New returns a SQLStore backed by db.
func New(db *sql.DB) *SQLStore { return &SQLStore{db: db} }

// SelectItem reads one feed item (a DB read effect).
func (s *SQLStore) SelectItem(ctx context.Context, id string, out *Item) error {
	const q = "SELECT id, actor, verb FROM feed_items WHERE id = $1"
	return s.db.QueryRowContext(ctx, q, id).Scan(&out.ID, &out.Actor, &out.Verb)
}
