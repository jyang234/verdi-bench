// Package repo is docsvc's persistence layer over database/sql. Its methods are
// the service's only DB boundary edges; nothing above the core layer may call
// into repo directly.
package repo

import (
	"context"
	"database/sql"
)

// Doc is one row of the documents table.
type Doc struct {
	ID    string
	Title string
	Body  string
}

// Store is the document persistence boundary. It is an interface so the domain
// layer can be exercised in tests against an in-memory double.
type Store interface {
	SelectDoc(ctx context.Context, id string, out *Doc) error
	InsertDoc(ctx context.Context, id, title, body string) error
	UpdateDoc(ctx context.Context, id, title string) error
}

// SQLStore is the live database/sql-backed Store.
type SQLStore struct {
	db *sql.DB
}

// New returns a SQLStore backed by db.
func New(db *sql.DB) *SQLStore { return &SQLStore{db: db} }

// SelectDoc reads one document by id (a DB read effect).
func (s *SQLStore) SelectDoc(ctx context.Context, id string, out *Doc) error {
	const q = "SELECT id, title, body FROM documents WHERE id = $1"
	return s.db.QueryRowContext(ctx, q, id).Scan(&out.ID, &out.Title, &out.Body)
}

// InsertDoc creates a document row (a DB mutate effect).
func (s *SQLStore) InsertDoc(ctx context.Context, id, title, body string) error {
	const q = "INSERT INTO documents (id, title, body) VALUES ($1, $2, $3)"
	_, err := s.db.ExecContext(ctx, q, id, title, body)
	return err
}

// UpdateDoc retitles a document (a DB mutate effect).
func (s *SQLStore) UpdateDoc(ctx context.Context, id, title string) error {
	const q = "UPDATE documents SET title = $2 WHERE id = $1"
	_, err := s.db.ExecContext(ctx, q, id, title)
	return err
}
