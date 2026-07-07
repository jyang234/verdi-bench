// Package core is billingsvc's domain layer. It is the only layer permitted to
// call repo; the api layer must go through it.
package core

import (
	"context"

	"example.com/billingsvc/internal/repo"
)

// Service holds the domain logic.
type Service struct {
	store repo.Store
}

// New returns a Service over store.
func New(store repo.Store) *Service { return &Service{store: store} }

// GetInvoice reads one invoice (one DB read).
func (s *Service) GetInvoice(ctx context.Context, id string) (repo.Invoice, error) {
	var inv repo.Invoice
	if err := s.store.SelectInvoice(ctx, id, &inv); err != nil {
		return repo.Invoice{}, err
	}
	return inv, nil
}

// CreateInvoice stores a new invoice and records an audit entry (two DB writes:
// INSERT invoices + INSERT audit_log).
func (s *Service) CreateInvoice(ctx context.Context, inv repo.Invoice) error {
	if err := s.store.InsertInvoice(ctx, inv.ID, inv.Customer, inv.Amount); err != nil {
		return err
	}
	return s.store.InsertAuditLog(ctx, inv.ID, "create")
}
