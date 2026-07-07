// Package core is userdirsvc's domain layer, the only layer that calls repo.
package core

import (
	"context"

	"example.com/userdirsvc/internal/repo"
)

// Service holds the domain logic.
type Service struct {
	store repo.Store
}

// New returns a Service over store.
func New(store repo.Store) *Service { return &Service{store: store} }

// GetUser reads one user (one DB read).
func (s *Service) GetUser(ctx context.Context, id string) (repo.User, error) {
	var u repo.User
	if err := s.store.SelectUser(ctx, id, &u); err != nil {
		return repo.User{}, err
	}
	return u, nil
}

// Rename renames a user and records an audit entry (two DB writes).
func (s *Service) Rename(ctx context.Context, id, name string) error {
	if err := s.store.UpdateUser(ctx, id, name); err != nil {
		return err
	}
	return s.store.InsertAudit(ctx, id, "rename")
}

// Summary is a user plus a derived group count — a read-only composition.
type Summary struct {
	User       repo.User
	GroupCount int
}

// GetSummary reads a user and their group count and composes a summary through
// core (two DB reads, no writes).
func (s *Service) GetSummary(ctx context.Context, id string) (Summary, error) {
	var u repo.User
	if err := s.store.SelectUser(ctx, id, &u); err != nil {
		return Summary{}, err
	}
	n, err := s.store.CountGroups(ctx, id)
	if err != nil {
		return Summary{}, err
	}
	return Summary{User: u, GroupCount: n}, nil
}
