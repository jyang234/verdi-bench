// Package core is deskflow's domain layer. It is the only layer permitted to
// call repo; the api layer must go through it.
package core

import (
	"context"

	"example.com/deskflow/internal/repo"
)

// Service holds the domain logic.
type Service struct {
	store repo.Store
}

// New returns a Service over store.
func New(store repo.Store) *Service { return &Service{store: store} }

// GetTicket reads one ticket (one DB read).
func (s *Service) GetTicket(ctx context.Context, id string) (repo.Ticket, error) {
	var t repo.Ticket
	if err := s.store.SelectTicket(ctx, id, &t); err != nil {
		return repo.Ticket{}, err
	}
	return t, nil
}

// RenameTicket renames a ticket and records an audit entry (two DB writes).
func (s *Service) RenameTicket(ctx context.Context, id, subject string) error {
	if err := s.store.UpdateTicket(ctx, id, subject); err != nil {
		return err
	}
	return s.store.InsertAudit(ctx, id, "rename")
}

// GetHistory reads a ticket together with its audit-entry count (two DB reads).
func (s *Service) GetHistory(ctx context.Context, id string) (repo.Ticket, int, error) {
	var t repo.Ticket
	if err := s.store.SelectTicket(ctx, id, &t); err != nil {
		return repo.Ticket{}, 0, err
	}
	n, err := s.store.CountAudit(ctx, id)
	if err != nil {
		return repo.Ticket{}, 0, err
	}
	return t, n, nil
}
