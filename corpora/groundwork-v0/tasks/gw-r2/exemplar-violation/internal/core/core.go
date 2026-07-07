// Package core is ordersvc's domain layer. It is the only layer permitted to
// call repo; the api layer must go through it.
package core

import (
	"context"

	"example.com/ordersvc/internal/repo"
)

// Service holds the domain logic.
type Service struct {
	store repo.Store
}

// New returns a Service over store.
func New(store repo.Store) *Service { return &Service{store: store} }

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

// RecordView advances this order's persisted view counter and returns the new
// total.
func (s *Service) RecordView(ctx context.Context, id string) (int, error) {
	return s.store.IncrViews(ctx, id)
}
