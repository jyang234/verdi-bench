// Package core is catalogsvc's domain layer, the only layer that calls repo.
package core

import (
	"context"

	"example.com/catalogsvc/internal/repo"
)

// Service holds the domain logic.
type Service struct {
	store repo.Store
}

// New returns a Service over store.
func New(store repo.Store) *Service { return &Service{store: store} }

// GetProduct reads one product (one DB read).
func (s *Service) GetProduct(ctx context.Context, id string) (repo.Product, error) {
	var p repo.Product
	if err := s.store.SelectProduct(ctx, id, &p); err != nil {
		return repo.Product{}, err
	}
	return p, nil
}

// Rename renames a product and records an audit entry (two DB writes).
func (s *Service) Rename(ctx context.Context, id, name string) error {
	if err := s.store.UpdateProduct(ctx, id, name); err != nil {
		return err
	}
	return s.store.InsertAudit(ctx, id, "rename")
}

// Create inserts a new product (one DB write).
func (s *Service) Create(ctx context.Context, p repo.Product) error {
	return s.store.InsertProduct(ctx, p)
}
