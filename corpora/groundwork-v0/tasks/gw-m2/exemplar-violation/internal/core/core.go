// Package core is inboxsvc's domain layer.
package core

import (
	"context"

	"example.com/inboxsvc/internal/delivery"
	"example.com/inboxsvc/internal/repo"
)

// Service holds the domain logic.
type Service struct {
	store    repo.Store
	delivery delivery.Deliverer
	receipts delivery.Deliverer
}

// New returns a Service over store, the delivery recorder, and the read-receipt
// recorder.
func New(store repo.Store, deliverer, receipts delivery.Deliverer) *Service {
	return &Service{store: store, delivery: deliverer, receipts: receipts}
}

// GetMessage reads one message and records a read receipt (one DB read; the
// receipt's effect depends on the wired read-receipt recorder).
func (s *Service) GetMessage(ctx context.Context, id string) (repo.Message, error) {
	var m repo.Message
	if err := s.store.SelectMessage(ctx, id, &m); err != nil {
		return repo.Message{}, err
	}
	if err := s.receipts.Record(ctx, id); err != nil {
		return repo.Message{}, err
	}
	return m, nil
}

// Deliver records a delivery receipt for a message (a write route).
func (s *Service) Deliver(ctx context.Context, id string) error {
	return s.delivery.Record(ctx, id)
}
