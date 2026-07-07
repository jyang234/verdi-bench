// Package core is pubsvc's domain layer.
package core

import (
	"example.com/pubsvc/internal/audit"
	"example.com/pubsvc/internal/bus"
)

// Service holds the domain logic.
type Service struct{}

// New returns a Service.
func New() *Service { return &Service{} }

// Notify records an event and publishes it, auditing before the publish.
func (s *Service) Notify(event string) {
	audit.Write(event)
	bus.Publish(event)
}

// Approve records an approval and publishes the "approved" event, auditing
// before the publish.
func (s *Service) Approve(id string) {
	audit.Write("approved " + id)
	bus.Publish("approved " + id)
}
