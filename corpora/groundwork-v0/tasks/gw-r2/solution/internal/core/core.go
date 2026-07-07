// Package core is ordersvc's domain layer. It is the only layer permitted to
// call repo; the api layer must go through it.
package core

import (
	"context"
	"sync"

	"example.com/ordersvc/internal/repo"
)

// Service holds the domain logic. views is an in-process, per-order read
// counter guarded by mu; it is intentionally not persisted, so serving a read
// performs no DB write.
type Service struct {
	store repo.Store
	mu    sync.Mutex
	views map[string]int
}

// New returns a Service over store.
func New(store repo.Store) *Service {
	return &Service{store: store, views: make(map[string]int)}
}

// GetOrder reads one order (one DB read).
func (s *Service) GetOrder(ctx context.Context, id string) (repo.Order, error) {
	var o repo.Order
	if err := s.store.SelectOrder(ctx, id, &o); err != nil {
		return repo.Order{}, err
	}
	return o, nil
}

// RenameOrder relabels an order (one DB write).
func (s *Service) RenameOrder(ctx context.Context, id, label string) error {
	return s.store.UpdateOrder(ctx, id, label)
}

// RecordView advances this order's in-process view counter and returns the new
// total. The count lives in memory only, so a read route stays read-only at the
// database boundary.
func (s *Service) RecordView(_ context.Context, id string) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.views[id]++
	return s.views[id], nil
}
