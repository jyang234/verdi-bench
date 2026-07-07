// Package core is ingestsvc's domain layer.
package core

import "example.com/ingestsvc/internal/store"

// Service holds the domain logic.
type Service struct {
	store *store.Store
}

// New returns a Service over st.
func New(st *store.Store) *Service { return &Service{store: st} }

// Count returns the number of committed rows.
func (s *Service) Count() int { return s.store.Count() }

// Seed loads a single row through a batch, releasing it on every path via the
// deferred discard.
func (s *Service) Seed(row string) error {
	b, err := s.store.OpenBatch()
	if err != nil {
		return err
	}
	defer b.Discard()
	if err := b.Add(row); err != nil {
		return err
	}
	return b.Flush()
}

// Import loads rows through a batch and flushes them.
func (s *Service) Import(rows []string) error {
	b, err := s.store.OpenBatch()
	if err != nil {
		return err
	}
	for _, row := range rows {
		if err := b.Add(row); err != nil {
			return err
		}
	}
	return b.Flush()
}
