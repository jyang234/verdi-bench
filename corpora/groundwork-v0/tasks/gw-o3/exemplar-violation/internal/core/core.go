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

// Approve publishes the "approved" event and records the approval.
func (s *Service) Approve(id string) {
	bus.Publish("approved " + id)
	audit.Write("approved " + id)
}
