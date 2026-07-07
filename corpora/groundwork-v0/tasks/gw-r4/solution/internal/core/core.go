// Package core is docsvc's domain layer. It is the only layer permitted to call
// repo; the api layer must go through it. Every mutating operation is expressed
// as an authorized action: it hands the actual repo write to Authorize, which
// runs it only after the access check passes. That funnels every write through
// Authorize so none can skip the guard.
package core

import (
	"context"
	"errors"

	"example.com/docsvc/internal/repo"
)

// Service holds the domain logic.
type Service struct {
	store repo.Store
}

// New returns a Service over store.
func New(store repo.Store) *Service { return &Service{store: store} }

// Get reads one document (one DB read); reads are not gated by Authorize.
func (s *Service) Get(ctx context.Context, id string) (repo.Doc, error) {
	var d repo.Doc
	if err := s.store.SelectDoc(ctx, id, &d); err != nil {
		return repo.Doc{}, err
	}
	return d, nil
}

// Authorize verifies the caller may mutate document id, then runs action. It is
// the mandatory waypoint for every write: the repo mutation is performed by
// action, which Authorize invokes only after the check passes, so a caller
// cannot reach the write without going through here.
func (s *Service) Authorize(ctx context.Context, id string, action func() error) error {
	if id == "" {
		return errors.New("forbidden: empty document id")
	}
	return action()
}

// Create stores a new document behind the authorization check.
func (s *Service) Create(ctx context.Context, id, title, body string) error {
	return s.Authorize(ctx, id, func() error {
		return s.store.InsertDoc(ctx, id, title, body)
	})
}

// Rename retitles a document behind the authorization check.
func (s *Service) Rename(ctx context.Context, id, title string) error {
	return s.Authorize(ctx, id, func() error {
		return s.store.UpdateDoc(ctx, id, title)
	})
}

// Delete removes a document behind the authorization check.
func (s *Service) Delete(ctx context.Context, id string) error {
	return s.Authorize(ctx, id, func() error {
		return s.store.DeleteDoc(ctx, id)
	})
}
