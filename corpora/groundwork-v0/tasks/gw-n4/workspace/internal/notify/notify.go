// Package notify publishes subscriber-lifecycle events. Created publishes a
// statically-named event the graph can resolve; the dynamic (parameterized) form
// the graph cannot name is added by the feature.
package notify

import (
	"context"

	"example.com/eventsvc/internal/bus"
	"example.com/eventsvc/internal/encode"
)

// Notifier publishes subscriber-lifecycle events.
type Notifier struct {
	bus *bus.Bus
}

// New returns a Notifier over b.
func New(b *bus.Bus) *Notifier { return &Notifier{bus: b} }

// Created publishes a statically-named event — a resolvable boundary edge. It
// marshals through encode.Marshal (a reflect call), so any route reaching it
// runs into a reflect blind spot.
func (n *Notifier) Created(ctx context.Context, sub any) error {
	return n.bus.Publish(ctx, "subscriber.created", encode.Marshal(sub))
}
