// Package core is feedsvc's domain layer.
package core

import (
	"context"

	"example.com/feedsvc/internal/emit"
	"example.com/feedsvc/internal/repo"
)

// Service holds the domain logic.
type Service struct {
	store    repo.Store
	emitter  emit.Emitter
	activity emit.Emitter
}

// New returns a Service over store, the reaction emitter, and the read-activity
// emitter.
func New(store repo.Store, emitter, activity emit.Emitter) *Service {
	return &Service{store: store, emitter: emitter, activity: activity}
}

// GetItem reads one feed item and emits a read-activity event (one DB read; the
// event's effect depends on the wired read-activity emitter).
func (s *Service) GetItem(ctx context.Context, id string) (repo.Item, error) {
	var it repo.Item
	if err := s.store.SelectItem(ctx, id, &it); err != nil {
		return repo.Item{}, err
	}
	if err := s.recordActivity(ctx, id); err != nil {
		return repo.Item{}, err
	}
	return it, nil
}

// recordActivity emits an activity event for a read through the wired
// read-activity emitter.
func (s *Service) recordActivity(ctx context.Context, id string) error {
	return s.activity.Emit(ctx, id)
}

// React emits an activity event for a reaction to a feed item (a write route).
func (s *Service) React(ctx context.Context, id string) error {
	return s.emitter.Emit(ctx, id)
}
