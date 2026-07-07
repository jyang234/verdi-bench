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

// Send persists a message, then records a send-audit entry on a background
// goroutine so writing the audit never blocks the send. The audit runs on a
// detached context because the request context may be cancelled once the send
// returns.
func (s *Service) Send(ctx context.Context, msg repo.Message) error {
	if err := s.store.InsertMessage(ctx, msg.ID, msg.Recipient, msg.Body); err != nil {
		return err
	}
	go func() {
		_ = s.store.InsertAudit(context.Background(), msg.ID, "sent")
	}()
	return nil
}
