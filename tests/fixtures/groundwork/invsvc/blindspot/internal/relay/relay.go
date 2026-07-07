// Package relay publishes events two ways on purpose: a static name the graph can
// resolve, and a dynamic (parameterized) name it cannot. Both render their
// payload through the reflect-based shape package, so any route reaching them
// crosses the reflect frontier.
package relay

import (
	"context"

	"example.com/alertsvc/internal/channel"
	"example.com/alertsvc/internal/shape"
)

// Relay publishes lifecycle events.
type Relay struct {
	channel *channel.Channel
}

// New returns a Relay over c.
func New(c *channel.Channel) *Relay { return &Relay{channel: c} }

// Created publishes a statically-named event — a resolvable boundary edge whose
// payload is rendered reflectively.
func (r *Relay) Created(ctx context.Context, item any) error {
	return r.channel.Publish(ctx, "item.created", shape.Marshal(item))
}

// Dynamic publishes an event whose name is chosen at runtime — a <dynamic> edge
// the static graph cannot name.
func (r *Relay) Dynamic(ctx context.Context, event string, item any) error {
	return r.channel.Publish(ctx, event, shape.Marshal(item))
}
