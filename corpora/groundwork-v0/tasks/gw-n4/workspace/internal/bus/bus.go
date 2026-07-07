// Package bus is eventsvc's event-publish seam (hinted busPublish). Publish's
// first string argument is the event name; when a caller passes a non-constant
// name the static extractor cannot name the event and records both a
// `boundary:bus PUBLISH <dynamic>` edge and a NonConstantBoundaryArg blind spot.
package bus

import "context"

// Event is one published event, retained in memory for observation.
type Event struct {
	Name    string
	Payload []byte
}

// Bus publishes events, recording them in memory.
type Bus struct{ Published []Event }

// New returns a Bus.
func New() *Bus { return &Bus{} }

// Publish emits event with payload. The event name is the first string argument.
func (b *Bus) Publish(ctx context.Context, event string, payload []byte) error {
	_ = ctx
	b.Published = append(b.Published, Event{Name: event, Payload: payload})
	return nil
}
