// Package store is ingestsvc's persistence layer: an in-memory sink whose
// OpenBatch acquires a batch that Flush or Discard releases.
package store

import "errors"

// ErrRejected is returned when a row fails the batch's admission check.
var ErrRejected = errors.New("row rejected")

// Store holds committed rows.
type Store struct{ rows []string }

// New returns an empty Store.
func New() *Store { return &Store{} }

// Count returns the number of committed rows.
func (s *Store) Count() int { return len(s.rows) }

// Batch buffers rows until Flush commits them or Discard drops them.
type Batch struct {
	parent *Store
	buf    []string
}

// OpenBatch acquires a batch (the obligation's acquire anchor).
func (s *Store) OpenBatch() (*Batch, error) {
	return &Batch{parent: s}, nil
}

// Add buffers a row, or rejects an empty one.
func (b *Batch) Add(row string) error {
	if row == "" {
		return ErrRejected
	}
	b.buf = append(b.buf, row)
	return nil
}

// Flush commits the buffered rows and releases the batch.
func (b *Batch) Flush() error {
	b.parent.rows = append(b.parent.rows, b.buf...)
	b.buf = nil
	return nil
}

// Discard drops the buffered rows and releases the batch.
func (b *Batch) Discard() { b.buf = nil }
