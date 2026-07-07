// Package core is accountsvc's domain layer.
package core

import (
	"context"

	"example.com/accountsvc/internal/repo"
	"example.com/accountsvc/internal/views"
)

// Service holds the domain logic.
type Service struct {
	store   repo.Store
	signups views.Counter
	views   views.Counter
}

// New returns a Service over store and the signup counter.
func New(store repo.Store, signups, views views.Counter) *Service {
	return &Service{store: store, signups: signups, views: views}
}

// GetAccount reads one account and records the view (one DB read; the view
// record's effect depends on the wired view counter).
func (s *Service) GetAccount(ctx context.Context, id string) (repo.Account, int, error) {
	var a repo.Account
	if err := s.store.SelectAccount(ctx, id, &a); err != nil {
		return repo.Account{}, 0, err
	}
	n, err := s.views.Bump(ctx, id)
	if err != nil {
		return repo.Account{}, 0, err
	}
	return a, n, nil
}

// Signup records a signup for an account (a write route).
func (s *Service) Signup(ctx context.Context, id string) error {
	_, err := s.signups.Bump(ctx, id)
	return err
}
