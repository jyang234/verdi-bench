// Package core is mailsvc's domain layer. It is the only layer permitted to call
// repo; the api layer must go through it.
package core

import (
	"context"

	"example.com/mailsvc/internal/repo"
)

// Service holds the domain logic.
type Service struct {
	store repo.Store
}

// New returns a Service over store.
func New(store repo.Store) *Service { return &Service{store: store} }

// Send persists a message and records a send-audit entry. Both writes run
// synchronously on the request goroutine; the audit is appended after the
// message is stored.
func (s *Service) Send(ctx context.Context, msg repo.Message) error {
	if err := s.store.InsertMessage(ctx, msg.ID, msg.Recipient, msg.Body); err != nil {
		return err
	}
	return s.store.InsertAudit(ctx, msg.ID, "sent")
}
