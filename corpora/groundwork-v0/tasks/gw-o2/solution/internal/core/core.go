// Package core is limitsvc's domain layer.
package core

import (
	"errors"

	"example.com/limitsvc/internal/store"
)

// ErrInvalid is returned when a payload fails validation.
var ErrInvalid = errors.New("invalid payload")

// Service holds the domain logic.
type Service struct {
	lim  *store.Limiter
	done []string
}

// New returns a Service over lim.
func New(lim *store.Limiter) *Service { return &Service{lim: lim} }

// Done reports the number of successfully completed work units.
func (s *Service) Done() int { return len(s.done) }

// Run does a unit of rate-limited work under a slot, releasing on every path.
func (s *Service) Run(job string) error {
	if err := s.lim.Acquire(); err != nil {
		return err
	}
	defer s.lim.Release()
	s.done = append(s.done, job)
	return nil
}

// Process handles a payload under a slot, releasing it on every path via the
// deferred release.
func (s *Service) Process(payload string) error {
	if err := s.lim.Acquire(); err != nil {
		return err
	}
	defer s.lim.Release()
	if err := validate(payload); err != nil {
		return err
	}
	s.done = append(s.done, payload)
	return nil
}

// validate rejects an empty payload.
func validate(payload string) error {
	if payload == "" {
		return ErrInvalid
	}
	return nil
}
