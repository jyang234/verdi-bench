// Package core is feedsvc's domain layer.
package core

import (
	"context"

	"example.com/feedsvc/internal/emit"
	"example.com/feedsvc/internal/repo"
)

// Service holds the domain logic.
type Service struct {
	store   repo.Store
	emitter emit.Emitter
}

// New returns a Service over store and the reaction emitter.
func New(store repo.Store, emitter emit.Emitter) *Service {
	return &Service{store: store, emitter: emitter}
}

// GetItem reads one feed item (one DB read).
func (s *Service) GetItem(ctx context.Context, id string) (repo.Item, error) {
	var it repo.Item
	if err := s.store.SelectItem(ctx, id, &it); err != nil {
		return repo.Item{}, err
	}
	return it, nil
}

// React emits an activity event for a reaction to a feed item (a write route).
func (s *Service) React(ctx context.Context, id string) error {
	return s.emitter.Emit(ctx, id)
}
