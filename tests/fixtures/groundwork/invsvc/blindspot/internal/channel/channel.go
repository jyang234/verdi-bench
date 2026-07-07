// Package channel is alertsvc's event-publish seam (hinted busPublish). Publish's
// first string argument is the event name; when a caller passes a non-constant
// name the static extractor cannot name the event and records a
// boundary:bus PUBLISH <dynamic> edge.
package channel

import "context"

// Channel publishes events.
type Channel struct{}

// New returns a Channel.
func New() *Channel { return &Channel{} }

// Publish emits event with payload. The event name is the first string argument.
func (c *Channel) Publish(ctx context.Context, event string, payload []byte) error {
	_ = ctx
	_ = event
	_ = payload
	return nil
}
